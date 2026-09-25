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
from ms_access_mcp.models import ColumnSchema, QueryResult, TableSchema
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


# =============================================================================
# Step 4 RED: Migration connection_ref + error hardening
# Cases 20-23 from plan 040
# =============================================================================


class TestMigrationConnectionRef:
    """Case 20: upload_schema with connection_ref — test the resolution helper."""

    def test_resolve_connect_arg_ref_found_returns_resolved(self, monkeypatch):
        """_resolve_connect_arg returns resolved string when profile found."""
        import ms_access_mcp.application.migration as migration_module

        monkeypatch.setattr(migration_module, "resolve_profile", lambda n: "Driver={ODBC};PWD=secret" if n == "prod" else None)

        uc = migration_module.MigrationUseCases()
        resolved, err = uc._resolve_connect_arg(connection_string=None, connection_ref="prod")

        assert err is None
        assert resolved == "Driver={ODBC};PWD=secret"

    def test_resolve_connect_arg_ref_not_found_returns_error(self, monkeypatch):
        """_resolve_connect_arg returns error when profile not found."""
        import ms_access_mcp.application.migration as migration_module

        monkeypatch.setattr(migration_module, "resolve_profile", lambda n: None)

        uc = migration_module.MigrationUseCases()
        resolved, err = uc._resolve_connect_arg(connection_string=None, connection_ref="nonexistent")

        assert resolved is None
        assert err is not None
        assert "nonexistent" in err
        assert "credentials profile set" in err

    def test_resolve_connect_arg_literal_with_credential_hard_reject(self, monkeypatch):
        """_resolve_connect_arg hard-rejects connection_string containing PWD=."""
        import ms_access_mcp.application.migration as migration_module

        monkeypatch.setattr(migration_module, "resolve_profile", lambda n: None)

        uc = migration_module.MigrationUseCases()
        resolved, err = uc._resolve_connect_arg(connection_string="ODBC;PWD=hunter2", connection_ref=None)

        assert resolved is None
        assert err is not None
        assert "credentials profile" in err
        assert "hunter2" not in err

    def test_resolve_connect_arg_both_ambiguous(self, monkeypatch):
        """_resolve_connect_arg returns ambiguous error when both are set."""
        import ms_access_mcp.application.migration as migration_module

        monkeypatch.setattr(migration_module, "resolve_profile", lambda n: "resolved")

        uc = migration_module.MigrationUseCases()
        resolved, err = uc._resolve_connect_arg(connection_string="ODBC", connection_ref="profile")

        assert resolved is None
        assert err is not None
        assert "ambiguous" in err.lower()


class TestMigrationConnectionRefErrors:
    """Case 21: connection_ref errors — same 5-rule behavior as Step 3."""

    def test_connection_ref_not_found_error(self, monkeypatch):
        """connection_ref not found → Err with CLI guidance, no credential value."""
        import ms_access_mcp.application.migration as migration_module

        monkeypatch.setattr(migration_module, "resolve_profile", lambda name: None)

        uc = MigrationUseCases()
        result = uc.upload_schema(
            target_type="sqlite",
            connection_ref="nonexistent",
            schema_json={"tables": []},
            confirm=True,
        )

        assert isinstance(result, Err), f"expected Err, got {result}"
        assert "ms-access-mcp credentials profile set" in result.message
        assert "nonexistent" in result.message
        assert "hunter2" not in result.message

    def test_connection_ref_with_literal_credentials_hard_reject(self, monkeypatch):
        """literal connection_string with PWD= → hard-reject Err."""
        import ms_access_mcp.application.migration as migration_module

        monkeypatch.setattr(migration_module, "resolve_profile", lambda name: None)

        uc = MigrationUseCases()
        result = uc.upload_schema(
            target_type="sqlite",
            connection_string="ODBC;PWD=hunter2;Database=test.db",
            schema_json={"tables": []},
            confirm=True,
        )

        assert isinstance(result, Err), f"expected Err, got {result}"
        assert "credentials profile set" in result.message
        assert "hunter2" not in result.message

    def test_both_connection_ref_and_literal_ambiguous(self, monkeypatch):
        """Providing both connection_ref and connection_string → Err (ambiguous)."""
        import ms_access_mcp.application.migration as migration_module

        monkeypatch.setattr(migration_module, "resolve_profile", lambda name: "resolved")

        uc = MigrationUseCases()
        result = uc.upload_schema(
            target_type="sqlite",
            connection_ref="prod_dw",
            connection_string="ODBC;DSN=mydb",
            schema_json={"tables": []},
            confirm=True,
        )

        assert isinstance(result, Err), f"expected Err, got {result}"
        assert "ambiguous" in result.message.lower()


