"""Tests for the profile-independent workflow prompts."""

import asyncio
import re

from ms_access_mcp.server import build_server

EXPECTED = {
    "analyze_database": {"get_tables", "get_table_schema", "get_relationships", "get_indexes", "get_database_statistics", "get_er_diagram", "analyze_query"},
    "develop_new_database": {"create_access_database", "create_table", "execute_sql_script", "create_index", "create_relationship", "export_data", "maintenance_backup"},
    "migrate_database": {"extract_schema", "upload_schema", "transfer_data", "get_migration_status"},
    "document_database": {"documentation_inspect", "documentation_render", "documentation_save"},
    "generate_er_documentation": {"documentation_inspect", "documentation_render", "documentation_save"},
}


async def _render(server, name: str, arguments: dict[str, str]) -> str:
    result = await server.get_prompt(name, arguments)
    assert len(result.messages) == 1
    return result.messages[0].content.text


def test_workflow_prompts_are_identical_for_core_and_full_profiles():
    servers = [build_server(profile) for profile in ("core", "full")]
    names = [{prompt.name for prompt in asyncio.run(server.list_prompts())} for server in servers]
    assert names == [set(EXPECTED), set(EXPECTED)]


def test_workflow_prompt_content_uses_live_tools_and_safe_credentials():
    server = build_server("full")
    tool_names = {tool.name for tool in asyncio.run(server.list_tools())}
    args = {
        "analyze_database": {"path": "C:/data/analyze.accdb"},
        "develop_new_database": {"path": "C:/data/new.accdb"},
        "migrate_database": {"path": "C:/data/source.accdb", "target": "postgres"},
        "document_database": {"path": "C:/data/document.accdb"},
        "generate_er_documentation": {"path": "C:/data/er.accdb"},
    }
    for name, expected_tools in EXPECTED.items():
        text = asyncio.run(_render(server, name, args[name]))
        assert args[name]["path"] in text
        assert "credentials set" in text or "credentials profile set" in text
        referenced_tools = set(re.findall(r"`([a-z][a-z0-9_]+)`", text))
        assert referenced_tools <= tool_names
        assert expected_tools <= referenced_tools
    migrate = asyncio.run(_render(server, "migrate_database", args["migrate_database"]))
    assert "postgres" in migrate
    assert "connection_ref" in migrate
