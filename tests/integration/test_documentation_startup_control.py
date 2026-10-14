"""Deliberate, doubly gated control run for the executable startup fixture.

This is the *only* test that intentionally executes the fixture's VBA/AutoExec code.
Its purpose is to prove the embedded marker routine is genuinely executable, so the
non-execution results of the inspection pipeline are not vacuous. It runs only when
both opt-in gates are set:

* ``ACCESS_DOCUMENTATION_FIXTURES_TEST=1`` builds/reuses the synthetic fixtures; and
* ``ACCESS_STARTUP_CONTROL_TEST=1`` authorizes this one deliberate startup execution.

Isolation (standing constraint L3): execution happens only on a disposable *copy* of
``startup.accdb`` inside an isolated scratch subdirectory of the self-created session
tree; the copy is opened by ``Access.Application`` with ``AutomationSecurity`` set to
the low (macro-enabled) automation level, the bounded marker poll confirms execution,
and the run then quits Access (force-killing only the captured process if ``Quit``
hangs). The marker must appear beside the copy and nowhere else; the session original
and the rest of the repository must stay marker-free.

Run explicitly on Windows with ACE DAO and pywin32::

    $env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
    $env:ACCESS_STARTUP_CONTROL_TEST = '1'
    .venv\\Scripts\\python.exe -m pytest tests\\integration\\test_documentation_startup_control.py -q

Missing either gate (or Windows, or ACE DAO) skips cleanly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import _has_ace_dao

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts" / "generate_documentation_fixtures.py"
FIXTURE_ROOT = ROOT / "db" / "fixtures"

FIXTURES_GATE = "ACCESS_DOCUMENTATION_FIXTURES_TEST"
CONTROL_GATE = "ACCESS_STARTUP_CONTROL_TEST"
BUILD_TIMEOUT_SECONDS = 600
MARKER_POLL_SECONDS = 30.0
QUIT_KILL_SECONDS = 20.0
ACQUIRE_EXIT_SECONDS = 20.0

# msoAutomationSecurityLow: macros run without a prompt for this automation instance.
# This is required for the deliberate control execution; the generator's build phase
# never sets it and never has an AutoExec macro present while Access is open.
MSO_AUTOMATION_SECURITY_LOW = 1
# Directories never scanned for stray marker files (large or non-source trees).
_EXCLUDED_DIRS = {".git", ".venv", "build", "dist", "__pycache__", ".pytest_cache", ".ruff_cache"}

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.skipif(not _has_ace_dao(), reason="Requires ACE DAO (DAO.DBEngine.120)"),
    pytest.mark.skipif(
        os.environ.get(FIXTURES_GATE) != "1",
        reason=f"Set {FIXTURES_GATE}=1 to build the shared documentation fixtures",
    ),
    pytest.mark.skipif(
        os.environ.get(CONTROL_GATE) != "1",
        reason=f"Set {CONTROL_GATE}=1 to deliberately execute the startup fixture under isolation",
    ),
    pytest.mark.com_integration,
]


def _venv_python() -> str:
    """Return the repository virtualenv interpreter, falling back to the current one."""
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def _msaccess_pids() -> set[int]:
    """Return running MSACCESS.EXE process ids (empty when the filter matches none)."""
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE", "/NH"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    pids: set[int] = set()
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].upper() == "MSACCESS.EXE" and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _wait_for_owned_access_exit(owned: set[int], timeout: float) -> set[int]:
    """Poll until none of the owned MSACCESS.EXE processes remain, or timeout."""
    deadline = time.monotonic() + timeout
    remaining = set(owned)
    while remaining and time.monotonic() < deadline:
        time.sleep(0.5)
        remaining = _msaccess_pids() & owned
    return remaining


def _terminate_owned(owned: set[int]) -> None:
    """Force-kill exactly the captured Access PIDs; never a blanket ``/IM`` kill."""
    for pid in sorted(owned):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True, text=True, timeout=30, check=False,
        )


def _marker_files(root: Path) -> list[str]:
    """Return every ``*marker*`` file under ``root``, excluding large non-source trees."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _EXCLUDED_DIRS]
        for filename in filenames:
            if "marker" in filename:
                found.append(str(Path(dirpath, filename)))
    return sorted(found)


