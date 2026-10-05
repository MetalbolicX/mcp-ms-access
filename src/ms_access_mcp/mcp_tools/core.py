"""MCP core tools — verbatim oracle names, typed signatures.

Each tool exposes concrete typed parameters rather than an opaque ``params`` dict.
FastMCP generates JSON Schema from the function signatures automatically.

The 21 core tools are split into:
- Read tools (introspection cohort): no write risk — no confirm param
- Write tools (query + data cohort): confirm=False default, write-policy gated
"""

from __future__ import annotations

import json
from functools import wraps
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from ..application.database import DatabaseUseCases, Err, Ok
from ..safety import allowed_dirs_from_env
from ._registry import ToolRegistry, register_tools
from ._session_registry import registry

__all__ = ["register", "to_mcp_result"]


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
    return result.fold(
        lambda data, rowcount: {"success": True, "data": data, "rowcount": rowcount},
        lambda message: {"success": False, "error": message},
    )


# =============================================================================
# Shared helpers
# =============================================================================


def _new_db(readonly: bool = True) -> DatabaseUseCases:
    """Construct a DatabaseUseCases honoring ACCESS_MCP_ALLOWED_DIRS."""
    return registry.track(DatabaseUseCases(readonly=readonly, allowed_dirs=allowed_dirs_from_env()))


def _open_or_err(db: DatabaseUseCases, path: str) -> CallToolResult | None:
    """Open path on db; return an error CallToolResult on failure, else None."""
    if not path:
        return None
    result = db.open_database(path)
    if isinstance(result, Err):
        return to_mcp_result({"success": False, "error": result.message})
    return None


def _close_per_call_sessions(function: Any) -> Any:
    """Release sessions created by a tool after its operation has completed."""
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        existing = registry.snapshot()
        try:
            return function(*args, **kwargs)
        finally:
            registry.close_sessions_created_after(existing)

    return wrapped


# =============================================================================
# Introspection cohort (read tools — no confirm param)
# =============================================================================


def connect_access(path: str) -> CallToolResult:
    """Connect to an Access database.

    Args:
        path: Absolute path to the .accdb or .mdb file.

    Password-protected databases resolve credentials server-side; if connection
    fails with an auth error, ask the user to run
    ``ms-access-mcp credentials set --db <path>`` — never request passwords.
    """
    db = _new_db(readonly=True)
    result = db.open_database(path)
    return to_mcp_result(_result_to_dict(result))


def disconnect_access() -> CallToolResult:
    """Close all tracked sessions; this tool has no path to filter by."""
    from ._session_registry import close_all_sessions

    close_all_sessions()
    return to_mcp_result({"success": True})


@_close_per_call_sessions
def list_connections() -> CallToolResult:
    """List all open connections and their current state."""
    db = _new_db(readonly=True)
    result = db.list_connections()
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def get_tables(path: str) -> CallToolResult:
    """Get user tables (excludes system and linked tables), without row counts.

    To get a row count, call ``query_data("SELECT COUNT(*) FROM [table]")``.

    Args:
        path: Absolute path to the .accdb or .mdb file.
    """
    db = _new_db(readonly=True)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.get_tables()
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def get_table_schema(path: str, table: str) -> CallToolResult:
    """Get schema for a table (column names, types, nullability).

    Args:
        path: Absolute path to the .accdb or .mdb file.
        table: Table name (case-insensitive, as stored in the database).
    """
    db = _new_db(readonly=True)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.get_table_schema(table)
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def get_indexes(path: str, table: str) -> CallToolResult:
    """Get indexes defined on a table.

    Args:
        path: Absolute path to the .accdb or .mdb file.
        table: Table name (case-insensitive, as stored in the database).
    """
    db = _new_db(readonly=True)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.get_indexes(table)
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def get_relationships(path: str) -> CallToolResult:
    """Get all relationships defined in the database.

    Args:
        path: Absolute path to the .accdb or .mdb file.
    """
    db = _new_db(readonly=True)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.get_relationships()
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def get_queries(path: str) -> CallToolResult:
    """Get list of saved queries (SELECT, action, pass-through).

    Args:
        path: Absolute path to the .accdb or .mdb file.
    """
    db = _new_db(readonly=True)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.get_queries()
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def get_database_statistics(path: str) -> CallToolResult:
    """Get database statistics (record counts, file size, last modified).

    Args:
        path: Absolute path to the .accdb or .mdb file.
    """
    db = _new_db(readonly=True)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.get_database_statistics()
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def get_system_tables(path: str) -> CallToolResult:
    """Get list of system tables (MSys* tables in Access).

    Args:
        path: Absolute path to the .accdb or .mdb file.
    """
    db = _new_db(readonly=True)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.get_system_tables()
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def get_linked_tables(path: str) -> CallToolResult:
    """Get list of tables linked from external data sources.

    Args:
        path: Absolute path to the .accdb or .mdb file.
    """
    db = _new_db(readonly=True)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.get_linked_tables()
    return to_mcp_result(_result_to_dict(result))


