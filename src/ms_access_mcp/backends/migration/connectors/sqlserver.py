"""SQL Server migration connector using pyodbc (core dependency)."""

from __future__ import annotations

import pyodbc

from ....models import ColumnSchema, TableSchema
from .. import CONNECTOR_FACTORIES
from . import BaseConnector
from . import dialect as _dialect

__all__ = ["SqlServerConnector", "TYPE_MAP", "quote_identifier", "render_table_ddl"]

# Access source_type -> SQL Server type
TYPE_MAP: dict[str, str] = {
    "Boolean": "BIT",
    "Byte": "TINYINT",
    "Unsigned Byte": "TINYINT",
    "Integer": "SMALLINT",
    "Unsigned Integer": "INT",
    "Long Integer": "INT",
    "Unsigned Long Integer": "BIGINT",
    "Big Integer": "BIGINT",
    "Single": "REAL",
    "Double": "FLOAT",
    "Currency": "NUMERIC(19,4)",
    "Decimal": "NUMERIC(28,14)",
    "Date/Time": "DATETIME2",
    "Text": "NVARCHAR(255)",  # Default, override per column
    "Memo": "NVARCHAR(MAX)",
    "Binary": "VARBINARY(MAX)",
    "GUID": "UNIQUEIDENTIFIER",
    "Unknown": "NVARCHAR(MAX)",
}


def _access_type_to_sqlserver(col: ColumnSchema) -> str:
    """Map an Access column type to SQL Server type."""
    access_type = col.source_type
    if access_type == "Text" and col.max_length:
        return f"NVARCHAR({col.max_length})"
    elif access_type == "Text":
        return "NVARCHAR(255)"
    elif access_type == "Binary" and col.max_length:
        return f"VARBINARY({col.max_length})"
    return TYPE_MAP.get(access_type, "NVARCHAR(MAX)")


def quote_identifier(name: str) -> str:
    """Quote a SQL Server identifier using [brackets].

    Args:
        name: The identifier to quote.

    Returns:
        The quoted identifier.
    """
    if not name:
        raise ValueError("Identifier cannot be empty")
    return f"[{name}]"


DIALECT = _dialect.DialectSpec(
    name="sqlserver",
    type_mapper=_access_type_to_sqlserver,
    quote=quote_identifier,
    autoincrement_clause="IDENTITY(1,1)",
    inline_autoincrement_consumes_pk=False,
)


def render_table_ddl(table: TableSchema) -> str:
    """Render a CREATE TABLE DDL statement for SQL Server.

    Args:
        table: TableSchema describing the table.

    Returns:
        A CREATE TABLE DDL string.
    """
    return _dialect.render_table_ddl(DIALECT, table)


class SqlServerConnector(BaseConnector):
    """SQL Server migration connector using pyodbc."""

    def __init__(self, connection_string: str) -> None:
        """Initialize SQL Server connector.

        Args:
            connection_string: pyodbc connection string for SQL Server.
        """
        super().__init__(connection_string)
        self._conn: pyodbc.Connection | None = None

    def connect(self) -> None:
        """Connect to the SQL Server database."""
        self._conn = pyodbc.connect(self.connection_string)

    def table_exists(self, table: str) -> bool:
        """Check if a table exists in the SQL Server database."""
        if self._conn is None:
            raise RuntimeError("Not connected")
        cursor = self._conn.execute(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = ?",
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
        """Insert multiple rows using executemany with parameter placeholders."""
        if self._conn is None:
            raise RuntimeError("Not connected")

        placeholders = ", ".join(["?"] * len(columns))
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
        cursor.execute(f"IF OBJECT_ID({quote_identifier(table)}, 'U') IS NOT NULL DROP TABLE {quote_identifier(table)}")
        self._conn.commit()

    def disconnect(self) -> None:
        """Close the SQL Server connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# Register the factory
CONNECTOR_FACTORIES["sqlserver"] = SqlServerConnector
