"""MCP maintenance tools — backup, dev copy, compact/repair, diagnose.

recover_access is intentionally CLI-only; it would damage a multi-user Access
session and is exposed only via the maintenance CLI group.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from ..application.maintenance import MaintenanceUseCases
from .core import _result_to_dict, to_mcp_result

__all__ = ["register", "to_mcp_result", "_TOOLS"]


def _new_maintenance(readonly: bool = True) -> MaintenanceUseCases:
    return MaintenanceUseCases(readonly=readonly, db_path=None)


def maintenance_backup(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Create a timestamped backup of the database file."""
    retention = (params or {}).get("retention")
    uc = _new_maintenance(readonly=True)
    result = uc.backup(path, retention=retention)
    return to_mcp_result(_result_to_dict(result))


def maintenance_dev_copy_status(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Check whether an active dev copy exists and return its manifest."""
    uc = _new_maintenance(readonly=True)
    result = uc.dev_copy_status()
    return to_mcp_result(_result_to_dict(result))


def maintenance_dev_copy_create(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Create a development copy of the production database."""
    source = (params or {}).get("source")
    uc = _new_maintenance(readonly=True)
    result = uc.create_dev_copy(source=source)
    return to_mcp_result(_result_to_dict(result))


def maintenance_dev_copy_deploy(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Deploy the dev copy over the production database (destructive)."""
    p = params or {}
    dev_path = p.get("dev_path", "")
    target = p.get("target", "")
    uc = _new_maintenance(readonly=False)
    result = uc.deploy_dev_copy(dev_path, target, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def maintenance_compact_repair(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Compact, repair, or compact-and-repair the database (destructive)."""
    p = params or {}
    mode = p.get("mode", "compact")
    uc = _new_maintenance(readonly=False)
    result = uc.compact_repair(path, mode, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def maintenance_diagnose_environment(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Run environment diagnostics and return check results."""
    uc = _new_maintenance(readonly=True)
    result = uc.diagnose_environment()
    return to_mcp_result(_result_to_dict(result))


_TOOLS: dict[str, tuple[Any, str]] = {
    "maintenance_backup": (
        maintenance_backup,
        "Create a timestamped backup of the database file.",
    ),
    "maintenance_dev_copy_status": (
        maintenance_dev_copy_status,
        "Check whether an active dev copy exists and return its manifest.",
    ),
    "maintenance_dev_copy_create": (
        maintenance_dev_copy_create,
        "Create a development copy of the production database.",
    ),
    "maintenance_dev_copy_deploy": (
        maintenance_dev_copy_deploy,
        "Deploy the dev copy over the production database (destructive).",
    ),
    "maintenance_compact_repair": (
        maintenance_compact_repair,
        "Compact, repair, or compact-and-repair the database (destructive).",
    ),
    "maintenance_diagnose_environment": (
        maintenance_diagnose_environment,
        "Run environment diagnostics and return check results.",
    ),
}


def register(mcp: FastMCP) -> None:
    """Register all maintenance tools with the FastMCP server."""
    for name, (fn, description) in _TOOLS.items():
        mcp.add_tool(fn, name=name, description=description)
