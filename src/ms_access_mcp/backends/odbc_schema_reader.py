"""ODBC schema reader — reads Access MSys* system tables via pyodbc.

SRP parallel to ``SchemaInspector``: this class owns "talk to the system tables";
``OdbcAdapter`` owns the rest of the data and connection lifecycle.

Mirrors the shape of ``SchemaInspector.get_relationships()`` so that
the ER diagram and relationship consumers get a uniform ``list[RelationshipInfo]``
regardless of which adapter is in use.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from ..models import RelationshipInfo

MSYS_RELATIONSHIPS_QUERY = (
    "SELECT szObject, szColumn, szReferencedObject, szReferencedColumn "
    "FROM MSysRelationships "
    "WHERE szObject IS NOT NULL AND szReferencedObject IS NOT NULL "
    "ORDER BY szObject, szReferencedObject, szColumn"
)


class OdbcSchemaReader:
    """Reads Access system tables (MSys*) via the active pyodbc connection.

    Conn must be an open ``pyodbc.Connection`` bound to an Access database.
    Gracefully degrades to an empty list when ``MSysRelationships`` is
    hidden or denied — matches the fallback pattern used by
    ``OdbcAdapter.get_database_statistics()``.
    """

    def __init__(self, conn: Any, logger: logging.Logger) -> None:
        """Initialize with the pyodbc connection and logger."""
        self._conn = conn
        self._logger = logger

    def get_relationships(self) -> list[RelationshipInfo]:
        """Get all foreign-key relationships from MSysRelationships.

        Returns:
            list[RelationshipInfo]: One entry per FK, with multi-column
            FKs aggregated into a single RelationshipInfo.

        """
        if self._conn is None:
            return []
        try:
            return self._query_relationships()
        except Exception as e:
            # MSysRelationships is hidden in many Jet/ACE builds —
            # gracefully fall back to empty list, same pattern as
            # OdbcAdapter.get_database_statistics().
            self._logger.warning(
                "get_relationships via MSysRelationships failed: %s", e
            )
            return []

    def _query_relationships(self) -> list[RelationshipInfo]:
        """Execute the MSysRelationships query and group results."""
        cursor = self._conn.cursor()
        try:
            cursor.execute(MSYS_RELATIONSHIPS_QUERY)
            rows = cursor.fetchall()
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        return self._group_rows(rows)

    @staticmethod
    def _group_rows(rows: list[tuple]) -> list[RelationshipInfo]:
        """Group per-column rows by (child, parent) into one per FK.

        ``MSysRelationships`` returns one row per (FK column, PK column)
        pair. Multi-column FKs produce multiple rows with the same
        ``(child_table, parent_table)`` combination — they are merged
        into a single ``RelationshipInfo`` with matching column lists.
        """
        buckets: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(
            lambda: {"columns": [], "foreign_columns": []}
        )

        for row in rows:
            child_table, child_col, parent_table, parent_col = row
            key = (child_table, parent_table)
            buckets[key]["columns"].append(child_col)
            buckets[key]["foreign_columns"].append(parent_col)

        result: list[RelationshipInfo] = []
        for (child_table, parent_table), cols in buckets.items():
            result.append(
                RelationshipInfo(
                    name=f"FK_{child_table}_{parent_table}",
                    table=child_table,
                    foreign_table=parent_table,
                    attributes="",
                    columns=cols["columns"],
                    foreign_columns=cols["foreign_columns"],
                )
            )

        # Deterministic sort — stable ordering across requests
        result.sort(key=lambda r: r.name)
        return result
