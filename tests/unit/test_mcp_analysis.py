"""Tests for mcp_tools/analysis.py — 3-tool registry, param validation, behavioral guards."""

from __future__ import annotations

from ms_access_mcp.mcp_tools import analysis
from ms_access_mcp.profiles import PROFILES, load_profile


class TestConnectedPath:
    def test_get_er_diagram_dispatches_through_connected_adapter(self, monkeypatch, tmp_path):
        from ms_access_mcp.backends import BackendSelector

        class FakeAdapter:
            connected = False

            def connect(self, path):
                self.connected = True
                self.connected_path = path

            def disconnect(self):
                self.connected = False

            def get_tables(self):
                self.tables_called = self.connected
                return []

            def get_relationships(self):
                self.relationships_called = self.connected
                return []

        adapter = FakeAdapter()
        monkeypatch.setattr(BackendSelector, "get_adapter", lambda **_kwargs: adapter)
        monkeypatch.setattr(analysis, "allowed_dirs_from_env", lambda: [tmp_path])
        path = str(tmp_path / "db.accdb")
        (tmp_path / "db.accdb").write_bytes(b"fake database")

        result = analysis.get_er_diagram(path)

        assert adapter.connected is False  # per-call session closes after dispatch
        assert adapter.connected_path == path
        assert adapter.tables_called and adapter.relationships_called
        assert "Not connected" not in result.content[0].text


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

    def test_get_er_diagram_rejects_disallowed_database_path(self):
        """The wrapper rejects a disallowed database path before dispatch."""
        fn, _desc = analysis._TOOLS["get_er_diagram"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True
        assert "Path not allowed" in result.content[0].text

    def test_generate_sql_path_not_allowed(self, monkeypatch, tmp_path):
        """generate_sql with output_path outside allowed_dirs returns 'Output path not allowed'."""
        from ms_access_mcp.backends import BackendSelector

        class FakeAdapter:
            connected = False

            def connect(self, path):
                self.connected = True

            def disconnect(self):
                self.connected = False

        monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
        monkeypatch.setattr(BackendSelector, "get_adapter", lambda **_kwargs: FakeAdapter())
        database_path = tmp_path / "db.accdb"
        database_path.write_bytes(b"fake database")
        fn, _desc = analysis._TOOLS["generate_sql"]
        result = fn(
            path=str(database_path),
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
