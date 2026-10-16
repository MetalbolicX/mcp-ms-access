"""Owner-thread regression coverage for the DAO documentation-inspection path (S1).

DAO is an in-proc COM server: the apartment-affine ``Database`` handle must be driven
from the STA thread that opened it. ``open_inspection_port`` used to hand the raw
handle to ``DaoInspectionPort``, whose reads then ran inline on whatever thread held
the port. These tests pin every COM surface the extraction touches back onto the
dispatcher's owner thread.

The rig is a real ``ComDispatcher`` (real STA thread, real ``call`` queue, real
``reopen_dao_database_read_only``) whose DAO engine is replaced by a recording fake
handle, so nothing here touches Access, ACE, or a database file.
"""

from __future__ import annotations

import threading
import types
from typing import Any

from ms_access_mcp.application.documentation_inspect import (
    DocumentationInspectService,
    inspect_open_database,
    open_inspection_port,
)
from ms_access_mcp.backends.dispatcher import ComDispatcher

# DAO ``dbLong``. The extraction only forwards the code to ``_access_type_name``.
_FIELD_TYPE_LONG = 4
# DAO ``dbText``, used for the fake query parameter type code.
_FIELD_TYPE_TEXT = 10
_FAKE_DB_PATH = "C:\\db\\core.accdb"


