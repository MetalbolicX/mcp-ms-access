"""Tests for mcp_tools/migration.py — 4-tool registry, param validation, confirm gating."""

from __future__ import annotations

from ms_access_mcp.mcp_tools import migration
from ms_access_mcp.profiles import PROFILES, load_profile


class TestToolNames:
    """All 4 migration verbatim oracle names are present in _TOOLS."""

    EXPECTED_TOOLS = {
        "extract_schema",
        "upload_schema",
        "transfer_data",
        "get_migration_status",
    }

    def test_all_tools_registered(self):
        """Every expected oracle name appears in _TOOLS."""
        actual = set(migration._TOOLS.keys())
        missing = self.EXPECTED_TOOLS - actual
        extra = actual - self.EXPECTED_TOOLS
        assert not missing, f"Missing tools: {missing}"
        assert not extra, f"Extra tools: {extra}"

    def test_tool_count(self):
        """Exactly 4 tools are registered."""
        assert len(migration._TOOLS) == 4


class TestParamValidation:
    """Wrapper-level early returns — no connector work triggered."""

    def test_extract_schema_rejects_empty_params(self):
        """extract_schema with {} returns 'target_type is required'."""
        fn, _desc = migration._TOOLS["extract_schema"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True
        assert "target_type is required" in result.content[0].text

    def test_upload_schema_rejects_empty_params(self):
        """upload_schema with {} returns 'target_type is required'."""
        fn, _desc = migration._TOOLS["upload_schema"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True
        assert "target_type is required" in result.content[0].text

    def test_upload_schema_rejects_missing_connection_string(self):
        """upload_schema with target_type but no connection_string returns error."""
        fn, _desc = migration._TOOLS["upload_schema"]
        result = fn(
            path="dummy.mdb",
            params={"target_type": "sqlite"},
            confirm=False,
        )
        assert result.isError is True
        assert "connection_string is required" in result.content[0].text

    def test_transfer_data_rejects_empty_params(self):
        """transfer_data with {} returns 'target_type is required'."""
        fn, _desc = migration._TOOLS["transfer_data"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True
        assert "target_type is required" in result.content[0].text

    def test_get_migration_status_rejects_empty_params(self):
        """get_migration_status with {} returns 'job_id is required'."""
        fn, _desc = migration._TOOLS["get_migration_status"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True
        assert "job_id is required" in result.content[0].text


class TestConfirmGates:
    """confirm gate fires before any connector work — safe, no COM dependency."""

    def test_upload_schema_confirm_false_rejects(self):
        """upload_schema with confirm=False returns isError with 'confirm=True'."""
        fn, _desc = migration._TOOLS["upload_schema"]
        result = fn(
            path="dummy.mdb",
            params={
                "target_type": "sqlite",
                "connection_string": "sqlite:///dummy.db",
                "schema_json": {},
            },
            confirm=False,
        )
        assert result.isError is True
        assert "confirm=True" in result.content[0].text

    def test_transfer_data_confirm_false_rejects(self):
        """transfer_data with confirm=False returns isError with 'confirm=True'."""
        fn, _desc = migration._TOOLS["transfer_data"]
        result = fn(
            path="dummy.mdb",
            params={
                "target_type": "sqlite",
                "connection_string": "sqlite:///dummy.db",
                "schema_json": {},
            },
            confirm=False,
        )
        assert result.isError is True
        assert "confirm=True" in result.content[0].text


class TestGetMigrationStatus:
    """get_migration_status hits the job tracker directly."""

    def test_unknown_job_returns_error(self):
        """Unknown job_id returns isError containing 'not found'."""
        fn, _desc = migration._TOOLS["get_migration_status"]
        result = fn(path="dummy.mdb", params={"job_id": "deadbeef"}, confirm=False)
        assert result.isError is True
        assert "not found" in result.content[0].text


class TestProfileWiring:
    """The migration profile is properly registered in profiles.py."""

    def test_migration_in_profiles(self):
        """PROFILES frozenset contains 'migration'."""
        assert "migration" in PROFILES

    def test_load_profile_migration_returns_migration_module(self):
        """load_profile('migration') returns the migration module."""
        modules = load_profile("migration")
        assert "ms_access_mcp.mcp_tools.migration" in modules

    def test_full_profile_includes_migration(self):
        """The 'full' profile includes migration tools."""
        modules = load_profile("full")
        assert "ms_access_mcp.mcp_tools.migration" in modules
