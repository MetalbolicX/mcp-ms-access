"""MCP DDL + database-admin tools — create/alter/delete table/index/relationship, linked tables, database properties."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from ..application.database import DatabaseUseCases
from ..safety import allowed_dirs_from_env
from .core import _result_to_dict, to_mcp_result

__all__ = ["register", "_TOOLS"]


def _new_db(readonly: bool = True) -> DatabaseUseCases:
    """Construct a DatabaseUseCases honoring ACCESS_MCP_ALLOWED_DIRS."""
    return DatabaseUseCases(readonly=readonly, allowed_dirs=allowed_dirs_from_env())


# =============================================================================
# DDL Tools (wrapping existing use cases at application/database.py:573-782)
# =============================================================================


def create_table(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Create a new table in the database."""
    p = params or {}
    table = p.get("table", "")
    columns = p.get("columns", {})
    uc = _new_db(readonly=False)
    result = uc.create_table(table_name=table, columns=columns, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def alter_table(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Alter a table (add or drop a column)."""
    p = params or {}
    table = p.get("table", "")
    action = p.get("action", "")
    column = p.get("column")
    sql_type = p.get("type")
    uc = _new_db(readonly=False)
    result = uc.alter_table(table_name=table, action=action, column_name=column, sql_type=sql_type, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def delete_table(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Delete a table from the database."""
    p = params or {}
    table = p.get("table", "")
    uc = _new_db(readonly=False)
    result = uc.delete_table(table_name=table, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def create_index(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Create an index on a table."""
    p = params or {}
    table = p.get("table", "")
    index = p.get("index", "")
    columns = p.get("columns", [])
    unique = p.get("unique", False)
    uc = _new_db(readonly=False)
    result = uc.create_index(table_name=table, index_name=index, columns=columns, unique=unique, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def drop_index(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Drop an index from a table."""
    p = params or {}
    table = p.get("table", "")
    index = p.get("index", "")
    uc = _new_db(readonly=False)
    result = uc.drop_index(table_name=table, index_name=index, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def create_relationship(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Create a relationship between two tables."""
    p = params or {}
    name = p.get("name", "")
    source_table = p.get("source_table", "")
    source_column = p.get("source_column", "")
    dest_table = p.get("dest_table", "")
    dest_column = p.get("dest_column", "")
    uc = _new_db(readonly=False)
    result = uc.create_relationship(
        relationship_name=name,
        source_table=source_table,
        source_column=source_column,
        dest_table=dest_table,
        dest_column=dest_column,
        confirm=confirm,
    )
    return to_mcp_result(_result_to_dict(result))


def delete_relationship(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Delete a relationship from the database."""
    p = params or {}
    name = p.get("name", "")
    uc = _new_db(readonly=False)
    result = uc.delete_relationship(relationship_name=name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


# =============================================================================
# Linked Table Tools (thin wrappers for DAO backend)
# =============================================================================


def create_linked_table(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Create a linked (attached) table (DAO backend required)."""
    p = params or {}
    name = p.get("name", "")
    source_table = p.get("source_table", "")
    connect_string = p.get("connect_string", "")
    connection_name = p.get("connection_name", "default")
    uc = _new_db(readonly=False)
    result = uc.create_linked_table(
        name=name,
        source_table=source_table,
        connect_string=connect_string,
        connection_name=connection_name,
        confirm=confirm,
    )
    return to_mcp_result(_result_to_dict(result))


def refresh_linked_table(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Re-authenticate a linked table with an optional new connection string."""
    p = params or {}
    name = p.get("name", "")
    connect_string = p.get("connect_string")
    connection_name = p.get("connection_name", "default")
    uc = _new_db(readonly=False)
    result = uc.refresh_linked_table(
        name=name,
        connect_string=connect_string,
        connection_name=connection_name,
        confirm=confirm,
    )
    return to_mcp_result(_result_to_dict(result))


def unlink_table(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Remove a linked table without deleting the remote source table."""
    p = params or {}
    name = p.get("name", "")
    connection_name = p.get("connection_name", "default")
    uc = _new_db(readonly=False)
    result = uc.unlink_table(name=name, connection_name=connection_name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def upsert_linked_table(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Recreate a linked table preserving hidden attributes (DAO backend required)."""
    p = params or {}
    name = p.get("name", "")
    source_table = p.get("source_table", "")
    connect_string = p.get("connect_string", "")
    connection_name = p.get("connection_name", "default")
    uc = _new_db(readonly=False)
    result = uc.upsert_linked_table(
        name=name,
        source_table=source_table,
        connect_string=connect_string,
        connection_name=connection_name,
        confirm=confirm,
    )
    return to_mcp_result(_result_to_dict(result))


# =============================================================================
# Admin Tools
# =============================================================================


def create_access_database(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Create a blank Access database at the target path (destructive). Requires confirm=True."""
    p = params or {}
    target_path = p.get("path", path)
    uc = _new_db(readonly=False)
    result = uc.create_access_database(path=target_path, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def get_database_properties(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Get database properties."""
    p = params or {}
    connection_name = p.get("connection_name", "default")
    uc = _new_db(readonly=True)
    result = uc.get_database_properties(connection_name=connection_name)
    return to_mcp_result(_result_to_dict(result))


def set_database_property(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Set a database property."""
    p = params or {}
    name = p.get("name", "")
    value = p.get("value", "")
    connection_name = p.get("connection_name", "default")
    uc = _new_db(readonly=False)
    result = uc.set_database_property(name=name, value=value, connection_name=connection_name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def get_object_metadata(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Get metadata for a database object (table, query, etc.)."""
    p = params or {}
    object_name = p.get("object_name", "")
    object_type = p.get("object_type", "table")
    connection_name = p.get("connection_name", "default")
    uc = _new_db(readonly=True)
    result = uc.get_object_metadata(object_name=object_name, object_type=object_type, connection_name=connection_name)
    return to_mcp_result(_result_to_dict(result))


def is_connected(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Check that the database at path can be opened (stateless probe per call)."""
    p = params or {}
    connection_name = p.get("connection_name", "default")
    uc = _new_db(readonly=True)
    result = uc.is_connected(connection_name=connection_name)
    return to_mcp_result(_result_to_dict(result))


# =============================================================================
# Registry
# =============================================================================

_TOOLS: dict[str, tuple[Any, str]] = {
    # DDL
    "create_table": (
        create_table,
        "Create a new table in the database.",
    ),
    "alter_table": (
        alter_table,
        "Alter a table (add or drop a column).",
    ),
    "delete_table": (
        delete_table,
        "Delete a table from the database.",
    ),
    "create_index": (
        create_index,
        "Create an index on a table.",
    ),
    "drop_index": (
        drop_index,
        "Drop an index from a table.",
    ),
    "create_relationship": (
        create_relationship,
        "Create a relationship between two tables.",
    ),
    "delete_relationship": (
        delete_relationship,
        "Delete a relationship from the database.",
    ),
    # Linked tables
    "create_linked_table": (
        create_linked_table,
        "Create a linked (attached) table. Requires DAO backend.",
    ),
    "refresh_linked_table": (
        refresh_linked_table,
        "Re-authenticate a linked table with an optional new connection string.",
    ),
    "unlink_table": (
        unlink_table,
        "Remove a linked table without deleting the remote source table.",
    ),
    "upsert_linked_table": (
        upsert_linked_table,
        "Recreate a linked table preserving hidden attributes. Requires DAO backend.",
    ),
    # Admin
    "create_access_database": (
        create_access_database,
        "Create a blank Access database at the target path. Requires confirm=True.",
    ),
    "get_database_properties": (
        get_database_properties,
        "Get database properties.",
    ),
    "set_database_property": (
        set_database_property,
        "Set a database property. Requires confirm=True.",
    ),
    "get_object_metadata": (
        get_object_metadata,
        "Get metadata for a database object (table, query, etc.).",
    ),
    "is_connected": (
        is_connected,
        "Check that the database at path can be opened. Stateless probe per call.",
    ),
}


def register(mcp: FastMCP) -> None:
    """Register all DDL and admin tools with the FastMCP server."""
    for name, (fn, description) in _TOOLS.items():
        mcp.add_tool(fn, name=name, description=description)
