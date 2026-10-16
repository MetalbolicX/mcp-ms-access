"""Unit tests for the ODBC documentation-inspection port (T2).

These tests never need the Access ODBC driver: a fake connection/cursor double
stands in for pyodbc, so the default qualification gate stays driver-independent.
They cover the port methods, the honest unavailable/observed-empty/failed seam
mapping, and the CLI/MCP wiring branch that selects the ODBC port.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from ms_access_mcp import cli
from ms_access_mcp.application import documentation_inspect
from ms_access_mcp.application.database import Ok
from ms_access_mcp.application.documentation_inspect import (
    DocumentationInspectService,
    InspectionCapabilities,
    InspectionCapabilityUnavailableError,
    OdbcInspectionPort,
    inspect_open_database,
    open_odbc_inspection_port,
)
from ms_access_mcp.backends.odbc import OdbcAdapter
from ms_access_mcp.backends.schema_inspector import QueryParameterInfo
from ms_access_mcp.errors import AccessMcpError
from ms_access_mcp.mcp_tools import analysis
from ms_access_mcp.models import IndexInfo

# ---------------------------------------------------------------------------
# Fake pyodbc doubles
# ---------------------------------------------------------------------------


def _table(name: str, table_type: str = "TABLE") -> SimpleNamespace:
    return SimpleNamespace(table_name=name, table_type=table_type)


def _column(table: str, name: str, type_name: str, size: int, nullable: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        table_name=table, column_name=name, type_name=type_name, column_size=size, nullable=nullable
    )


def _index(
    table: str, index: str, column: str, ordinal: int = 1, non_unique: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(
        table_name=table,
        index_name=index,
        column_name=column,
        ordinal_position=ordinal,
        non_unique=non_unique,
        type=3,
    )


def _parameter(
    procedure: str, name: str, column_type: int, data_type: int, type_name: str, ordinal: int = 1
) -> SimpleNamespace:
    return SimpleNamespace(
        procedure_name=procedure,
        column_name=name,
        column_type=column_type,
        data_type=data_type,
        type_name=type_name,
        ordinal_position=ordinal,
    )


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection
        self._rows: list[Any] = []
        self.closed = False

    def tables(self, table: str | None = None, tableType: str | None = None) -> None:  # noqa: N803
        self._connection.calls.append(("tables", {"table": table, "tableType": tableType}))
        rows = list(self._connection.tables)
        if tableType:
            allowed = {item.strip() for item in tableType.split(",")}
            rows = [row for row in rows if row.table_type in allowed]
        if table is not None:
            rows = [row for row in rows if row.table_name == table]
        self._rows = rows

    def columns(self, table: str | None = None, **_kwargs: Any) -> None:
        self._connection.calls.append(("columns", {"table": table}))
        self._rows = [row for row in self._connection.columns if table is None or row.table_name == table]

    def statistics(self, table: str | None = None, **_kwargs: Any) -> None:
        self._connection.calls.append(("statistics", {"table": table}))
        if self._connection.statistics_error is not None:
            raise self._connection.statistics_error
        self._rows = [row for row in self._connection.statistics if table is None or row.table_name == table]

    def procedureColumns(self, procedure: str | None = None, **_kwargs: Any) -> None:  # noqa: N802
        self._connection.calls.append(("procedureColumns", {"procedure": procedure}))
        if self._connection.procedure_columns_error is not None:
            raise self._connection.procedure_columns_error
        self._rows = [
            row
            for row in self._connection.procedure_columns
            if procedure is None or row.procedure_name == procedure
        ]

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(
        self,
        *,
        tables: list[Any] | None = None,
        columns: list[Any] | None = None,
        statistics: list[Any] | None = None,
        procedure_columns: list[Any] | None = None,
        statistics_error: Exception | None = None,
        procedure_columns_error: Exception | None = None,
    ) -> None:
        self.tables = tables or []
        self.columns = columns or []
        self.statistics = statistics or []
        self.procedure_columns = procedure_columns or []
        self.statistics_error = statistics_error
        self.procedure_columns_error = procedure_columns_error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.cursors: list[_FakeCursor] = []
        self.closed = False
        self.close_calls = 0

    def cursor(self) -> _FakeCursor:
        cursor = _FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1


def _odbc_connection() -> _FakeConnection:
    """Build a fake connection shaped like the probed core.accdb catalog."""
    return _FakeConnection(
        tables=[
            _table("Customers"),
            _table("LinkedCustomers", "SYNONYM"),
            _table("MSysObjects", "SYSTEM TABLE"),
            _table("qryOpenOrders", "VIEW"),
            _table("qryOrdersByCustomer", "VIEW"),
        ],
        columns=[
            _column("Customers", "CustomerID", "INTEGER", 10),
            _column("Customers", "Name", "VARCHAR", 50),
            _column("LinkedCustomers", "CustomerID", "INTEGER", 10),
        ],
        statistics=[
            _index("Customers", "PK_Customers", "CustomerID"),
            _index("LinkedCustomers", "PK_SentinelCustomers", "CustomerID"),
        ],
        procedure_columns=[
            _parameter("qryOrdersByCustomer", "[pCustomerID]", 1, 4, "INTEGER"),
            _parameter("qryOrdersByCustomer", "OrderID", 3, 4, "INTEGER"),
            _parameter("qryOpenOrders", "OrderID", 3, 4, "INTEGER"),
        ],
    )


class _UnfilteredTypeCursor(_FakeCursor):
    """A cursor whose SQLTables ignores the requested tableType filter.

    Real drivers may return a row type the inspection query did not request; the port
    must classify it as unsafe and read nothing from it.
    """

    def tables(self, table: str | None = None, tableType: str | None = None) -> None:  # noqa: N803
        self._connection.calls.append(("tables", {"table": table, "tableType": tableType}))
        rows = list(self._connection.tables)
        if table is not None:
            rows = [row for row in rows if row.table_name == table]
        self._rows = rows


class _UnfilteredTypeConnection(_FakeConnection):
    def cursor(self) -> _FakeCursor:
        cursor = _UnfilteredTypeCursor(self)
        self.cursors.append(cursor)
        return cursor


def _run_cli(argv: list[str]) -> int:
    with patch.object(sys, "argv", ["ms-access-mcp", *argv]):
        return cli.main()


class _WiringAdapter:
    """Fake ODBC adapter exposing only the production inspection seam.

    ``connection`` is the adapter's own writable CRUD handle; the inspection path must
    never read it. It is a recording property so a test can prove the wiring opens a
    distinct read-only handle instead of reusing the CRUD connection.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self.connection_reads = 0
        self.readonly_open_calls = 0

    @property
    def connection(self) -> Any:
        self.connection_reads += 1
        return self._connection

    def open_readonly_inspection_connection(self) -> Any:
        self.readonly_open_calls += 1
        return self._connection


