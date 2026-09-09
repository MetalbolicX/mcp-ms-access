"""Tests for configure opencode subcommand — RED then GREEN for S5/S7/S8.

These tests verify the CLI wiring for:
- S5: --global flag switches between CWD and user-level opencode.json
- S7: --remove deletes only mcp.ms-access key, missing key is no-op
- S8: command[0] is absolute path, never bare name
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
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

        # McpEntry contract: command, type, enabled, timeout
        assert isinstance(entry["command"], list)
        assert len(entry["command"]) >= 2
        assert entry["command"][1] == "mcp"
        assert entry["enabled"] is True
        assert entry["timeout"] == 30000
        assert entry["type"] == "local"


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
# U1 — _run_validator Windows resolution (shutil.which)
# =============================================================================


class TestRunValidatorSandbox:
    """U1-sandbox: _run_validator sandboxes the subprocess to protect user config."""

    def test_validator_does_not_touch_real_user_config(self, tmp_path, monkeypatch):
        """Sandboxed validator never reads or writes the real ~/.config/opencode/opencode.json."""
        fake_opencode = tmp_path / "opencode.cmd"
        fake_opencode.touch()

        real_user_config = _resolve_opencode_user_config()
        real_user_config.parent.mkdir(parents=True, exist_ok=True)
        original_content = '{"other": "data"}'
        real_user_config.write_text(original_content)

        sandbox_contents: dict = {}

        def fake_run(cmd, **kwargs):
            # Verify the subprocess is running with a redirected HOME/USERPROFILE.
            env = kwargs.get("env", {})
            sandbox_home = env.get("HOME") or env.get("USERPROFILE")
            assert sandbox_home is not None, "HOME or USERPROFILE must be redirected in env"
            # Record the sandbox path for cleanup verification.
            sandbox_contents["sandbox"] = sandbox_home
            # Simulate opencode creating/modifying a config in the sandbox.
            sandbox_config = Path(sandbox_home) / ".config" / "opencode" / "opencode.json"
            sandbox_config.parent.mkdir(parents=True, exist_ok=True)
            sandbox_config.write_text('{"$schema": "https://opencode.ai/opencode.schema.json"}')
            result = type("R", (), {"returncode": 0})()
            return result

        def fake_which(name):
            if name == "opencode":
                return str(fake_opencode)
            return None

        monkeypatch.setattr(shutil, "which", fake_which)
        monkeypatch.setattr(subprocess, "run", fake_run)

        # Run from a temp CWD that has no opencode.json so it falls back to user config.
        import tempfile as tmp_mod
        other_tmp = Path(tmp_mod.gettempdir()) / "opencode_test_cwd"
        other_tmp.mkdir(exist_ok=True)
        monkeypatch.chdir(other_tmp)

        from ms_access_mcp.cli import _run_validator

        result = _run_validator(other_tmp)

        assert result == "ok"
        # Real user config must be completely untouched.
        assert real_user_config.read_text() == original_content, \
            "Real user config was mutated by validator"

        # Clean up.
        if real_user_config.exists():
            real_user_config.unlink()

    def test_sandbox_is_cleaned_up_after_success(self, tmp_path, monkeypatch):
        """Sandbox directory is removed after _run_validator returns 'ok'."""
        fake_opencode = tmp_path / "opencode.cmd"
        fake_opencode.touch()
        preexisting_sandboxes = set(Path(tempfile.gettempdir()).iterdir())

        def fake_which(name):
            return str(fake_opencode)

        class FakeResult:
            returncode = 0

        def fake_run(cmd, **kwargs):
            return FakeResult()

        monkeypatch.setattr(shutil, "which", fake_which)
        monkeypatch.setattr(subprocess, "run", fake_run)

        from ms_access_mcp.cli import _run_validator

        result = _run_validator(tmp_path)

        assert result == "ok"
        # No new sandbox directories should remain.
        current_sandboxes = set(Path(tempfile.gettempdir()).iterdir()) - preexisting_sandboxes
        opencode_sandboxes = [p for p in current_sandboxes if p.name.startswith("opencode_validate_")]
        assert len(opencode_sandboxes) == 0, \
            f"Sandbox(es) not cleaned up: {opencode_sandboxes}"

    def test_sandbox_is_cleaned_up_when_subprocess_raises(self, tmp_path, monkeypatch):
        """Sandbox directory is removed even when subprocess.run throws an exception."""
        fake_opencode = tmp_path / "opencode.cmd"
        fake_opencode.touch()
        preexisting_sandboxes = set(Path(tempfile.gettempdir()).iterdir())

        def fake_which(name):
            return str(fake_opencode)

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, timeout=30)

        monkeypatch.setattr(shutil, "which", fake_which)
        monkeypatch.setattr(subprocess, "run", fake_run)

        from ms_access_mcp.cli import _run_validator

        result = _run_validator(tmp_path)

        # Should return "failed" due to unexpected exception.
        assert result == "failed"
        # No sandbox directories should remain.
        current_sandboxes = set(Path(tempfile.gettempdir()).iterdir()) - preexisting_sandboxes
        opencode_sandboxes = [p for p in current_sandboxes if p.name.startswith("opencode_validate_")]
        assert len(opencode_sandboxes) == 0, \
            f"Sandbox(es) not cleaned up after exception: {opencode_sandboxes}"


class TestRunValidator:
    """U1: _run_validator uses shutil.which to resolve opencode on Windows."""

    def test_which_resolves_and_runs_resolved_path(self, tmp_path, monkeypatch):
        """When shutil.which finds opencode, subprocess.run uses the resolved path."""
        fake_opencode = tmp_path / "opencode.cmd"
        fake_opencode.touch()

        captured_calls: list = []

        def fake_which(name):
            if name == "opencode":
                return str(fake_opencode)
            return None

        def fake_run(cmd, **kwargs):
            captured_calls.append(cmd)
            result = type("R", (), {"returncode": 0})()
            return result

        monkeypatch.setattr(shutil, "which", fake_which)
        monkeypatch.setattr(subprocess, "run", fake_run)

        from ms_access_mcp.cli import _run_validator

        result = _run_validator(tmp_path)

        assert result == "ok"
        assert len(captured_calls) == 1
        assert captured_calls[0][0] == str(fake_opencode)
        assert list(captured_calls[0][1:]) == ["debug", "config"]

    def test_returns_unavailable_when_which_returns_none(self, tmp_path, monkeypatch):
        """When shutil.which returns None, _run_validator returns 'unavailable'."""
        def fake_which(name):
            return None

        def fake_run(cmd, **kwargs):
            raise AssertionError("subprocess.run should not be called when opencode is not on PATH")

        monkeypatch.setattr(shutil, "which", fake_which)
        monkeypatch.setattr(subprocess, "run", fake_run)

        from ms_access_mcp.cli import _run_validator

        result = _run_validator(tmp_path)

        assert result == "unavailable"

    def test_returns_unavailable_on_file_not_found_error(self, tmp_path, monkeypatch):
        """When subprocess.run raises FileNotFoundError, _run_validator returns 'unavailable'."""
        def fake_which(name):
            return str(tmp_path / "opencode.cmd")

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("opencode not found")

        monkeypatch.setattr(shutil, "which", fake_which)
        monkeypatch.setattr(subprocess, "run", fake_run)

        from ms_access_mcp.cli import _run_validator

        result = _run_validator(tmp_path)

        assert result == "unavailable"

    def test_returns_ok_on_subprocess_exit_0(self, tmp_path, monkeypatch):
        """When subprocess.run succeeds (exit 0), _run_validator returns 'ok'."""
        def fake_which(name):
            return str(tmp_path / "opencode.cmd")

        class FakeResult:
            returncode = 0

        def fake_run(cmd, **kwargs):
            return FakeResult()

        monkeypatch.setattr(shutil, "which", fake_which)
        monkeypatch.setattr(subprocess, "run", fake_run)

        from ms_access_mcp.cli import _run_validator

        result = _run_validator(tmp_path)

        assert result == "ok"

    def test_returns_failed_on_subprocess_nonzero_exit(self, tmp_path, monkeypatch):
        """When subprocess.run returns non-zero exit, _run_validator returns 'failed'."""
        def fake_which(name):
            return str(tmp_path / "opencode.cmd")

        class FakeResult:
            returncode = 1

        def fake_run(cmd, **kwargs):
            return FakeResult()

        monkeypatch.setattr(shutil, "which", fake_which)
        monkeypatch.setattr(subprocess, "run", fake_run)

        from ms_access_mcp.cli import _run_validator

        result = _run_validator(tmp_path)

        assert result == "failed"


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
