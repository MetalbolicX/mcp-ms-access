"""Integration tests for ADO backend — direct ACE OLE DB connection."""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.skipif(
        __import__("os").name != "nt",
        reason="Windows-only test",
    ),
    pytest.mark.com_integration,
    pytest.mark.odbc_integration,
]


class TestAdoAdapterConnection:
    """Test ADO adapter basic connection and teardown."""

    def test_ado_adapter_not_connected_initially(self):
        from ms_access_mcp.backends.ado import AdoAdapter
        adapter = AdoAdapter("test.accdb")
        assert adapter.is_connected() is False

    def test_disconnect_when_not_connected(self):
        from ms_access_mcp.backends.ado import AdoAdapter
        adapter = AdoAdapter("test.accdb")
        adapter.disconnect()  # should not raise


class TestAdoAdapterScriptParsing:
    """Test SQL script parsing for ADO execute_script."""

    def test_parse_script_lines_basic(self):
        from ms_access_mcp.backends.ado import _parse_script_lines
        script = "SELECT 1; SELECT 2; SELECT 3"
        result = _parse_script_lines(script)
        assert result == ["SELECT 1", "SELECT 2", "SELECT 3"]

    def test_parse_script_lines_strips_whitespace(self):
        from ms_access_mcp.backends.ado import _parse_script_lines
        script = "  SELECT 1  ;  SELECT 2  "
        result = _parse_script_lines(script)
        assert result == ["SELECT 1", "SELECT 2"]

    def test_parse_script_lines_filters_empty(self):
        from ms_access_mcp.backends.ado import _parse_script_lines
        script = "SELECT 1; ; SELECT 2"
        result = _parse_script_lines(script)
        assert result == ["SELECT 1", "SELECT 2"]

    def test_parse_script_lines_removes_comments(self):
        from ms_access_mcp.backends.ado import _parse_script_lines
        script = "SELECT 1; -- this is a comment\nSELECT 2"
        result = _parse_script_lines(script)
        assert result == ["SELECT 1", "SELECT 2"]

    def test_parse_script_lines_empty_script(self):
        from ms_access_mcp.backends.ado import _parse_script_lines
        assert _parse_script_lines("") == []
        assert _parse_script_lines(";") == []
        assert _parse_script_lines("  ") == []
