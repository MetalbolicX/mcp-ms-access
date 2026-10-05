"""Integration tests for SQLite migration connector.

These tests require the sqlite3 stdlib and are marked with pytest.mark.odbc_integration
to allow selective execution.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from ms_access_mcp.models import ColumnSchema, ForeignKeySchema, IndexSchema, TableSchema
from ms_access_mcp.models.migration import ExtractedSchema

pytestmark = pytest.mark.odbc_integration


class TestSqliteConnectorIntegration:
    """End-to-end integration tests for SqliteConnector against a real sqlite3 db."""

    @pytest.fixture
    def temp_sqlite_db(self):
        """Create a temporary SQLite database file."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        yield db_path
        try:
            Path(db_path).unlink(missing_ok=True)
        except Exception:
            pass

    def test_connect_and_disconnect(self, temp_sqlite_db):
        """Connector connects and disconnects cleanly."""
        from ms_access_mcp.backends.migration.connectors.sqlite import SqliteConnector

        conn = SqliteConnector(temp_sqlite_db)
        conn.connect()
        assert conn._conn is not None
        conn.disconnect()
        assert conn._conn is None

    def test_create_and_query_table(self, temp_sqlite_db):
        """Connector can create a table and query it."""
        from ms_access_mcp.backends.migration.connectors.sqlite import SqliteConnector

        conn = SqliteConnector(temp_sqlite_db)
        conn.connect()

        ddl = 'CREATE TABLE "Users" (\n    "id" INTEGER PRIMARY KEY AUTOINCREMENT,\n    "name" TEXT NOT NULL\n)'
        conn.create_table(ddl)

        assert conn.table_exists("Users") is True
        assert conn.table_exists("NonExistent") is False

        conn.disconnect()

    def test_transfer_reports_failure_when_sqlite_loses_inserted_row(self, tmp_path, monkeypatch):
        from ms_access_mcp.application import migration as migration_module
        from ms_access_mcp.application.migration import MigrationUseCases
        from ms_access_mcp.backends.migration.connectors.sqlite import SqliteConnector

        database_path = str(tmp_path / "row-loss.db")
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
        use_cases = MigrationUseCases()
        use_cases.upload_schema(
            target_type="sqlite",
            connection_string=database_path,
            schema_json=schema.model_dump(),
            confirm=True,
        )

        class Adapter:
            def connect(self, database_path):
                pass

            def execute_query(self, query):
                return {"rows": [{"id": 1}, {"id": 2}]}

            def disconnect(self):
                pass

        monkeypatch.setattr(
            migration_module, "_select_migration_adapter", lambda _path: Adapter()
        )
        insert_batch = SqliteConnector.insert_batch

        def lose_one_row(connector, table, columns, rows):
            return insert_batch(connector, table, columns, rows[:1])

        monkeypatch.setattr(SqliteConnector, "insert_batch", lose_one_row)
        result = use_cases.transfer_data(
            target_type="sqlite",
            connection_string=database_path,
            database_path="source.accdb",
            schema_json=schema.model_dump(),
            confirm=True,
        )

        assert result.data["success"] is False
        job = migration_module._JOBS[result.data["job_id"]]
        assert job.results[0].success is False
        assert job.results[0].verification["source_rows"] == 2
        assert job.results[0].verification["target_rows"] == 1
        assert "source 2" in job.results[0].error
        assert "target 1" in job.results[0].error

    def test_insert_batch_and_count(self, temp_sqlite_db):
        """Connector inserts rows and they are queryable."""
        from ms_access_mcp.backends.migration.connectors.sqlite import SqliteConnector

        conn = SqliteConnector(temp_sqlite_db)
        conn.connect()

        ddl = 'CREATE TABLE "Products" (\n    "id" INTEGER PRIMARY KEY AUTOINCREMENT,\n    "name" TEXT NOT NULL\n)'
        conn.create_table(ddl)

        rows_inserted = conn.insert_batch(
            "Products",
            ["name"],
            [("Widget",), ("Gadget",)],
        )
        assert rows_inserted == 2

        # Verify via raw cursor
        assert conn._conn is not None
        cursor = conn._conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM "Products"')
        count = cursor.fetchone()[0]
        assert count == 2

        conn.disconnect()

    def test_rollback_table(self, temp_sqlite_db):
        """Connector can drop a table via rollback_table."""
        from ms_access_mcp.backends.migration.connectors.sqlite import SqliteConnector

        conn = SqliteConnector(temp_sqlite_db)
        conn.connect()

        ddl = 'CREATE TABLE "Temp" (\n    "id" INTEGER PRIMARY KEY AUTOINCREMENT\n)'
        conn.create_table(ddl)
        assert conn.table_exists("Temp") is True

        conn.rollback_table("Temp")
        assert conn.table_exists("Temp") is False

        conn.disconnect()


