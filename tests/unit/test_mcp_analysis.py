"""Tests for mcp_tools/analysis.py — 3-tool registry, param validation, behavioral guards."""

from __future__ import annotations

from ms_access_mcp.mcp_tools import analysis
from ms_access_mcp.profiles import PROFILES, load_profile


class TestToolNames:
    """All 3 analysis verbatim oracle names are present in _TOOLS."""

    EXPECTED_TOOLS = {
        "get_er_diagram",
        "analyze_query",
        "generate_sql",
    }

    def test_all_tools_registered(self):
        """Every expected oracle name appears in _TOOLS."""
        actual = set(analysis._TOOLS.keys())
        missing = self.EXPECTED_TOOLS - actual
        extra = actual - self.EXPECTED_TOOLS
        assert not missing, f"Missing tools: {missing}"
        assert not extra, f"Extra tools: {extra}"

    def test_tool_count(self):
        """Exactly 3 tools are registered."""
        assert len(analysis._TOOLS) == 3


class TestParamValidation:
    """Wrapper-level early returns — no connector work triggered."""

    def test_analyze_query_rejects_empty_params(self):
        """analyze_query with {} returns 'sql parameter is required'."""
        fn, _desc = analysis._TOOLS["analyze_query"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True
        assert "sql parameter is required" in result.content[0].text

    def test_generate_sql_rejects_empty_params(self):
        """generate_sql with {} returns isError (output_path required)."""
        fn, _desc = analysis._TOOLS["generate_sql"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True


class TestBehavioralNoCom:
    """Behavioral tests that require no COM/ODBC — path gates and UC state checks."""

    def test_get_er_diagram_no_connection_returns_error(self):
        """get_er_diagram without a connection returns isError with 'Not connected'."""
        fn, _desc = analysis._TOOLS["get_er_diagram"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True
        assert "Not connected" in result.content[0].text

    def test_generate_sql_path_not_allowed(self):
        """generate_sql with output_path outside allowed_dirs returns 'Output path not allowed'."""
        fn, _desc = analysis._TOOLS["generate_sql"]
        result = fn(
            path="dummy.mdb",
            params={"output_path": "<nonexistent>.sql"},
            confirm=False,
        )
        assert result.isError is True
        assert "Output path not allowed" in result.content[0].text


class TestProfileWiring:
    """The analysis profile is properly registered in profiles.py."""

    def test_analysis_in_profiles(self):
        """PROFILES frozenset contains 'analysis'."""
        assert "analysis" in PROFILES

    def test_load_profile_analysis_returns_analysis_module(self):
        """load_profile('analysis') returns the analysis module."""
        modules = load_profile("analysis")
        assert "ms_access_mcp.mcp_tools.analysis" in modules

    def test_full_profile_includes_analysis(self):
        """The 'full' profile includes analysis tools."""
        modules = load_profile("full")
        assert "ms_access_mcp.mcp_tools.analysis" in modules
