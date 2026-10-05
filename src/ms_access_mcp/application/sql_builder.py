"""SQL Builder — pure functions for SQL construction and row extraction.

Port from oracle services/sql_builder.py.
"""

from __future__ import annotations

from typing import Any

from ..backends.dao_sql import quote_ident


def build_select(
    table_name: str,
    columns: list[str] | None,
    where: str | None,
    order_by: list[str] | None,
) -> str:
    """Build SELECT statement with optional column list, WHERE, ORDER BY.

    columns=None → SELECT *
    columns=[] → invalid (should not reach here)
    columns=[...] → SELECT col, col, ...
    """
    cols = "*" if columns is None else ", ".join(quote_ident(col) for col in columns)
    sql = f"SELECT {cols} FROM {quote_ident(table_name)}"
    if where:
        sql += f" WHERE {where}"
    if order_by:
        sql += f" ORDER BY {', '.join(quote_ident(column) for column in order_by)}"
    return sql


def validate_columns(requested: list[str], available: list[str]) -> None:
    """Raise ValueError if any requested column not in available."""
    available_set = set(available)
    for col in requested:
        if col not in available_set:
            raise ValueError(f"Invalid column '{col}' not found in table schema")


def extract_rows(query_result: dict | list) -> list[dict]:
    """Safely extract row dicts from varying query result formats."""
    if isinstance(query_result, dict):
        return query_result.get("rows", []) if query_result.get("success", False) else []
    return query_result


def normalize_value(value: Any) -> str:
    """Normalize a value for comparison or storage, converting None to placeholder."""
    return "<NULL>" if value is None else str(value)
