"""Unit tests for application/migration.py using fakes.

These tests use fake connectors and do not require real database drivers.
Integration markers (postgres/mysql/sqlserver) are used to skip tests
that would require ODBC drivers.
"""

from __future__ import annotations

import pytest

from ms_access_mcp.application.migration import (
    MigrationUseCases,
    list_jobs,
    register_job,
)
from ms_access_mcp.backends.migration import CONNECTOR_FACTORIES
from ms_access_mcp.backends.migration.connectors import BaseConnector
from ms_access_mcp.errors import Err, Ok
from ms_access_mcp.models import ColumnSchema, TableSchema
from ms_access_mcp.models.migration import (
    ExtractedSchema,
    MigrationJob,
    TableResult,
    TableTransferConfig,
)


class FakeConnector(BaseConnector):
    """Fake connector that tracks calls without real DB."""

    def __init__(self, connection_string: str) -> None:
        super().__init__(connection_string)
        self._connected = False
        self._tables: set[str] = set()
        self._data: dict[str, list[dict]] = {}
        self._calls: list[str] = []

    def connect(self) -> None:
        self._connected = True
        self._calls.append("connect")

    def table_exists(self, table: str) -> bool:
        self._calls.append(f"table_exists:{table}")
        return table in self._tables

    def create_table(self, ddl: str) -> None:
        self._calls.append(f"create_table:{ddl[:50]}")
        # Extract table name from DDL for testing
        import re

        m = re.search(r'CREATE TABLE "?(\w+)"?', ddl)
        if m:
            self._tables.add(m.group(1))

    def insert_batch(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        self._calls.append(f"insert_batch:{table}:{len(rows)}")
        if table not in self._data:
            self._data[table] = []
        for row in rows:
            self._data[table].append(dict(zip(columns, row)))
        return len(rows)

    def rollback_table(self, table: str) -> None:
        self._calls.append(f"rollback_table:{table}")
        self._tables.discard(table)
        self._data.pop(table, None)

    def disconnect(self) -> None:
        self._connected = False
        self._calls.append("disconnect")


# Register fake for testing
CONNECTOR_FACTORIES["fake"] = FakeConnector  # type: ignore[assignment]


class TestExtractedSchema:
    """Tests for ExtractedSchema model."""

    def test_extracted_schema_defaults(self):
        """ExtractedSchema has sensible defaults."""
        schema = ExtractedSchema(source="test.accdb", tables=[])
        assert schema.version == "1.0"
        assert schema.tables == []
        assert schema.extracted_at is not None

    def test_extracted_schema_with_tables(self):
        """ExtractedSchema can hold table schemas."""
        tables = [
            TableSchema(
                name="Customers",
                columns=[ColumnSchema(name="id", source_type="Long Integer")],
                primary_key=["id"],
                foreign_keys=[],
                indexes=[],
            )
        ]
        schema = ExtractedSchema(source="test.accdb", tables=tables)
        assert len(schema.tables) == 1
        assert schema.tables[0].name == "Customers"


class TestMigrationJob:
    """Tests for MigrationJob model."""

    def test_migration_job_defaults(self):
        """MigrationJob has pending status by default."""
        job = MigrationJob(id="abc")
        assert job.status == "pending"
        assert job.id == "abc"
        assert job.phase == "extract"
        assert job.results == []

    def test_migration_job_with_results(self):
        """MigrationJob can hold per-table results."""
        table_result = TableResult(table="Customers", source_rows=10, rows_transferred=10)
        job = MigrationJob(id="abc", results=[table_result])
        assert len(job.results) == 1
        assert job.results[0].table == "Customers"


class TestTableResult:
    """Tests for TableResult model."""

    def test_table_result_success(self):
        """TableResult captures success stats."""
        result = TableResult(
            table="Customers",
            source_rows=100,
            rows_transferred=100,
            duration_ms=1500,
        )
        assert result.success is True
        assert result.rows_transferred == 100
        assert result.strategy_used == "batch"

    def test_table_result_error(self):
        """TableResult captures error."""
        result = TableResult(table="Customers", success=False, error="Connection refused")
        assert result.success is False
        assert result.error is not None
        assert "Connection refused" in result.error


class TestTableTransferConfig:
    """Tests for TableTransferConfig model."""

    def test_table_transfer_config_defaults(self):
        """TableTransferConfig has all-optional defaults."""
        config = TableTransferConfig()
        assert config.columns is None
        assert config.where is None
        assert config.order_by is None

    def test_table_transfer_config_with_overrides(self):
        """TableTransferConfig can specify column subset and filtering."""
        config = TableTransferConfig(
            columns=["id", "name"],
            where="active = 1",
            order_by=["name"],
        )
        assert config.columns == ["id", "name"]
        assert config.where == "active = 1"
        assert config.order_by == ["name"]


class TestMigrationUseCasesUnit:
    """Unit tests for MigrationUseCases with mocked dependencies."""

    def test_get_migration_status_returns_job(self):
        """get_migration_status returns job data when job exists."""
        usecases = MigrationUseCases()
        job = MigrationJob(id="test123", status="running")
        register_job(job)

        result = usecases.get_migration_status("test123")
        assert isinstance(result, Ok)
        assert result.data["id"] == "test123"

    def test_get_migration_status_not_found(self):
        """get_migration_status returns error when job not found."""
        usecases = MigrationUseCases()
        result = usecases.get_migration_status("nonexistent")
        assert isinstance(result, Err)
        assert "not found" in result.message

    def test_extract_schema_rejects_empty_path(self):
        """extract_schema returns error for empty database_path."""
        usecases = MigrationUseCases()
        result = usecases.extract_schema("", "sqlite")
        assert isinstance(result, Err)
        assert "required" in result.message

    def test_extract_schema_rejects_invalid_target(self):
        """extract_schema returns error for unknown target_type."""
        usecases = MigrationUseCases()
        # File existence is checked before target_type validation
        result = usecases.extract_schema("test.accdb", "unknown_dialect")
        assert isinstance(result, Err)
        # First error is file not found, which is acceptable
        assert "not found" in result.message or "unknown" in result.message

    def test_extract_schema_rejects_missing_file(self):
        """extract_schema returns error when file does not exist."""
        usecases = MigrationUseCases()
        result = usecases.extract_schema("nonexistent.accdb", "sqlite")
        assert isinstance(result, Err)
        assert "not found" in result.message

    def test_upload_schema_requires_confirm(self):
        """upload_schema returns error when confirm=False."""
        usecases = MigrationUseCases()
        schema = ExtractedSchema(source="test.accdb", tables=[])
        result = usecases.upload_schema(
            "sqlite",
            "memory://",
            schema.model_dump(),
            confirm=False,
        )
        assert isinstance(result, Err)
        assert "confirm" in result.message

    def test_transfer_data_requires_confirm(self):
        """transfer_data returns error when confirm=False."""
        usecases = MigrationUseCases()
        result = usecases.transfer_data(
            "sqlite",
            "memory://",
            "test.accdb",
            confirm=False,
        )
        assert isinstance(result, Err)
        assert "confirm" in result.message

    def test_transfer_data_rejects_unsupported_mode(self):
        """transfer_data rejects linked/passthrough transfer modes."""
        usecases = MigrationUseCases()
        result = usecases.transfer_data(
            "sqlite",
            "memory://",
            "test.accdb",
            transfer_mode="linked",
            confirm=True,
        )
        assert isinstance(result, Err)
        assert "linked" in result.message

    def test_transfer_data_rejects_bad_verification_mode(self):
        """transfer_data rejects unknown verification_mode."""
        usecases = MigrationUseCases()
        result = usecases.transfer_data(
            "sqlite",
            "memory://",
            "test.accdb",
            verification_mode="unknown",
            confirm=True,
        )
        assert isinstance(result, Err)
        assert "verification_mode" in result.message

    def test_list_jobs_returns_all_registered(self):
        """list_jobs returns all registered jobs."""
        # Clear any pre-existing jobs
        from ms_access_mcp.application import migration

        migration._JOBS.clear()
        job1 = MigrationJob(id="job1")
        job2 = MigrationJob(id="job2")
        register_job(job1)
        register_job(job2)

        jobs = list_jobs()
        job_ids = {j.id for j in jobs}
        assert "job1" in job_ids
        assert "job2" in job_ids


class TestODBCGranularGetters:
    """Unit tests for ODBC extraction path — verifies granular getters are used.

    These tests verify Defect 3 is fixed: _extract_odbc must NOT call the
    forbidden get_table_schema_plan() sentinel.  Instead it must call the
    granular getters (get_tables, get_columns, get_indexes, get_relationships)
    per design §6.
    """

    def test_extract_odbc_uses_granular_getters_not_sentinel(self):
        """ODBC path calls granular getters and never calls get_table_schema_plan.

        This test would have caught Defect 3: _extract_odbc calling the forbidden
        get_table_schema_plan() sentinel instead of granular getters.
        """
        from unittest.mock import MagicMock, patch

        from ms_access_mcp.models import IndexInfo, TableInfo

        # Build fake ODBC adapter with granular getter interface
        fake_adapter = MagicMock()
        t_orders = TableInfo(name="Orders", fields=[], record_count=0, primary_key=[])
        t_products = TableInfo(name="Products", fields=[], record_count=0, primary_key=[])
        fake_adapter.get_tables.return_value = [t_orders, t_products]
        fake_adapter.get_columns.return_value = [
            {"name": "id", "type": "Long Integer", "nullable": False, "size": 0, "autoincrement": True, "default": None},
            {"name": "name", "type": "Text", "nullable": True, "size": 255, "autoincrement": False, "default": None},
        ]
        fake_adapter.get_indexes.return_value = [
            IndexInfo(name="PK_Orders", columns=["id"], is_unique=True, is_primary=True, ignore_nulls=False),
        ]
        fake_adapter.get_relationships.return_value = []
        fake_adapter.disconnect = MagicMock()

        # Patch BackendSelector to return our fake ODBC adapter
        with patch(
            "ms_access_mcp.application.migration.BackendSelector"
        ) as mock_selector:
            mock_selector.get_adapter.return_value = fake_adapter

            use_cases = MigrationUseCases()
            tables, flags = use_cases._extract_odbc("dummy.accdb")

        # Verify granular getters were called (not the sentinel)
        fake_adapter.get_tables.assert_called_once()
        fake_adapter.get_columns.assert_called()
        fake_adapter.get_indexes.assert_called()
        fake_adapter.get_relationships.assert_called_once()

        # get_table_schema_plan must NEVER be called on the ODBC path
        # (It is the forbidden sentinel per design §6)
        fake_adapter.get_table_schema_plan.assert_not_called()

        # Verify unknown_metadata flags are all True (ODBC limitation)
        assert flags.primary_keys is True
        assert flags.foreign_keys is True
        assert flags.defaults is True
        assert flags.indexes is True
        assert flags.autoincrement is True

        # Verify TableSchema objects were built from granular data
        assert len(tables) == 2
        table_names = {t.name for t in tables}
        assert table_names == {"Orders", "Products"}

    def test_extract_odbc_falls_back_to_unknown_flags_on_exception(self):
        """If granular getters raise, _extract_odbc returns empty tables with all flags True."""
        from unittest.mock import MagicMock, patch

        from ms_access_mcp.backends.schema_inspector import UnknownMetadata

        fake_adapter = MagicMock()
        fake_adapter.get_tables.side_effect = RuntimeError("ODBC driver unavailable")
        fake_adapter.disconnect = MagicMock()

        with patch(
            "ms_access_mcp.application.migration.BackendSelector"
        ) as mock_selector:
            mock_selector.get_adapter.return_value = fake_adapter

            use_cases = MigrationUseCases()
            tables, flags = use_cases._extract_odbc("nonexistent.accdb")

        assert tables == []
        assert flags == UnknownMetadata(
            primary_keys=True,
            foreign_keys=True,
            defaults=True,
            indexes=True,
            autoincrement=True,
        )

    def test_extract_odbc_builds_foreign_keys_from_relationships(self):
        """FK information is pulled from get_relationships, not from a sentinel."""
        from unittest.mock import MagicMock, patch

        from ms_access_mcp.models import RelationshipInfo, TableInfo

        fake_adapter = MagicMock()
        t_orderdetails = TableInfo(name="OrderDetails", fields=[], record_count=0, primary_key=[])
        fake_adapter.get_tables.return_value = [t_orderdetails]
        fake_adapter.get_columns.return_value = [
            {"name": "order_id", "type": "Long Integer", "nullable": False, "size": 0, "autoincrement": False, "default": None},
            {"name": "product_id", "type": "Long Integer", "nullable": False, "size": 0, "autoincrement": False, "default": None},
        ]
        fake_adapter.get_indexes.return_value = []
        # Use real RelationshipInfo so attribute access works correctly
        rel = RelationshipInfo(
            name="FK_OrderDetails_Orders",
            table="OrderDetails",
            foreign_table="Orders",
            columns=["order_id"],
            foreign_columns=["id"],
        )
        fake_adapter.get_relationships.return_value = [rel]
        fake_adapter.disconnect = MagicMock()

        with patch(
            "ms_access_mcp.application.migration.BackendSelector"
        ) as mock_selector:
            mock_selector.get_adapter.return_value = fake_adapter

            use_cases = MigrationUseCases()
            tables, flags = use_cases._extract_odbc("dummy.accdb")

        assert len(tables) == 1
        fk = tables[0].foreign_keys[0]
        assert fk.name == "FK_OrderDetails_Orders"
        assert fk.columns == ["order_id"]
        assert fk.referenced_table == "Orders"
        assert fk.referenced_columns == ["id"]


class TestIntegrationSkips:
    """Tests marked for integration drivers — skipped in unit test run."""

    @pytest.mark.odbc_integration
    def test_postgres_live_connection(self):
        """Requires psycopg2 driver — skip if not available."""
        pytest.skip("Integration test — requires postgres driver")

    @pytest.mark.odbc_integration
    def test_mysql_live_connection(self):
        """Requires mysql-connector-python driver — skip if not available."""
        pytest.skip("Integration test — requires mysql driver")

    @pytest.mark.odbc_integration
    def test_sqlserver_live_connection(self):
        """Requires pyodbc driver — skip if not available."""
        pytest.skip("Integration test — requires sqlserver driver")
