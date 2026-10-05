"""Postgres migration connector with lazy psycopg2 import.

psycopg2 is imported only inside connect() to allow the module to be imported
without the driver installed. If connect() is called without psycopg2,
a helpful error message is raised.
"""

from __future__ import annotations

from typing import Any

from ....models import ColumnSchema, TableSchema
from .. import CONNECTOR_FACTORIES, INSTALL_MESSAGE
from . import BaseConnector
from . import dialect as _dialect

__all__ = ["PostgresConnector", "TYPE_MAP", "quote_identifier", "render_table_ddl"]

# Access source_type -> PostgreSQL type
TYPE_MAP: dict[str, str] = {
    "Boolean": "BOOLEAN",
    "Byte": "SMALLINT",
    "Unsigned Byte": "SMALLINT",
    "Integer": "SMALLINT",
    "Unsigned Integer": "INTEGER",
    "Long Integer": "INTEGER",
    "Unsigned Long Integer": "BIGINT",
    "Big Integer": "BIGINT",
    "Single": "REAL",
    "Double": "DOUBLE PRECISION",
    "Currency": "NUMERIC(19,4)",
    "Decimal": "NUMERIC(28,14)",
    "Date/Time": "TIMESTAMP",
    "Text": "TEXT",
    "Memo": "TEXT",
    "Binary": "BYTEA",
    "GUID": "UUID",
    "Unknown": "TEXT",
}


def _access_type_to_postgres(col: ColumnSchema) -> str:
    """Map an Access column type to PostgreSQL type."""
    access_type = col.source_type
    if access_type == "Text" and col.max_length:
        return f"VARCHAR({col.max_length})"
    return TYPE_MAP.get(access_type, "TEXT")


def quote_identifier(name: str) -> str:
    """Quote a PostgreSQL identifier using double quotes.

    Args:
        name: The identifier to quote.

    Returns:
        The quoted identifier.
    """
    if not name:
        raise ValueError("Identifier cannot be empty")
    return f'"{name}"'


DIALECT = _dialect.DialectSpec(
    name="postgres",
    type_mapper=_access_type_to_postgres,
    quote=quote_identifier,
    autoincrement_clause="GENERATED ALWAYS AS IDENTITY",
    inline_autoincrement_consumes_pk=False,
)


def render_table_ddl(table: TableSchema) -> str:
    """Render a CREATE TABLE DDL statement for PostgreSQL.

    Args:
        table: TableSchema describing the table.

    Returns:
        A CREATE TABLE DDL string.
    """
    return _dialect.render_table_ddl(DIALECT, table)


class PostgresConnector(BaseConnector):
    """PostgreSQL migration connector with lazy psycopg2 import."""

    def quote_identifier(self, name: str) -> str:
        return quote_identifier(name)

    def __init__(self, connection_string: str) -> None:
        """Initialize PostgreSQL connector.

        Args:
            connection_string: psycopg2 connection string for PostgreSQL.
        """
        super().__init__(connection_string)
        self._conn: Any = None
        self._psycopg2: Any = None

    def connect(self) -> None:
        """Connect to the PostgreSQL database.

        Raises:
            ImportError: If psycopg2 is not installed.
            ConnectionError: If the connection fails.
        """
        try:
            import psycopg2  # type: ignore
        except ImportError:
            raise ImportError(
                f"psycopg2 is required for postgres connectors. {INSTALL_MESSAGE}"
            )

        self._psycopg2 = psycopg2
        self._conn = psycopg2.connect(self.connection_string)

    def table_exists(self, table: str) -> bool:
        """Check if a table exists in the PostgreSQL database."""
        if self._conn is None:
            raise RuntimeError("Not connected")
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (table,),
        )
        result = cursor.fetchone()
        return result is not None

    def create_table(self, ddl: str) -> None:
        """Execute a CREATE TABLE DDL statement."""
        if self._conn is None:
            raise RuntimeError("Not connected")
        cursor = self._conn.cursor()
        cursor.execute(ddl)
        self._conn.commit()

    def insert_batch(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        """Insert multiple rows using executemany with %s placeholders."""
        if self._conn is None:
            raise RuntimeError("Not connected")

        placeholders = ", ".join(["%s"] * len(columns))
        col_names = ", ".join(quote_identifier(c) for c in columns)
        sql = f"INSERT INTO {quote_identifier(table)} ({col_names}) VALUES ({placeholders})"

        cursor = self._conn.cursor()
        cursor.executemany(sql, rows)
        self._conn.commit()
        return cursor.rowcount

    def rollback_table(self, table: str) -> None:
        """Drop the table if it exists."""
        if self._conn is None:
            raise RuntimeError("Not connected")
        cursor = self._conn.cursor()
        cursor.execute(f"DROP TABLE IF EXISTS {quote_identifier(table)} CASCADE")
        self._conn.commit()

    def disconnect(self) -> None:
        """Close the PostgreSQL connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# Register the factory
CONNECTOR_FACTORIES["postgres"] = PostgresConnector
