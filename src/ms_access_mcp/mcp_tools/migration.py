"""MCP migration tools — extract_schema, upload_schema, transfer_data, get_migration_status.

Thin wrappers following the pattern in mcp_tools/versioning.py.
No business logic in these wrappers — they delegate to MigrationUseCases.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import ValidationError

from ..application.migration import MigrationUseCases
from ..models.migration import (
    ExtractSchemaParams,
    GetMigrationStatusParams,
    TransferDataParams,
    UploadSchemaParams,
)
from ..safety import allowed_dirs_from_env
from ._registry import ToolRegistry, register_tools
from ._session_registry import registry
from .core import _result_to_dict, to_mcp_result

__all__ = ["register", "_TOOLS"]


def _new_migration() -> MigrationUseCases:
    """Construct a MigrationUseCases instance."""
    return registry.track(MigrationUseCases(allowed_dirs=allowed_dirs_from_env()))


# =============================================================================
# Migration Tools (thin wrappers, no business logic)
# =============================================================================


def extract_schema(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Extract schema from an Access database for migration to a target dialect.

    Args:
        path: Path to the Access .accdb file.
        params: Dict with 'target_type' (postgres, mysql, sqlite, sqlserver).
        confirm: Not used for extract (read-only operation).

    Returns:
        CallToolResult with ExtractedSchema dict on success.

    Note:
        The connection_string in error messages is sanitized — credentials
        are never echoed to output.
    """
    uc = _new_migration()
    try:
        parsed = ExtractSchemaParams.model_validate(params or {})
    except ValidationError as e:
        return to_mcp_result({"success": False, "error": f"Invalid params: {e}"})

    if not parsed.target_type:
        dict_result = {"success": False, "error": "target_type is required"}
        return to_mcp_result(dict_result)

    result = uc.extract_schema(path, parsed.target_type)
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def upload_schema(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Upload schema to a target database (requires confirm=True).

    Args:
        path: Path to the Access .accdb file (unused for upload, but required
              for MCP tool signature consistency).
        params: Dict with target_type, connection_string or connection_ref,
                and schema_json.
        confirm: Must be True to execute DDL.

    Returns:
        CallToolResult with {tables_created, tables_failed} on success.

    Note:
        Credentials resolve server-side from named profiles. If the connection
        fails with an auth error, ask the user to run
        ``ms-access-mcp credentials profile set --name <name>`` — never
        request or transmit the connection string.
    """
    uc = _new_migration()
    try:
        parsed = UploadSchemaParams.model_validate(params or {})
    except ValidationError as e:
        return to_mcp_result({"success": False, "error": f"Invalid params: {e}"})

    if not parsed.target_type:
        dict_result = {"success": False, "error": "target_type is required"}
        return to_mcp_result(dict_result)
    if not parsed.connection_string and not parsed.connection_ref:
        dict_result = {"success": False, "error": "connection_string is required"}
        return to_mcp_result(dict_result)

    result = uc.upload_schema(
        target_type=parsed.target_type,
        connection_string=parsed.connection_string,
        connection_ref=parsed.connection_ref,
        schema_json=parsed.schema_json,
        confirm=confirm,
    )
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def transfer_data(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Transfer data from Access to target database (requires confirm=True).

    Args:
        path: Path to the Access .accdb file.
        params: Dict with target_type, connection_string or connection_ref,
                schema_json, transfer_mode, verification_mode, table_overrides.
        confirm: Must be True to execute transfer.

    Returns:
        CallToolResult with {success, job_id} on completion.

    Note:
        Credentials resolve server-side from named profiles. If the connection
        fails with an auth error, ask the user to run
        ``ms-access-mcp credentials profile set --name <name>`` — never
        request or transmit the connection string.
    """
    uc = _new_migration()
    try:
        parsed = TransferDataParams.model_validate(params or {})
    except ValidationError as e:
        return to_mcp_result({"success": False, "error": f"Invalid params: {e}"})

    if not parsed.target_type:
        dict_result = {"success": False, "error": "target_type is required"}
        return to_mcp_result(dict_result)
    if not parsed.connection_string and not parsed.connection_ref:
        dict_result = {"success": False, "error": "connection_string is required"}
        return to_mcp_result(dict_result)

    result = uc.transfer_data(
        target_type=parsed.target_type,
        connection_string=parsed.connection_string,
        connection_ref=parsed.connection_ref,
        database_path=path,
        schema_json=parsed.schema_json,
        transfer_mode=parsed.transfer_mode,
        verification_mode=parsed.verification_mode,
        table_overrides=parsed.table_overrides,
        confirm=confirm,
    )
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


def get_migration_status(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get the status of a migration job.

    Args:
        path: Unused (present for MCP tool signature consistency).
        params: Dict with job_id.
        confirm: Not used for status lookup (read-only operation).

    Returns:
        CallToolResult with MigrationJob dict on success.
    """
    uc = _new_migration()
    try:
        parsed = GetMigrationStatusParams.model_validate(params or {})
    except ValidationError as e:
        return to_mcp_result({"success": False, "error": f"Invalid params: {e}"})

    if not parsed.job_id:
        dict_result = {"success": False, "error": "job_id is required"}
        return to_mcp_result(dict_result)

    result = uc.get_migration_status(parsed.job_id)
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)


# =============================================================================
# Registry
# =============================================================================

_TOOLS: ToolRegistry = {
    "extract_schema": (
        extract_schema,
        "Extract schema from an Access database for migration.",
    ),
    "upload_schema": (
        upload_schema,
        "Upload schema to a target database. Requires confirm=True.",
    ),
    "transfer_data": (
        transfer_data,
        "Transfer data from Access to target database. Requires confirm=True.",
    ),
    "get_migration_status": (
        get_migration_status,
        "Get the status of a migration job.",
    ),
}


def register(mcp: FastMCP) -> None:
    """Register all migration tools with the FastMCP server.

    Args:
        mcp: FastMCP server instance.
    """
    register_tools(mcp, _TOOLS)
