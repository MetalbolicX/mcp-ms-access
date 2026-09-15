"""SQLite migration connector (stdlib sqlite3, no third-party dependency)."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from ....models import ColumnSchema, TableSchema
from .. import CONNECTOR_FACTORIES
from . import BaseConnector
from . import dialect as _dialect

__all__ = ["SqliteConnector", "TYPE_MAP", "quote_identifier", "render_table_ddl"]

# Access source_type -> SQLite type
TYPE_MAP: dict[str, str] = {
    "Boolean": "INTEGER",
    "Byte": "INTEGER",
    "Unsigned Byte": "INTEGER",
    "Integer": "INTEGER",
    "Unsigned Integer": "INTEGER",
    "Long Integer": "INTEGER",
    "Unsigned Long Integer": "INTEGER",
    "Big Integer": "INTEGER",
    "Single": "REAL",
    "Double": "REAL",
    "Currency": "NUMERIC",
    "Decimal": "NUMERIC",
    "Date/Time": "TEXT",
    "Text": "TEXT",
    "Memo": "TEXT",
    "Binary": "BLOB",
    "GUID": "TEXT",
    "Unknown": "TEXT",
}

# Characters that are forbidden in SQLite identifiers
_FORBIDDEN_CHARS = frozenset('; "\'-')
_FORBIDDEN_WHITESPACE = frozenset(" \t\n\r\f\v")


def quote_identifier(name: str) -> str:
    """Quote a SQLite identifier (table or column name).

    Rejects identifiers containing semicolon, double quote, single quote,
    double-dash, or whitespace.

    Args:
        name: The identifier to quote.

    Returns:
        The quoted identifier.

    Raises:
        ValueError: If the identifier contains forbidden characters.
    """
    if not name:
        raise ValueError("Identifier cannot be empty")

    for char in name:
        if char in _FORBIDDEN_CHARS:
            raise ValueError(
                f"Identifier {name!r} contains forbidden character {char!r}"
            )
        if char in _FORBIDDEN_WHITESPACE:
            raise ValueError(
                f"Identifier {name!r} contains forbidden whitespace character"
            )

    return f'"{name}"'


def _access_type_to_sqlite(col: ColumnSchema) -> str:
    """Map an Access column type to SQLite type."""
    mapped = TYPE_MAP.get(col.source_type, "TEXT")
    # If we have a max_length for TEXT columns, we could use VARCHAR but
    # SQLite doesn't enforce types, so TEXT is fine
    return mapped


DIALECT = _dialect.DialectSpec(
    name="sqlite",
    type_mapper=_access_type_to_sqlite,
    quote=quote_identifier,
    autoincrement_clause="PRIMARY KEY AUTOINCREMENT",
    inline_autoincrement_consumes_pk=True,
)


def render_table_ddl(table: TableSchema) -> str:
    """Render a CREATE TABLE DDL statement for SQLite.

    Args:
        table: TableSchema describing the table.

    Returns:
        A CREATE TABLE DDL string.
    """
    return _dialect.render_table_ddl(DIALECT, table)


class SqliteConnector(BaseConnector):
    """SQLite migration connector using stdlib sqlite3."""

    def __init__(self, connection_string: str) -> None:
        """Initialize SQLite connector.

        Args:
            connection_string: Path to the SQLite database file.
        """
        super().__init__(connection_string)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Connect to the SQLite database."""
        self._conn = sqlite3.connect(self.connection_string)
        self._conn.row_factory = sqlite3.Row

    def table_exists(self, table: str) -> bool:
        """Check if a table exists in the SQLite database."""
        if self._conn is None:
            raise RuntimeError("Not connected")
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        result = cursor.fetchone()
        return result is not None

    def create_table(self, ddl: str) -> None:
        """Execute a CREATE TABLE DDL statement."""
        if self._conn is None:
            raise RuntimeError("Not connected")
        self._conn.execute(ddl)
        self._conn.commit()

    def insert_batch(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        """Insert multiple rows using executemany with parameter placeholders.

        Decimal values are converted to float since sqlite3 does not support
        the decimal.Decimal type.
        """
        if self._conn is None:
            raise RuntimeError("Not connected")

        # Convert Decimal -> float for sqlite3 compatibility
        def _normalize_row(row: tuple) -> tuple:
            return tuple(float(v) if isinstance(v, Decimal) else v for v in row)

        normalized_rows = [_normalize_row(r) for r in rows]

        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(quote_identifier(c) for c in columns)
        sql = f"INSERT INTO {quote_identifier(table)} ({col_names}) VALUES ({placeholders})"

        cursor = self._conn.cursor()
        cursor.executemany(sql, normalized_rows)
        self._conn.commit()
        return cursor.rowcount

    def rollback_table(self, table: str) -> None:
        """Drop the table if it exists."""
        if self._conn is None:
            raise RuntimeError("Not connected")
        self._conn.execute(f"DROP TABLE IF EXISTS {quote_identifier(table)}")
        self._conn.commit()

    def disconnect(self) -> None:
        """Close the SQLite connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# Register the factory
CONNECTOR_FACTORIES["sqlite"] = SqliteConnector
