"""Tests for mcp_tools/core.py — verbatim tool names, isError mapping, execute_raw_sql gate."""

from __future__ import annotations

import json

from ms_access_mcp.mcp_tools.core import (
    _TOOLS,
    to_mcp_result,
)


class TestToMcpResult:
    """Adapter: result dict -> (isError, content) tuple."""

    def test_success_false_becomes_is_error_true(self):
        """{success: false, error} → (True, json_stringified)."""
        result = {"success": False, "error": "connection failed"}
        is_error, content = to_mcp_result(result)
        assert is_error is True
        parsed = json.loads(content)
        assert parsed["success"] is False
        assert parsed["error"] == "connection failed"

    def test_success_true_becomes_is_error_false(self):
        """{success: true, ...} → (False, json_stringified)."""
        result = {"success": True, "data": ["tbl1", "tbl2"], "rowcount": 2}
        is_error, content = to_mcp_result(result)
        assert is_error is False
        parsed = json.loads(content)
        assert parsed["success"] is True
        assert parsed["data"] == ["tbl1", "tbl2"]

    def test_missing_success_key_defaults_to_not_error(self):
        """Result without 'success' key treated as success."""
        result = {"data": [1, 2, 3]}
        is_error, content = to_mcp_result(result)
        assert is_error is False

    def test_explicit_success_false_is_error(self):
        """Explicit success: False is always isError."""
        result = {"success": False, "error": "explicit false"}
        is_error, _ = to_mcp_result(result)
        assert is_error is True


class TestToolNames:
    """All verbatim oracle names are present."""

    EXPECTED_TOOLS = {
        "connect_access",
        "disconnect_access",
        "list_connections",
        "get_tables",
        "get_table_schema",
        "get_indexes",
        "get_relationships",
        "get_queries",
        "create_query",
        "set_query_sql",
        "delete_query",
        "query_data",
        "insert_data",
        "update_data",
        "delete_data",
        "execute_raw_sql",
        "export_data",
        "get_database_statistics",
        "get_system_tables",
        "get_linked_tables",
    }

    def test_all_verbatim_names_registered(self):
        """Every verbatim oracle name appears in _TOOLS."""
        registered = set(_TOOLS.keys())
        missing = self.EXPECTED_TOOLS - registered
        assert len(missing) == 0, f"Missing tools: {missing}"

    def test_no_extra_tools_registered(self):
        """Only verbatim oracle names are registered."""
        registered = set(_TOOLS.keys())
        extra = registered - self.EXPECTED_TOOLS
        assert len(extra) == 0, f"Extra tools: {extra}"

    def test_tool_count(self):
        """Core profile should have exactly 20 tools."""
        assert len(_TOOLS) == 20


class TestExecuteRawSqlGate:
    """execute_raw_sql requires confirm=True and write policy."""

    def test_execute_raw_sql_gate_returns_error_without_confirm(self):
        """execute_raw_sql without confirm returns isError."""
        from ms_access_mcp.mcp_tools.core import execute_raw_sql

        result = execute_raw_sql("", params={"sql": "SELECT 1"}, confirm=False)
        assert len(result) == 1
        content = json.loads(result[0].text)
        assert content["success"] is False
        assert "confirm=True" in content["error"]

    def test_execute_raw_sql_describes_write_policy_in_error(self):
        """execute_raw_sql error message mentions write policy."""
        from ms_access_mcp.mcp_tools.core import execute_raw_sql

        result = execute_raw_sql("", params={"sql": "SELECT 1"}, confirm=False)
        content = json.loads(result[0].text)
        assert "write policy" in content["error"].lower()
