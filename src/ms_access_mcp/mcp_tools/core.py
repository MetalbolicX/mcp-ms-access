"""MCP core tools — verbatim oracle names wrapping plan-003 use cases.

Each tool:
- Accepts str path, params: dict | None, confirm: bool = False
- Returns the use-case dict unchanged
- isError mapping via to_mcp_result()

execute_raw_sql requires confirm=True and write policy check.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from ..application.database import DatabaseUseCases, Err, Ok
from ..safety import allowed_dirs_from_env

__all__ = ["register", "to_mcp_result"]


def _new_db(readonly: bool = True) -> DatabaseUseCases:
    """Construct a DatabaseUseCases honoring ACCESS_MCP_ALLOWED_DIRS."""
    return DatabaseUseCases(readonly=readonly, allowed_dirs=allowed_dirs_from_env())


# =============================================================================
# Result Adapter (oracle plan-008 rule)
# =============================================================================


def to_mcp_result(result: dict[str, Any]) -> CallToolResult:
    """Convert a use-case result dict to an MCP ``CallToolResult``.

    oracle plan-008 rule: ``{success: false, error}`` envelopes must surface as
    ``isError: true`` with the JSON-stringified envelope as the single text block.
    Successful results return ``isError: false`` with the same JSON payload.

    Args:
        result: Use-case dict with a ``success`` key.

    Returns:
        ``CallToolResult`` with one ``TextContent`` block and the appropriate
        ``isError`` flag, ready to be returned from a tool function.
    """
    payload = json.dumps(result)
    return CallToolResult(
        content=[TextContent(type="text", text=payload)],
        isError=not result.get("success", True),
    )


def _result_to_dict(result: Ok | Err) -> dict[str, Any]:
    """Convert Ok/Err to use-case dict."""
    if isinstance(result, Ok):
        return {"success": True, "data": result.data, "rowcount": result.rowcount}
    return {"success": False, "error": result.message}


# =============================================================================
# Per-Tool Wrappers
# =============================================================================


def _make_tool(name: str, fn, *, write_op: bool = False):
    """Create an MCP tool wrapper for a use-case method.

    Args:
        name: Tool name (verbatim oracle name)
        fn: Use-case method to call
        write_op: If True, requires confirm=True and write policy check
    """
    from functools import wraps

    @wraps(fn)
    def wrapper(
        path: str,
        params: dict[str, Any] | None = None,
        confirm: bool = False,
    ) -> CallToolResult:
        # Create a fresh db instance for each call
        db = DatabaseUseCases(readonly=not write_op)

        # Connect if path provided
        if path:
            result = db.open_database(path)
            if isinstance(result, Err):
                dict_result = {"success": False, "error": result.message}
                return to_mcp_result(dict_result)

        # Execute the operation
        try:
            # Build kwargs from params
            kwargs: dict[str, Any] = {}
            if params:
                kwargs.update(params)

            # Add confirm for write ops
            if write_op:
                kwargs["confirm"] = confirm

            # Handle execute_raw_sql specially
            if name == "execute_raw_sql":
                if not confirm:
                    dict_result = {
                        "success": False,
                        "error": "write policy blocks this; pass confirm=True and enable writes",
                    }
                    return to_mcp_result(dict_result)
                # Check write policy
                if db._policy.readonly and not confirm:
                    dict_result = {
                        "success": False,
                        "error": "write policy blocks this; pass confirm=True and enable writes",
                    }
                    return to_mcp_result(dict_result)

            result = fn(db, **kwargs)
        except Exception as e:
            result = Err(message=str(e))

        dict_result = _result_to_dict(result)
        return to_mcp_result(dict_result)

    return wrapper


# =============================================================================
# Tool Functions (one per verbatim oracle name)
# =============================================================================


def connect_access(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Connect to an Access database."""
    db = _new_db(readonly=True)
    result = db.open_database(path)
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def disconnect_access(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Disconnect from an Access database."""
    db = _new_db(readonly=True)
    result = db.close_database()
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def list_connections(
    path: str = "",
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """List all open connections."""
    db = _new_db(readonly=True)
    result = db.list_connections()
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def get_tables(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get list of user tables."""
    db = _new_db(readonly=True)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    result = db.get_tables()
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def get_table_schema(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get schema for a table."""
    db = _new_db(readonly=True)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    table_name = params.get("table") if params else None
    if not table_name:
        dict_result = {"success": False, "error": "table name required"}
        return to_mcp_result(dict_result)
    result = db.get_table_schema(table_name)
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def get_indexes(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get indexes for a table."""
    db = _new_db(readonly=True)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    table_name = params.get("table") if params else None
    if not table_name:
        dict_result = {"success": False, "error": "table name required"}
        return to_mcp_result(dict_result)
    result = db.get_indexes(table_name)
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def get_relationships(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get all relationships."""
    db = _new_db(readonly=True)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    result = db.get_relationships()
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def get_queries(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get list of saved queries."""
    db = _new_db(readonly=True)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    result = db.get_queries()
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def create_query(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Create a saved query."""
    db = _new_db(readonly=False)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    if not params or "name" not in params or "sql" not in params:
        dict_result = {"success": False, "error": "name and sql required"}
        return to_mcp_result(dict_result)
    result = db.create_query(params["name"], params["sql"], confirm=confirm)
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def set_query_sql(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Update SQL for a saved query."""
    db = _new_db(readonly=False)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    if not params or "name" not in params or "sql" not in params:
        dict_result = {"success": False, "error": "name and sql required"}
        return to_mcp_result(dict_result)
    result = db.set_query_sql(params["name"], params["sql"], confirm=confirm)
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def delete_query(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Delete a saved query."""
    db = _new_db(readonly=False)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    if not params or "name" not in params:
        dict_result = {"success": False, "error": "name required"}
        return to_mcp_result(dict_result)
    result = db.delete_query(params["name"], confirm=confirm)
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def query_data(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Execute a SELECT query."""
    db = _new_db(readonly=True)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    if not params or "sql" not in params:
        dict_result = {"success": False, "error": "sql required"}
        return to_mcp_result(dict_result)
    result = db.query_data(
        params["sql"],
        params=params.get("params"),
        limit=params.get("limit"),
    )
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def insert_data(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Insert a row into a table."""
    db = _new_db(readonly=False)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    if not params or "table" not in params or "data" not in params:
        dict_result = {"success": False, "error": "table and data required"}
        return to_mcp_result(dict_result)
    result = db.insert_data(params["table"], params["data"], confirm=confirm)
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def update_data(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Update rows in a table."""
    db = _new_db(readonly=False)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    if not params or "table" not in params or "data" not in params:
        dict_result = {"success": False, "error": "table and data required"}
        return to_mcp_result(dict_result)
    result = db.update_data(
        params["table"],
        params["data"],
        where=params.get("where"),
        confirm=confirm,
    )
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def delete_data(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Delete rows from a table."""
    db = _new_db(readonly=False)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    if not params or "table" not in params:
        dict_result = {"success": False, "error": "table required"}
        return to_mcp_result(dict_result)
    result = db.delete_data(params["table"], where=params.get("where"), confirm=confirm)
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def execute_raw_sql(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Execute raw SQL (write policy blocks by default)."""
    if not confirm:
        dict_result = {
            "success": False,
            "error": "write policy blocks this; pass confirm=True and enable writes",
        }
        return to_mcp_result(dict_result)

    db = _new_db(readonly=False)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    if not params or "sql" not in params:
        dict_result = {"success": False, "error": "sql required"}
        return to_mcp_result(dict_result)
    result = db.query_data(params["sql"])
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def export_data(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Export table or query data to a file."""
    db = _new_db(readonly=True)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    if not params or "out" not in params or "source" not in params:
        dict_result = {"success": False, "error": "out and source (table or query) required"}
        return to_mcp_result(dict_result)
    result = db.export_data(
        params["source"],
        params["out"],
        format=params.get("format", "csv"),
    )
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def get_database_statistics(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get database statistics."""
    db = _new_db(readonly=True)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    result = db.get_database_statistics()
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def get_system_tables(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get list of system tables."""
    db = _new_db(readonly=True)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    result = db.get_system_tables()
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def get_linked_tables(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get list of linked tables."""
    db = _new_db(readonly=True)
    if path:
        result = db.open_database(path)
        if isinstance(result, Err):
            dict_result = {"success": False, "error": result.message}
            return to_mcp_result(dict_result)
    result = db.get_linked_tables()
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


# =============================================================================
# Tool Registry
# =============================================================================

# Dispatch table: tool name -> (function, description)
_TOOLS: dict[str, tuple[Any, str]] = {
    "connect_access": (connect_access, "Connect to an Access database"),
    "disconnect_access": (disconnect_access, "Disconnect from an Access database"),
    "list_connections": (list_connections, "List all open connections"),
    "get_tables": (get_tables, "Get list of user tables"),
    "get_table_schema": (get_table_schema, "Get schema for a table"),
    "get_indexes": (get_indexes, "Get indexes for a table"),
    "get_relationships": (get_relationships, "Get all relationships"),
    "get_queries": (get_queries, "Get list of saved queries"),
    "create_query": (create_query, "Create a saved query"),
    "set_query_sql": (set_query_sql, "Update SQL for a saved query"),
    "delete_query": (delete_query, "Delete a saved query"),
    "query_data": (query_data, "Execute a SELECT query"),
    "insert_data": (insert_data, "Insert a row into a table"),
    "update_data": (update_data, "Update rows in a table"),
    "delete_data": (delete_data, "Delete rows from a table"),
    "execute_raw_sql": (execute_raw_sql, "Execute raw SQL (requires confirm=True and write policy)"),
    "export_data": (export_data, "Export table or query data to a file"),
    "get_database_statistics": (get_database_statistics, "Get database statistics"),
    "get_system_tables": (get_system_tables, "Get list of system tables"),
    "get_linked_tables": (get_linked_tables, "Get list of linked tables"),
}


def register(mcp: FastMCP) -> None:
    """Register all core tools with the FastMCP server.

    Args:
        mcp: FastMCP server instance
    """
    for name, (fn, description) in _TOOLS.items():
        mcp.add_tool(fn, name=name, description=description)
