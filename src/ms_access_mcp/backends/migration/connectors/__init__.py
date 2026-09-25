"""Migration connector base class and registry.

BaseConnector ABC defines the interface for all dialect connectors.
_CONNECTOR_FACTORIES holds lazy-registered factories for each target type.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ...dao_sql import quote_ident

__all__ = ["BaseConnector", "_register_factory", "_CONNECTOR_FACTORIES"]

_CONNECTOR_FACTORIES: dict[str, Any] = {}


def _register_factory(target_type: str, factory: Any) -> None:
    """Register a connector factory for a target type."""
    _CONNECTOR_FACTORIES[target_type] = factory


class BaseConnector(ABC):
    """Abstract base class for migration target connectors.

    All dialect connectors must implement these methods:
    - connect: Establish connection to the target database
    - table_exists: Check if a table exists
    - create_table: Execute a CREATE TABLE DDL statement
    - insert_batch: Insert multiple rows in a single transaction
    - rollback_table: Rollback (drop if exists) a table after failure
    - disconnect: Close the connection

    Each dialect module also provides module-level helpers:
    - TYPE_MAP: dict[str, str] mapping Access source_type -> dialect type
    - quote_identifier(name: str) -> str: Quote identifier with dialect rules
    - render_table_ddl(table: TableSchema) -> str: Render CREATE TABLE DDL
    """

    def __init__(self, connection_string: str) -> None:
        """Initialize the connector with a connection string.

        Args:
            connection_string: Dialect-specific connection string.
        """
        self.connection_string = connection_string
        self._conn: Any

    def count_rows(self, table: str) -> int:
        """Count rows in *table* (used by migration verification)."""
        cursor = self._conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}")
            return int(cursor.fetchone()[0])
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the target database.

        Raises:
            ImportError: If the required driver is not installed.
            ConnectionError: If the connection fails.
        """

    @abstractmethod
    def table_exists(self, table: str) -> bool:
        """Check if a table exists in the target database.

        Args:
            table: Table name to check.

        Returns:
            True if the table exists, False otherwise.
        """

    @abstractmethod
    def create_table(self, ddl: str) -> None:
        """Execute a CREATE TABLE DDL statement.

        Args:
            ddl: The CREATE TABLE statement to execute.
        """

    @abstractmethod
    def insert_batch(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        """Insert multiple rows in a single transaction.

        Args:
            table: Target table name.
            columns: List of column names.
            rows: List of row tuples matching the columns.

        Returns:
            Number of rows inserted.
        """

    @abstractmethod
    def rollback_table(self, table: str) -> None:
        """Rollback a table (drop if exists) after a failure.

        This is called when a table transfer fails to clean up partial work.

        Args:
            table: Table name to drop.
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Close the connection to the target database."""

    def __repr__(self) -> str:
        """Redacted repr — never echoes the connection string."""
        return f"<{type(self).__name__} [redacted]>"