def _remove_self_created_tree(output_dir: Path, created_fixture_root: bool) -> None:
    """Delete exactly this fixture's ``e2e-<uuid>`` tree and nothing else."""
    assert output_dir.parent == FIXTURE_ROOT
    assert output_dir.name.startswith("e2e-")
    shutil.rmtree(output_dir, ignore_errors=True)
    if created_fixture_root:
        try:
            FIXTURE_ROOT.rmdir()
        except OSError:
            pass


@pytest.fixture(scope="session")
def startup_build() -> Iterator[dict[str, Any]]:
    """Build the fixtures once through the public generator CLI into a fresh directory."""
    output_dir = FIXTURE_ROOT / f"e2e-{uuid.uuid4().hex}"
    created_fixture_root = not FIXTURE_ROOT.exists()
    if created_fixture_root:
        FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            [_venv_python(), str(GENERATOR), "--build", "--output-dir", str(output_dir)],
            capture_output=True, text=True, timeout=BUILD_TIMEOUT_SECONDS, check=False, cwd=ROOT,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stderr == "", completed.stderr
        inventory = json.loads((output_dir / "inventory.json").read_text(encoding="utf-8"))
        assert not (output_dir / "startup_executed.marker").exists()
        yield {"directory": output_dir, "inventory": inventory}
    finally:
        _remove_self_created_tree(output_dir, created_fixture_root)


def test_control_run_executes_marker_under_isolation(startup_build: dict[str, Any]) -> None:
    """Open a disposable copy; AutoExec runs the marker routine and nothing leaks.

    Positive result: the marker appears beside the copy, proving the fixture genuinely
    executes. Isolation result: the marker exists only in the scratch directory, the
    session original and the rest of the repository stay marker-free, no ``.laccdb``
    remains, and no MSACCESS.EXE process started here survives.
    """
    pythoncom = pytest.importorskip("pythoncom")
    win32com_client = pytest.importorskip("win32com.client")

    session_dir = startup_build["directory"]
    scratch_dir = session_dir / "startup-control-scratch"
    scratch_dir.mkdir()
    original = session_dir / "startup.accdb"
    copy = scratch_dir / "startup.accdb"
    marker = scratch_dir / "startup_executed.marker"
    shutil.copy2(original, copy)
    assert not marker.exists(), "precondition: the disposable copy starts marker-free"
    assert len(_marker_files(session_dir)) == 0, "precondition: the session tree is marker-free"

    baseline = _msaccess_pids()
    pythoncom.CoInitialize()
    app: Any = None
    owned: set[int] = set()
    kill_watchdog: threading.Timer | None = None
    try:
        app = win32com_client.DispatchEx("Access.Application")
        app.Visible = False
        owned = _msaccess_pids() - baseline
        app.AutomationSecurity = MSO_AUTOMATION_SECURITY_LOW
        app.OpenCurrentDatabase(str(copy))
        deadline = time.monotonic() + MARKER_POLL_SECONDS
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.5)
        assert marker.exists(), (
            "the copy's AutoExec macro did not create the marker within "
            f"{MARKER_POLL_SECONDS:.0f}s"
        )
        assert marker.read_text(encoding="utf-8").strip() == "startup fixture executed"

        # Bound a hung Quit: force-kill only the process this test started. This kill path
        # uses baseline-diff ownership, so an unrelated MSACCESS.EXE that starts during the
        # control window would be misattributed as owned and killed, while an Access
        # instance already running when the test began stays baseline-protected.
        kill_watchdog = threading.Timer(QUIT_KILL_SECONDS, _terminate_owned, args=(owned,))
        kill_watchdog.daemon = True
        kill_watchdog.start()
        app.CloseCurrentDatabase()
        app.Quit()
        app = None
    finally:
        if kill_watchdog is not None:
            kill_watchdog.cancel()
        if app is not None:
            try:
                app.Quit()
            except Exception:
                _terminate_owned(owned)
        app = None
        pythoncom.CoUninitialize()

    remaining = _wait_for_owned_access_exit(owned, ACQUIRE_EXIT_SECONDS)
    if remaining:
        _terminate_owned(remaining)
        remaining = _wait_for_owned_access_exit(remaining, ACQUIRE_EXIT_SECONDS)

    assert not remaining, f"MSACCESS.EXE process(es) survived the control run: {sorted(remaining)}"
    assert not (scratch_dir / "startup.accdb.laccdb").exists()
    assert not (session_dir / "startup.accdb.laccdb").exists()

    expected_marker = str(marker)
    assert _marker_files(session_dir) == [expected_marker]
    assert not (session_dir / "startup_executed.marker").exists()
    assert _marker_files(ROOT) == [expected_marker]
