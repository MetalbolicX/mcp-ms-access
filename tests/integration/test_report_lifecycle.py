"""Live lifecycle acceptance for report creation (098 leftover + defect #7).

Verifies against real Access that the forms-winner recipe persists
reports: CreateReport -> Close(acReport, name, acSaveYes) -> Rename.
Historically this was recorded as "reports do not persist live"; the
actual root cause was OBJECT_KINDS carrying ac_type=4 (acMacro) for
reports — Close(4, ...) opens a hidden modal dialog and the COM call
hangs. With the corrected acReport=3 the recipe persists.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(
        __import__("os").name != "nt",
        reason="Windows-only test",
    ),
    pytest.mark.com_integration,
]


def _msaccess_pids() -> set[int]:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            'Get-CimInstance Win32_Process -Filter "Name=\'MSACCESS.EXE\'" '
            "| Select-Object -ExpandProperty ProcessId",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return {int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()}


def _stop_owned_pids(pids: set[int]) -> None:
    for pid in pids:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )


def _assert_no_new_access(baseline: set[int], owned_pids: set[int], phase: str) -> None:
    time.sleep(3)
    remaining = _msaccess_pids() - baseline
    owned_remaining = remaining & owned_pids
    if owned_remaining:
        _stop_owned_pids(owned_remaining)
    assert not remaining, f"{phase}: new MSACCESS.EXE PIDs remained after disconnect: {sorted(remaining)}"
    print(f"ORPHAN CHECK {phase}: baseline restored; no new MSACCESS.EXE PIDs")


def _dao_report_names(adapter) -> list[str]:
    def read_names() -> list[str]:
        documents = adapter._dispatcher.current_db.Containers("Reports").Documents
        return [documents(index).Name for index in range(documents.Count)]

    return adapter._dispatcher.call(read_names)


def test_report_lifecycle_live(temp_db_copy: str) -> None:
    pytest.importorskip("win32com")
    from ms_access_mcp.backends.access import AccessAdapter

    scratch_path = Path(temp_db_copy).resolve()
    temp_root = Path(os.environ["TEMP"]).resolve()
    assert scratch_path.is_relative_to(temp_root), f"database scratch must be under %TEMP%: {scratch_path}"
    print(f"SCRATCH DATABASE: {scratch_path}")

    baseline = _msaccess_pids()
    owned_pids: set[int] = set()
    adapters = []
    lock_path = scratch_path.with_suffix(".laccdb")

    def connect(adapter) -> None:
        before = _msaccess_pids()
        adapter.connect(str(scratch_path))
        after = _msaccess_pids()
        pid = adapter._dispatcher._access_pid
        if pid is not None and pid in (after - before):
            owned_pids.add(pid)
        adapters.append(adapter)

    try:
        adapter = AccessAdapter(str(scratch_path))
        connect(adapter)

        created = adapter.create_report("GateReport")
        assert created is True, "adapter.create_report('GateReport') returned False"
        assert adapter.report_exists("GateReport") is True, "report_exists did not find GateReport"
        dao_names = _dao_report_names(adapter)
        assert "GateReport" in dao_names, f"DAO Reports container omitted GateReport: {dao_names}"
        listed_names = [report.name for report in adapter.get_reports()]
        assert "GateReport" in listed_names, f"get_reports omitted GateReport: {listed_names}"
        print("REPORT CREATE: create_report=True; report_exists=True; DAO Reports=True; get_reports=True")

        adapter.disconnect()
        _assert_no_new_access(baseline, owned_pids, "before-reopen")
        assert not lock_path.exists(), f"stale Access lock remains before reopen: {lock_path}"

        # Persistence ground truth (the 098 leftover claim): the report
        # must survive a full disconnect/reopen cycle.
        reopened = AccessAdapter(str(scratch_path))
        connect(reopened)
        assert reopened.report_exists("GateReport") is True, (
            "098 leftover: created report did not persist after disconnect and reopen"
        )
        assert "GateReport" in _dao_report_names(reopened), "GateReport missing from DAO container after reopen"
        print("PERSISTENCE: GateReport survived disconnect/reopen")

        assert reopened.rename_report("GateReport", "GateReportRenamed") is True, (
            "rename_report returned False"
        )
        assert reopened.report_exists("GateReportRenamed") is True, "renamed report not found"
        assert reopened.report_exists("GateReport") is False, "old report name still exists after rename"
        print("RENAME: GateReport -> GateReportRenamed persisted")

        assert reopened.delete_report("GateReportRenamed") is True, "delete_report returned False"
        assert reopened.report_exists("GateReportRenamed") is False, "deleted report still exists"
        print("DELETE: GateReportRenamed removed")
    finally:
        for adapter in reversed(adapters):
            if adapter.connected:
                adapter.disconnect()
        time.sleep(3)
        remaining = _msaccess_pids() - baseline
        owned_remaining = remaining & owned_pids
        if owned_remaining:
            _stop_owned_pids(owned_remaining)
            time.sleep(3)
        remaining_after_cleanup = _msaccess_pids() - baseline
        assert not remaining_after_cleanup, (
            "ORPHAN CHECK final: unexpected new MSACCESS.EXE PIDs remain: "
            f"{sorted(remaining_after_cleanup)}"
        )
        assert not lock_path.exists(), f"temporary Access lock was not cleaned: {lock_path}"
        print("ORPHAN CHECK final: baseline restored; scratch lock removed")