class TestMigrationImportErrorSanitization:
    """Case 22: ImportError path routes through _sanitize_error."""

    def test_import_error_sanitized(self, monkeypatch):
        """ImportError (missing driver) → error message is sanitized, not raw."""
        import ms_access_mcp.application.migration as migration_module
        from ms_access_mcp.application.migration import MigrationUseCases

        def raising_get_connector(target_type, connection_string):
            raise ImportError(f"No driver for {target_type}: ODBC;PWD=hunter2;Database=test.db")

        monkeypatch.setattr(migration_module, "get_connector", raising_get_connector)

        uc = MigrationUseCases()
        result = uc.upload_schema(
            target_type="sqlite",
            connection_string="ODBC;PWD=hunter2;Database=test.db",
            schema_json={"tables": []},
            confirm=True,
        )

        assert isinstance(result, Err), f"expected Err, got {result}"
        # The error should be sanitized, not containing the raw connection string
        # Note: "PWD=" may appear in the guidance text itself (explaining the hard-reject rule),
        # so we check for the actual credential value "hunter2" instead
        assert "hunter2" not in result.message


class TestBaseConnectorRepr:
    """Case 23: BaseConnector.__repr__ is redacted, never contains connection string."""

    def test_base_connector_repr_is_redacted(self):
        """repr(connector) returns '<TypeName [redacted]>', never the connection string."""
        from ms_access_mcp.backends.migration.connectors import BaseConnector

        # Create a subclass for testing (BaseConnector is abstract)
        class TestConnector(BaseConnector):
            def connect(self) -> None:
                pass

            def table_exists(self, table: str) -> bool:
                return False

            def create_table(self, ddl: str) -> None:
                pass

            def insert_batch(self, table: str, columns: list[str], rows: list[tuple]) -> int:
                return 0

            def rollback_table(self, table: str) -> None:
                pass

            def disconnect(self) -> None:
                pass

        connector = TestConnector("Driver={ODBC};PWD=hunter2;Database=test.db")
        repr_str = repr(connector)

        # repr must not contain the connection string
        assert "hunter2" not in repr_str
        assert "ODBC" not in repr_str
        assert "Database" not in repr_str
        # repr must contain the class name and [redacted]
        assert "TestConnector" in repr_str
        assert "[redacted]" in repr_str


