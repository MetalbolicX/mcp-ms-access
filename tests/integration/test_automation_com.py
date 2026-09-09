"""Real-COM integration tests for the automation profile.

These tests run against a hidden Access.Application and require:
- Windows x64 + Microsoft Access (or Access Runtime)
- pywin32 (already a project dep on win32)
- The canonical fixture at tests/fixtures/test_db.accdb

Each test uses the temp_db_copy fixture so the canonical fixture is
never mutated. They are guarded by the com_integration marker so they
do NOT run by default with `pytest tests/unit`. Run via:

    uv run pytest tests/integration -q -m com_integration

per plans/006-automation-profile.md step 5.
"""

from __future__ import annotations

import subprocess
import threading
import time

import pytest

# The conftest at tests/integration/conftest.py applies
# skip_unless_windows / skip_unless_pywin32 / skip_unless_db via its own
# module-level pytestmark, so we only need to add the com_integration marker.
pytestmark = [pytest.mark.com_integration]


def _visible_access_handles() -> list[int]:
    """Return list of MainWindowHandle values for running MSACCESS processes."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process MSACCESS -ErrorAction SilentlyContinue | "
         "Where-Object { $_.MainWindowHandle -ne 0 } | "
         "Select-Object -ExpandProperty MainWindowHandle"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return [int(h) for h in out.stdout.split() if h.strip().isdigit()]


def _orphan_count() -> int:
    """Return count of running MSACCESS.EXE processes (visible or not)."""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE", "/NH"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return sum(1 for ln in out.stdout.splitlines() if "MSACCESS.EXE" in ln)


class TestHiddenSessionInvariant:
    """During form-open + compile workload, no Access window may be visible."""

    def test_no_visible_window_during_workload(self, temp_db_copy):
        from ms_access_mcp.backends.access import AccessAdapter

        poll_results: list[list[int]] = []
        stop = threading.Event()

        def poll() -> None:
            while not stop.is_set():
                h = _visible_access_handles()
                if h:
                    poll_results.append(h)
                stop.wait(0.05)

        poller = threading.Thread(target=poll, daemon=True)
        poller.start()

        try:
            adapter = AccessAdapter(str(temp_db_copy))
            adapter.connect()
            try:
                _ = adapter.get_forms()
                _ = adapter.get_reports()
                _ = adapter.get_macros()
                _ = adapter.get_modules()
                _ = adapter.compile_vba()
            finally:
                adapter.disconnect()
        finally:
            stop.set()
            poller.join(timeout=2.0)

        assert poll_results == [], (
            f"Found visible Access windows during workload: {poll_results}"
        )


class TestVBALifecycle:
    """Inject a compile error → structured error; fix → clean compile."""

    def test_broken_then_fixed_vba_round_trip(self, temp_db_copy):
        from ms_access_mcp.backends.access import AccessAdapter

        adapter = AccessAdapter(str(temp_db_copy))
        adapter.connect()
        try:
            bad_module = "TempBrokenModule_IntegrationTest"
            bad_code = (
                "Sub ThisWillNotCompile()\n"
                "    Dim x As Integer\n"
                "    x = \"not an integer\"\n"
                "End Sub\n"
            )
            good_code = (
                "Sub ThisWillCompile()\n"
                "    Dim x As Integer\n"
                "    x = 42\n"
                "End Sub\n"
            )

            created = adapter.create_module(bad_module, module_type=1)
            assert created is True
            assert adapter.set_vba_code(bad_module, bad_code) is True
            bad_result = adapter.compile_vba()
            assert isinstance(bad_result, dict)
            assert "success" in bad_result

            assert adapter.set_vba_code(bad_module, good_code) is True
            fixed_result = adapter.compile_vba()
            assert isinstance(fixed_result, dict)
            assert "success" in fixed_result

            adapter.delete_module(bad_module)
        finally:
            adapter.disconnect()


class TestSessionRounds:
    """10 open/close cycles leave no orphaned MSACCESS.EXE processes."""

    def test_no_orphans_after_10_rounds(self, temp_db_copy):
        from ms_access_mcp.backends.access import AccessAdapter

        # Pre-clean any stray
        subprocess.run(
            ["taskkill", "/F", "/IM", "MSACCESS.EXE"],
            capture_output=True, text=True, timeout=10,
        )
        time.sleep(0.5)

        for _ in range(10):
            adapter = AccessAdapter(str(temp_db_copy))
            adapter.connect()
            try:
                _ = adapter.get_forms()
                _ = adapter.get_reports()
                _ = adapter.get_macros()
                _ = adapter.get_modules()
            finally:
                adapter.disconnect()

        # Wait up to 10s for any lingering process to disappear
        settled = False
        for _ in range(20):
            time.sleep(0.5)
            if _orphan_count() == 0:
                settled = True
                break

        assert settled, (
            f"Orphan MSACCESS.EXE still present after 10s (count={_orphan_count()})"
        )


class TestFixtureHashDiscipline:
    """Canonical fixture must not be mutated by any automation test."""

    def test_canonical_hash_unchanged(self):
        import hashlib
        from pathlib import Path

        canonical = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "test_db.accdb"
        if not canonical.exists():
            pytest.skip(f"Canonical fixture not found: {canonical}")

        # Baseline hash captured during plan 006 implementation; the canonical
        # fixture must remain unchanged.
        actual = hashlib.sha256(canonical.read_bytes()).hexdigest()
        expected = "8e1cdd683fb9cc5003dbce77e436f4537ed428defaf9755642504430de4b14e9"
        assert actual == expected, (
            f"Canonical fixture hash changed!\n  expected: {expected}\n  actual:   {actual}"
        )
