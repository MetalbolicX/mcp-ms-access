"""MySQL migration connector with lazy mysql-connector import.

mysql-connector-python is imported only inside connect() to allow the module to be
imported without the driver installed. If connect() is called without the driver,
a helpful error message is raised.
"""

from __future__ import annotations

from typing import Any

from ....models import ColumnSchema, TableSchema
from .. import CONNECTOR_FACTORIES, INSTALL_MESSAGE
from . import BaseConnector

__all__ = ["MySqlConnector", "TYPE_MAP", "quote_identifier", "render_table_ddl"]

# Access source_type -> MySQL type
TYPE_MAP: dict[str, str] = {
    "Boolean": "TINYINT(1)",
    "Byte": "TINYINT",
    "Unsigned Byte": "TINYINT UNSIGNED",
    "Integer": "SMALLINT",
    "Unsigned Integer": "SMALLINT UNSIGNED",
    "Long Integer": "INT",
    "Unsigned Long Integer": "INT UNSIGNED",
    "Big Integer": "BIGINT",
    "Single": "FLOAT",
    "Double": "DOUBLE",
    "Currency": "DECIMAL(19,4)",
    "Decimal": "DECIMAL(28,14)",
    "Date/Time": "DATETIME",
    "Text": "VARCHAR({max_length})" if False else "VARCHAR(255)",
    "Memo": "LONGTEXT",
    "Binary": "VARBINARY({max_length})" if False else "VARBINARY(255)",
    "GUID": "CHAR(36)",
    "Unknown": "TEXT",
}


def _access_type_to_mysql(col: ColumnSchema) -> str:
    """Map an Access column type to MySQL type."""
    access_type = col.source_type
    if access_type == "Text" and col.max_length:
        return f"VARCHAR({col.max_length})"
    elif access_type == "Text":
        return "VARCHAR(255)"
    elif access_type == "Binary" and col.max_length:
        return f"VARBINARY({col.max_length})"
    elif access_type == "Binary":
        return "VARBINARY(255)"
    return TYPE_MAP.get(access_type, "TEXT")


def quote_identifier(name: str) -> str:
    """Quote a MySQL identifier using backticks.

    Args:
        name: The identifier to quote.

    Returns:
        The quoted identifier.
    """
    if not name:
        raise ValueError("Identifier cannot be empty")
    return f"`{name}`"


def render_table_ddl(table: TableSchema) -> str:
    """Render a CREATE TABLE DDL statement for MySQL.

    MySQL requires AUTO_INCREMENT columns to be a key. If the autoincrement
    column is the sole primary key, we emit it inline.

    Args:
        table: TableSchema describing the table.

    Returns:
        A CREATE TABLE DDL string.
    """
    columns_ddl: list[str] = []
    pk_columns: list[str] = []

    for col in table.columns:
        col_ddl = f"{quote_identifier(col.name)} {_access_type_to_mysql(col)}"

        # NOT NULL
        if not col.allow_null:
            col_ddl += " NOT NULL"

        # DEFAULT value
        if col.default_value is not None:
            col_ddl += f" DEFAULT {col.default_value}"

        # AUTO_INCREMENT for single-column integer PK
        if col.is_autoincrement and col.source_type in ("Long Integer", "Integer", "Big Integer"):
            if len(table.primary_key) == 1 and table.primary_key[0] == col.name:
                col_ddl += " AUTO_INCREMENT"

        columns_ddl.append(col_ddl)
        if col.name in table.primary_key:
            pk_columns.append(quote_identifier(col.name))

    # Primary key constraint
    if table.primary_key:
        pk_parts = [quote_identifier(pk) for pk in table.primary_key]
        columns_ddl.append(f"PRIMARY KEY ({', '.join(pk_parts)})")

    # Build the statement
    col_defs = ",\n    ".join(columns_ddl)
    ddl = f"CREATE TABLE {quote_identifier(table.name)} (\n    {col_defs}\n)"

    return ddl


class MySqlConnector(BaseConnector):
    """MySQL migration connector with lazy mysql-connector import."""

    def __init__(self, connection_string: str) -> None:
        """Initialize MySQL connector.

        Args:
            connection_string: mysql-connector-python connection string.
        """
        super().__init__(connection_string)
        self._conn: Any = None

    def connect(self) -> None:
        """Connect to the MySQL database.

        Raises:
            ImportError: If mysql-connector-python is not installed.
            ConnectionError: If the connection fails.
        """
        try:
            import mysql.connector  # type: ignore
        except ImportError:
            raise ImportError(
                f"mysql-connector-python is required for mysql connectors. {INSTALL_MESSAGE}"
            )

        self._conn = mysql.connector.connect(self.connection_string)

    def table_exists(self, table: str) -> bool:
        """Check if a table exists in the MySQL database."""
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
        cursor.execute(f"DROP TABLE IF EXISTS {quote_identifier(table)}")
        self._conn.commit()

    def disconnect(self) -> None:
        """Close the MySQL connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# Register the factory
CONNECTOR_FACTORIES["mysql"] = MySqlConnector
