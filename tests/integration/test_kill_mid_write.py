"""Integration test: kill-mid-write chaos harness (plan 047, Step 6).

Promotes the G13 chaos harness to a regression gate. Runs 5 rounds via
subprocess and asserts zero unrecovered FATAL verdicts.

Requires: Windows + pywin32 + test fixture DB.
Falls back to skip if any requirement is unmet.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip helpers (defined before pytestmark so the decorators can reference them)
# ---------------------------------------------------------------------------

def _import_pywin32() -> bool:
    try:
        import pythoncom  # noqa: F401
        import win32com  # noqa: F401
        return True
    except ImportError:
        return False


def _has_fixture_db() -> bool:
    p = Path(__file__).resolve().parents[1] / "fixtures" / "test_db.accdb"
    return p.exists()


pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows-only (COM/ACE required)"),
    pytest.mark.skipif(not _import_pywin32(), reason="Requires pywin32"),
    pytest.mark.skipif(not _has_fixture_db(), reason="No test fixture found"),
    pytest.mark.slow,
]


def test_kill_mid_write_regression() -> None:
    """Run the G13 chaos harness for 5 rounds and assert clean recovery.

    This is a promotion of scripts/g13_kill_mid_write.py to a regression gate.
    The harness classifies each round as CLEAN / LOCK_STALE / REPAIRABLE / FATAL.
    With Rungs 1-4 implemented, all observed failure classes should be recovered.
    """
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "g13_kill_mid_write.py"

    result = subprocess.run(
        [sys.executable, str(script_path), "--rounds", "5", "--seed", "42"],
        capture_output=True,
        text=True,
        timeout=300,
    )

    # Print output for debugging
    if result.stdout:
        print("STDOUT:", result.stdout, flush=True)
    if result.stderr:
        print("STDERR:", result.stderr, flush=True)

    assert result.returncode == 0, (
        f"Chaos harness exited {result.returncode}.\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
