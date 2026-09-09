"""SchemaInspector — DAO schema read surface (minimal stub for plan 002)."""

from typing import Any


class SchemaInspector:
    """DAO schema read operations via ComDispatcher."""

    def __init__(self, dispatcher: Any) -> None:
        self._dispatcher = dispatcher

    def get_tables(self) -> list:
        if not self._dispatcher.is_connected():
            return []
        return []

    def get_system_tables(self) -> list:
        return []

    def get_queries(self) -> list:
        return []

    def get_indexes(self, table_name: str) -> list:
        return []

    def get_relationships(self) -> list:
        return []

    def get_object_metadata(self, object_name: str) -> dict:
        return {}

    def get_table_schema_plan(self) -> tuple:
        return ([], "unknown")

    def generate_sql(self, output_path: str) -> dict:
        return {"success": False, "error": "Not implemented"}

    def get_database_statistics(self) -> dict:
        return {}

    def create_relationship(
        self,
        table_name: str,
        relationship_name: str,
        columns: list[str],
        foreign_table: str,
        foreign_columns: list[str],
    ) -> dict:
        return {"success": False, "error": "Not implemented"}

    def delete_relationship(self, table_name: str, relationship_name: str) -> dict:
        return {"success": False, "error": "Not implemented"}
