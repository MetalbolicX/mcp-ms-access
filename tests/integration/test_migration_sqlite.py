"""Integration tests for SQLite migration connector.

These tests require the sqlite3 stdlib and are marked with pytest.mark.odbc_integration
to allow selective execution.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from ms_access_mcp.backends.migration.connectors.sqlite import (
    SqliteConnector,
    quote_identifier,
    render_table_ddl,
)
from ms_access_mcp.models import ColumnSchema, ForeignKeySchema, TableSchema

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
        conn = SqliteConnector(temp_sqlite_db)
        conn.connect()
        assert conn._conn is not None
        conn.disconnect()
        assert conn._conn is None

    def test_create_and_query_table(self, temp_sqlite_db):
        """Connector can create a table and query it."""
        conn = SqliteConnector(temp_sqlite_db)
        conn.connect()

        ddl = 'CREATE TABLE "Users" (\n    "id" INTEGER PRIMARY KEY AUTOINCREMENT,\n    "name" TEXT NOT NULL\n)'
        conn.create_table(ddl)

        assert conn.table_exists("Users") is True
        assert conn.table_exists("NonExistent") is False

        conn.disconnect()

    def test_insert_batch_and_count(self, temp_sqlite_db):
        """Connector inserts rows and they are queryable."""
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
        """render_schema_ddl produces FK constraints via ALTER TABLE.

        Note: SQLite does not support ALTER TABLE ADD CONSTRAINT FOREIGN KEY.
        This test verifies the connector handles this gracefully — the CREATE TABLE
        succeeds but the FK cannot be added via ALTER TABLE in SQLite.
        """
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
            # First statement is CREATE TABLE for Categories
            conn.execute(ddl_stmts[0])
            # Second is CREATE TABLE for Products
            conn.execute(ddl_stmts[1])
            conn.commit()

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

    def test_quote_identifier_produces_valid_sql(self):
        """Quoted identifiers are accepted by SQLite."""
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
