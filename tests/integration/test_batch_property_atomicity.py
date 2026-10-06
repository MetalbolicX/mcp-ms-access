"""Live persistence acceptance for transactional form property batches."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
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
    if remaining & owned_pids:
        _stop_owned_pids(remaining & owned_pids)
    assert not remaining, f"{phase}: unexpected new MSACCESS.EXE PIDs: {sorted(remaining)}"
    print(f"ORPHAN CHECK {phase}: baseline restored; no new MSACCESS.EXE PIDs")


def test_batch_property_failure_is_rolled_back_live(temp_db_copy: str) -> None:
    pytest.importorskip("win32com")
    from ms_access_mcp.backends.access import AccessAdapter

    scratch_path = Path(temp_db_copy).resolve()
    temp_root = Path(os.environ["TEMP"]).resolve()
    assert scratch_path.is_relative_to(temp_root), f"database scratch must be under %TEMP%: {scratch_path}"
    print(f"SCRATCH DATABASE: {scratch_path}")

    baseline_pids = _msaccess_pids()
    owned_pids: set[int] = set()
    adapters = []
    lock_path = scratch_path.with_suffix(".laccdb")

    def connect(adapter: AccessAdapter) -> None:
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
        assert adapter.create_form("GateAtomicity") is True
        baseline_caption = adapter.get_form_properties("GateAtomicity").get("Caption")

        result = adapter.set_form_properties(
            "GateAtomicity",
            {"Caption": "transactional-batch-mutated", "ZzBogus": "x"},
        )
        assert any(value is False for key, value in result.items() if key != "rolled_back"), result
        assert result.get("rolled_back") is True, result
        print(f"BATCH FAILURE: per-property failure observed; rolled_back=True; baseline={baseline_caption!r}")

        adapter.disconnect()
        _assert_no_new_access(baseline_pids, owned_pids, "before-reopen")
        assert not lock_path.exists(), f"stale Access lock remains before reopen: {lock_path}"

        reopened = AccessAdapter(str(scratch_path))
        connect(reopened)
        persisted_caption = reopened.get_form_properties("GateAtomicity").get("Caption")
        assert persisted_caption == baseline_caption, (
            f"Caption changed after failed batch: baseline={baseline_caption!r}, "
            f"persisted={persisted_caption!r}"
        )
        print("PERSISTENCE: failed batch left Caption at baseline after reconnect")
    finally:
        for adapter in reversed(adapters):
            if adapter.connected:
                adapter.disconnect()
        time.sleep(3)
        remaining = _msaccess_pids() - baseline_pids
        if remaining & owned_pids:
            _stop_owned_pids(remaining & owned_pids)
            time.sleep(3)
        remaining_after_cleanup = _msaccess_pids() - baseline_pids
        assert not remaining_after_cleanup, (
            "ORPHAN CHECK final: unexpected new MSACCESS.EXE PIDs remain: "
            f"{sorted(remaining_after_cleanup)}"
        )
        assert not lock_path.exists(), f"temporary Access lock was not cleaned: {lock_path}"
        print("ORPHAN CHECK final: baseline restored; scratch lock removed")