class _NoInspectionAdapter:
    """Fake adapter with neither a DAO dispatcher nor an ODBC inspection seam."""


class _WiringDatabase:
    def __init__(self, connection: Any, *, backend: str = "odbc") -> None:
        self._adapter = _WiringAdapter(connection)
        self._backend_name = backend
        self.closed = False

    def inspection_context(self) -> tuple[str, list[Any]]:
        return self._backend_name, []

    def open_database(self, _path: str) -> Ok:
        return Ok(data={"path": _path})

    def close_database(self) -> Ok:
        self.closed = True
        return Ok()


class _NoInspectionDatabase(_WiringDatabase):
    """A database whose adapter exposes no DAO dispatcher and no ODBC inspection seam."""

    def __init__(self) -> None:
        super().__init__(None)
        self._adapter = _NoInspectionAdapter()


class _FailingOpenAdapter:
    """Fake ODBC adapter whose read-only inspection open always fails."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.readonly_open_calls = 0

    def open_readonly_inspection_connection(self) -> Any:
        self.readonly_open_calls += 1
        raise self._error


# ---------------------------------------------------------------------------
# 1. Port methods over fake ODBC catalog rows
# ---------------------------------------------------------------------------


def test_list_tables_reads_user_tables_and_synonyms_with_odbc_column_fidelity() -> None:
    port = OdbcInspectionPort(_odbc_connection())
    tables = port.list_tables()
    assert [table.name for table in tables] == ["Customers", "LinkedCustomers"]
    customers = tables[0]
    assert [(field.name, field.type, field.size) for field in customers.fields] == [
        ("CustomerID", "INTEGER", 10),
        ("Name", "VARCHAR", 50),
    ]
    # The ODBC taxonomy is passed through unchanged; DAO names are never invented.
    assert all(field.type != "Long Integer" for table in tables for field in table.fields)


def test_list_tables_closes_every_cursor_it_opens() -> None:
    connection = _odbc_connection()
    OdbcInspectionPort(connection).list_tables()
    assert connection.cursors and all(cursor.closed for cursor in connection.cursors)


def test_list_tables_does_not_read_columns_for_a_linked_synonym() -> None:
    """T1: a SYNONYM is classified before any column read, so the source is not touched."""
    connection = _odbc_connection()
    tables = OdbcInspectionPort(connection).list_tables()
    linked = next(table for table in tables if table.name == "LinkedCustomers")
    assert linked.fields == []
    assert ("columns", {"table": "LinkedCustomers"}) not in connection.calls
    # PRESERVE: the local table still has its columns read.
    customers = next(table for table in tables if table.name == "Customers")
    assert [field.name for field in customers.fields] == ["CustomerID", "Name"]


def test_linked_table_indexes_are_refused_without_a_statistics_read() -> None:
    """T1: SQLStatistics would contact the linked source, so the port refuses instead."""
    connection = _odbc_connection()
    port = OdbcInspectionPort(connection)
    port.list_tables()
    with pytest.raises(InspectionCapabilityUnavailableError):
        port.get_indexes("LinkedCustomers")
    assert ("statistics", {"table": "LinkedCustomers"}) not in connection.calls


def test_unclassified_table_type_is_not_read_and_fails_closed() -> None:
    """T1: a table type the port cannot classify is never read (fail closed)."""
    connection = _UnfilteredTypeConnection(
        tables=[_table("Mystery", "GLOBAL TEMPORARY")],
        columns=[_column("Mystery", "id", "INTEGER", 10)],
        statistics=[_index("Mystery", "ix", "id")],
    )
    port = OdbcInspectionPort(connection)
    assert port.list_tables()[0].fields == []
    assert ("columns", {"table": "Mystery"}) not in connection.calls
    with pytest.raises(InspectionCapabilityUnavailableError):
        port.get_indexes("Mystery")
    assert not any(call[0] == "statistics" for call in connection.calls)


def test_get_indexes_groups_statistics_rows_and_reports_observed_uniqueness() -> None:
    connection = _FakeConnection(
        tables=[_table("Stock")],
        statistics=[
            _index("Stock", "PK_Stock", "ProductID", ordinal=1),
            _index("Stock", "PK_Stock", "WarehouseID", ordinal=2),
            _index("Stock", "ProductsStock", "ProductID", non_unique=1),
            _index("Stock", None, None, non_unique=0),
        ],
    )
    indexes = OdbcInspectionPort(connection).get_indexes("Stock")
    assert indexes == [
        IndexInfo("PK_Stock", ["ProductID", "WarehouseID"], is_unique=True, is_primary=False, ignore_nulls=False),
        IndexInfo("ProductsStock", ["ProductID"], is_unique=False, is_primary=False, ignore_nulls=False),
    ]


def test_get_queries_lists_views_without_inventing_sql_or_category() -> None:
    connection = _odbc_connection()
    queries = OdbcInspectionPort(connection).get_queries()
    assert [query.name for query in queries] == ["qryOpenOrders", "qryOrdersByCustomer"]
    assert all(query.sql == "" and query.type == "" for query in queries)
    # Only SQLTables is used: no saved query is ever executed to read it back.
    assert {call[0] for call in connection.calls} == {"tables"}


def test_get_query_parameters_reads_input_parameters_and_known_empty() -> None:
    port = OdbcInspectionPort(_odbc_connection())
    assert port.get_query_parameters("qryOrdersByCustomer") == [
        QueryParameterInfo("[pCustomerID]", 4, "INTEGER")
    ]
    assert port.get_query_parameters("qryOpenOrders") == []


def test_get_query_parameters_is_unavailable_when_the_driver_call_fails() -> None:
    connection = _FakeConnection(procedure_columns_error=RuntimeError("IM001 unsupported"))
    # A failed capability read is unavailable (None), never a fabricated known empty.
    assert OdbcInspectionPort(connection).get_query_parameters("qryOpenOrders") is None


def test_get_table_link_metadata_reports_synonyms_without_a_target() -> None:
    port = OdbcInspectionPort(_odbc_connection())
    assert port.get_table_link_metadata("Customers") is None
    assert port.get_table_link_metadata("LinkedCustomers") == {
        "connect": None,
        "source_table_name": None,
    }


def test_get_relationships_refuses_instead_of_claiming_an_empty_catalogue() -> None:
    port = OdbcInspectionPort(_odbc_connection())
    try:
        port.get_relationships()
    except InspectionCapabilityUnavailableError as gap:
        assert str(gap).strip()
    else:  # pragma: no cover - the raise is the assertion
        raise AssertionError("ODBC must not claim an observed-empty relationship catalogue")


def test_capabilities_declare_odbc_identity_and_per_capability_reasons() -> None:
    capabilities = OdbcInspectionPort(_odbc_connection()).capabilities()
    assert capabilities.backend_identity == "ODBC"
    for capability in (
        "field_required",
        "field_allow_zero_length",
        "primary_key",
        "relationships",
        "query_sql",
        "query_type",
    ):
        reason = capabilities.reason(capability)
        assert reason and reason.strip(), capability
    # A capability the port can express has no reason.
    assert capabilities.reason("field_type") is None
    assert capabilities == InspectionCapabilities(
        backend_identity="ODBC", unavailable=dict(capabilities.unavailable)
    )


def test_close_leaves_the_adapter_owned_connection_open() -> None:
    """PRESERVE: the session owns the ODBC connection, so close() is a no-op."""
    connection = _odbc_connection()
    OdbcInspectionPort(connection).close()
    assert connection.closed is False


# ---------------------------------------------------------------------------
# 2. Honest seam mapping: unavailable vs observed-empty vs failed
# ---------------------------------------------------------------------------


def test_odbc_seam_keeps_visible_facts_observed_and_gaps_unavailable() -> None:
    document = DocumentationInspectService().inspect(
        OdbcInspectionPort(_odbc_connection()),
        tables=None,
        queries=None,
        # The SQL facts below are opt-in; the default excludes them before the
        # capability check (see the default-off regression test next to this one).
        include_query_sql=True,
    )
    assert document.source.backend_identity == "ODBC"
    assert [table.name for table in document.tables] == ["Customers", "LinkedCustomers"]
    customers = document.tables[0]
    fields = {field.name: field for field in customers.fields}
    assert fields["CustomerID"].type.state == "observed" and fields["CustomerID"].type.value == "INTEGER"
    assert fields["CustomerID"].size.state == "observed" and fields["CustomerID"].size.value == 10
    for name in ("CustomerID", "Name"):
        assert fields[name].required.state == "unavailable"
        assert fields[name].required.reason
        assert fields[name].allow_zero_length.state == "unavailable"
        assert fields[name].allow_zero_length.reason
        assert fields[name].default_value.state == "unavailable"
        assert fields[name].default_value.reason
    assert customers.primary_key.state == "unavailable"
    assert customers.primary_key.reason
    assert [index.name for index in customers.indexes] == ["PK_Customers"]
    assert customers.indexes[0].is_unique is True
    assert document.coverage.tables.unsupported["Customers"]
    linked = document.tables[1]
    assert linked.kind == "linked"
    assert linked.connect is None and linked.source_table_name is None
    assert document.relationships == []
    assert document.coverage.relationships.unsupported["relationships"]
    queries = {query.name: query for query in document.queries}
    assert set(queries) == {"qryOpenOrders", "qryOrdersByCustomer"}
    assert queries["qryOpenOrders"].sql.state == "unavailable" and queries["qryOpenOrders"].sql.reason
    assert queries["qryOpenOrders"].type.state == "unavailable" and queries["qryOpenOrders"].type.reason
    assert queries["qryOrdersByCustomer"].parameters.state == "observed"
    assert queries["qryOrdersByCustomer"].parameters.value == [
        {"name": "[pCustomerID]", "type": 4, "type_name": "INTEGER"}
    ]
    assert queries["qryOpenOrders"].parameters.state == "observed"
    assert queries["qryOpenOrders"].parameters.value == []
    assert document.status == "partial"


def test_odbc_seam_excludes_sql_by_default_and_reports_unavailable_when_opted_in() -> None:
    """ODBC precedence: default off excludes the SQL fact; opt-in reports the real gap."""
    default_port = OdbcInspectionPort(_odbc_connection())
    default_document = DocumentationInspectService().inspect(
        default_port, tables=None, queries=None
    )
    default_queries = {query.name: query for query in default_document.queries}
    assert default_queries["qryOpenOrders"].sql.state == "excluded"
    assert default_queries["qryOpenOrders"].sql.reason
    assert default_queries["qryOpenOrders"].type.state == "unavailable"
    assert default_document.coverage.queries.unsupported == {}
    assert default_document.coverage.queries.failed == {}

    opted_in_port = OdbcInspectionPort(_odbc_connection())
    opted_in_document = DocumentationInspectService().inspect(
        opted_in_port, tables=None, queries=None, include_query_sql=True
    )
    opted_in_queries = {query.name: query for query in opted_in_document.queries}
    assert opted_in_queries["qryOpenOrders"].sql.state == "unavailable"
    assert "VIEW rows" in opted_in_queries["qryOpenOrders"].sql.reason


def test_inspect_open_database_threads_include_query_sql_to_the_service() -> None:
    """The open-database seam forwards the flag; the default stays excluded."""
    opted_in = inspect_open_database(
        _WiringDatabase(_odbc_connection()), tables=None, queries=None, include_query_sql=True
    )
    opted_in_queries = {query.name: query for query in opted_in.queries}
    assert opted_in_queries["qryOpenOrders"].sql.state == "unavailable"

    default = inspect_open_database(_WiringDatabase(_odbc_connection()), tables=None, queries=None)
    default_queries = {query.name: query for query in default.queries}
    assert default_queries["qryOpenOrders"].sql.state == "excluded"


def test_odbc_index_read_failure_is_failed_not_unavailable() -> None:
    connection = _FakeConnection(
        tables=[_table("Customers")],
        # A readable column keeps this an index-read failure, not a zero-column failure.
        columns=[_column("Customers", "CustomerID", "INTEGER", 10)],
        statistics_error=RuntimeError("statistics failed"),
    )
    document = DocumentationInspectService().inspect(
        OdbcInspectionPort(connection), tables=None, queries=None
    )
    table = document.tables[0]
    assert table.primary_key.state == "failed"
    assert "statistics failed" in table.primary_key.reason
    assert document.coverage.tables.failed["Customers"]
    assert document.coverage.tables.unsupported == {}
    assert document.status == "partial"


def test_odbc_linked_synonym_is_not_read_and_reports_explicit_gap() -> None:
    """T1: a linked SYNONYM is never read; its gap is explicit, never failed/empty.

    The old contract read SQLColumns/SQLStatistics for the SYNONYM (contacting the
    linked source and, when it was unreachable, fabricating a failed zero-column table).
    The new contract classifies first: the linked table keeps its kind with empty
    fields/indexes, an ``unavailable`` primary key, and a coverage ``unsupported``
    reason, and no catalog call targets it.
    """
    connection = _FakeConnection(
        tables=[_table("Local"), _table("LinkedCustomers", "SYNONYM")],
        columns=[_column("Local", "Note", "VARCHAR", 50)],
        statistics=[_index("LinkedCustomers", "PK_SentinelCustomers", "CustomerID")],
    )
    document = DocumentationInspectService().inspect(
        OdbcInspectionPort(connection), tables=None, queries=None
    )
    assert ("columns", {"table": "LinkedCustomers"}) not in connection.calls
    assert ("statistics", {"table": "LinkedCustomers"}) not in connection.calls
    linked = next(table for table in document.tables if table.name == "LinkedCustomers")
    assert linked.kind == "linked"
    assert linked.fields == []
    assert linked.indexes == []
    assert linked.primary_key.state == "unavailable"
    assert "LinkedCustomers" in document.coverage.tables.unsupported
    assert "LinkedCustomers" not in document.coverage.tables.failed
    # A local table in the same database is still inspected normally (PRESERVE).
    local = next(table for table in document.tables if table.name == "Local")
    assert [field.name for field in local.fields] == ["Note"]
    assert "Local" in document.coverage.tables.inspected
    assert "Local" not in document.coverage.tables.failed


def test_odbc_zero_column_local_table_is_failed_not_observed_empty() -> None:
    """B1 PRESERVE: a local table whose column read returns nothing is ``failed``.

    A listed Access table can never legitimately have zero columns, so a local ``TABLE``
    row that yields no SQLColumns rows stays a failed read, not an observed-empty schema.
    """
    connection = _FakeConnection(
        tables=[_table("Local"), _table("Empty")],
        columns=[_column("Local", "Note", "VARCHAR", 50)],
    )
    document = DocumentationInspectService().inspect(
        OdbcInspectionPort(connection), tables=None, queries=None
    )
    reason = document.coverage.tables.failed["Empty"]
    assert "no columns" in reason
    empty = next(table for table in document.tables if table.name == "Empty")
    assert empty.indexes == []
    assert empty.primary_key.state == "failed"
    assert empty.primary_key.reason == reason


def test_odbc_seam_never_executes_a_query_or_writes() -> None:
    connection = _odbc_connection()
    DocumentationInspectService().inspect(
        OdbcInspectionPort(connection), tables=None, queries=None
    )
    catalog_calls = {call[0] for call in connection.calls}
    assert catalog_calls <= {"tables", "columns", "statistics", "procedureColumns"}
    assert "execute" not in catalog_calls


# ---------------------------------------------------------------------------
# 3. Wiring: the CLI and MCP select the ODBC port for the ODBC backend
# ---------------------------------------------------------------------------


def test_cli_inspect_with_odbc_backend_returns_evidence_envelope(capsys) -> None:
    connection = _odbc_connection()
    database = _WiringDatabase(connection)
    with patch.object(
        cli,
        "run_documentation_inspection",
        lambda *a, **k: inspect_open_database(database, tables=k.get("tables"), queries=k.get("queries")),
    ):
        result = _run_cli(["documentation", "inspect", "--db", "core.accdb", "--backend", "odbc", "--json"])

    captured = capsys.readouterr()
    assert result == 0, captured.err
    assert "Traceback" not in captured.err
    envelope = json.loads(captured.out)
    assert envelope["success"] is True
    assert envelope["data"]["source"]["backend_identity"] == "ODBC"
    assert envelope["data"]["data"]["status"] == "partial"


def test_inspect_open_database_without_dao_or_odbc_fails_closed() -> None:
    with pytest.raises(ValueError, match="connected DAO or ODBC backend"):
        inspect_open_database(_NoInspectionDatabase(), tables=None, queries=None)


def test_cli_inspect_surfaces_a_facade_open_failure_as_an_error_envelope(capsys) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("Documentation inspection requires a connected DAO or ODBC backend")

    with patch.object(cli, "run_documentation_inspection", _boom):
        result = _run_cli(["documentation", "inspect", "--db", "core.accdb", "--backend", "odbc"])

    captured = capsys.readouterr()
    assert result == 1
    assert "Traceback" not in captured.err
    assert "connected DAO or ODBC backend" in captured.err


def test_inspect_open_database_odbc_uses_the_readonly_inspection_factory() -> None:
    """T2: the worker extraction branch opens the read-only seam, never the CRUD connection."""
    connection = _odbc_connection()
    database = _WiringDatabase(connection)
    document = inspect_open_database(database, tables=None, queries=None)

    adapter = database._adapter
    assert document.source.backend_identity == "ODBC"
    assert adapter.readonly_open_calls == 1
    assert adapter.connection_reads == 0
    assert connection.close_calls == 1


def test_mcp_inspect_forwards_inspection_context_to_the_facade() -> None:
    """The MCP callback never opens the source itself: it forwards backend/dir policy."""
    connection = _odbc_connection()
    database = _WiringDatabase(connection)
    seen: dict[str, Any] = {}

    def fake_run(path: str, *, backend: str, allowed_dirs: Any, tables: Any, queries: Any, include_query_sql: bool = False) -> Any:
        seen.update(path=path, backend=backend, allowed_dirs=list(allowed_dirs), tables=tables, queries=queries, include_query_sql=include_query_sql)
        return inspect_open_database(database, tables=tables, queries=queries, include_query_sql=include_query_sql)

    with (
        patch.object(analysis, "_new_db", return_value=database),
        patch.object(analysis, "run_documentation_inspection", fake_run),
    ):
        result = analysis.documentation_inspect(path="core.accdb", tables=["Customers"], queries=None)

    assert json.loads(result.content[0].text)["success"] is True
    assert seen == {
        "path": "core.accdb",
        "backend": "odbc",
        "allowed_dirs": [],
        "tables": ["Customers"],
        "queries": None,
        "include_query_sql": False,
    }


def test_mcp_inspect_forwards_the_sql_opt_in_flag() -> None:
    """MCP opt-in is forwarded to the facade; the default stays false."""
    connection = _odbc_connection()
    database = _WiringDatabase(connection)
    seen: list[bool] = []

    def fake_run(path: str, *, backend: str, allowed_dirs: Any, tables: Any, queries: Any, include_query_sql: bool = False) -> Any:
        seen.append(include_query_sql)
        return inspect_open_database(database, tables=tables, queries=queries, include_query_sql=include_query_sql)

    with (
        patch.object(analysis, "_new_db", return_value=database),
        patch.object(analysis, "run_documentation_inspection", fake_run),
    ):
        analysis.documentation_inspect(path="core.accdb")
        analysis.documentation_inspect(path="core.accdb", include_query_sql=True)

    assert seen == [False, True]


def test_mcp_inspect_odbc_uses_the_readonly_inspection_factory() -> None:
    """T2: the MCP callback's facade extraction opens the read-only seam, not the CRUD handle."""
    connection = _odbc_connection()
    database = _WiringDatabase(connection)
    with (
        patch.object(analysis, "_new_db", return_value=database),
        patch.object(
            analysis,
            "run_documentation_inspection",
            lambda *a, **k: inspect_open_database(database, tables=k.get("tables"), queries=k.get("queries")),
        ),
    ):
        result = analysis.documentation_inspect(path="core.accdb")

    adapter = database._adapter
    assert json.loads(result.content[0].text)["success"] is True
    assert adapter.readonly_open_calls == 1
    assert adapter.connection_reads == 0
    assert connection.close_calls == 1


