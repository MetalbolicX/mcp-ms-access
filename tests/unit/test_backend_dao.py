"""Characterization tests for ms_access_mcp.backends.dao (plan 014).

The DAO seam is constructor injection: ``DaoAdapter(db_path, dispatcher=...)``
drives a ``ComDispatcher`` protocol (``start`` / ``open_dao_database`` /
``close_dao_database`` / ``call`` / ``current_db`` / ``is_connected``).
These tests inject a fake dispatcher + fake DAO Database/Recordset graph the
same way ``test_odbc_adapter.py`` fakes its driver boundary — no real
Access/COM is ever touched.

Tests marked ``# characterization: documents current swallow behavior`` pin
behavior the code exhibits TODAY (error swallowing, per-op failure
continuation), not what it ideally should do.
"""

from __future__ import annotations

import sys
from typing import Any, cast

import pytest

from ms_access_mcp.backends.dao import DaoAdapter, DaoOperationError, DaoSession
from ms_access_mcp.backends.dispatcher import DAO_DB_FAIL_ON_ERROR

# ---------------------------------------------------------------------------
# Fake DAO boundary
# ---------------------------------------------------------------------------


class FakeComError(Exception):
    """Simulates a DAO/COM failure."""


class FakeField:
    def __init__(self, name: str, value: Any = None) -> None:
        self.Name = name
        self.Value = value


class FakeFieldCollection:
    """Fields over the recordset's CURRENT row (DAO semantics)."""

    def __init__(self, recordset: FakeRecordset) -> None:
        self._rs = recordset

    @property
    def Count(self) -> int:
        return len(self._rs._keys)

    def __call__(self, index: int) -> FakeField:
        name = self._rs._keys[index]
        row = self._rs._rows[self._rs._pos] if self._rs._pos < len(self._rs._rows) else {}
        return FakeField(name, row.get(name))


