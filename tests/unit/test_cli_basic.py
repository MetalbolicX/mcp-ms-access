"""Tests for cli.py — TDD RED phase."""

import subprocess
import sys


class TestCliVersion:
    def test_version_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "ms-access-mcp 0.1.0" in result.stdout, f"stdout: {result.stdout}"

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"


class TestDoctorPlaceholder:
    def test_doctor_exits_one(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "doctor"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1, f"expected exit 1, got {result.returncode}"
        assert "not implemented" in result.stdout or "not implemented" in result.stderr
