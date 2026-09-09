"""Tests for mcp_tools/versioning.py — MCP tool registry for versioning."""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp.types import TextContent


class TestVersioningTools:
    """Test versioning MCP tool registry and registration."""

    def test_tools_dict_has_expected_names(self):
        from ms_access_mcp.mcp_tools.versioning import _TOOLS

        expected = {
            "versioning_export_object",
            "versioning_export_all",
            "versioning_compare",
            "versioning_export_schema_ddl",
            "versioning_import_object",
            "versioning_import_all",
        }
        actual = set(_TOOLS.keys())
        assert expected.issubset(actual), f"Missing tools: {expected - actual}"

    def test_register_adds_all_tools(self):
        from ms_access_mcp.mcp_tools.versioning import register

        mock_mcp = MagicMock()
        register(mock_mcp)
        assert mock_mcp.add_tool.call_count == len(
            {"versioning_export_object", "versioning_export_all", "versioning_compare",
             "versioning_export_schema_ddl", "versioning_import_object", "versioning_import_all"}
        )

    def test_versioning_export_object_returns_call_tool_result(self, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_export_object

        out_dir = str(tmp_path / "out")
        result = versioning_export_object("form", "Form1", out_dir)
        assert hasattr(result, "content")
        block = result.content[0]
        assert isinstance(block, TextContent)

    def test_versioning_export_all_returns_call_tool_result(self, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_export_all

        out_dir = str(tmp_path / "out")
        result = versioning_export_all(out_dir)
        assert hasattr(result, "content")
        block = result.content[0]
        assert isinstance(block, TextContent)

    def test_versioning_compare_returns_call_tool_result(self, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_compare

        export_dir = str(tmp_path / "export")
        result = versioning_compare(export_dir)
        assert hasattr(result, "content")
        block = result.content[0]
        assert isinstance(block, TextContent)

    def test_versioning_export_schema_ddl_returns_call_tool_result(self, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_export_schema_ddl

        out_dir = str(tmp_path / "out")
        result = versioning_export_schema_ddl(out_dir)
        assert hasattr(result, "content")
        block = result.content[0]
        assert isinstance(block, TextContent)

    def test_versioning_import_object_confirm_required(self, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_import_object

        file_path = str(tmp_path / "form.txt")
        result = versioning_import_object("form", "Form1", file_path, confirm=False)
        block = result.content[0]
        assert isinstance(block, TextContent)
        import json
        data = json.loads(block.text)
        assert data["success"] is False
        assert "confirmation" in data["error"].lower()

    def test_versioning_import_all_confirm_required(self, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_import_all

        input_dir = str(tmp_path / "in")
        result = versioning_import_all(input_dir, confirm=False)
        block = result.content[0]
        assert isinstance(block, TextContent)
        import json
        data = json.loads(block.text)
        assert data["success"] is False
        assert "confirmation" in data["error"].lower()