class FakeRecordset:
    """Recordset over a list of row dicts."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self._keys = list(self._rows[0].keys()) if self._rows else []
        self._pos = 0
        self.closed = False
        self._fields = FakeFieldCollection(self)

    @property
    def EOF(self) -> bool:
        return self._pos >= len(self._rows)

    @property
    def Fields(self) -> FakeFieldCollection:
        return self._fields

    def MoveNext(self) -> None:
        self._pos += 1

    def Close(self) -> None:
        self.closed = True


class FakeRelation:
    def __init__(self, name: str, table: str, foreign_table: str) -> None:
        self.Name = name
        self.Table = table
        self.ForeignTable = foreign_table


class FakeRelations:
    def __init__(self, relations: list[FakeRelation] | None = None) -> None:
        self._relations = relations or []
        self.deleted: list[str] = []

    @property
    def Count(self) -> int:
        return len(self._relations)

    def __call__(self, index: int) -> FakeRelation:
        return self._relations[index]

    def Delete(self, name: str) -> None:
        self.deleted.append(name)
        self._relations = [r for r in self._relations if r.Name != name]


class FakeTableDefField:
    def __init__(self, name: str) -> None:
        self.Name = name


class FakeTableDef:
    def __init__(
        self,
        name: str,
        attributes: int = 0,
        connect: str = "",
        source_table: str = "",
    ) -> None:
        self.Name = name
        self.Attributes = attributes
        self.Connect = connect
        self.SourceTableName = source_table
        self._fields: dict[str, FakeTableDefField] = {}
        self.refreshed = False

    def Fields(self, name: str) -> FakeTableDefField:
        if name not in self._fields:
            self._fields[name] = FakeTableDefField(name)
        return self._fields[name]

    def RefreshLink(self) -> None:
        self.refreshed = True


class FakeTableDefs:
    def __init__(self, tdefs: list[FakeTableDef] | None = None) -> None:
        self._tdefs: dict[str, FakeTableDef] = {t.Name: t for t in (tdefs or [])}
        self.appended: list[FakeTableDef] = []
        self.deleted: list[str] = []

    @property
    def Count(self) -> int:
        return len(self._tdefs)

    def __call__(self, key: int | str) -> FakeTableDef:
        if isinstance(key, int):
            return list(self._tdefs.values())[key]
        return self._tdefs[key]

    def Append(self, tdef: FakeTableDef) -> None:
        self.appended.append(tdef)
        self._tdefs[tdef.Name] = tdef

    def Delete(self, name: str) -> None:
        self.deleted.append(name)
        self._tdefs.pop(name, None)


class FakeQueryDef:
    def __init__(self, name: str, sql: str = "") -> None:
        self.Name = name
        self.SQL = sql


class FakeQueryDefs:
    def __init__(self) -> None:
        self._qdefs: dict[str, FakeQueryDef] = {}
        self.appended: list[FakeQueryDef] = []
        self.deleted: list[str] = []
        self.fail_lookup = False

    def __call__(self, name: str) -> FakeQueryDef:
        if self.fail_lookup:
            raise FakeComError(f"QueryDef '{name}' not found")
        if name not in self._qdefs:
            self._qdefs[name] = FakeQueryDef(name)
        return self._qdefs[name]

    def Append(self, qdef: FakeQueryDef) -> None:
        self.appended.append(qdef)
        self._qdefs[qdef.Name] = qdef

    def Delete(self, name: str) -> None:
        self.deleted.append(name)
        if name not in self._qdefs:
            raise FakeComError(f"QueryDef '{name}' not found")
        self._qdefs.pop(name)


class FakeDb:
    """Fake DAO Database: records Execute SQL, scripts COM failures."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.records_affected = 1
        self.recordset_rows: list[dict[str, Any]] | None = None
        self.fail_pred: Any = None  # callable(sql) -> bool, True means raise
        self.fail_open_recordset = False
        self.relations = FakeRelations()
        self.table_defs = FakeTableDefs()
        self.query_defs = FakeQueryDefs()

    def Execute(self, sql: str, options: int = 0) -> None:
        assert options == DAO_DB_FAIL_ON_ERROR
        if self.fail_pred is not None and self.fail_pred(sql):
            raise FakeComError(f"DAO Execute failed: {sql}")
        self.executed.append(sql)

    @property
    def RecordsAffected(self) -> int:
        return self.records_affected

    def OpenRecordset(self, sql: str) -> FakeRecordset:
        if self.fail_open_recordset:
            raise FakeComError(f"OpenRecordset failed: {sql}")
        return FakeRecordset(self.recordset_rows)

    def CreateQueryDef(self, name: str, sql: str) -> FakeQueryDef:
        return FakeQueryDef(name, sql)

    @property
    def QueryDefs(self) -> FakeQueryDefs:
        return self.query_defs

    @property
    def Relations(self) -> FakeRelations:
        return self.relations

    @property
    def TableDefs(self) -> FakeTableDefs:
        return self.table_defs

    def CreateTableDef(self, name: str) -> FakeTableDef:
        return FakeTableDef(name)


class FakeDispatcher:
    """Drop-in for ComDispatcher at the DaoAdapter seam.

    Starts disconnected by default (like the real dispatcher); a successful
    ``open_dao_database`` hands out the fake handle. Pass ``connected=True``
    for a dispatcher that already holds a handle.
    """

    def __init__(self, connected: bool = False) -> None:
        self._db: FakeDb | None = FakeDb() if connected else None
        self.started = 0
        self.open_calls: list[tuple[str, str, bool]] = []
        self.close_calls = 0
        self.create_calls: list[tuple[str, str]] = []
        self.unhealthy_marked = False
        self.fail_open: Exception | None = None
        self.fail_close: Exception | None = None
        self.fail_create: Exception | None = None

    @property
    def db(self) -> FakeDb:
        """The fake handle; only valid while the dispatcher is connected."""
        assert self._db is not None, "dispatcher is not connected"
        return self._db

    # -- ComDispatcher protocol -------------------------------------------

    def start(self) -> None:
        self.started += 1

    def open_dao_database(self, db_path: str, password: str = "", read_only: bool = False) -> FakeDb:
        self.open_calls.append((db_path, password, read_only))
        if self.fail_open is not None:
            raise self.fail_open
        if self._db is None:
            self._db = FakeDb()
        return self._db

    def close_dao_database(self) -> None:
        self.close_calls += 1
        if self.fail_close is not None:
            raise self.fail_close
        self._db = None

    def create_dao_database(self, db_path: str, *, format: str = "accdb") -> None:
        self.create_calls.append((db_path, format))
        if self.fail_create is not None:
            raise self.fail_create

    def call(self, fn: Any) -> Any:
        return fn()

    @property
    def current_db(self) -> FakeDb:
        return self.db

    def is_connected(self) -> bool:
        return self._db is not None

    def mark_unhealthy(self) -> None:
        self.unhealthy_marked = True


