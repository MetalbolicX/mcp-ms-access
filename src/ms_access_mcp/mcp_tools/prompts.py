"""Profile-independent MCP prompts for common Access database workflows."""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import Prompt
from mcp.server.fastmcp.prompts.base import PromptArgument

PromptFn = Callable[..., str]


def _analyze_database(path: str) -> str:
    return f"""Analyze the Access database at `{path}`.

Use `connect_access` to open it (requires the core profile). For a protected or remote database, never pass a plaintext password as a tool argument: configure it with `ms-access-mcp credentials set` or `ms-access-mcp credentials profile set`, then use the resulting connection_ref.

1. Inventory tables with `get_tables`; inspect relevant tables with `get_table_schema` and `get_indexes`.
2. Map relations with `get_relationships`; review aggregate size and object counts with `get_database_statistics`.
3. For diagrams or query analysis, use the analysis profile (`--profile analysis`): `get_er_diagram` and `analyze_query`.
4. Summarize the schema, relationships, likely normalization issues, and index opportunities. Ask before any write; all write operations require `confirm=True` and writes must be enabled explicitly.

The server is readonly by default. No remote MCP transport is supported."""


def _develop_new_database(path: str) -> str:
    return f"""Develop a new Access database at `{path}`.

1. Create the file with `create_access_database` (ddl profile; `--profile ddl`). Plan the schema before making changes.
2. Create tables with `create_table` or execute a reviewed DDL script with `execute_sql_script`; add `create_index` and `create_relationship` as justified by the design. These are write operations: pass `confirm=True` and enable writes.
3. Optionally validate seed data with `export_data` after loading it through approved means.
4. Before destructive changes, make a recoverable copy with `maintenance_backup` (maintenance profile; `--profile maintenance`).

The server is readonly by default; enable writes through the server's write policy and provide `confirm=True` on each write. For protected or remote databases, configure secrets with `ms-access-mcp credentials set` or `ms-access-mcp credentials profile set` and use connection_ref; never pass plaintext passwords as tool arguments. No remote MCP transport is supported."""


def _migrate_database(path: str, target: str) -> str:
    return f"""Migrate the Access database at `{path}` to {target}.

1. Extract the source model with `extract_schema` (migration profile; `--profile migration`), providing the source path and any supported schema filters.
2. Review and adjust the schema model before transfer. The typed migration parameters use schema_json, transfer_mode, verification_mode, and table_overrides as applicable; inspect the tool schema and pass the validated shape.
3. Upload the reviewed model with `upload_schema`, then transfer rows with `transfer_data`.
4. Poll `get_migration_status` and verify the reported counts and errors against the source.

Use a configured connection_ref for target credentials, never inline passwords. Configure credentials with `ms-access-mcp credentials set` or `ms-access-mcp credentials profile set`. PostgreSQL and MySQL targets require the migrate extra and their respective drivers. Ask before writes; write operations require `confirm=True` and explicit write enablement. No remote MCP transport is supported."""


_PROMPTS: dict[str, tuple[str, PromptFn, list[PromptArgument]]] = {
    "analyze_database": (
        "Analyze an Access database schema and identify improvement opportunities.",
        _analyze_database,
        [PromptArgument(name="path", description="Access database path", required=True)],
    ),
    "develop_new_database": (
        "Plan and build a new Access database safely.",
        _develop_new_database,
        [PromptArgument(name="path", description="New Access database path", required=True)],
    ),
    "migrate_database": (
        "Guide a database migration from Access to a target platform.",
        _migrate_database,
        [
            PromptArgument(name="path", description="Source Access database path", required=True),
            PromptArgument(name="target", description="Migration target platform", required=True),
        ],
    ),
}


def register_prompts(mcp: FastMCP) -> None:
    """Register all workflow prompts, independent of the selected tool profile."""
    for name, (description, fn, arguments) in _PROMPTS.items():
        mcp.add_prompt(Prompt(name=name, description=description, fn=fn, arguments=arguments))
