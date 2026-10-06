"""Live lifecycle acceptance for form creation and design-view mutations."""

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


def _dao_form_names(adapter) -> list[str]:
    def read_names() -> list[str]:
        documents = adapter._dispatcher.current_db.Containers("Forms").Documents
        return [documents(index).Name for index in range(documents.Count)]

    return adapter._dispatcher.call(read_names)


def test_form_lifecycle_live(temp_db_copy: str) -> None:
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

        created = adapter.create_form("GateForm")
        assert created is True, "adapter.create_form('GateForm') returned False"
        assert adapter.form_exists("GateForm") is True, "form_exists did not find GateForm"
        dao_names = _dao_form_names(adapter)
        assert "GateForm" in dao_names, f"DAO Forms container omitted GateForm: {dao_names}"
        listed_names = [form.name for form in adapter.get_forms()]
        assert "GateForm" in listed_names, f"get_forms omitted GateForm: {listed_names}"
        print("FORM CREATE: create_form=True; form_exists=True; DAO Forms=True; get_forms=True")

        assert adapter.add_control("GateForm", "CommandButton", "cmdTransient") is True
        assert adapter.remove_control("GateForm", "cmdTransient") is True
        adapter.disconnect()
        _assert_no_new_access(baseline, owned_pids, "before-reopen")
        assert not lock_path.exists(), f"stale Access lock remains before reopen: {lock_path}"

        reopened = AccessAdapter(str(scratch_path))
        connect(reopened)
        reopened_controls = reopened.get_form_controls("GateForm")
        assert "cmdTransient" not in {control.name for control in reopened_controls}, (
            f"092(a): removed control persisted after reopen: {reopened_controls}"
        )
        print("092(a): removed control absent after disconnect and reopen")

        assert reopened.add_control("GateForm", "CommandButton", "cmdEvent") is True
        assert reopened.set_form_property("GateForm", "HasModule", True) is True
        procedure_name = "cmdEvent_Click"
        assert reopened.vba_get_procedure("Form_GateForm", procedure_name) == {}, (
            "092(c): event procedure unexpectedly existed before creation"
        )
        code = 'Private Sub cmdEvent_Click()\n    Debug.Print "form lifecycle"\nEnd Sub'
        assert reopened.set_control_event_procedure(
            "GateForm", "cmdEvent", "Click", code
        ) is True, "092(c): set_control_event_procedure returned False"
        procedure = reopened.vba_get_procedure("Form_GateForm", procedure_name)
        assert procedure and "cmdEvent_Click" in procedure.get("code", ""), (
            f"092(c): event procedure stub/code missing: {procedure}"
        )
        control_properties = reopened.get_control_properties("GateForm", "cmdEvent")
        assert control_properties.get("OnClick") == "[Event Procedure]", (
            f"092(c): OnClick was not set after procedure creation: {control_properties}"
        )
        print("092(c): missing procedure created; OnClick=[Event Procedure]")
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