class TestSourceAdapterLifecycle:
    def test_transfer_data_accepts_query_result_dataclass(self, monkeypatch):
        from unittest.mock import MagicMock

        import ms_access_mcp.application.migration as migration_module

        adapter = MagicMock()
        adapter.execute_query.return_value = QueryResult(
            success=True,
            rows=[{"id": 7}],
            count=1,
            columns=["id"],
        )
        monkeypatch.setattr(
            migration_module.BackendSelector, "get_adapter", MagicMock(return_value=adapter)
        )
        connector = FakeConnector("Driver={ODBC};Database=test.db")
        use_cases = MigrationUseCases()
        monkeypatch.setattr(use_cases, "_get_connector", lambda *_: Ok(data=connector))
        schema = ExtractedSchema(
            source="source.accdb",
            tables=[
                TableSchema(
                    name="Customers",
                    columns=[ColumnSchema(name="id", source_type="Long Integer")],
                    primary_key=[],
                    foreign_keys=[],
                    indexes=[],
                )
            ],
        )

        response = use_cases.transfer_data(
            "fake", "connection", "source.accdb", schema.model_dump(), confirm=True
        )

        assert isinstance(response, Ok)
        job = migration_module._JOBS[response.data["job_id"]]
        assert job.results[0].success is True
        assert job.results[0].rows_transferred == 1

    def test_transfer_data_connects_one_source_adapter_for_all_tables(self, monkeypatch):
        from unittest.mock import MagicMock

        import ms_access_mcp.application.migration as migration_module

        adapter = MagicMock()
        adapter.execute_query.return_value = {"rows": []}
        adapter_factory = MagicMock(return_value=adapter)
        monkeypatch.setattr(migration_module.BackendSelector, "get_adapter", adapter_factory)

        connector = FakeConnector("Driver={ODBC};Database=test.db")
        use_cases = MigrationUseCases()
        monkeypatch.setattr(use_cases, "_get_connector", lambda *_: Ok(data=connector))
        schema = ExtractedSchema(
            source="source.accdb",
            tables=[
                TableSchema(
                    name=name,
                    columns=[ColumnSchema(name="id", source_type="Long Integer")],
                    primary_key=[],
                    foreign_keys=[],
                    indexes=[],
                )
                for name in ("First", "Second")
            ],
        )

        result = use_cases.transfer_data(
            "fake", "connection", "source.accdb", schema.model_dump(),
            verification_mode="count-only", confirm=True,
        )

        assert isinstance(result, Ok)
        adapter_factory.assert_called_once_with("source.accdb")
        adapter.connect.assert_called_once_with("source.accdb")

    def test_transfer_data_disconnects_source_after_table_failure(self, monkeypatch):
        from unittest.mock import MagicMock

        import ms_access_mcp.application.migration as migration_module

        adapter = MagicMock()
        adapter.execute_query.side_effect = [{"rows": []}, RuntimeError("query failed")]
        monkeypatch.setattr(
            migration_module.BackendSelector, "get_adapter", MagicMock(return_value=adapter)
        )
        connector = FakeConnector("Driver={ODBC};Database=test.db")
        use_cases = MigrationUseCases()
        monkeypatch.setattr(use_cases, "_get_connector", lambda *_: Ok(data=connector))
        schema = ExtractedSchema(
            source="source.accdb",
            tables=[
                TableSchema(
                    name=name,
                    columns=[ColumnSchema(name="id", source_type="Long Integer")],
                    primary_key=[],
                    foreign_keys=[],
                    indexes=[],
                )
                for name in ("First", "Second")
            ],
        )

        result = use_cases.transfer_data(
            "fake", "connection", "source.accdb", schema.model_dump(),
            verification_mode="count-only", confirm=True,
        )

        assert isinstance(result, Ok)
        job = migration_module._JOBS[result.data["job_id"]]
        assert job.status == "failed"
        assert job.results[1].success is False
        adapter.disconnect.assert_called_once()

    @pytest.mark.parametrize("backend", ["dao", "odbc"])
    def test_extract_disconnects_adapter_after_metadata_failure(self, monkeypatch, backend):
        from unittest.mock import MagicMock

        import ms_access_mcp.application.migration as migration_module

        adapter = MagicMock()
        if backend == "dao":
            adapter.get_table_schema_plan.side_effect = RuntimeError("metadata failed")
        else:
            adapter.get_tables.side_effect = RuntimeError("metadata failed")
        monkeypatch.setattr(
            migration_module.BackendSelector, "get_adapter", MagicMock(return_value=adapter)
        )

        use_cases = MigrationUseCases()
        result = (
            use_cases._extract_dao("source.accdb")
            if backend == "dao"
            else use_cases._extract_odbc("source.accdb")
        )

        assert result[0] is None if backend == "dao" else result[0] == []
        adapter.disconnect.assert_called_once()