class _ComLog:
    """Thread-aware log of every fake COM surface access."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, int]] = []

    def record(self, surface: str) -> None:
        self.entries.append((surface, threading.get_ident()))

    def thread_idents(self) -> set[int]:
        return {ident for _, ident in self.entries}

    def surfaces(self) -> list[str]:
        return [surface for surface, _ in self.entries]


class _FakeComObject:
    """Recording stand-in for one DAO COM object.

    On the real proxy an attribute read is a COM property read, a collection
    iteration/``Count``/index read is a DAO collection read, and ``Close()`` is a COM
    call, so each of them records the executing thread ident.
    """

    def __init__(self, log: _ComLog, path: str, attributes: dict[str, Any]) -> None:
        self._log = log
        self._path = path
        self._attributes = attributes

    def __getattr__(self, item: str) -> Any:
        attributes = self.__dict__.get("_attributes")
        if attributes is None or item not in attributes:
            raise AttributeError(item)
        self._log.record(f"{self._path}.{item}")
        return attributes[item]

    def record_close(self) -> None:
        self._log.record(f"{self._path}.Close()")
        self.closed = True


class _FakeCollection(_FakeComObject):
    """Recording DAO collection: iteration, ``Count``, and indexing are COM reads."""

    def __init__(
        self,
        log: _ComLog,
        path: str,
        items: list[Any],
        *,
        by_name: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(log, path, {"Count": len(items)})
        self._items = items
        self._by_name = by_name or {}

    def __iter__(self) -> Any:
        self._log.record(f"{self._path}[]")
        return iter(self._items)

    def __getitem__(self, key: Any) -> Any:
        self._log.record(f"{self._path}[{key!r}]")
        if isinstance(key, str):
            return self._by_name[key]
        return self._items[key]

    def __call__(self, index: int) -> Any:
        self._log.record(f"{self._path}({index})")
        return self._items[index]


def _recording_catalogue(log: _ComLog) -> _FakeComObject:
    """Build a fake DAO catalogue with two tables, an index, a query, and a relationship."""

    def _field(name: str, type_code: int, **extra: Any) -> _FakeComObject:
        attributes = {
            "Name": name,
            "Type": type_code,
            "Size": 4,
            "Required": False,
            "AllowZeroLength": False,
            "DefaultValue": None,
            **extra,
        }
        return _FakeComObject(log, f"field({name})", attributes)

    def _table(name: str, fields: list[_FakeComObject], indexes: list[_FakeComObject]) -> _FakeComObject:
        attributes = {
            "Name": name,
            "Attributes": 0,
            "Fields": _FakeCollection(log, f"{name}.Fields", fields),
            "Indexes": _FakeCollection(log, f"{name}.Indexes", indexes),
            "Connect": "",
            "SourceTableName": "",
        }
        return _FakeComObject(log, f"tdef({name})", attributes)

    primary_key = _FakeComObject(
        log,
        "idx(PrimaryKey)",
        {
            "Name": "PrimaryKey",
            "Fields": _FakeCollection(log, "Customers.PrimaryKey.Fields", [_field("CustomerID", _FIELD_TYPE_LONG)]),
            "Unique": True,
            "Primary": True,
            "IgnoreNulls": False,
        },
    )
    customers = _table("Customers", [_field("CustomerID", _FIELD_TYPE_LONG, Required=True)], [primary_key])
    orders = _table("Orders", [_field("OrderID", _FIELD_TYPE_LONG)], [])
    table_defs = _FakeCollection(
        log,
        "db.TableDefs",
        [customers, orders],
        by_name={"Customers": customers, "Orders": orders},
    )

    parameters = _FakeCollection(
        log,
        "qryOrdersByCustomer.Parameters",
        [_FakeComObject(log, "param(customer)", {"Name": "customer", "Type": _FIELD_TYPE_TEXT})],
    )
    query = _FakeComObject(
        log,
        "qdef(qryOrdersByCustomer)",
        {
            "Name": "qryOrdersByCustomer",
            "SQL": "SELECT * FROM Orders WHERE CustomerID = [customer]",
            "Type": 0,
            "Parameters": parameters,
        },
    )
    query_defs = _FakeCollection(
        log,
        "db.QueryDefs",
        [query],
        by_name={"qryOrdersByCustomer": query},
    )

    relationship = _FakeComObject(
        log,
        "rel(FK_Orders_Customers)",
        {
            "Name": "FK_Orders_Customers",
            "Fields": _FakeCollection(
                log,
                "rel(FK_Orders_Customers).Fields",
                [_FakeComObject(log, "relField(CustomerID)", {"Name": "CustomerID", "ForeignName": "CustomerID"})],
            ),
            "ForeignTable": "Orders",
            "Table": "Customers",
            "Attributes": 0,
        },
    )
    relations = _FakeCollection(log, "db.Relations", [relationship])

    database = _FakeComObject(
        log,
        "db",
        {"TableDefs": table_defs, "QueryDefs": query_defs, "Relations": relations},
    )
    database._attributes["Close"] = database.record_close  # noqa: SLF001
    return database


class _RecordingStaDispatcher(ComDispatcher):
    """Real STA dispatcher rig whose DAO engine is a recording fake handle.

    ``open_dao_database`` is replaced so the rig never touches a real DAO engine or a
    database file, but every install still happens inside ``self.call`` -- that is, on
    the real STA thread this production dispatcher starts.
    """

    def __init__(self, handle: _FakeComObject) -> None:
        super().__init__()
        self._handle = handle
        self.open_calls: list[tuple[str, str, bool]] = []
        self.handle_reads: list[int] = []
        self.call_count = 0

    @property
    def current_db(self) -> Any:
        self.handle_reads.append(threading.get_ident())
        return self._current_db

    @property
    def owner_thread_ident(self) -> int | None:
        return self._thread.ident if self._thread is not None else None

    def call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        self.call_count += 1
        return super().call(fn, *args, **kwargs)

    def open_dao_database(self, db_path: str, password: str = "", read_only: bool = False) -> Any:
        self.open_calls.append((db_path, password, read_only))

        def _install() -> Any:
            self._current_db = self._handle
            self._db_path = db_path
            self._db_password = password
            return self._handle

        return self.call(_install)

    def connect(self, db_path: str = _FAKE_DB_PATH) -> None:
        """Stand in for the adapter's first (read-write) tool open."""
        self.open_dao_database(db_path)


class _WiringDatabase:
    """Minimal ``DatabaseUseCases`` stand-in: ``inspect_open_database`` reads ``_adapter._dispatcher``."""

    def __init__(self, dispatcher: ComDispatcher) -> None:
        self._adapter = types.SimpleNamespace(_dispatcher=dispatcher)


class _Rig:
    """Started dispatcher rig with a recording fake DAO handle, closed on exit."""

    def __init__(self) -> None:
        self.log = _ComLog()
        self.handle = _recording_catalogue(self.log)
        self.dispatcher = _RecordingStaDispatcher(self.handle)

    def __enter__(self) -> _RecordingStaDispatcher:
        self.dispatcher.start()
        self.dispatcher.connect()
        return self.dispatcher

    def __exit__(self, *exc_info: Any) -> None:
        self.dispatcher.shutdown()