@pytest.fixture
def dispatcher() -> FakeDispatcher:
    """A dispatcher that already holds a handle (adapter ops work directly)."""
    return FakeDispatcher(connected=True)


def _make_adapter(dispatcher: FakeDispatcher, path: str = "C:\\db\\test.accdb") -> DaoAdapter:
    """Construct a DaoAdapter around the fake boundary (structural, not nominal)."""
    return DaoAdapter(path, dispatcher=cast(Any, dispatcher))


@pytest.fixture
def adapter(dispatcher: FakeDispatcher) -> DaoAdapter:
    return _make_adapter(dispatcher)


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_fresh_adapter_not_connected(self, adapter: DaoAdapter):
        assert adapter.is_connected() is False

    def test_connect_opens_database(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        adapter.connect()

        assert adapter.is_connected() is True
        assert dispatcher.started == 1
        assert dispatcher.open_calls == [("C:\\db\\test.accdb", "", False)]

    def test_connect_with_password_and_path_override(self, dispatcher: FakeDispatcher):
        adapter = _make_adapter(dispatcher, path="C:\\db\\old.accdb")
        adapter.connect(db_path="C:\\db\\new.accdb", password="secret")

        assert dispatcher.open_calls == [("C:\\db\\new.accdb", "secret", False)]
        assert adapter.is_connected() is True

    def test_connect_failure_raises_dao_operation_error(self, dispatcher: FakeDispatcher):
        dispatcher.fail_open = FakeComError("engine unavailable")
        adapter = _make_adapter(dispatcher)

        with pytest.raises(DaoOperationError) as exc_info:
            adapter.connect()

        assert "DAO connect failed for C:\\db\\test.accdb" in str(exc_info.value)
        assert isinstance(exc_info.value.cause, FakeComError)
        assert adapter.is_connected() is False
        assert dispatcher.unhealthy_marked is True

    def test_disconnect_closes_database(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        adapter.connect()
        adapter.disconnect()

        assert adapter.is_connected() is False
        assert dispatcher.close_calls == 1

    def test_disconnect_is_idempotent(self, adapter: DaoAdapter):
        adapter.disconnect()
        adapter.disconnect()  # must not raise

        assert adapter.is_connected() is False

    def test_disconnect_swallows_close_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher, caplog: pytest.LogCaptureFixture):
        # characterization: documents current swallow behavior — a failing
        # close_dao_database is logged as a warning and swallowed; the adapter
        # still transitions to disconnected.
        adapter.connect()
        dispatcher.fail_close = FakeComError("close exploded")

        with caplog.at_level("WARNING"):
            adapter.disconnect()

        assert adapter.is_connected() is False
        assert any("close_dao_database failed" in r.message for r in caplog.records)

    def test_create_database_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        assert adapter.create_database("C:\\db\\new.accdb") is True
        assert dispatcher.create_calls == [("C:\\db\\new.accdb", "accdb")]

    def test_create_database_mdb_format(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        assert adapter.create_database("C:\\db\\new.mdb", format="mdb") is True
        assert dispatcher.create_calls == [("C:\\db\\new.mdb", "mdb")]

    def test_create_database_explicit_accdb_format(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        assert adapter.create_database("C:\\db\\new.accdb", format="accdb") is True
        assert dispatcher.create_calls == [("C:\\db\\new.accdb", "accdb")]

    def test_create_database_failure_raises_dao_operation_error(self, dispatcher: FakeDispatcher):
        dispatcher.fail_create = FakeComError("create failed")
        adapter = _make_adapter(dispatcher)

        with pytest.raises(DaoOperationError) as exc_info:
            adapter.create_database("C:\\db\\new.accdb")

        assert "DAO create_database failed for C:\\db\\new.accdb" in str(exc_info.value)
        assert isinstance(exc_info.value.cause, FakeComError)
        assert adapter.is_connected() is False
        assert dispatcher.unhealthy_marked is True


# ---------------------------------------------------------------------------
# Row CRUD
# ---------------------------------------------------------------------------


class TestRowCrud:
    def test_execute_query_not_connected(self):
        adapter = _make_adapter(FakeDispatcher())  # disconnected

        result = adapter.execute_query("SELECT 1")

        assert result.success is False
        assert result.rows == []
        assert result.count == 0
        assert result.columns == []
        assert result.error == "Not connected"

    def test_execute_query_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.recordset_rows = [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]

        result = adapter.execute_query("SELECT id, name FROM t")

        assert result.success is True
        assert result.count == 2
        assert result.columns == ["id", "name"]
        assert result.rows == [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]

    def test_execute_query_empty_recordset(self, adapter: DaoAdapter):
        result = adapter.execute_query("SELECT 1")

        assert result.success is True
        assert result.rows == []
        assert result.count == 0
        assert result.columns == []

    def test_execute_query_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.fail_open_recordset = True

        result = adapter.execute_query("SELECT 1")

        assert result.success is False
        assert result.error is not None and "OpenRecordset failed" in result.error
        assert result.rows == []
        assert result.count == 0

    def test_insert_data_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.records_affected = 3

        result = adapter.insert_data("tbl", {"name": "O'Hara", "active": True, "score": 7})

        assert result.success is True
        assert result.affected == 3
        assert result.error is None
        assert dispatcher.db.executed == [
            "INSERT INTO [tbl] ([name], [active], [score]) VALUES ('O''Hara', -1, 7)"
        ]

    def test_insert_data_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.fail_pred = lambda sql: sql.startswith("INSERT")

        result = adapter.insert_data("tbl", {"name": "x"})

        assert result.success is False
        assert result.error is not None and "Execute failed" in result.error
        assert result.affected == 0

    def test_insert_data_not_connected(self):
        adapter = _make_adapter(FakeDispatcher())  # disconnected

        result = adapter.insert_data("tbl", {"name": "x"})

        assert result.success is False
        assert result.error == "Not connected"
        assert result.affected == 0

    def test_update_data_with_dict_where(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        result = adapter.update_data("tbl", {"name": "new"}, {"id": 5})

        assert result.success is True
        assert result.affected == 1
        assert dispatcher.db.executed == ["UPDATE [tbl] SET [name] = 'new' WHERE [id] = 5"]

    def test_update_data_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.fail_pred = lambda sql: sql.startswith("UPDATE")

        result = adapter.update_data("tbl", {"name": "new"})

        assert result.success is False
        assert result.affected == 0

    def test_update_data_string_where_sanitized(self, adapter: DaoAdapter):
        result = adapter.update_data("tbl", {"name": "x"}, "1=1; DROP TABLE t")

        assert result.success is False
        assert result.error is not None and "SQL injection blocked" in result.error
        assert result.affected == 0

    def test_delete_data_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        result = adapter.delete_data("tbl", {"id": 5})

        assert result.success is True
        assert result.affected == 1
        assert dispatcher.db.executed == ["DELETE FROM [tbl] WHERE [id] = 5"]

    def test_delete_data_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.fail_pred = lambda sql: sql.startswith("DELETE")

        result = adapter.delete_data("tbl")

        assert result.success is False
        assert result.affected == 0

    def test_execute_raw_sql_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.records_affected = 4

        assert adapter.execute_raw_sql("UPDATE t SET a = 1") == 4

    def test_execute_raw_sql_not_connected_raises(self):
        adapter = _make_adapter(FakeDispatcher())  # disconnected

        with pytest.raises(RuntimeError, match="Not connected"):
            adapter.execute_raw_sql("SELECT 1")

    def test_execute_raw_sql_com_failure_propagates(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        # characterization: documents current behavior — unlike the dict-returning
        # CRUD methods, execute_raw_sql does not wrap COM errors; the raw
        # exception propagates out of the dispatcher call.
        dispatcher.db.fail_pred = lambda sql: True

        with pytest.raises(FakeComError):
            adapter.execute_raw_sql("SELECT 1")


# ---------------------------------------------------------------------------
# Table / index DDL
# ---------------------------------------------------------------------------


class TestDdl:
    def test_create_table_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        result = adapter.create_table(
            "new_tbl",
            [
                {"name": "id", "type": "Long Integer", "primary_key": True, "is_autoincrement": True},
                {"name": "title", "type": "Text", "size": 100, "required": True},
            ],
        )

        assert result == {"success": True}
        assert dispatcher.db.executed == [
            "CREATE TABLE [new_tbl] ([id] INTEGER NOT NULL, [title] VARCHAR(100) NOT NULL, PRIMARY KEY ([id]))"
        ]

    def test_create_table_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.fail_pred = lambda sql: sql.startswith("CREATE TABLE")

        result = adapter.create_table("new_tbl", [{"name": "a"}])

        assert result["success"] is False
        assert "DAO Execute failed" in result["error"]

    def test_delete_table_drops_referencing_relations_first(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.relations = FakeRelations(
            [
                FakeRelation("rel_keep", "other1", "other2"),
                FakeRelation("rel_drop_t", "tbl", "other"),
                FakeRelation("rel_drop_f", "other", "tbl"),
            ]
        )

        result = adapter.delete_table("tbl")

        assert result == {"success": True}
        assert sorted(dispatcher.db.relations.deleted) == ["rel_drop_f", "rel_drop_t"]
        assert dispatcher.db.executed == ["DROP TABLE [tbl]"]

    def test_delete_table_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.fail_pred = lambda sql: sql.startswith("DROP TABLE")

        result = adapter.delete_table("tbl")

        assert result["success"] is False

    def test_create_index_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        result = adapter.create_index("tbl", "idx_name", ["a", "b"], unique=True, ignore_nulls=True)

        assert result == {"success": True}
        assert dispatcher.db.executed == [
            "CREATE UNIQUE INDEX [idx_name] ON [tbl] ([a], [b]) WITH IGNORE NULL"
        ]

    def test_create_index_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.fail_pred = lambda sql: "INDEX" in sql

        result = adapter.create_index("tbl", "idx", ["a"])

        assert result["success"] is False

    def test_drop_index_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        result = adapter.drop_index("tbl", "idx")

        assert result == {"success": True}
        assert dispatcher.db.executed == ["DROP INDEX [idx] ON [tbl]"]

    def test_drop_index_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.fail_pred = lambda sql: sql.startswith("DROP INDEX")

        result = adapter.drop_index("tbl", "idx")

        assert result["success"] is False


# ---------------------------------------------------------------------------
# alter_table — highest-risk method
# ---------------------------------------------------------------------------


class TestAlterTable:
    def test_not_connected(self):
        adapter = _make_adapter(FakeDispatcher())  # disconnected

        result = adapter.alter_table("tbl", [{"action": "drop_column", "params": {"name": "c"}}])

        assert result == {"success": False, "operations": [], "error": "Not connected"}

    def test_all_supported_actions_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.table_defs = FakeTableDefs([FakeTableDef("tbl")])

        operations = [
            {"action": "add_column", "params": {"name": "c1", "type": "Text", "size": 50}},
            {"action": "drop_column", "params": {"name": "c2"}},
            {"action": "modify_column", "params": {"name": "c3", "type": "Long Integer", "nullable": False}},
            {"action": "rename_table", "params": {"new_name": "tbl2"}},
            {"action": "rename_column", "params": {"name": "old", "new_name": "new"}},
        ]

        result = adapter.alter_table("tbl", operations)

        assert result["success"] is True
        assert [r["success"] for r in result["operations"]] == [True] * 5
        assert [r["action"] for r in result["operations"]] == [
            "add_column",
            "drop_column",
            "modify_column",
            "rename_table",
            "rename_column",
        ]
        assert dispatcher.db.executed == [
            "ALTER TABLE [tbl] ADD COLUMN [c1] VARCHAR(50) NULL",
            "ALTER TABLE [tbl] DROP COLUMN [c2]",
            "ALTER TABLE [tbl] ALTER COLUMN [c3] INTEGER NOT NULL",
        ]
        # The fake collection keeps the original key; DAO renames the object
        # in place, so the same TableDef is reachable under its old key.
        renamed_table = dispatcher.db.table_defs("tbl")
        assert renamed_table.Name == "tbl2"
        assert renamed_table.Fields("old").Name == "new"

    def test_unknown_action_is_reported_per_op_and_continues(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        # characterization: documents current behavior — an unknown action is
        # recorded as a per-op failure and the loop CONTINUES with the
        # remaining operations; overall success reflects every op.
        operations = [
            {"action": "bogus", "params": {}},
            {"action": "drop_column", "params": {"name": "c"}},
        ]

        result = adapter.alter_table("tbl", operations)

        assert result["success"] is False
        assert result["operations"][0] == {"action": "bogus", "success": False, "error": "Unknown action: bogus"}
        assert result["operations"][1]["success"] is True
        assert dispatcher.db.executed == ["ALTER TABLE [tbl] DROP COLUMN [c]"]

    def test_com_failure_in_one_op_continues_with_rest(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        # characterization: documents current behavior — a COM failure inside
        # one operation is caught per-op (recorded as success=False) and does
        # NOT abort the remaining operations.
        dispatcher.db.fail_pred = lambda sql: "DROP COLUMN [bad]" in sql
        operations = [
            {"action": "drop_column", "params": {"name": "bad"}},
            {"action": "drop_column", "params": {"name": "good"}},
        ]

        result = adapter.alter_table("tbl", operations)

        assert result["success"] is False
        assert result["operations"][0]["success"] is False
        assert "DROP COLUMN [bad]" in result["operations"][0]["error"]
        assert result["operations"][1] == {"action": "drop_column", "success": True}
        assert dispatcher.db.executed == ["ALTER TABLE [tbl] DROP COLUMN [good]"]

    def test_rename_column_missing_field_raises_per_op(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        # Empty TableDefs: any lookup raises KeyError, simulating a missing table.
        dispatcher.db.table_defs = FakeTableDefs([])
        operations = [{"action": "rename_column", "params": {"name": "old", "new_name": "new"}}]

        result = adapter.alter_table("tbl", operations)

        assert result["success"] is False
        assert "tbl" in result["operations"][0]["error"]


# ---------------------------------------------------------------------------
# Query CRUD
# ---------------------------------------------------------------------------


class TestQueryCrud:
    def test_create_query_not_connected(self):
        adapter = _make_adapter(FakeDispatcher())  # disconnected

        result = adapter.create_query("q1", "SELECT 1")

        assert result == {"success": False, "error": "Not connected"}

    def test_create_query_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        result = adapter.create_query("q1", "SELECT 1")

        assert result == {"success": True}
        assert len(dispatcher.db.query_defs.appended) == 1
        assert dispatcher.db.query_defs.appended[0].Name == "q1"
        assert dispatcher.db.query_defs.appended[0].SQL == "SELECT 1"

    def test_create_query_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        # CreateQueryDef raising simulates the COM failure:
        dispatcher.db.CreateQueryDef = lambda name, sql: (_ for _ in ()).throw(FakeComError("append denied"))  # type: ignore[method-assign]

        result = adapter.create_query("q1", "SELECT 1")

        assert result["success"] is False
        assert result["error"] == "append denied"

    def test_set_query_sql_happy_path(self, adapter: DaoAdapter):
        adapter.create_query("q1", "SELECT 1")

        result = adapter.set_query_sql("q1", "SELECT 2")

        assert result == {"success": True}

    def test_set_query_sql_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.query_defs.fail_lookup = True

        result = adapter.set_query_sql("missing", "SELECT 2")

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_delete_query_happy_path(self, adapter: DaoAdapter):
        adapter.create_query("q1", "SELECT 1")

        result = adapter.delete_query("q1")

        assert result == {"success": True}

    def test_delete_query_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        dispatcher.db.query_defs.fail_lookup = True

        result = adapter.delete_query("missing")

        assert result["success"] is False


# ---------------------------------------------------------------------------
# Linked tables
# ---------------------------------------------------------------------------


class TestLinkedTables:
    def _link_fixtures(self, dispatcher: FakeDispatcher) -> None:
        dispatcher.db.table_defs = FakeTableDefs(
            [
                FakeTableDef("local", attributes=0),
                FakeTableDef(
                    "lnk_odbc",
                    attributes=0x80000000,
                    connect="ODBC;DATABASE=db;UID=u;PWD=secret",
                    source_table="src_odbc",
                ),
                FakeTableDef(
                    "lnk_access",
                    attributes=0x80000000,
                    connect=r"Access;DATABASE=C:\db\be.accdb",
                    source_table="src_access",
                ),
            ]
        )

    def test_get_linked_tables_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        self._link_fixtures(dispatcher)

        result = adapter.get_linked_tables()

        assert result["success"] is True
        assert [t["name"] for t in result["linked_tables"]] == ["lnk_odbc", "lnk_access"]
        by_name = {t["name"]: t for t in result["linked_tables"]}
        assert by_name["lnk_odbc"]["type"] == "ODBC"
        assert by_name["lnk_access"]["type"] == "Access"
        assert by_name["lnk_odbc"]["source_table"] == "src_odbc"

    def test_get_linked_tables_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        # TableDefs iteration raising simulates the COM failure:
        class BoomingTableDefs(FakeTableDefs):
            @property
            def Count(self) -> int:
                raise FakeComError("TableDefs unavailable")

        dispatcher.db.table_defs = BoomingTableDefs([])

        result = adapter.get_linked_tables()

        assert result["success"] is False
        assert result["error"] == "TableDefs unavailable"

    def test_create_linked_table_strips_password(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        result = adapter.create_linked_table("lnk", "src", "ODBC;DATABASE=db;UID=u;PWD=secret")

        assert result == {"success": True}
        tdef = dispatcher.db.table_defs.appended[0]
        assert tdef.Name == "lnk"
        assert tdef.Attributes == 0x80000000
        assert "PWD=secret" not in (tdef.Connect or "")
        assert tdef.Connect == "ODBC;DATABASE=db;UID=u"

    def test_unlink_table_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        self._link_fixtures(dispatcher)

        result = adapter.unlink_table("lnk_odbc")

        assert result == {"success": True}
        assert dispatcher.db.table_defs.deleted == ["lnk_odbc"]

    def test_unlink_table_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        class BoomingTableDefs(FakeTableDefs):
            def Delete(self, name: str) -> None:
                raise FakeComError("delete denied")

        dispatcher.db.table_defs = BoomingTableDefs([])

        result = adapter.unlink_table("lnk")

        assert result["success"] is False
        assert result["error"] == "delete denied"

    def test_refresh_linked_table_happy_path(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        self._link_fixtures(dispatcher)

        result = adapter.refresh_linked_table("lnk_odbc", "ODBC;DATABASE=db;UID=u2;PWD=newsecret")

        assert result == {"success": True}
        tdef = dispatcher.db.table_defs("lnk_odbc")
        assert tdef.refreshed is True
        assert "PWD=" not in (tdef.Connect or "")
        assert "UID=u2" in (tdef.Connect or "")

    def test_refresh_linked_table_com_failure(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        class BoomingTableDefs(FakeTableDefs):
            def __call__(self, key: int | str) -> FakeTableDef:
                raise FakeComError("no such table")

        dispatcher.db.table_defs = BoomingTableDefs([])

        result = adapter.refresh_linked_table("missing")

        assert result["success"] is False
        assert result["error"] == "no such table"

    def test_recreate_linked_table_preserves_attributes(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        self._link_fixtures(dispatcher)

        result = adapter.recreate_linked_table(
            "lnk_odbc", "src_odbc", "ODBC;DATABASE=db;UID=u3;PWD=third"
        )

        assert result == {"success": True}
        assert dispatcher.db.table_defs.deleted == ["lnk_odbc"]
        new_tdef = dispatcher.db.table_defs.appended[0]
        assert new_tdef.Attributes == 0x80000000
        assert "PWD=third" not in (new_tdef.Connect or "")


# ---------------------------------------------------------------------------
# Schema/database-property delegation requires real collaborators — only
# the not-connected / lazy-init seam is safe to characterize here.
# ---------------------------------------------------------------------------


class TestDelegationSeam:
    def test_schema_inspector_lazy_init_uses_dispatcher(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        from ms_access_mcp.backends.schema_inspector import SchemaInspector

        inspector = adapter._schema_inspector

        assert isinstance(inspector, SchemaInspector)
        assert inspector._dispatcher is dispatcher  # type: ignore[attr-defined]
        # Second access reuses the cached instance.
        assert adapter._schema_inspector is inspector

    def test_db_operations_lazy_init_uses_dispatcher(self, adapter: DaoAdapter, dispatcher: FakeDispatcher):
        from ms_access_mcp.backends.db_operations import DbOperations

        ops = adapter._db_operations

        assert isinstance(ops, DbOperations)
        assert adapter._db_operations is ops


# ---------------------------------------------------------------------------
# DaoSession — context manager over a short-lived handle
# ---------------------------------------------------------------------------


class _FakeEngine:
    def __init__(self, db: Any) -> None:
        self._db = db
        self.open_calls: list[tuple[str, bool, bool, str]] = []

    def OpenDatabase(self, path: str, exclusive: bool, read_only: bool, connect: str) -> Any:
        self.open_calls.append((path, exclusive, read_only, connect))
        return self._db


def _patch_win32com(monkeypatch: pytest.MonkeyPatch, engine: _FakeEngine) -> None:
    class FakeClient:
        @staticmethod
        def Dispatch(progid: str) -> _FakeEngine:
            assert progid == "DAO.DBEngine.120"
            return engine

    class FakeWin32Com:
        client = FakeClient()

    monkeypatch.setitem(sys.modules, "win32com", FakeWin32Com())
    monkeypatch.setitem(sys.modules, "win32com.client", FakeWin32Com.client)


class TestDaoSession:
    def test_enter_opens_database_with_password(self, monkeypatch: pytest.MonkeyPatch):
        engine = _FakeEngine(FakeDb())
        _patch_win32com(monkeypatch, engine)

        session = DaoSession("C:\\db\\test.accdb", password="pw", read_only=False)
        with session as s:
            assert s.db is engine._db

        assert engine.open_calls == [("C:\\db\\test.accdb", False, False, ";PWD=pw")]

    def test_default_read_only_and_empty_password(self, monkeypatch: pytest.MonkeyPatch):
        engine = _FakeEngine(FakeDb())
        _patch_win32com(monkeypatch, engine)

        with DaoSession("C:\\db\\test.accdb") as session:
            assert session.read_only is True

        assert engine.open_calls == [("C:\\db\\test.accdb", False, True, "")]

    def test_exit_closes_handle(self, monkeypatch: pytest.MonkeyPatch):
        db = FakeDb()
        engine = _FakeEngine(db)
        _patch_win32com(monkeypatch, engine)

        session = DaoSession("C:\\db\\test.accdb")
        with session:
            pass

        assert session.db is None

    def test_close_is_idempotent(self, monkeypatch: pytest.MonkeyPatch):
        engine = _FakeEngine(FakeDb())
        _patch_win32com(monkeypatch, engine)

        session = DaoSession("C:\\db\\test.accdb")
        with session:
            session.close()
            session.close()  # must not raise

        assert session.db is None

    def test_close_swallows_com_error(self, monkeypatch: pytest.MonkeyPatch):
        # characterization: documents current swallow behavior — a failing
        # db.Close() inside DaoSession._close is swallowed; the handle is
        # still nulled so the session ends disconnected.
        class Unclosable:
            def Close(self) -> None:
                raise FakeComError("close exploded")

        engine = _FakeEngine(Unclosable())
        _patch_win32com(monkeypatch, engine)

        session = DaoSession("C:\\db\\test.accdb")
        with session:
            pass

        assert session.db is None
