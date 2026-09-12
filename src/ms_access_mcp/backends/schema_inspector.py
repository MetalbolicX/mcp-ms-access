"""SchemaInspector — DAO schema read surface (minimal stub for plan 002)."""

from typing import Any


class SchemaInspector:
    """DAO schema read operations via ComDispatcher."""

    def __init__(self, dispatcher: Any) -> None:
        """Initialize schema operations with a dispatcher."""
        self._dispatcher = dispatcher

    def get_tables(self) -> list:
        """Return user tables, or an empty list when disconnected."""
        if not self._dispatcher.is_connected():
            return []
        return []

    def get_system_tables(self) -> list:
        """Return system tables, or an empty list when unavailable."""
        return []

    def get_queries(self) -> list:
        """Return saved queries, or an empty list when unavailable."""
        return []

    def get_indexes(self, table_name: str) -> list:
        """Return indexes for a table, or an empty list when unavailable."""
        return []

    def get_relationships(self) -> list:
        """Return relationships, or an empty list when unavailable."""
        return []

    def get_object_metadata(self, object_name: str) -> dict:
        """Return object metadata, or an empty mapping when unavailable."""
        return {}

    def get_table_schema_plan(self) -> tuple:
        """Return the table schema plan and its detected format."""
        return ([], "unknown")

    def generate_sql(self, output_path: str) -> dict:
        """Return the SQL generation result for the requested output path."""
        return {"success": False, "error": "Not implemented"}

    def get_database_statistics(self) -> dict:
        """Return database statistics, or an empty mapping when unavailable."""
        return {}

    def create_relationship(
        self,
        table_name: str,
        relationship_name: str,
        columns: list[str],
        foreign_table: str,
        foreign_columns: list[str],
    ) -> dict:
        """Return the result of creating a relationship."""
        return {"success": False, "error": "Not implemented"}

    def delete_relationship(self, table_name: str, relationship_name: str) -> dict:
        """Return the result of deleting a relationship."""
        return {"success": False, "error": "Not implemented"}