def test_open_inspection_port_drives_every_com_read_on_the_owner_thread() -> None:
    """S1: the public inspection seam must read DAO only on the dispatcher owner thread.

    RED before the fix: ``DaoInspectionPort`` ran ``SchemaInspector`` inline on the
    caller thread, so every recorded ident belonged to the caller, not the STA thread.
    """
    rig = _Rig()
    caller_ident = threading.get_ident()
    with rig as dispatcher:
        sta_ident = dispatcher.owner_thread_ident
        assert sta_ident is not None and sta_ident != caller_ident, (
            "the rig must drive the inspection from a thread other than the STA thread"
        )

        port = open_inspection_port(dispatcher)
        document = DocumentationInspectService().inspect(port, tables=None, queries=None)

        assert set(dispatcher.handle_reads) == {sta_ident}, dispatcher.handle_reads

    assert {table.name for table in document.tables} == {"Customers", "Orders"}
    assert [query.name for query in document.queries] == ["qryOrdersByCustomer"]
    assert rig.log.thread_idents() == {sta_ident}, rig.log.entries
    # The extraction really walked each COM surface, so the ident assertion is not vacuous.
    assert {"db.TableDefs", "db.QueryDefs", "db.Relations"} <= set(rig.log.surfaces())


def test_inspect_open_database_keeps_one_dispatched_unit_without_reentrant_enqueue() -> None:
    """The whole-extraction dispatch stays a single unit: no port call re-enters the queue.

    PRESERVE: ``inspect_open_database`` already dispatched the entire extraction as one
    unit. Routing must keep that exactly (the read-only reopen plus one extraction
    dispatch) instead of enqueuing per-object calls that the blocked owner thread could
    never service. ``DISPATCH_TIMEOUT`` is shortened so a re-entrant enqueue fails fast
    rather than hanging for the production 120s timeout.
    """
    rig = _Rig()
    rig.dispatcher.DISPATCH_TIMEOUT = 5.0
    with rig as dispatcher:
        sta_ident = dispatcher.owner_thread_ident
        dispatched_before = dispatcher.call_count
        document = inspect_open_database(_WiringDatabase(dispatcher), tables=None, queries=None)
        dispatched_for_inspection = dispatcher.call_count - dispatched_before

    assert dispatched_for_inspection == 2, (
        "expected exactly the read-only reopen and one whole-extraction dispatch; "
        f"observed {dispatched_for_inspection} dispatch(es) for {rig.log.surfaces()}"
    )
    assert rig.log.thread_idents() == {sta_ident}, rig.log.entries
    assert {table.name for table in document.tables} == {"Customers", "Orders"}


def test_open_inspection_port_never_reads_the_handle_off_the_owner_thread() -> None:
    """PRESERVE: the read-only reopen stays, and no caller-thread DAO proxy is taken.

    RED before the fix: ``open_inspection_port`` read ``dispatcher.current_db`` on the
    caller thread and stored that reference in the port.
    """
    rig = _Rig()
    caller_ident = threading.get_ident()
    with rig as dispatcher:
        dispatcher.handle_reads.clear()
        open_inspection_port(dispatcher)

    assert dispatcher.open_calls == [
        (_FAKE_DB_PATH, "", False),
        (_FAKE_DB_PATH, "", True),
    ]
    assert caller_ident not in dispatcher.handle_reads, dispatcher.handle_reads


def test_port_close_releases_the_dao_handle_on_the_owner_thread() -> None:
    """S1: closing the inspection handle is a COM call and stays on the owner thread."""
    rig = _Rig()
    with rig as dispatcher:
        sta_ident = dispatcher.owner_thread_ident
        port = open_inspection_port(dispatcher)
        rig.log.entries.clear()
        port.close()

    assert getattr(rig.handle, "closed", False) is True
    assert rig.log.thread_idents() == {sta_ident}, rig.log.entries
    assert "db.Close()" in rig.log.surfaces()
