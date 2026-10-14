"""MCP Analysis tools — ER diagram, query analysis, SQL generation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from contextvars import ContextVar
from functools import wraps
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from ..application.analysis import QueryAnalyzerService
from ..application.database import DatabaseUseCases, Err
from ..application.documentation import EvidenceDoc
from ..application.documentation_inspect import (
    DocumentationInspectService,
    OdbcInspectionPort,
    open_inspection_port,
)
from ..application.documentation_render import ReportDoc, render_document
from ..application.documentation_save import DocumentationSaveService, DocumentationWritePolicy
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
        connection_name=_connection_name,
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
# Documentation Tools
# =============================================================================


def documentation_inspect(
    path: str,
    tables: list[str] | None = None,
    queries: list[str] | None = None,
    connection_ref: str | None = None,
) -> CallToolResult:
    """Inspect Access metadata into versioned documentation evidence."""
    del connection_ref  # Credentials are resolved by the existing path/session policy.
    try:
        database = _new_db(readonly=True)
        opened = database.open_database(path)
        if isinstance(opened, Err):
            return to_mcp_result({"success": False, "error": opened.message})
        adapter = getattr(database, "_adapter", None)
        dispatcher = getattr(adapter, "_dispatcher", None)
        if dispatcher is not None and dispatcher.is_connected():
            port = open_inspection_port(dispatcher)

            def leave_session_open() -> None:
                return None

            port.close = leave_session_open  # The session owns and closes this handle.
            evidence = dispatcher.call(
                lambda: DocumentationInspectService().inspect(port, tables=tables, queries=queries)
            )
        else:
            connection = getattr(adapter, "connection", None)
            if connection is None:
                raise ValueError("Documentation inspection requires a connected DAO or ODBC backend")
            evidence = DocumentationInspectService().inspect(
                OdbcInspectionPort(connection), tables=tables, queries=queries
            )
        data = evidence.to_dict()
        data["status"] = evidence.status
        return to_mcp_result({"success": True, "data": data})
    except Exception as exc:
        return to_mcp_result({"success": False, "error": str(exc)})


def documentation_render(
    evidence: dict[str, Any],
    kind: Literal["database", "er"] = "database",
    language: str = "en",
    interpretations: list[dict[str, str]] | None = None,
) -> CallToolResult:
    """Render documentation evidence deterministically without database I/O."""
    try:
        rendered = render_document(
            EvidenceDoc.from_dict(evidence), kind=kind, language=language, interpretations=interpretations
        )
        return to_mcp_result({"success": True, "data": rendered.to_dict()})
    except Exception as exc:
        return to_mcp_result({"success": False, "error": str(exc)})


def documentation_save(
    report: dict[str, Any],
    output_path: str,
    confirm: bool,
    overwrite: bool = False,
    expected_fingerprint: str | None = None,
) -> CallToolResult:
    """Save a rendered report under separate operator and per-call authorization."""
    import os

    allowed = os.environ.get("ACCESS_MCP_ALLOW_DOC_WRITE", "").casefold() in {"1", "true"}
    if not allowed:
        return to_mcp_result({"success": False, "error": "Documentation save permission denied: operator doc-write is not enabled."})
    if not confirm:
        return to_mcp_result({"success": False, "error": "Documentation save requires explicit confirm=True."})
    try:
        document = ReportDoc.from_dict(report)
        result = DocumentationSaveService(DocumentationWritePolicy(allowed=allowed)).save(
            document.markdown,
            output_path,
            confirm=confirm,
            overwrite=overwrite,
            expected_fingerprint=expected_fingerprint,
        )
        return to_mcp_result({"success": True, "data": result.__dict__})
    except Exception as exc:
        return to_mcp_result({"success": False, "error": str(exc)})


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

_DOCUMENTATION_TOOLS: ToolRegistry = {
    "documentation_inspect": (documentation_inspect, "Inspect Access metadata into documentation evidence."),
    "documentation_render": (documentation_render, "Render documentation evidence as Markdown."),
    "documentation_save": (documentation_save, "Save a rendered report with explicit authorization."),
}


def register(mcp: FastMCP) -> None:
    """Register all analysis tools with the MCP server."""
    register_tools(mcp, {**_TOOLS, **_DOCUMENTATION_TOOLS})
