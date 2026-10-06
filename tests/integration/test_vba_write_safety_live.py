"""Live VBA write-safety acceptance (092(b) operator confirmation, adjusted).

The plan-092(b) rollback path (restore prior module text when AddFromString
raises after DeleteLines) has no safe natural live fault seam: embedded NUL
bytes are silently truncated by COM marshaling (no raise), oversized strings
modal-hang hidden Access, and project passwords block reads rather than
writes. The rollback logic stays proven by fakes.

What IS verifiable live — and what this gate proves — is the end-to-end
safety property operators actually care about: a REJECTED VBA write leaves
the module's prior text intact. Defect #8 (silent NUL truncation) made the
opposite true before the fix: a poisoned write returned True and corrupted
the module.
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


CODE_ORIGINAL = (
    "Option Explicit\r\n"
    "\r\n"
    "Public Function GateFn() As String\r\n"
    "    GateFn = \"original\"\r\n"
    "End Function\r\n"
)


def _norm(text: str) -> str:
    """Normalize line endings and trailing whitespace for VBE read-back."""
    return text.replace("\r\n", "\n").strip()


def test_vba_write_safety_live(temp_db_copy: str) -> None:
    pytest.importorskip("win32com")
    from ms_access_mcp.backends.access import AccessAdapter

    scratch_path = Path(temp_db_copy).resolve()
    temp_root = Path(os.environ["TEMP"]).resolve()
    assert scratch_path.is_relative_to(temp_root), f"database scratch must be under %TEMP%: {scratch_path}"

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

        assert adapter.set_vba_code("GateMod", CODE_ORIGINAL) is True, "initial module write failed"
        assert _norm(adapter.get_vba_code("GateMod")) == _norm(CODE_ORIGINAL), "initial read-back mismatch"
        print("SETUP: GateMod written and read back byte-identical")

        poisoned = CODE_ORIGINAL.replace("original", "poisoned") + "\x00tail"
        assert "\x00" in poisoned
        assert adapter.set_vba_code("GateMod", poisoned) is False, (
            "defect #8: NUL-poisoned write was not rejected"
        )
        assert _norm(adapter.get_vba_code("GateMod")) == _norm(CODE_ORIGINAL), (
            "rejected NUL write corrupted the module (silent truncation)"
        )
        print("DEFECT #8: NUL-poisoned set_vba_code rejected; module text intact")

        assert adapter.add_vba_procedure("GateMod", "PoisonProc", "Sub PoisonProc()\n    \x00\nEnd Sub") is False, (
            "NUL-poisoned add_vba_procedure was not rejected"
        )
        assert _norm(adapter.get_vba_code("GateMod")) == _norm(CODE_ORIGINAL), "module text changed after rejected procedure add"
        print("DEFECT #8: NUL-poisoned add_vba_procedure rejected; module text intact")

        assert adapter.set_vba_code("GateMod", CODE_ORIGINAL.replace("original", "replaced")) is True, (
            "valid write after rejections failed"
        )
        assert "replaced" in adapter.get_vba_code("GateMod"), "valid write content missing"
        print("CONTROL: valid write still succeeds after rejections")
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
