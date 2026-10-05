"""Tests for mcp_tools/versioning.py — MCP tool registry for versioning."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from mcp.types import TextContent


def _payload(result):
    block = result.content[0]
    assert isinstance(block, TextContent)
    return json.loads(block.text)


def _connected_adapter(monkeypatch, tmp_path):
    from ms_access_mcp.backends import BackendSelector

    class FakeAdapter:
        connected = False

        def connect(self, path):
            self.connected = True
            self.connected_path = path

        def disconnect(self):
            self.connected = False

        def export_form_to_text(self, _name):
            return "form text"

        def export_all_versioning(self, out_dir):
            return {"output_dir": out_dir, "file_count": 0, "exported": {}}

        def compare_versioning(self, _export_dir):
            return {"new": [], "missing": [], "changed": [], "unchanged": []}

        def export_schema_ddl(self, _out_dir):
            return {"ddl_tables": "", "ddl_relationships": "", "tables_exported": 0, "relationships_exported": 0}

    adapter = FakeAdapter()
    monkeypatch.setattr(BackendSelector, "get_adapter", lambda **_kwargs: adapter)
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    path = tmp_path / "db.accdb"
    path.write_bytes(b"fake database")
    return str(path), adapter


class TestVersioningConnectedPath:
    def test_export_object_dispatches_through_connected_adapter(self, monkeypatch, tmp_path):
        from ms_access_mcp.backends import BackendSelector
        from ms_access_mcp.mcp_tools import versioning

        class FakeAdapter:
            connected = False

            def connect(self, path):
                self.connected = True
                self.connected_path = path

            def disconnect(self):
                self.connected = False

            def export_form_to_text(self, name):
                self.connected_at_dispatch = self.connected
                self.exported_name = name
                return "form text"

        adapter = FakeAdapter()
        monkeypatch.setattr(BackendSelector, "get_adapter", lambda **_kwargs: adapter)
        monkeypatch.setattr(versioning, "allowed_dirs_from_env", lambda: [tmp_path])
        path = str(tmp_path / "db.accdb")
        (tmp_path / "db.accdb").write_bytes(b"fake database")

        result = versioning.versioning_export_object(
            path=path, kind="form", name="Main", out_dir=str(tmp_path / "exports")
        )

        assert result.isError is False
        assert _payload(result)["success"] is True
        assert adapter.connected_at_dispatch is True
        assert adapter.connected is False  # per-call session closes after dispatch
        assert adapter.connected_path == path
        assert adapter.exported_name == "Main"


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

    def test_versioning_export_object_returns_call_tool_result(self, monkeypatch, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_export_object

        path, adapter = _connected_adapter(monkeypatch, tmp_path)
        result = versioning_export_object(path, "form", "Form1", str(tmp_path / "out"))
        assert _payload(result)["success"] is True
        assert adapter.connected is False

    def test_versioning_export_all_returns_call_tool_result(self, monkeypatch, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_export_all

        path, _adapter = _connected_adapter(monkeypatch, tmp_path)
        result = versioning_export_all(path, str(tmp_path / "out"))
        assert _payload(result)["success"] is True

    def test_versioning_compare_returns_call_tool_result(self, monkeypatch, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_compare

        path, _adapter = _connected_adapter(monkeypatch, tmp_path)
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        result = versioning_compare(path, str(export_dir))
        assert _payload(result)["success"] is True

    def test_versioning_export_schema_ddl_returns_call_tool_result(self, monkeypatch, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_export_schema_ddl

        path, _adapter = _connected_adapter(monkeypatch, tmp_path)
        result = versioning_export_schema_ddl(path, str(tmp_path / "out"))
        assert _payload(result)["success"] is True

    def test_versioning_import_object_confirm_required(self, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_import_object

        file_path = str(tmp_path / "form.txt")
        result = versioning_import_object("dummy.mdb", "form", "Form1", file_path, confirm=False)
        data = _payload(result)
        assert data["success"] is False
        assert "confirmation" in data["error"].lower()

    def test_versioning_import_all_confirm_required(self, tmp_path):
        from ms_access_mcp.mcp_tools.versioning import versioning_import_all

        input_dir = str(tmp_path / "in")
        result = versioning_import_all("dummy.mdb", input_dir, confirm=False)
        data = _payload(result)
        assert data["success"] is False
        assert "confirmation" in data["error"].lower()
