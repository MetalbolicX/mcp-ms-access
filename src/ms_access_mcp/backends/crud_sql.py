"""Shared CRUD SQL builder for ADO and ODBC backends.

This module centralizes all INSERT/UPDATE/DELETE SQL construction so that
ADO and ODBC backends delegate to these functions instead of duplicating
the same f-string logic.
"""

from __future__ import annotations

from .dao_sql import quote_ident


def build_insert(table: str, data: dict | list[dict]) -> tuple[str, list]:
    """Build an INSERT statement and return (sql, params).

    Args:
        table: Table name (will be bracket-quoted as [table]).
        data: Single row dict or list of row dicts.

    Returns:
        Tuple of (sql_string, list_of_params).

    """
    rows = data if isinstance(data, list) else [data]
    if not rows:
        msg = "data must contain at least one row"
        raise ValueError(msg)
    row = rows[0]
    cols = list(row.keys())
    vals = list(row.values())
    placeholders = ", ".join(["?" for _ in vals])
    sql = (
        f"INSERT INTO {quote_ident(table)} "
        f"({', '.join(quote_ident(col) for col in cols)}) VALUES ({placeholders})"
    )
    return sql, vals


def build_update(
    table: str, set_dict: dict, where_dict: dict | None = None
) -> tuple[str, list]:
    """Build an UPDATE statement and return (sql, params).

    Args:
        table: Table name (will be bracket-quoted as [table]).
        set_dict: Column values to set.
        where_dict: Optional WHERE conditions (AND-ed).

    Returns:
        Tuple of (sql_string, list_of_params).

    """
    set_clause = ", ".join(f"{quote_ident(k)} = ?" for k in set_dict)
    vals = list(set_dict.values())
    sql = f"UPDATE {quote_ident(table)} SET {set_clause}"
    if where_dict:
        where_clause = " AND ".join(
            f"{quote_ident(k)} = ?" for k in where_dict.keys()
        )
        sql += f" WHERE {where_clause}"
        vals.extend(where_dict.values())
    return sql, vals


def build_delete(table: str, where_dict: dict | None = None) -> tuple[str, list]:
    """Build a DELETE statement and return (sql, params).

    Args:
        table: Table name (will be bracket-quoted as [table]).
        where_dict: Optional WHERE conditions (AND-ed).

    Returns:
        Tuple of (sql_string, list_of_params).

    """
    sql = f"DELETE FROM {quote_ident(table)}"
    vals: list = []
    if where_dict:
        where_clause = " AND ".join(
            f"{quote_ident(k)} = ?" for k in where_dict.keys()
        )
        sql += f" WHERE {where_clause}"
        vals = list(where_dict.values())
    return sql, vals