# =============================================================================
# Query + data-write cohort (confirm=False default on all write tools)
# =============================================================================


@_close_per_call_sessions
def query_data(path: str, sql: str, params: list[Any] | None = None, limit: int | None = None) -> CallToolResult:
    """Execute a SELECT query and return rows.

    Args:
        path: Absolute path to the .accdb or .mdb file.
        sql: SQL query string (SELECT statement).
        params: Optional list of parameter values for parameterized queries.
        limit: Optional maximum number of rows to return.
    """
    db = _new_db(readonly=True)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.query_data(sql, params=params, limit=limit)
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def execute_raw_sql(path: str, sql: str, confirm: bool = False) -> CallToolResult:
    """Execute raw SQL (write policy blocks by default — requires confirm=True).

    This tool bypasses the query parser and passes SQL directly to the engine.
    Requires confirm=True and a writable connection.

    Args:
        path: Absolute path to the .accdb or .mdb file.
        sql: Raw SQL statement to execute.
        confirm: Must be True to execute. Defaults to False (write-policy gated).
    """
    if not confirm:
        return to_mcp_result({
            "success": False,
            "error": "write policy blocks this; pass confirm=True and enable writes",
        })
    db = _new_db(readonly=False)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.execute_action_sql(sql, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def create_query(path: str, name: str, sql: str, confirm: bool = False) -> CallToolResult:
    """Create a saved query with the given name and SQL.

    Args:
        path: Absolute path to the .accdb or .mdb file.
        name: Name for the saved query (must be unique within the database).
        sql: SQL statement for the query.
        confirm: Must be True to create. Defaults to False.
    """
    db = _new_db(readonly=False)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.create_query(name, sql, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def set_query_sql(path: str, name: str, sql: str, confirm: bool = False) -> CallToolResult:
    """Update the SQL definition of an existing saved query.

    Args:
        path: Absolute path to the .accdb or .mdb file.
        name: Name of the existing saved query.
        sql: New SQL statement to assign.
        confirm: Must be True to modify. Defaults to False.
    """
    db = _new_db(readonly=False)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.set_query_sql(name, sql, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def delete_query(path: str, name: str, confirm: bool = False) -> CallToolResult:
    """Delete a saved query from the database.

    Args:
        path: Absolute path to the .accdb or .mdb file.
        name: Name of the saved query to delete.
        confirm: Must be True to delete. Defaults to False.
    """
    db = _new_db(readonly=False)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.delete_query(name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def save_query(
    path: str,
    name: str,
    sql: str,
    overwrite: bool = False,
    confirm: bool = False,
) -> CallToolResult:
    """Save or update a saved query definition in the Access database.

    If overwrite=True and the query exists, updates the SQL.
    If overwrite=False and the query exists, returns an error.
    If the query does not exist, creates it.

    Args:
        path: Absolute path to the .accdb or .mdb file.
        name: Name for the saved query.
        sql: SQL statement for the query.
        overwrite: If True, replace an existing query with the same name.
        confirm: Must be True to save. Defaults to False.
    """
    db = _new_db(readonly=False)
    if (err := _open_or_err(db, path)) is not None:
        return err
    # Check if query already exists
    existing = db.get_queries()
    if isinstance(existing, Err):
        return to_mcp_result(_result_to_dict(existing))
    query_names = []
    queries_data = existing.data
    if isinstance(queries_data, list):
        for q in queries_data:
            if isinstance(q, dict):
                n = q.get("name")
                if n:
                    query_names.append(n)
    if name in query_names:
        if not overwrite:
            return to_mcp_result({
                "success": False,
                "error": f"Query '{name}' already exists. Use overwrite=True to update.",
            })
        result = db.set_query_sql(name, sql, confirm=confirm)
        dict_result = _result_to_dict(result)
        dict_result["action"] = "updated"
        return to_mcp_result(dict_result)
    else:
        result = db.create_query(name, sql, confirm=confirm)
        dict_result = _result_to_dict(result)
        dict_result["action"] = "created"
        return to_mcp_result(dict_result)


@_close_per_call_sessions
def insert_data(path: str, table: str, data: dict[str, Any], confirm: bool = False) -> CallToolResult:
    """Insert a row into a table.

    Args:
        path: Absolute path to the .accdb or .mdb file.
        table: Target table name.
        data: Dictionary mapping column names to values.
        confirm: Must be True to insert. Defaults to False.
    """
    db = _new_db(readonly=False)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.insert_data(table, data, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def update_data(
    path: str,
    table: str,
    data: dict[str, Any],
    where: str | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Update rows in a table.

    Args:
        path: Absolute path to the .accdb or .mdb file.
        table: Target table name.
        data: Dictionary mapping column names to new values.
        where: WHERE clause WITHOUT the WHERE keyword (e.g. "id = 5").
               If None, updates all rows.
        confirm: Must be True to update. Defaults to False.
    """
    db = _new_db(readonly=False)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.update_data(table, data, where=where, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def delete_data(path: str, table: str, where: str | None = None, confirm: bool = False) -> CallToolResult:
    """Delete rows from a table.

    Args:
        path: Absolute path to the .accdb or .mdb file.
        table: Target table name.
        where: WHERE clause WITHOUT the WHERE keyword (e.g. "id = 5").
               If None, deletes all rows.
        confirm: Must be True to delete. Defaults to False.
    """
    db = _new_db(readonly=False)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.delete_data(table, where=where, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


@_close_per_call_sessions
def export_data(
    path: str,
    source: str,
    out: str,
    format: Literal["csv", "json", "xlsx"] = "csv",
    sheet_name: str = "Sheet1",
) -> CallToolResult:
    """Export table or query data to a file (supports csv, json, xlsx).

    Args:
        path: Absolute path to the .accdb or .mdb file.
        source: Name of the table or saved query to export.
        out: Output file path (must be within ACCESS_MCP_ALLOWED_DIRS).
        format: Export format — one of 'csv', 'json', 'xlsx'. Defaults to 'csv'.
        sheet_name: Sheet name for xlsx format. Defaults to 'Sheet1'.
    """
    db = _new_db(readonly=True)
    if (err := _open_or_err(db, path)) is not None:
        return err
    result = db.export_data(source, out, format=format, sheet_name=sheet_name)
    return to_mcp_result(_result_to_dict(result))


# =============================================================================
# Tool Registry
# =============================================================================

# Dispatch table: tool name -> (function, description)
_TOOLS: ToolRegistry = {
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
    "save_query": (save_query, "Save or update a saved query definition"),
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
    register_tools(mcp, _TOOLS)
