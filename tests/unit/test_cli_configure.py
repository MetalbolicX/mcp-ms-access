"""Tests for configure opencode subcommand — RED then GREEN for S5/S7/S8.

These tests verify the CLI wiring for:
- S5: --global flag switches between CWD and user-level opencode.json
- S7: --remove deletes only mcp.ms-access key, missing key is no-op
- S8: command[0] is absolute path, never bare name
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# The CLI module under test
from ms_access_mcp import cli

# =============================================================================
# Helpers
# =============================================================================


def _resolve_opencode_user_config() -> Path:
    """Return the user-level opencode.json path (platform-aware)."""
    if os.name == "nt":
        return Path(os.environ["USERPROFILE"]) / ".config" / "opencode" / "opencode.json"
    return Path.home() / ".config" / "opencode" / "opencode.json"


class FakeValidator:
    """A validator that always succeeds when called."""

    def __call__(self, path: Path) -> None:
        pass


class FailingValidator:
    """A validator that always fails."""

    def __call__(self, path: Path) -> None:
        raise RuntimeError("injected validator failure")


# =============================================================================
# S5 — scope resolution (CWD default vs --global user config)
# =============================================================================


class TestConfigureOpencodeS5CwdDefault:
    """S5: no flag -> ./opencode.json in CWD."""

    def test_no_flag_writes_cwd_opencode_json(self, tmp_path, monkeypatch):
        """configure opencode without --global writes ./opencode.json in CWD."""
        monkeypatch.chdir(tmp_path)

        # Provide a fake exe path so the CLI doesn't need the real tool installed
        fake_exe = tmp_path / "ms-access-mcp.exe"
        fake_exe.touch()

        with patch.object(sys, "argv", [
            "ms-access-mcp", "configure", "opencode",
            "--exe-path", str(fake_exe),
        ]):
            exit_code = cli.main()

        target = tmp_path / "opencode.json"
        assert exit_code == 0, f"expected exit 0, got {exit_code}"
        assert target.exists(), f"expected {target} to exist"

        data = json.loads(target.read_text())
        assert "mcp" in data
        assert "ms-access" in data["mcp"]

    def test_no_flag_leaves_user_config_untouched(self, tmp_path, monkeypatch):
        """configure opencode without --global does NOT touch user config."""
        monkeypatch.chdir(tmp_path)

        fake_exe = tmp_path / "ms-access-mcp.exe"
        fake_exe.touch()

        user_config = _resolve_opencode_user_config()
        user_config.parent.mkdir(parents=True, exist_ok=True)
        user_config.write_text('{"other": "data"}')

        try:
            with patch.object(sys, "argv", [
                "ms-access-mcp", "configure", "opencode",
                "--exe-path", str(fake_exe),
            ]):
                exit_code = cli.main()

            assert exit_code == 0
            # User config must be untouched
            assert json.loads(user_config.read_text()) == {"other": "data"}
        finally:
            if user_config.exists():
                user_config.unlink()


class TestConfigureOpencodeS5Global:
    """S5: --global -> user-level opencode.json."""

    def test_global_flag_writes_user_config(self, tmp_path, monkeypatch):
        """configure opencode --global writes %USERPROFILE%/.config/opencode/opencode.json."""
        monkeypatch.chdir(tmp_path)

        fake_exe = tmp_path / "ms-access-mcp.exe"
        fake_exe.touch()

        user_config = _resolve_opencode_user_config()
        user_config.parent.mkdir(parents=True, exist_ok=True)
        # Clean up any existing entry that would cause conflict
        if user_config.exists():
            user_config.unlink()

        try:
            with patch.object(sys, "argv", [
                "ms-access-mcp", "configure", "opencode", "--global",
                "--exe-path", str(fake_exe),
            ]):
                exit_code = cli.main()

            assert exit_code == 0, f"expected exit 0, got {exit_code}"
            assert user_config.exists(), f"expected {user_config} to exist"

            data = json.loads(user_config.read_text())
            assert "mcp" in data
            assert "ms-access" in data["mcp"]
        finally:
            if user_config.exists():
                user_config.unlink()

    def test_global_flag_leaves_cwd_untouched(self, tmp_path, monkeypatch):
        """configure opencode --global does NOT write ./opencode.json in CWD."""
        monkeypatch.chdir(tmp_path)

        fake_exe = tmp_path / "ms-access-mcp.exe"
        fake_exe.touch()

        cwd_config = tmp_path / "opencode.json"

        user_config = _resolve_opencode_user_config()
        user_config.parent.mkdir(parents=True, exist_ok=True)
        # Clean up any existing entry that would cause conflict
        if user_config.exists():
            user_config.unlink()

        try:
            with patch.object(sys, "argv", [
                "ms-access-mcp", "configure", "opencode", "--global",
                "--exe-path", str(fake_exe),
            ]):
                exit_code = cli.main()

            assert exit_code == 0
            assert not cwd_config.exists(), "CWD opencode.json should not be created"
        finally:
            if user_config.exists():
                user_config.unlink()


# =============================================================================
# S7 — remove semantics
# =============================================================================


class TestConfigureOpencodeS7Remove:
    """S7: --remove deletes only mcp.ms-access; missing key is no-op exit 0."""

    def test_remove_deletes_only_ms_access_key(self, tmp_path, monkeypatch):
        """--remove deletes only mcp.ms-access, preserving siblings."""
        monkeypatch.chdir(tmp_path)

        # Write a config with mcp.ms-access AND other sibling keys
        config_path = tmp_path / "opencode.json"
        config_path.write_text(json.dumps({
            "$schema": "https://opencode.ai/opencode.schema.json",
            "mcp": {
                "ms-access": {"type": "local", "command": ["ms-access-mcp", "mcp"], "enabled": True},
                "engram": {"type": "local", "command": ["engram", "mcp"], "enabled": True},
            },
            "other": {"key": "preserved"},
        }))

        fake_exe = tmp_path / "ms-access-mcp.exe"
        fake_exe.touch()

        with patch.object(sys, "argv", [
            "ms-access-mcp", "configure", "opencode", "--remove",
            "--exe-path", str(fake_exe),
        ]):
            exit_code = cli.main()

        assert exit_code == 0, f"expected exit 0, got {exit_code}"

        data = json.loads(config_path.read_text())
        assert "mcp" in data
        assert "ms-access" not in data.get("mcp", {}), "ms-access key should be deleted"
        assert "engram" in data["mcp"], "engram sibling must survive"
        assert data["other"]["key"] == "preserved"

    def test_remove_missing_key_is_noop_exit_0(self, tmp_path, monkeypatch):
        """--remove on absent mcp.ms-access returns exit 0 (no-op)."""
        monkeypatch.chdir(tmp_path)

        config_path = tmp_path / "opencode.json"
        config_path.write_text(json.dumps({
            "$schema": "https://opencode.ai/opencode.schema.json",
            "mcp": {
                "other-tool": {"type": "local", "command": ["other", "mcp"]},
            },
        }))

        fake_exe = tmp_path / "ms-access-mcp.exe"
        fake_exe.touch()

        with patch.object(sys, "argv", [
            "ms-access-mcp", "configure", "opencode", "--remove",
            "--exe-path", str(fake_exe),
        ]):
            exit_code = cli.main()

        assert exit_code == 0, f"expected exit 0 for no-op remove, got {exit_code}"

        # Config unchanged
        data = json.loads(config_path.read_text())
        assert "ms-access" not in data.get("mcp", {})


# =============================================================================
# S8 — absolute command registration
# =============================================================================


class TestConfigureOpencodeS8AbsoluteCommand:
    """S8: command[0] is absolute path, never bare name."""

    def test_command_0_is_absolute_path(self, tmp_path, monkeypatch):
        """The registered command[0] is the resolved absolute path, never bare."""
        monkeypatch.chdir(tmp_path)

        fake_exe = tmp_path / "ms-access-mcp.exe"
        fake_exe.touch()

        with patch.object(sys, "argv", [
            "ms-access-mcp", "configure", "opencode",
            "--exe-path", str(fake_exe),
        ]):
            exit_code = cli.main()

        assert exit_code == 0

        config_path = tmp_path / "opencode.json"
        data = json.loads(config_path.read_text())
        cmd = data["mcp"]["ms-access"]["command"]

        assert Path(cmd[0]).is_absolute(), f"command[0] must be absolute, got {cmd[0]}"
        assert not cmd[0].startswith("ms-access-mcp"), "bare name forbidden"

    def test_entry_shape_matches_contract(self, tmp_path, monkeypatch):
        """Entry shape is { command, profile, enabled, timeout } per McpEntry contract."""
        monkeypatch.chdir(tmp_path)

        fake_exe = tmp_path / "ms-access-mcp.exe"
        fake_exe.touch()

        with patch.object(sys, "argv", [
            "ms-access-mcp", "configure", "opencode",
            "--exe-path", str(fake_exe),
            "--profile", "automation",
        ]):
            exit_code = cli.main()

        assert exit_code == 0

        config_path = tmp_path / "opencode.json"
        data = json.loads(config_path.read_text())
        entry = data["mcp"]["ms-access"]

        # McpEntry contract: command, profile, enabled, timeout
        assert isinstance(entry["command"], list)
        assert len(entry["command"]) >= 2
        assert entry["command"][1] == "mcp"
        assert entry["enabled"] is True
        assert entry["timeout"] == 30000
        assert entry["profile"] == "automation"


# =============================================================================
# Exit codes
# =============================================================================


class TestConfigureOpencodeExitCodes:
    """Exit codes: 0 success/no-op, 1 conflict/validation failure, 2 unavailable, 3 internal."""

    def test_exit_0_on_success(self, tmp_path, monkeypatch):
        """Successful registration returns exit 0."""
        monkeypatch.chdir(tmp_path)
        fake_exe = tmp_path / "ms-access-mcp.exe"
        fake_exe.touch()

        with patch.object(sys, "argv", [
            "ms-access-mcp", "configure", "opencode",
            "--exe-path", str(fake_exe),
        ]):
            exit_code = cli.main()

        assert exit_code == 0

    def test_exit_1_on_conflict_without_force(self, tmp_path, monkeypatch):
        """Conflict without --force returns exit 1."""
        monkeypatch.chdir(tmp_path)

        config_path = tmp_path / "opencode.json"
        config_path.write_text(json.dumps({
            "$schema": "https://opencode.ai/opencode.schema.json",
            "mcp": {
                "ms-access": {
                    "type": "local",
                    "command": ["C:/different/path/ms-access-mcp.exe", "mcp"],
                    "enabled": True,
                    "timeout": 30000,
                    "profile": "core",
                },
            },
        }))

        fake_exe = tmp_path / "ms-access-mcp.exe"
        fake_exe.touch()

        with patch.object(sys, "argv", [
            "ms-access-mcp", "configure", "opencode",
            "--exe-path", str(fake_exe),
        ]):
            exit_code = cli.main()

        assert exit_code == 1, f"expected exit 1 for conflict, got {exit_code}"
        # Original file unchanged
        assert json.loads(config_path.read_text())["mcp"]["ms-access"]["command"][0] == "C:/different/path/ms-access-mcp.exe"

    def test_exit_0_on_conflict_with_force(self, tmp_path, monkeypatch):
        """Conflict with --force returns exit 0 (overwrites)."""
        monkeypatch.chdir(tmp_path)

        config_path = tmp_path / "opencode.json"
        config_path.write_text(json.dumps({
            "$schema": "https://opencode.ai/opencode.schema.json",
            "mcp": {
                "ms-access": {
                    "type": "local",
                    "command": ["C:/different/path/ms-access-mcp.exe", "mcp"],
                    "enabled": True,
                    "timeout": 30000,
                    "profile": "core",
                },
            },
        }))

        fake_exe = tmp_path / "ms-access-mcp.exe"
        fake_exe.touch()

        with patch.object(sys, "argv", [
            "ms-access-mcp", "configure", "opencode",
            "--exe-path", str(fake_exe),
            "--force",
        ]):
            exit_code = cli.main()

        assert exit_code == 0, f"expected exit 0 with --force, got {exit_code}"

        data = json.loads(config_path.read_text())
        assert Path(data["mcp"]["ms-access"]["command"][0]).resolve() == fake_exe.resolve()


# =============================================================================
# Help output
# =============================================================================


class TestConfigureOpencodeHelp:
    """Help flags work and exit 0."""

    def test_configure_opencode_help_exits_0(self, monkeypatch):
        """ms-access-mcp configure opencode --help exits 0."""
        with patch.object(sys, "argv", ["ms-access-mcp", "configure", "opencode", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code == 0

    def test_configure_opencode_remove_help_exits_0(self, monkeypatch):
        """ms-access-mcp configure opencode --remove --help exits 0."""
        with patch.object(sys, "argv", ["ms-access-mcp", "configure", "opencode", "--remove", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code == 0