class TestSqliteRenderTableDdlIntegration:
    """Integration tests for render_table_ddl producing valid SQLite DDL."""

    def test_render_simple_table_executes(self):
        """Rendered DDL is valid SQLite DDL that executes without error."""
        from ms_access_mcp.backends.migration.connectors.sqlite import render_table_ddl

        table = TableSchema(
            name="Orders",
            columns=[
                ColumnSchema(name="id", source_type="Long Integer"),
                ColumnSchema(name="customer_name", source_type="Text"),
                ColumnSchema(name="amount", source_type="Currency"),
            ],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )
        ddl = render_table_ddl(table)
        # Should be executable by sqlite3 without raising
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(ddl)
            conn.commit()
        finally:
            if conn:
                conn.close()
            try:
                Path(db_path).unlink(missing_ok=True)
            except PermissionError:
                pass  # Windows temp file lock

    def test_render_table_with_fk_via_schema_ddl(self):
        """SQLite schema DDL renders FKs inline and emits no FK ALTER statement."""
        from ms_access_mcp.backends.migration import render_schema_ddl

        parent = TableSchema(
            name="Categories",
            columns=[
                ColumnSchema(name="id", source_type="Long Integer"),
                ColumnSchema(name="name", source_type="Text"),
            ],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )
        child = TableSchema(
            name="Products",
            columns=[
                ColumnSchema(name="id", source_type="Long Integer"),
                ColumnSchema(name="category_id", source_type="Long Integer"),
                ColumnSchema(name="name", source_type="Text"),
            ],
            primary_key=["id"],
            foreign_keys=[
                ForeignKeySchema(
                    name="FK_Products_Categories",
                    columns=["category_id"],
                    referenced_table="Categories",
                    referenced_columns=["id"],
                )
            ],
            indexes=[],
        )

        ddl_stmts = render_schema_ddl("sqlite", [child, parent])

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            assert all("ALTER TABLE" not in statement for statement in ddl_stmts)
            assert any(
                'FOREIGN KEY ("category_id") REFERENCES "Categories"("id")' in statement
                for statement in ddl_stmts
            )
            for statement in ddl_stmts:
                conn.execute(statement)
            conn.commit()

            foreign_keys = conn.execute('PRAGMA foreign_key_list("Products")').fetchall()
            assert any(row[2] == "Categories" and row[3] == "category_id" for row in foreign_keys)

            # Verify both tables exist
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in cursor.fetchall()]
            assert "Categories" in tables
            assert "Products" in tables
        finally:
            if conn:
                conn.close()
            try:
                Path(db_path).unlink(missing_ok=True)
            except PermissionError:
                pass  # Windows temp file lock — best effort cleanup

    def test_upload_schema_creates_sqlite_foreign_key_and_index(self, tmp_path):
        from ms_access_mcp.application.migration import MigrationUseCases

        temp_sqlite_db = str(tmp_path / "upload.db")
        schema = ExtractedSchema(
            source="source.accdb",
            tables=[
                TableSchema(
                    name="Customers",
                    columns=[ColumnSchema(name="id", source_type="Long Integer", allow_null=False)],
                    primary_key=["id"],
                    foreign_keys=[],
                    indexes=[],
                ),
                TableSchema(
                    name="Orders",
                    columns=[
                        ColumnSchema(name="id", source_type="Long Integer", allow_null=False),
                        ColumnSchema(name="customer_id", source_type="Long Integer", allow_null=False),
                    ],
                    primary_key=["id"],
                    foreign_keys=[
                        ForeignKeySchema(
                            name="FK_Orders_Customers",
                            columns=["customer_id"],
                            referenced_table="Customers",
                            referenced_columns=["id"],
                        )
                    ],
                    indexes=[IndexSchema(name="IX_Orders_customer", columns=["customer_id"])],
                ),
            ],
        )

        result = MigrationUseCases().upload_schema(
            target_type="sqlite",
            connection_string=temp_sqlite_db,
            schema_json=schema.model_dump(),
            confirm=True,
        )

        assert result.data["tables_created"] == ["Customers", "Orders"]
        assert result.data["tables_existing"] == []
        assert result.data["tables_failed"] == []

        with sqlite3.connect(temp_sqlite_db) as conn:
            foreign_keys = conn.execute('PRAGMA foreign_key_list("Orders")').fetchall()
            assert any(
                row[2] == "Customers" and row[3] == "customer_id" and row[4] == "id"
                for row in foreign_keys
            )
            indexes = conn.execute('PRAGMA index_list("Orders")').fetchall()
            assert any(row[1] == "IX_Orders_customer" for row in indexes)

    def test_quote_identifier_produces_valid_sql(self):
        """Quoted identifiers are accepted by SQLite."""
        from ms_access_mcp.backends.migration.connectors.sqlite import quote_identifier

        # "OrderDetails" is a valid identifier but we test the quoting path
        name = quote_identifier("OrderDetails")
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            # Using the quoted identifier directly in DDL
            ddl = f"CREATE TABLE {name} (\n    {quote_identifier('id')} INTEGER\n)"
            conn.execute(ddl)
            conn.commit()
        finally:
            if conn:
                conn.close()
            try:
                Path(db_path).unlink(missing_ok=True)
            except PermissionError:
                pass  # Windows temp file lock
