"""MCP versioning tools — export, import, compare, schema DDL."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from contextlib import ExitStack
from contextvars import ContextVar
from functools import wraps
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from ..application.versioning import VersioningUseCases
from ..safety import allowed_dirs_from_env
from ._registry import ToolRegistry, register_tools
from ._session_registry import SessionOpenError, registry
from .core import _result_to_dict, to_mcp_result

__all__ = ["register", "_TOOLS"]


class _ToolSession:
    def __init__(self, path: str) -> None:
        self.path = path
        self.stack = ExitStack()
        self.database = None

    def open(self):
        if self.database is None:
            self.database = self.stack.enter_context(
                registry.open_session(
                    self.path,
                    backend="com",
                    readonly=True,
                    allowed_dirs=allowed_dirs_from_env(),
                )
            )
        return self.database


_active_session: ContextVar[_ToolSession | None] = ContextVar("versioning_tool_session", default=None)


def _connected_tool(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(path: str, *args: Any, **kwargs: Any) -> Any:
        confirm = inspect.signature(function).bind_partial(path, *args, **kwargs).arguments.get("confirm", False)
        if function.__name__ in {"versioning_import_object", "versioning_import_all"} and not confirm:
            return function(path, *args, **kwargs)
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


def _new_versioning(readonly: bool = True) -> VersioningUseCases:
    session = _active_session.get()
    if session is not None:
        database = session.open()
        return VersioningUseCases(
            adapter=database._get_adapter(),  # noqa: SLF001 - the registry-owned DB session manages this adapter
            readonly=readonly,
            db_path=session.path,
            allowed_dirs=allowed_dirs_from_env(),
        )
    return registry.track(VersioningUseCases(readonly=readonly, db_path=None, allowed_dirs=allowed_dirs_from_env()))


def versioning_export_object(path: str, kind: str, name: str, out_dir: str) -> CallToolResult:
    """Export a single object (form, report, macro, query, module) to a text file."""
    uc = _new_versioning(readonly=True)
    result = uc.export_object(kind, name, out_dir)
    return to_mcp_result(_result_to_dict(result))


def versioning_export_all(path: str, out_dir: str) -> CallToolResult:
    """Export all objects of all kinds to a directory tree."""
    uc = _new_versioning(readonly=True)
    result = uc.export_all(out_dir)
    return to_mcp_result(_result_to_dict(result))


def versioning_compare(path: str, export_dir: str) -> CallToolResult:
    """Compare exported objects against current database state."""
    uc = _new_versioning(readonly=True)
    result = uc.compare(export_dir)
    return to_mcp_result(_result_to_dict(result))


def versioning_export_schema_ddl(path: str, out_dir: str) -> CallToolResult:
    """Export table and relationship DDL to files."""
    uc = _new_versioning(readonly=True)
    result = uc.export_schema_ddl(out_dir)
    return to_mcp_result(_result_to_dict(result))


def versioning_import_object(path: str, kind: str, name: str, file_path: str, confirm: bool = False) -> CallToolResult:
    """Import an object from a text file (destructive). Requires confirm=True."""
    uc = _new_versioning(readonly=False)
    result = uc.import_object(kind, name, file_path, confirm)
    return to_mcp_result(_result_to_dict(result))


def versioning_import_all(path: str, input_dir: str, confirm: bool = False) -> CallToolResult:
    """Import all objects from a directory tree (destructive). Requires confirm=True."""
    uc = _new_versioning(readonly=False)
    result = uc.import_all(input_dir, confirm)
    return to_mcp_result(_result_to_dict(result))


_TOOLS: ToolRegistry = {
    "versioning_export_object": (
        versioning_export_object,
        "Export a single object to a text file.",
    ),
    "versioning_export_all": (
        versioning_export_all,
        "Export all objects to a directory tree.",
    ),
    "versioning_compare": (
        versioning_compare,
        "Compare exported objects against current database state.",
    ),
    "versioning_export_schema_ddl": (
        versioning_export_schema_ddl,
        "Export table and relationship DDL.",
    ),
    "versioning_import_object": (
        versioning_import_object,
        "Import an object from a text file (destructive).",
    ),
    "versioning_import_all": (
        versioning_import_all,
        "Import all objects from a directory tree (destructive).",
    ),
}

for _tool_name, (_tool, _description) in tuple(_TOOLS.items()):
    _wrapped_tool = _connected_tool(_tool)
    _TOOLS[_tool_name] = (_wrapped_tool, _description)
    globals()[_tool.__name__] = _wrapped_tool


def register(mcp: FastMCP) -> None:
    """Register all versioning tools with the FastMCP server."""
    register_tools(mcp, _TOOLS)
