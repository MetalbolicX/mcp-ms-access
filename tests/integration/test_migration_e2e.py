"""End-to-end migration integration tests.

Tests the full extract→upload→transfer→verify cycle using a fixture Access DB
copy against a temporary SQLite target.  Requires com_integration or odbc_integration
markers (fixture may use DAO or ODBC depending on availability).

These tests would have caught Defects 1 and 2:
- Defect 1: render_schema_ddl crash on indexed tables (is_primary AttributeError)
- Defect 2: sqlite3 rejecting decimal.Decimal (Currency columns)
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from ms_access_mcp.application.migration import MigrationUseCases
from ms_access_mcp.errors import Ok

# Source fixture — must not be modified in place
FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "test_db.accdb"

pytestmark = pytest.mark.com_integration


class TestMigrationE2E:
    """Full extract→upload→transfer→verify cycle against a real Access DB."""

    @pytest.fixture
    def fixture_copy(self):
        """Copy the fixture Access DB to a temp file so tests can modify it."""
        with tempfile.NamedTemporaryFile(suffix=".accdb", delete=False) as f:
            temp_path = f.name
        shutil.copy2(FIXTURE_PATH, temp_path)
        yield temp_path
        try:
            Path(temp_path).unlink(missing_ok=True)
        except PermissionError:
            pass  # Windows lock on Access files

    @pytest.fixture
    def temp_sqlite_db(self):
        """Create a clean temporary SQLite database."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        yield db_path
        try:
            Path(db_path).unlink(missing_ok=True)
        except PermissionError:
            pass

    def test_extract_upload_transfer_verify_e2e(
        self, fixture_copy: str, temp_sqlite_db: str
    ):
        """Extract schema, upload to SQLite, transfer data, verify counts.

        This is the happy-path e2e that would have caught Defects 1 and 2.
        """
        use_cases = MigrationUseCases(db_path=fixture_copy)

        # --- Extract ---
        extract_result = use_cases.extract_schema(
            database_path=fixture_copy, target_type="sqlite"
        )
        assert isinstance(extract_result, Ok), f"extract_schema failed: {extract_result.message}"
        schema = extract_result.data
        assert len(schema.tables) > 0, "Expected at least one table in fixture"

        # --- Upload ---
        upload_result = use_cases.upload_schema(
            target_type="sqlite",
            connection_string=temp_sqlite_db,
            schema_json=schema.model_dump(),
            confirm=True,
        )
        assert isinstance(upload_result, Ok), f"upload_schema failed: {upload_result.message}"
        upload_data = upload_result.data
        assert len(upload_data["tables_created"]) > 0
        if upload_data["tables_failed"]:
            print(f"upload_schema tables_failed: {upload_data['tables_failed']}")

        # --- Transfer ---
        transfer_result = use_cases.transfer_data(
            target_type="sqlite",
            connection_string=temp_sqlite_db,
            database_path=fixture_copy,
            schema_json=schema.model_dump(),
            confirm=True,
        )
        assert isinstance(transfer_result, Ok), f"transfer_data failed: {transfer_result.message}"
        transfer_data = transfer_result.data
        assert transfer_data["success"] is True

        job_id = transfer_data["job_id"]
        status_result = use_cases.get_migration_status(job_id)
        assert isinstance(status_result, Ok)
        job = status_result.data
        assert job["status"] in ("completed", "failed")

        # --- Verify counts via direct sqlite3 query ---
        conn = sqlite3.connect(temp_sqlite_db)
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables_in_target = {row[0] for row in cursor.fetchall()}

            for table in schema.tables:
                if table.name not in tables_in_target:
                    continue
                cursor.execute(f'SELECT COUNT(*) FROM "{table.name}"')
                target_count = cursor.fetchone()[0]
                job_result = next(
                    (r for r in job.get("results", []) if r.get("table") == table.name),
                    None,
                )
                if job_result:
                    source_rows = job_result.get("source_rows", 0)
                    assert (
                        target_count == source_rows
                    ), f"Row count mismatch for {table.name}: source={source_rows}, target={target_count}"
        finally:
            conn.close()

    def test_extract_schema_from_access_db(self, fixture_copy: str):
        """Verify extract_schema returns a well-formed ExtractedSchema."""
        use_cases = MigrationUseCases()
        result = use_cases.extract_schema(
            database_path=fixture_copy, target_type="sqlite"
        )
        assert isinstance(result, Ok), f"extract failed: {result.message}"
        schema = result.data
        assert schema.source == fixture_copy
        assert schema.version == "1.0"
        assert isinstance(schema.tables, list)
        assert len(schema.tables) >= 1
        assert schema.unknown_metadata is not None

    def test_upload_schema_creates_tables(self, fixture_copy: str, temp_sqlite_db: str):
        """upload_schema creates tables in the target database."""
        use_cases = MigrationUseCases()

        extract_result = use_cases.extract_schema(
            database_path=fixture_copy, target_type="sqlite"
        )
        assert isinstance(extract_result, Ok)
        schema = extract_result.data

        upload_result = use_cases.upload_schema(
            target_type="sqlite",
            connection_string=temp_sqlite_db,
            schema_json=schema.model_dump(),
            confirm=True,
        )
        assert isinstance(upload_result, Ok)
        upload_data = upload_result.data

        conn = sqlite3.connect(temp_sqlite_db)
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in cursor.fetchall()}
            for name in upload_data["tables_created"]:
                assert name in tables, f"Table {name} not found in SQLite"
        finally:
            conn.close()

    def test_render_schema_ddl_indexed_table_roundtrip(
        self, fixture_copy: str, temp_sqlite_db: str
    ):
        """render_schema_ddl works on tables that have indexes (Defect 1 probe).

        Orders and Products from the fixture have indexes; the is_primary fix
        must not raise AttributeError when processing them.

        SQLite foreign keys render inline in CREATE TABLE statements; schema DDL
        contains no phase-2 FK ALTER statements.
        """
        use_cases = MigrationUseCases()

        extract_result = use_cases.extract_schema(
            database_path=fixture_copy, target_type="sqlite"
        )
        assert isinstance(extract_result, Ok)
        schema = extract_result.data

        from ms_access_mcp.backends.migration import render_schema_ddl

        ddl_stmts = render_schema_ddl("sqlite", schema.tables)
        assert isinstance(ddl_stmts, list)
        assert len(ddl_stmts) > 0

        conn = sqlite3.connect(temp_sqlite_db)
        try:
            assert all("ALTER TABLE" not in stmt for stmt in ddl_stmts)
            for stmt in ddl_stmts:
                conn.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    def test_transfer_data_with_currency_columns(
        self, fixture_copy: str, temp_sqlite_db: str
    ):
        """transfer_data handles Currency (decimal.Decimal) columns without sqlite3 error.

        This is the Defect 2 probe — decimal.Decimal values must be converted to float.
        """
        use_cases = MigrationUseCases()

        extract_result = use_cases.extract_schema(
            database_path=fixture_copy, target_type="sqlite"
        )
        assert isinstance(extract_result, Ok)
        schema = extract_result.data

        upload_result = use_cases.upload_schema(
            target_type="sqlite",
            connection_string=temp_sqlite_db,
            schema_json=schema.model_dump(),
            confirm=True,
        )
        assert isinstance(upload_result, Ok)

        transfer_result = use_cases.transfer_data(
            target_type="sqlite",
            connection_string=temp_sqlite_db,
            database_path=fixture_copy,
            schema_json=schema.model_dump(),
            confirm=True,
        )
        assert isinstance(transfer_result, Ok), f"transfer_data (Decimal handling): {transfer_result.message}"

        job_id = transfer_result.data["job_id"]
        status = use_cases.get_migration_status(job_id)
        assert isinstance(status, Ok)
        assert status.data["status"] == "completed"