def test_mcp_inspect_with_odbc_backend_returns_evidence_envelope() -> None:
    connection = _odbc_connection()
    database = _WiringDatabase(connection)
    with (
        patch.object(analysis, "_new_db", return_value=database),
        patch.object(
            analysis,
            "run_documentation_inspection",
            lambda *a, **k: inspect_open_database(database, tables=k.get("tables"), queries=k.get("queries")),
        ),
    ):
        result = analysis.documentation_inspect(path="core.accdb")

    payload = json.loads(result.content[0].text)
    assert payload["success"] is True
    assert payload["data"]["source"]["backend_identity"] == "ODBC"
    assert result.isError is False


# ---------------------------------------------------------------------------
# 4. T2: inspection-specific engine-level read-only connection
# ---------------------------------------------------------------------------

# The exact connection string ``OdbcAdapter.connect`` builds; the inspection open must
# reuse the same recorded target/password and add ``readonly=True``.
_EXPECTED_CONN_STR = "DBQ=core.accdb;PWD=canary-secret;DRIVER={Microsoft Access Driver (*.mdb, *.accdb)}"


def test_odbc_readonly_open_pins_readonly_and_reuses_recorded_target_and_password(
    monkeypatch,
) -> None:
    """T2: the inspection open is a fresh ``readonly=True`` connection with recorded state."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_connect(conn_str: str, **kwargs: Any) -> _FakeConnection:
        calls.append((conn_str, kwargs))
        return _odbc_connection()

    monkeypatch.setattr("ms_access_mcp.backends.odbc.pyodbc.connect", fake_connect)
    adapter = OdbcAdapter("core.accdb")
    adapter.connect("core.accdb", password="canary-secret")
    inspection = adapter.open_readonly_inspection_connection()

    assert calls == [
        (_EXPECTED_CONN_STR, {}),
        (_EXPECTED_CONN_STR, {"readonly": True}),
    ]
    # The CRUD handle is untouched and the inspection handle is a distinct instance.
    assert adapter._conn is not inspection
    assert adapter.connected is True
    assert adapter.is_connected() is True


def test_odbc_readonly_open_requires_a_connected_recorded_session(monkeypatch) -> None:
    """T2: without a connected session there is no recorded target/password to reuse."""
    calls: list[Any] = []
    monkeypatch.setattr("ms_access_mcp.backends.odbc.pyodbc.connect", lambda *a, **k: calls.append((a, k)))
    adapter = OdbcAdapter("core.accdb")

    with pytest.raises(RuntimeError):
        adapter.open_readonly_inspection_connection()
    assert calls == []


def test_odbc_readonly_open_never_falls_back_to_a_writable_connection(monkeypatch) -> None:
    """T2: a read-only failure is not retried without the read-only attribute."""
    kwargs_seen: list[dict[str, Any]] = []

    def fake_connect(_conn_str: str, **kwargs: Any) -> _FakeConnection:
        kwargs_seen.append(kwargs)
        if kwargs.get("readonly"):
            raise RuntimeError("driver does not support read-only access mode")
        return _odbc_connection()

    monkeypatch.setattr("ms_access_mcp.backends.odbc.pyodbc.connect", fake_connect)
    adapter = OdbcAdapter("core.accdb")
    adapter.connect("core.accdb")

    with pytest.raises(RuntimeError, match="read-only"):
        adapter.open_readonly_inspection_connection()
    assert kwargs_seen == [{}, {"readonly": True}]


def test_open_odbc_inspection_port_owns_and_closes_the_readonly_connection_exactly_once() -> None:
    """T2: the factory port owns the handle and closes it exactly once, on success."""
    connection = _odbc_connection()
    adapter = _WiringAdapter(connection)
    port = open_odbc_inspection_port(adapter)
    assert adapter.readonly_open_calls == 1
    assert adapter.connection_reads == 0

    DocumentationInspectService().inspect(port, tables=None, queries=None)
    assert connection.close_calls == 1
    port.close()  # idempotent: a second close never closes the handle again
    assert connection.close_calls == 1


def test_open_odbc_inspection_port_closes_on_inspection_failure() -> None:
    """T2: the owned handle is released once even when a catalog read raises."""

    class _BoomConnection(_FakeConnection):
        def cursor(self) -> _FakeCursor:
            raise RuntimeError("catalog read failed")

    connection = _BoomConnection()
    port = open_odbc_inspection_port(_WiringAdapter(connection))

    with pytest.raises(RuntimeError, match="catalog read failed"):
        DocumentationInspectService().inspect(port, tables=None, queries=None)
    assert connection.close_calls == 1


def test_open_odbc_inspection_port_does_not_leak_a_handle_when_wrapping_fails(monkeypatch) -> None:
    """T2: a setup exception after the open still releases the new handle."""
    connection = _odbc_connection()
    adapter = _WiringAdapter(connection)

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("port construction failed")

    monkeypatch.setattr(documentation_inspect, "OdbcInspectionPort", boom)
    with pytest.raises(RuntimeError, match="port construction failed"):
        open_odbc_inspection_port(adapter)
    assert connection.close_calls == 1


def test_open_odbc_inspection_port_scrubs_credentials_from_open_failure() -> None:
    """T2: an open failure reaches the caller as a scrubbed AccessMcpError."""
    adapter = _FailingOpenAdapter(
        RuntimeError(f"SQLDriverConnect failed: {_EXPECTED_CONN_STR}")
    )

    with pytest.raises(AccessMcpError) as excinfo:
        open_odbc_inspection_port(adapter)
    message = str(excinfo.value)
    assert adapter.readonly_open_calls == 1
    assert "canary-secret" not in message
    assert "PWD=***" in message


def test_open_odbc_inspection_port_rejects_an_adapter_without_the_readonly_seam() -> None:
    """T2: no writable fallback — an adapter without the seam is refused, not reused."""
    class _ConnectionOnlyAdapter:
        connection = _odbc_connection()

    with pytest.raises(AccessMcpError, match="read-only inspection"):
        open_odbc_inspection_port(_ConnectionOnlyAdapter())
