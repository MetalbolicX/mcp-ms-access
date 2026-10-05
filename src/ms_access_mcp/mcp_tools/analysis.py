"""MCP Analysis tools — ER diagram, query analysis, SQL generation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from contextvars import ContextVar
from functools import wraps
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from ..application.analysis import QueryAnalyzerService
from ..application.database import DatabaseUseCases
from ..safety import allowed_dirs_from_env
from ._registry import ToolRegistry, register_tools
from ._session_registry import SessionOpenError, registry
from .core import _result_to_dict, to_mcp_result

__all__ = ["register", "_TOOLS"]


class _ToolSession:
    def __init__(self, path: str) -> None:
        self.path = path
        self.stack = ExitStack()
        self.database: DatabaseUseCases | None = None

    def open(self, *, readonly: bool):
        if self.database is None:
            self.database = self.stack.enter_context(
                registry.open_session(
                    self.path, readonly=readonly, allowed_dirs=allowed_dirs_from_env()
                )
            )
        return self.database


_active_session: ContextVar[_ToolSession | None] = ContextVar("analysis_tool_session", default=None)


def _connected_tool(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(path: str, *args: Any, **kwargs: Any) -> Any:
        session = _ToolSession(path)
        token = _active_session.set(session)
        try:
            return function(path, *args, **kwargs)
        except SessionOpenError as exc:
            return to_mcp_result(_result_to_dict(exc.result))
        finally:
            _active_session.reset(token)
            session.stack.close()

    return wrapped


def _new_db(readonly: bool = True) -> DatabaseUseCases:
    """Return this tool call's opened database session when one is active."""
    session = _active_session.get()
    if session is not None:
        return session.open(readonly=readonly)
    return registry.track(DatabaseUseCases(readonly=readonly, allowed_dirs=allowed_dirs_from_env()))


# =============================================================================
# ER Diagram Tool
# =============================================================================


def get_er_diagram(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Get the database schema as nodes and edges for ER diagram visualization.

    Returns tables as nodes with columns, record counts, and relationships as edges.
    Read-only operation — no confirm required.
    """
    from ..errors import Err

    uc = _new_db(readonly=True)
    result = uc.get_er_diagram()
    if isinstance(result, Err):
        return to_mcp_result({"success": False, "error": result.message})

    data = result.data
    return to_mcp_result({
        "success": True,
        "nodes": data.get("nodes", []),
        "edges": data.get("edges", []),
        "node_count": data.get("node_count", 0),
        "edge_count": data.get("edge_count", 0),
    })


# =============================================================================
# Query Analysis Tool
# =============================================================================


def analyze_query(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Analyze query performance and return complexity, timing, and schema analysis.

    Args:
        params: dict with keys:
            - sql: SQL statement to analyze
            - params: Optional list of query parameters
            - dry_run: If True, skip execution (structural analysis only)
            - sample_size: Number of rows to sample (0 = full execution)
            - connection_name: Connection name (default "default")
    """
    p = params or {}
    sql = p.get("sql", "")
    query_params = p.get("params")
    dry_run = p.get("dry_run", False)
    sample_size = p.get("sample_size", 0)
    _connection_name = p.get("connection_name", "default")

    if not sql:
        return to_mcp_result({"success": False, "error": "sql parameter is required"})

    uc = _new_db(readonly=True)
    result = QueryAnalyzerService.analyze(
        sql=sql,
        params=query_params,
        db_use_cases=uc,
        dry_run=dry_run,
        sample_size=sample_size,
    )
    return to_mcp_result(result)


# =============================================================================
# Generate SQL Tool
# =============================================================================


def generate_sql(path: str, params: dict[str, Any] | None = None, confirm: bool = False) -> CallToolResult:
    """Generate SQL DDL script for the database schema.

    Args:
        params: dict with keys:
            - output_path: Path to write the SQL script (required)
            - tables: Optional list of table names to generate (all if empty)
            - connection_name: Connection name (default "default")
    """
    p = params or {}
    output_path = p.get("output_path", "")

    if not output_path:
        return to_mcp_result({"success": False, "error": "output_path is required"})

    uc = _new_db(readonly=True)
    connection_name = p.get("connection_name", "default")

    result = uc.generate_sql(output_path=output_path, connection_name=connection_name)
    return to_mcp_result(_result_to_dict(result))


# =============================================================================
# Tool Registry
# =============================================================================


_TOOLS: ToolRegistry = {
    "get_er_diagram": (
        get_er_diagram,
        "Get the database schema as nodes and edges for ER diagram visualization. "
        "Returns tables as nodes with columns, record counts, and relationships as edges.",
    ),
    "analyze_query": (
        analyze_query,
        "Analyze query performance and return complexity, timing, and schema analysis. "
        "Supports structural analysis (dry_run) and execution timing.",
    ),
    "generate_sql": (
        generate_sql,
        "Generate SQL DDL script for the database schema. "
        "Writes ALTER/CREATE statements to the specified output path.",
    ),
}

for _tool_name, (_tool, _description) in tuple(_TOOLS.items()):
    _wrapped_tool = _connected_tool(_tool)
    _TOOLS[_tool_name] = (_wrapped_tool, _description)
    globals()[_tool.__name__] = _wrapped_tool


def register(mcp: FastMCP) -> None:
    """Register all analysis tools with the MCP server."""
    register_tools(mcp, _TOOLS)
