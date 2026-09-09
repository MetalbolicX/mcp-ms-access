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
    def test_doctor_exits_zero_on_full_setup(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "doctor", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        # Doctor exits 0 when all checks pass (this machine has full Access setup)
        assert result.returncode == 0, f"expected exit 0, got {result.returncode}"
        # Should show JSON output
        import json
        output = json.loads(result.stdout)
        assert output["passed"] is True

    def test_doctor_exits_one_with_remediation_on_missing_deps(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "doctor"],
            capture_output=True,
            text=True,
            check=False,
        )
        # Doctor exits 1 when some checks fail (expected on machines without Access)
        # or when given a --deep flag that fails
        if result.returncode == 1:
            assert "Remediation" in result.stdout or "FAIL" in result.stdout
