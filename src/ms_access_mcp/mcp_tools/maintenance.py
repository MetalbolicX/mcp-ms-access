"""MCP maintenance tools — backup, dev copy, compact/repair, diagnose.

recover_access is intentionally CLI-only; it would damage a multi-user Access
session and is exposed only via the maintenance CLI group.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from ..application.maintenance import MaintenanceUseCases
from ..safety import allowed_dirs_from_env
from ._registry import ToolRegistry, register_tools
from .core import _result_to_dict, to_mcp_result

__all__ = ["register", "to_mcp_result", "_TOOLS"]


def _new_maintenance(readonly: bool = True) -> MaintenanceUseCases:
    return MaintenanceUseCases(readonly=readonly, db_path=None, allowed_dirs=allowed_dirs_from_env())


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


def maintenance_discard_dev_copy(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Discard the active dev copy without deploying to production.

    Deletes the dev copy file and removes its manifest.
    Does NOT modify the production database.
    """
    uc = _new_maintenance(readonly=False)
    result = uc.discard_dev_copy(confirm=confirm)
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


def maintenance_repair_with_access_cli(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Repair via msaccess.exe /compact CLI (Rung 3 recovery).

    Runs ``msaccess.exe "<path>" /compact`` to repair a corrupt database.
    Requires Windows and Microsoft Access installed.
    Confirm gate required.
    """
    uc = _new_maintenance(readonly=False)
    result = uc.repair_with_access_cli(path, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def maintenance_salvage_database(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Salvage data from a corrupt .accdb (Rung 4 recovery).

    Renames the corrupt file to ``<name>.corrupt-<timestamp>.accdb``, creates
    a fresh .accdb, then copies tables row-by-row from the corrupt file.
    Per-table errors are recorded; the operation is not aborted on table loss.
    Confirm gate required.
    """
    uc = _new_maintenance(readonly=False)
    result = uc.salvage_database(path, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def convert_legacy_database(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Convert an Access 95/97 (Jet 3) .mdb file to Jet 4 format.

    Jet 3 files cannot be opened by ACE (the modern Access runtime).  This tool
    uses the 32-bit JRO.JetEngine CompactDatabase via a WOW64 script bridge
    to produce a Jet 4 .mdb that ACE can open.

    Parameters
    ----------
    path:
        Absolute path to the source Jet 3 .mdb file.
    params:
        Optional dict with ``dst`` key — absolute path for the converted file.
        If omitted, the output is written alongside the source as
        ``<name>.jet4.mdb``.
    """
    from pathlib import Path

    from ..backends.jet3_bridge import convert_jet3_to_jet4
    from ..safety import allowed_dirs_from_env, is_path_allowed

    src = path
    allowed = allowed_dirs_from_env()
    if not is_path_allowed(src, allowed):
        return to_mcp_result({"success": False, "error": f"Source path not allowed: {src}"})

    p = params or {}
    dst_param = p.get("dst")
    if dst_param:
        dst = dst_param
    else:
        src_path = Path(src)
        dst = str(src_path.with_name(src_path.stem + ".jet4.mdb"))

    if not is_path_allowed(dst, allowed):
        return to_mcp_result({"success": False, "error": f"Destination path not allowed: {dst}"})

    result = convert_jet3_to_jet4(src, dst)
    return to_mcp_result(_result_to_dict(result))


_TOOLS: ToolRegistry = {
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
    "maintenance_discard_dev_copy": (
        maintenance_discard_dev_copy,
        "Discard the active dev copy without deploying to production.",
    ),
    "maintenance_compact_repair": (
        maintenance_compact_repair,
        "Compact, repair, or compact-and-repair the database (destructive).",
    ),
    "maintenance_diagnose_environment": (
        maintenance_diagnose_environment,
        "Run environment diagnostics and return check results.",
    ),
    "maintenance_repair_with_access_cli": (
        maintenance_repair_with_access_cli,
        "Repair database via msaccess.exe /compact CLI (Rung 3, confirm required).",
    ),
    "maintenance_salvage_database": (
        maintenance_salvage_database,
        "Salvage data from a corrupt .accdb by copying tables to a fresh DB (Rung 4, confirm required).",
    ),
    "convert_legacy_database": (
        convert_legacy_database,
        "Convert an Access 95/97 (Jet 3) .mdb to Jet 4 format via 32-bit JRO bridge.",
    ),
}


def register(mcp: FastMCP) -> None:
    """Register all maintenance tools with the FastMCP server."""
    register_tools(mcp, _TOOLS)
