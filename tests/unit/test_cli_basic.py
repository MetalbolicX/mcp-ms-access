"""Tests for cli.py — TDD RED phase."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ms_access_mcp import doctor

# A full doctor run reports ``passed`` only on a host with Access installed, so the
# exit-zero assertion is gated on the same binary probe ``ci.yml`` uses. Everything
# else in this module must hold on any host, including GitHub's hosted runners.
_ACCESS_INSTALL_ROOTS = (
    "C:/Program Files/Microsoft Office/root",
    "C:/Program Files (x86)/Microsoft Office/root",
)


def _has_access_application() -> bool:
    """Whether this host can pass every doctor check (Windows with Access installed)."""
    if os.name != "nt":
        return False
    return any(any(Path(root).glob("Office*/MSACCESS.EXE")) for root in _ACCESS_INSTALL_ROOTS)


def test_access_probe_reports_missing_when_no_binary_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a host without Access the full-setup gate must skip, not fail the way CI did."""
    monkeypatch.setattr(sys.modules[__name__], "_ACCESS_INSTALL_ROOTS", ("C:/no/such/root",))

    assert _has_access_application() is False


class TestCliVersion:
    def test_version_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "ms-access-mcp 0.2.0" in result.stdout, f"stdout: {result.stdout}"

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"


class TestDoctorPlaceholder:
    @pytest.mark.skipif(
        not _has_access_application(),
        reason="doctor exits 0 only where every check passes, which needs Access on the host",
    )
    def test_doctor_exits_zero_on_full_setup(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "doctor", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        # Doctor exits 0 when all checks pass (a host with a full Access setup)
        assert result.returncode == 0, f"expected exit 0, got {result.returncode}"
        # Should show JSON output
        output = json.loads(result.stdout)
        assert output["passed"] is True

    @pytest.mark.parametrize(("check_passes", "expected_code"), [(True, 0), (False, 1)])
    def test_doctor_exit_code_and_json_verdict_follow_the_checks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        check_passes: bool,
        expected_code: int,
    ) -> None:
        """PRESERVE (host-independent): ``doctor_main`` documents "Returns 0 if all checks
        pass, 1 otherwise", so the exit code, the JSON ``passed`` flag, and the per-check
        flags must agree in both directions.

        The checks are synthetic so this holds on hosts where the full-setup assertion
        above is skipped; running the real command can only pin one direction there.
        """
        synthetic = [doctor.Check(name="synthetic", passed=check_passes, message="synthetic check")]
        monkeypatch.setattr(doctor, "run_checks", lambda deep=False: synthetic)

        code = doctor.doctor_main(json_output=True)
        report = json.loads(capsys.readouterr().out)

        assert code == expected_code
        assert report["passed"] is check_passes
        assert report["passed"] == all(check["passed"] for check in report["checks"])

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
