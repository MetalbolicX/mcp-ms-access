"""Tests for --python-mode on configure opencode (portable runtime support).

RED phase: tests describe expected behaviour for module_mode flag on entry_from()
and --python-mode flag on the CLI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ms_access_mcp import cli
from ms_access_mcp.opencode_config import McpEntry, entry_from


class TestEntryFromModuleMode:
    """entry_from() with module_mode=True produces python -m argv."""

    def test_module_mode_true_inserts_m_ms_access_mcp(self) -> None:
        """module_mode=True: argv is [exe, -m, ms_access_mcp, mcp, --profile, core]."""
        exe = Path("C:/runtime/python.exe")
        entry = entry_from(exe, profile="core", module_mode=True)

        assert isinstance(entry, McpEntry)
        assert entry.command == (
            str(exe),
            "-m",
            "ms_access_mcp",
            "mcp",
            "--profile",
            "core",
        ), f"expected module-mode argv, got {entry.command!r}"
        assert entry.profile == "core"

    def test_module_mode_false_default_matches_traditional(self) -> None:
        """module_mode=False (default): zero-drift from traditional uv-install argv.

        Traditional uv-install argv: [exe, mcp, --profile, core]
        This must be byte-identical to today's output.
        """
        exe = Path("C:/runtime/python.exe")
        entry_default = entry_from(exe, profile="core")
        entry_explicit_false = entry_from(exe, profile="core", module_mode=False)

        expected = (str(exe), "mcp", "--profile", "core")
        assert entry_default.command == expected, (
            f"default entry_from must match traditional argv, got {entry_default.command!r}"
        )
        assert entry_explicit_false.command == expected, (
            f"module_mode=False must match traditional argv, got {entry_explicit_false.command!r}"
        )

    def test_module_mode_true_with_different_profile(self) -> None:
        """module_mode=True works with non-core profiles."""
        exe = Path("C:/runtime/python.exe")
        entry = entry_from(exe, profile="automation", module_mode=True)

        assert entry.command == (
            str(exe),
            "-m",
            "ms_access_mcp",
            "mcp",
            "--profile",
            "automation",
        )


class TestConfigurePythonModeCli:
    """CLI: configure opencode --python-mode uses sys.executable as exe path."""

    def test_python_mode_flag_registered(self, tmp_path: Path, monkeypatch) -> None:
        """The --python-mode flag is registered on the opencode subparser."""
        monkeypatch.chdir(tmp_path)

        # A fake python.exe to satisfy the existence check
        fake_python = tmp_path / "python.exe"
        fake_python.touch()

        # Patch sys.executable so the handler picks it up
        with patch.object(sys, "executable", str(fake_python)):
            with patch.object(sys, "argv", [
                "ms-access-mcp", "configure", "opencode",
                "--python-mode",
            ]):
                exit_code = cli.main()

        assert exit_code == 0, f"expected exit 0, got {exit_code}"
        target = tmp_path / "opencode.json"
        assert target.exists(), f"expected {target} to exist"

        data = json.loads(target.read_text())
        cmd = data["mcp"]["ms-access"]["command"]
        # Must use sys.executable path and -m ms_access_mcp
        assert cmd[0] == str(fake_python), f"expected cmd[0] to be sys.executable, got {cmd[0]!r}"
        assert cmd[1] == "-m", f"expected cmd[1] to be -m, got {cmd[1]!r}"
        assert cmd[2] == "ms_access_mcp", f"expected cmd[2] to be ms_access_mcp, got {cmd[2]!r}"
        assert cmd[3] == "mcp", f"expected cmd[3] to be mcp, got {cmd[3]!r}"
        assert "--profile" in cmd, f"expected --profile in command, got {cmd!r}"

    def test_python_mode_uses_sys_executable(self, tmp_path: Path, monkeypatch) -> None:
        """When --python-mode is set, the registered command starts with sys.executable."""
        monkeypatch.chdir(tmp_path)

        fake_python = tmp_path / "fake_python.exe"
        fake_python.touch()

        with patch.object(sys, "executable", str(fake_python)):
            with patch.object(sys, "argv", [
                "ms-access-mcp", "configure", "opencode",
                "--python-mode",
            ]):
                exit_code = cli.main()

        assert exit_code == 0, "configure opencode --python-mode should succeed"
        target = tmp_path / "opencode.json"
        data = json.loads(target.read_text())
        cmd = data["mcp"]["ms-access"]["command"]
        assert cmd[0] == str(fake_python), (
            f"command should start with sys.executable ({fake_python}), got {cmd[0]!r}"
        )
        assert cmd[1] == "-m"
        assert cmd[2] == "ms_access_mcp"

    def test_python_mode_and_exe_path_mutually_exclusive(self, tmp_path: Path, monkeypatch) -> None:
        """--python-mode and --exe-path cannot be used together."""
        monkeypatch.chdir(tmp_path)

        # Both flags set — should fail with argparse error
        with patch.object(sys, "argv", [
            "ms-access-mcp", "configure", "opencode",
            "--python-mode",
            "--exe-path", "C:/some/path.exe",
        ]):
            # argparse exits with 2 on error
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code == 2, (
                f"mutually exclusive flags should exit with 2, got {exc_info.value.code}"
            )
