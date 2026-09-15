"""MCP versioning tools — export, import, compare, schema DDL."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from ..application.versioning import VersioningUseCases
from ._registry import ToolRegistry, register_tools
from .core import _result_to_dict, to_mcp_result

__all__ = ["register", "_TOOLS"]


def _new_versioning(readonly: bool = True) -> VersioningUseCases:
    return VersioningUseCases(readonly=readonly, db_path=None)


def versioning_export_object(kind: str, name: str, out_dir: str) -> CallToolResult:
    """Export a single object (form, report, macro, query, module) to a text file."""
    uc = _new_versioning(readonly=True)
    result = uc.export_object(kind, name, out_dir)
    return to_mcp_result(_result_to_dict(result))


def versioning_export_all(out_dir: str) -> CallToolResult:
    """Export all objects of all kinds to a directory tree."""
    uc = _new_versioning(readonly=True)
    result = uc.export_all(out_dir)
    return to_mcp_result(_result_to_dict(result))


def versioning_compare(export_dir: str) -> CallToolResult:
    """Compare exported objects against current database state."""
    uc = _new_versioning(readonly=True)
    result = uc.compare(export_dir)
    return to_mcp_result(_result_to_dict(result))


def versioning_export_schema_ddl(out_dir: str) -> CallToolResult:
    """Export table and relationship DDL to files."""
    uc = _new_versioning(readonly=True)
    result = uc.export_schema_ddl(out_dir)
    return to_mcp_result(_result_to_dict(result))


def versioning_import_object(kind: str, name: str, file_path: str, confirm: bool = False) -> CallToolResult:
    """Import an object from a text file (destructive). Requires confirm=True."""
    uc = _new_versioning(readonly=False)
    result = uc.import_object(kind, name, file_path, confirm)
    return to_mcp_result(_result_to_dict(result))


def versioning_import_all(input_dir: str, confirm: bool = False) -> CallToolResult:
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


def register(mcp: FastMCP) -> None:
    """Register all versioning tools with the FastMCP server."""
    register_tools(mcp, _TOOLS)
