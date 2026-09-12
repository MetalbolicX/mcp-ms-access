"""Tests for mcp_tools/ddl.py — DDL + admin profile tool registry, confirm gating."""

from __future__ import annotations

from ms_access_mcp.mcp_tools import ddl
from ms_access_mcp.profiles import PROFILES, load_profile


class TestToolNames:
    """All 16 DDL/admin verbatim oracle names are present in _TOOLS."""

    EXPECTED_TOOLS = {
        # 7 DDL tools (use cases already at database.py:573-782)
        "create_table",
        "alter_table",
        "delete_table",
        "create_index",
        "drop_index",
        "create_relationship",
        "delete_relationship",
        # 4 linked-table tools (DAO backend at dao.py:713,735,753)
        "create_linked_table",
        "refresh_linked_table",
        "unlink_table",
        "upsert_linked_table",
        # 5 admin tools
        "create_access_database",
        "get_database_properties",
        "set_database_property",
        "get_object_metadata",
        "is_connected",
    }

    def test_all_tools_registered(self):
        """Every expected oracle name appears in _TOOLS."""
        actual = set(ddl._TOOLS.keys())
        missing = self.EXPECTED_TOOLS - actual
        extra = actual - self.EXPECTED_TOOLS
        assert not missing, f"Missing tools: {missing}"
        assert not extra, f"Extra tools: {extra}"


class TestConfirmGating:
    """Write tools require confirm=True or return isError=True."""

    WRITE_TOOLS = {
        "create_table",
        "alter_table",
        "delete_table",
        "create_index",
        "drop_index",
        "create_relationship",
        "delete_relationship",
        "set_database_property",
        "create_linked_table",
        "refresh_linked_table",
        "unlink_table",
        "upsert_linked_table",
        "create_access_database",
    }

    def _call_without_confirm(self, name: str):
        """Call the tool function with no confirm arg; return the result."""
        fn, _desc = ddl._TOOLS[name]
        # Tools always have (path, params, confirm) signature
        return fn(path="dummy.mdb", params={}, confirm=False)

    def test_write_tools_reject_without_confirm(self):
        """Each write tool returns isError=True when confirm=False."""
        for name in self.WRITE_TOOLS:
            result = self._call_without_confirm(name)
            assert result.isError is True, f"{name} should reject without confirm"


class TestProfileWiring:
    """The ddl profile is properly registered in profiles.py."""

    def test_ddl_in_profiles(self):
        """PROFILES frozenset contains 'ddl'."""
        assert "ddl" in PROFILES

    def test_load_profile_ddl_returns_ddl_module(self):
        """load_profile('ddl') returns the ddl module."""
        modules = load_profile("ddl")
        assert "ms_access_mcp.mcp_tools.ddl" in modules

    def test_full_profile_includes_ddl(self):
        """The 'full' profile includes all ddl tools."""
        modules = load_profile("full")
        assert "ms_access_mcp.mcp_tools.ddl" in modules
