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

from ms_access_mcp import cli
from ms_access_mcp.application.database import Ok
from ms_access_mcp.application.documentation_inspect import (
    DocumentationInspectService,
    InspectionCapabilities,
    InspectionCapabilityUnavailableError,
    OdbcInspectionPort,
)
from ms_access_mcp.backends.schema_inspector import QueryParameterInfo
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

    def cursor(self) -> _FakeCursor:
        cursor = _FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def close(self) -> None:
        self.closed = True


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


def _run_cli(argv: list[str]) -> int:
    with patch.object(sys, "argv", ["ms-access-mcp", *argv]):
        return cli.main()


class _WiringAdapter:
    def __init__(self, connection: Any) -> None:
        self.connection = connection


class _WiringDatabase:
    def __init__(self, connection: Any) -> None:
        self._adapter = _WiringAdapter(connection)
        self.closed = False

    def open_database(self, _path: str) -> Ok:
        return Ok(data={"path": _path})

    def close_database(self) -> Ok:
        self.closed = True
        return Ok()


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
        OdbcInspectionPort(_odbc_connection()), tables=None, queries=None
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


def test_odbc_zero_column_linked_table_is_failed_not_observed_empty() -> None:
    """B1: an unreachable linked source reads zero columns and must be ``failed``.

    Access ODBC returns zero SQLColumns rows *without raising* for a linked table whose
    source is unreachable, so the port's exception -> failed path never fires and the
    table was recorded as an observed-empty field/index list (a false fact). A listed
    Access table can never legitimately have zero columns, so the service must route it
    through the same failed-table path it uses for read failures.
    """
    connection = _FakeConnection(
        tables=[_table("Local"), _table("LinkedCustomers", "SYNONYM")],
        columns=[_column("Local", "Note", "VARCHAR", 50)],
        statistics=[_index("LinkedCustomers", "PK_SentinelCustomers", "CustomerID")],
    )
    document = DocumentationInspectService().inspect(
        OdbcInspectionPort(connection), tables=None, queries=None
    )
    # The zero-column table is a failure with a zero-columns reason, never observed empty.
    reason = document.coverage.tables.failed["LinkedCustomers"]
    assert "no columns" in reason
    linked = next(table for table in document.tables if table.name == "LinkedCustomers")
    assert linked.fields == []
    assert linked.indexes == []
    assert linked.primary_key.state == "failed"
    assert linked.primary_key.reason == reason
    # A local table with real columns is still inspected normally (PRESERVE).
    local = next(table for table in document.tables if table.name == "Local")
    assert [field.name for field in local.fields] == ["Note"]
    assert "Local" in document.coverage.tables.inspected
    assert "Local" not in document.coverage.tables.failed


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
    database = _WiringDatabase(_odbc_connection())
    with patch.object(cli, "DatabaseUseCases", return_value=database):
        result = _run_cli(["documentation", "inspect", "--db", "core.accdb", "--backend", "odbc", "--json"])

    captured = capsys.readouterr()
    assert result == 0, captured.err
    assert database.closed is True
    assert "Traceback" not in captured.err
    envelope = json.loads(captured.out)
    assert envelope["success"] is True
    assert envelope["data"]["source"]["backend_identity"] == "ODBC"
    assert envelope["data"]["data"]["status"] == "partial"


def test_cli_inspect_without_dao_or_odbc_reports_an_error_envelope(capsys) -> None:
    database = _WiringDatabase(None)
    with patch.object(cli, "DatabaseUseCases", return_value=database):
        result = _run_cli(["documentation", "inspect", "--db", "core.accdb", "--backend", "odbc"])

    captured = capsys.readouterr()
    assert result == 1
    assert "Traceback" not in captured.err
    assert "connected DAO or ODBC backend" in captured.err
    assert database.closed is True


def test_mcp_inspect_with_odbc_backend_returns_evidence_envelope() -> None:
    database = _WiringDatabase(_odbc_connection())
    with patch.object(analysis, "_new_db", return_value=database):
        result = analysis.documentation_inspect(path="core.accdb")

    payload = json.loads(result.content[0].text)
    assert payload["success"] is True
    assert payload["data"]["source"]["backend_identity"] == "ODBC"
    assert result.isError is False
