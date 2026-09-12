"""Tests for profile registry — resolution order and unknown-name behavior."""

from __future__ import annotations

import pytest

from ms_access_mcp.profiles import PROFILES, load_profile, resolve_profile


class TestProfileRegistry:
    def test_profiles_contains_expected_profiles(self):
        assert PROFILES == frozenset({"core", "automation", "maintenance", "versioning", "ddl", "full"})

    def test_load_profile_core_returns_list(self):
        result = load_profile("core")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_load_profile_full_returns_union(self):
        core = set(load_profile("core"))
        full = set(load_profile("full"))
        assert core.issubset(full)

    def test_load_profile_unknown_raises_with_valid_profiles(self):
        with pytest.raises(ValueError) as exc_info:
            load_profile("nonexistent")
        assert "nonexistent" in str(exc_info.value)
        # Should list valid profiles
        for p in PROFILES:
            assert p in str(exc_info.value)

    def test_load_profile_each_valid(self):
        for profile in PROFILES:
            result = load_profile(profile)
            assert isinstance(result, list), f"{profile} did not return a list"


class TestResolveProfile:
    def test_explicit_arg_takes_precedence(self):
        result = resolve_profile(explicit="automation")
        assert result == "automation"

    def test_env_var_used_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("ACCESS_MCP_PROFILE", "versioning")
        result = resolve_profile(explicit=None)
        assert result == "versioning"

    def test_default_is_core(self, monkeypatch):
        # Ensure env var is not set
        monkeypatch.delenv("ACCESS_MCP_PROFILE", raising=False)
        result = resolve_profile(explicit=None)
        assert result == "core"

    def test_none_explicit_with_env(self, monkeypatch):
        monkeypatch.setenv("ACCESS_MCP_PROFILE", "maintenance")
        result = resolve_profile(explicit=None)
        assert result == "maintenance"

    def test_empty_string_explicit_is_not_none(self):
        # Empty string from argparse default is still falsy but not None
        # resolve_profile treats None as "not provided"
        # Empty string would pass through as-is (argparse default behavior)
        result = resolve_profile(explicit="")
        assert result == ""

    def test_unknown_profile_not_resolved_here(self):
        # resolve_profile doesn't validate - validation happens in load_profile
        result = resolve_profile(explicit="unknown_profile")
        assert result == "unknown_profile"
        with pytest.raises(ValueError):
            load_profile(result)