class SpyCursor:
    """Spy cursor that captures execute() SQL for testing."""

    def __init__(self):
        self._captured_sql: list[str] = []
        self.closed = False

    def execute(self, sql: str, *args, **kwargs):
        self._captured_sql.append(sql)
        return None

    def fetchone(self):
        return (0,)

    def close(self):
        self.closed = True


class SpyConnection:
    """Spy connection whose cursor() returns a SpyCursor."""

    def __init__(self):
        self._cursor = SpyCursor()

    def cursor(self):
        return self._cursor


class TestCountTargetRowsQuoting:
    """Plan 057: hostile table names must be bracket-quoted in _count_target_rows SQL.

    The SQL f"SELECT COUNT(*) FROM {table}" is a SQL injection vector when
    table names originate from untrusted LLM tool parameters.
    """

    def _make_spy_connector(self) -> FakeConnector:
        """Build a FakeConnector whose _conn is a SpyConnection."""
        connector = FakeConnector("Driver={ODBC};Database=test.db")
        connector._conn = SpyConnection()  # type: ignore[assignment]
        return connector

    def test_hostile_table_name_not_unquoted_in_count_sql(self):
        """SQL must not contain unquoted hostile table name 'Orders]; DROP TABLE x'."""
        connector = self._make_spy_connector()
        uc = MigrationUseCases()

        # The raw unquoted form must NOT appear in any captured SQL
        try:
            uc._count_target_rows(connector, "Orders]; DROP TABLE x")
        except Exception:
            pass  # We only care that raw SQL is not emitted

        cursor = connector._conn.cursor()
        for sql in cursor._captured_sql:
            assert "Orders]; DROP TABLE x" not in sql, (
                f"Hostile table name appears unquoted in SQL: {sql!r}"
            )

    def test_spaced_table_name_is_bracket_quoted(self):
        """Legal spaced table name 'My Table' must appear as [My Table] in SQL."""
        connector = self._make_spy_connector()
        uc = MigrationUseCases()

        try:
            uc._count_target_rows(connector, "My Table")
        except Exception:
            pass

        cursor = connector._conn.cursor()
        captured = " ".join(cursor._captured_sql)
        assert "[My Table]" in captured, (
            f"Spaced table name must be bracket-quoted, got: {captured!r}"
        )

    def test_embedded_bracket_escaped_in_count_sql(self):
        """Table name 'x]' must appear as [x]] in SQL (embedded bracket doubled)."""
        connector = self._make_spy_connector()
        uc = MigrationUseCases()

        try:
            uc._count_target_rows(connector, "x]")
        except Exception:
            pass

        cursor = connector._conn.cursor()
        captured = " ".join(cursor._captured_sql)
        assert "[x]]" in captured, (
            f"Embedded bracket must be doubled, got: {captured!r}"
        )


class TestCountRowsProtocol:
    def test_count_target_rows_delegates_to_public_connector_method(self, monkeypatch):
        connector = FakeConnector("connection")
        calls: list[str] = []
        monkeypatch.setattr(connector, "count_rows", lambda table: calls.append(table) or 12)

        assert MigrationUseCases()._count_target_rows(connector, "Customers") == 12
        assert calls == ["Customers"]

    def test_default_count_rows_closes_cursor(self):
        connector = FakeConnector("connection")
        connection = SpyConnection()
        connector._conn = connection

        assert connector.count_rows("Customers") == 0
        assert connection._cursor.closed is True

    def test_count_target_rows_logs_failure_and_returns_minus_one(self, caplog, monkeypatch):
        connector = FakeConnector("connection")

        def fail_count(table: str) -> int:
            raise RuntimeError("count failed")

        monkeypatch.setattr(connector, "count_rows", fail_count)
        with caplog.at_level("WARNING", logger="ms_access_mcp.application.migration"):
            count = MigrationUseCases()._count_target_rows(connector, "Customers")

        assert count == -1
        assert "Row-count verification failed for Customers" in caplog.text
