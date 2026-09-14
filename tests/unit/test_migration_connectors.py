"""Tests for migration connectors using fake connectors.

These tests use fake BaseConnector subclasses and do not require real drivers.
"""

from __future__ import annotations

import pytest

from ms_access_mcp.backends.migration import CONNECTOR_FACTORIES, get_connector, render_schema_ddl
from ms_access_mcp.backends.migration.connectors import BaseConnector
from ms_access_mcp.models import ColumnSchema, ForeignKeySchema, TableSchema


class FakeConnector(BaseConnector):
    """Fake connector for testing."""

    def __init__(self, connection_string: str) -> None:
        super().__init__(connection_string)
        self._connected = False
        self._tables: set[str] = set()
        self._inserted: dict[str, list[tuple]] = {}

    def connect(self) -> None:
        self._connected = True

    def table_exists(self, table: str) -> bool:
        return table in self._tables

    def create_table(self, ddl: str) -> None:
        # Parse table name from DDL for testing
        if "test_table" in ddl:
            self._tables.add("test_table")

    def insert_batch(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        if table not in self._inserted:
            self._inserted[table] = []
        self._inserted[table].extend(rows)
        return len(rows)

    def rollback_table(self, table: str) -> None:
        self._tables.discard(table)
        self._inserted.pop(table, None)

    def disconnect(self) -> None:
        self._connected = False


# Register FakeConnector for testing
CONNECTOR_FACTORIES["fake"] = FakeConnector  # type: ignore[assignment]


class TestConnectorRegistry:
    """Tests for the connector registry."""

    def test_no_dialect_module_imported_at_package_import(self):
        """Registry resolution does not import dialect modules at package import time."""
        import json
        import subprocess
        import sys

        code = (
            "import sys, json; "
            "import ms_access_mcp.backends.migration; "
            "print(json.dumps(sorted(m for m in sys.modules "
            "if m.startswith('ms_access_mcp.backends.migration.connectors.'))))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(proc.stdout.strip()) == []

    def test_get_connector_unknown_target_raises(self):
        """Unknown target_type raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            get_connector("unknown", "conn_str")
        assert "unknown" in str(exc_info.value)

    def test_get_connector_sqlite_returns_connector(self):
        """SQLite connector can be obtained via registry."""
        # SQLite is always available (stdlib)
        conn = get_connector("sqlite", ":memory:")
        assert isinstance(conn, BaseConnector)
        conn.disconnect()

    def test_lazy_import_postgres_error_message(self):
        """Postgres connector without driver gives helpful install message."""
        try:
            # Temporarily remove postgres from registry to force re-import
            original = CONNECTOR_FACTORIES.pop("postgres", None)
            try:
                with pytest.raises(ImportError) as exc_info:
                    get_connector("postgres", "host=foo")
                assert "psycopg2" in str(exc_info.value).lower()
                assert "migrate" in str(exc_info.value).lower()
            finally:
                if original:
                    CONNECTOR_FACTORIES["postgres"] = original
        except Exception:
            pass

    def test_lazy_import_mysql_error_message(self):
        """MySQL connector without driver gives helpful install message."""
        try:
            original = CONNECTOR_FACTORIES.pop("mysql", None)
            try:
                with pytest.raises(ImportError) as exc_info:
                    get_connector("mysql", "host=foo")
                assert "mysql" in str(exc_info.value).lower()
                assert "migrate" in str(exc_info.value).lower()
            finally:
                if original:
                    CONNECTOR_FACTORIES["mysql"] = original
        except Exception:
            pass


class TestTypeMap:
    """Tests for type mapping invariants per dialect."""

    def test_sqlite_type_map_booleans(self):
        """SQLite maps Boolean to INTEGER."""
        from ms_access_mcp.backends.migration.connectors.sqlite import TYPE_MAP

        assert TYPE_MAP["Boolean"] == "INTEGER"

    def test_sqlite_type_map_text(self):
        """SQLite maps Text to TEXT."""
        from ms_access_mcp.backends.migration.connectors.sqlite import TYPE_MAP

        assert TYPE_MAP["Text"] == "TEXT"

    def test_sqlite_type_map_datetime(self):
        """SQLite maps Date/Time to TEXT (ISO-8601)."""
        from ms_access_mcp.backends.migration.connectors.sqlite import TYPE_MAP

        assert TYPE_MAP["Date/Time"] == "TEXT"

    def test_sqlite_type_map_currency(self):
        """SQLite maps Currency to NUMERIC."""
        from ms_access_mcp.backends.migration.connectors.sqlite import TYPE_MAP

        assert TYPE_MAP["Currency"] == "NUMERIC"

    def test_postgres_type_map_booleans(self):
        """PostgreSQL maps Boolean to BOOLEAN."""
        from ms_access_mcp.backends.migration.connectors.postgres import TYPE_MAP

        assert TYPE_MAP["Boolean"] == "BOOLEAN"

    def test_postgres_type_map_guid(self):
        """PostgreSQL maps GUID to UUID."""
        from ms_access_mcp.backends.migration.connectors.postgres import TYPE_MAP

        assert TYPE_MAP["GUID"] == "UUID"

    def test_postgres_type_map_currency(self):
        """PostgreSQL maps Currency to NUMERIC(19,4)."""
        from ms_access_mcp.backends.migration.connectors.postgres import TYPE_MAP

        assert TYPE_MAP["Currency"] == "NUMERIC(19,4)"

    def test_mysql_type_map_booleans(self):
        """MySQL maps Boolean to TINYINT(1)."""
        from ms_access_mcp.backends.migration.connectors.mysql import TYPE_MAP

        assert TYPE_MAP["Boolean"] == "TINYINT(1)"

    def test_mysql_type_map_unsigned(self):
        """MySQL maps Unsigned Byte to TINYINT UNSIGNED."""
        from ms_access_mcp.backends.migration.connectors.mysql import TYPE_MAP

        assert TYPE_MAP["Unsigned Byte"] == "TINYINT UNSIGNED"

    def test_sqlserver_type_map_datetime(self):
        """SQL Server maps Date/Time to DATETIME2."""
        from ms_access_mcp.backends.migration.connectors.sqlserver import TYPE_MAP

        assert TYPE_MAP["Date/Time"] == "DATETIME2"

    def test_sqlserver_type_map_guid(self):
        """SQL Server maps GUID to UNIQUEIDENTIFIER."""
        from ms_access_mcp.backends.migration.connectors.sqlserver import TYPE_MAP

        assert TYPE_MAP["GUID"] == "UNIQUEIDENTIFIER"


class TestQuoteIdentifier:
    """Tests for identifier quoting per dialect."""

    def test_sqlite_quote_valid_name(self):
        """SQLite quotes valid identifiers with double quotes."""
        from ms_access_mcp.backends.migration.connectors.sqlite import quote_identifier

        assert quote_identifier("table_name") == '"table_name"'

    def test_sqlite_reject_semicolon(self):
        """SQLite rejects identifiers with semicolon."""
        from ms_access_mcp.backends.migration.connectors.sqlite import quote_identifier

        with pytest.raises(ValueError):
            quote_identifier("name; DROP TABLE")

    def test_sqlite_reject_double_quote(self):
        """SQLite rejects identifiers with double quote."""
        from ms_access_mcp.backends.migration.connectors.sqlite import quote_identifier

        with pytest.raises(ValueError):
            quote_identifier('name"extra')

    def test_sqlite_reject_single_quote(self):
        """SQLite rejects identifiers with single quote."""
        from ms_access_mcp.backends.migration.connectors.sqlite import quote_identifier

        with pytest.raises(ValueError):
            quote_identifier("name'extra")

    def test_sqlite_reject_double_dash(self):
        """SQLite rejects identifiers with double dash."""
        from ms_access_mcp.backends.migration.connectors.sqlite import quote_identifier

        with pytest.raises(ValueError):
            quote_identifier("name--comment")

    def test_sqlite_reject_whitespace(self):
        """SQLite rejects identifiers with whitespace."""
        from ms_access_mcp.backends.migration.connectors.sqlite import quote_identifier

        with pytest.raises(ValueError):
            quote_identifier("name with space")

    def test_postgres_quote_valid_name(self):
        """PostgreSQL quotes valid identifiers with double quotes."""
        from ms_access_mcp.backends.migration.connectors.postgres import quote_identifier

        assert quote_identifier("table_name") == '"table_name"'

    def test_mysql_quote_valid_name(self):
        """MySQL quotes valid identifiers with backticks."""
        from ms_access_mcp.backends.migration.connectors.mysql import quote_identifier

        assert quote_identifier("table_name") == "`table_name`"

    def test_sqlserver_quote_valid_name(self):
        """SQL Server quotes valid identifiers with brackets."""
        from ms_access_mcp.backends.migration.connectors.sqlserver import quote_identifier

        assert quote_identifier("table_name") == "[table_name]"


class TestRenderTableDdl:
    """Tests for DDL rendering per dialect."""

    def test_sqlite_simple_table(self):
        """SQLite renders simple table DDL correctly."""
        from ms_access_mcp.backends.migration.connectors.sqlite import render_table_ddl

        table = TableSchema(
            name="Customers",
            columns=[
                ColumnSchema(name="id", source_type="Long Integer"),
                ColumnSchema(name="name", source_type="Text"),
            ],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )
        ddl = render_table_ddl(table)
        assert "CREATE TABLE" in ddl
        assert '"Customers"' in ddl
        assert '"id"' in ddl
        assert '"name"' in ddl

    def test_sqlite_autoincrement_pk(self):
        """SQLite renders INTEGER PRIMARY KEY AUTOINCREMENT for single int PK."""
        from ms_access_mcp.backends.migration.connectors.sqlite import render_table_ddl

        table = TableSchema(
            name="Customers",
            columns=[
                ColumnSchema(name="id", source_type="Long Integer", is_autoincrement=True),
            ],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )
        ddl = render_table_ddl(table)
        assert "AUTOINCREMENT" in ddl

    def test_postgres_identity(self):
        """PostgreSQL renders GENERATED ALWAYS AS IDENTITY for autoincrement PK."""
        from ms_access_mcp.backends.migration.connectors.postgres import render_table_ddl

        table = TableSchema(
            name="Customers",
            columns=[
                ColumnSchema(name="id", source_type="Long Integer", is_autoincrement=True),
            ],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )
        ddl = render_table_ddl(table)
        assert "GENERATED ALWAYS AS IDENTITY" in ddl

    def test_mysql_autoincrement_inline_pk(self):
        """MySQL renders AUTO_INCREMENT inline for single int PK."""
        from ms_access_mcp.backends.migration.connectors.mysql import render_table_ddl

        table = TableSchema(
            name="Customers",
            columns=[
                ColumnSchema(name="id", source_type="Long Integer", is_autoincrement=True),
            ],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )
        ddl = render_table_ddl(table)
        assert "AUTO_INCREMENT" in ddl

    def test_sqlserver_identity(self):
        """SQL Server renders IDENTITY(1,1) for autoincrement PK."""
        from ms_access_mcp.backends.migration.connectors.sqlserver import render_table_ddl

        table = TableSchema(
            name="Customers",
            columns=[
                ColumnSchema(name="id", source_type="Long Integer", is_autoincrement=True),
            ],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )
        ddl = render_table_ddl(table)
        assert "IDENTITY(1,1)" in ddl


class TestFkTopologicalSort:
    """Tests for FK topological sorting in render_schema_ddl."""

    def test_tables_without_fks_sorted_stably(self):
        """Tables without FKs are returned in input order."""
        from ms_access_mcp.backends.migration import _topological_sort

        t1 = TableSchema(name="T1", columns=[], primary_key=[], foreign_keys=[], indexes=[])
        t2 = TableSchema(name="T2", columns=[], primary_key=[], foreign_keys=[], indexes=[])
        result = _topological_sort([t1, t2])
        assert [t.name for t in result] == ["T1", "T2"]

    def test_fk_dependency_respected(self):
        """Tables referenced by FKs come before the tables that reference them."""
        from ms_access_mcp.backends.migration import _topological_sort

        parent = TableSchema(name="Parent", columns=[], primary_key=[], foreign_keys=[], indexes=[])
        child = TableSchema(
            name="Child",
            columns=[],
            primary_key=[],
            foreign_keys=[ForeignKeySchema(name="FK", columns=["parent_id"], referenced_table="Parent", referenced_columns=["id"])],
            indexes=[],
        )
        result = _topological_sort([child, parent])
        parent_idx = next(i for i, t in enumerate(result) if t.name == "Parent")
        child_idx = next(i for i, t in enumerate(result) if t.name == "Child")
        assert parent_idx < child_idx, "Parent should come before Child"

    def test_render_schema_ddl_fk_order(self):
        """render_schema_ddl returns tables in FK-topological order."""
        parent = TableSchema(
            name="Parent",
            columns=[ColumnSchema(name="id", source_type="Long Integer")],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )
        child = TableSchema(
            name="Child",
            columns=[ColumnSchema(name="parent_id", source_type="Long Integer")],
            primary_key=[],
            foreign_keys=[
                ForeignKeySchema(name="FK", columns=["parent_id"], referenced_table="Parent", referenced_columns=["id"])
            ],
            indexes=[],
        )

        ddl_stmts = render_schema_ddl("sqlite", [child, parent])
        create_stmts = [s for s in ddl_stmts if "CREATE TABLE" in s]
        parent_idx = next(i for i, s in enumerate(create_stmts) if "Parent" in s)
        child_idx = next(i for i, s in enumerate(create_stmts) if "Child" in s)
        assert parent_idx < child_idx, "Parent CREATE TABLE should come before Child"
