"""Unit tests for the reusable dialog watcher (``tests/helpers/dialog_watcher.py``).

The watcher is the control that makes the SQLite ODBC link suite unattended: it must
detect real driver dialogs, close them, never touch an unrelated process's window, and
never let a step hang. These tests exercise those rules directly, with a real in-process
``MessageBoxW`` positive control and a forged recycled-pid tree.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.helpers import dialog_watcher
from tests.helpers.dialog_watcher import (
    CANARY,
    EXPECTED_DRIVER_DIALOG_CLASS,
    EXPECTED_DRIVER_DIALOG_TITLE,
    POSITIVE_CONTROL_TITLE,
    Dismissal,
    WatchResult,
    WindowInfo,
    assert_expected_driver_dialog,
    assert_no_dialogs,
    descendants_of,
    find_visible_windows,
    positive_control_argv,
    redact,
    run_watched,
    terminate_tree,
    validate_positive_control,
    window_exists,
)

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only test")


def _watch_result(
    *,
    dialogs: list[WindowInfo] | None = None,
    dismissals: list[Dismissal] | None = None,
    timed_out: bool = False,
    cap_exceeded: bool = False,
) -> WatchResult:
    """Build a WatchResult for the pure assertion rules."""
    return WatchResult(
        argv=["demo"],
        cwd=".",
        returncode=0,
        stdout="",
        stderr="",
        dialogs=list(dialogs or []),
        dismissals=list(dismissals or []),
        timed_out=timed_out,
        cap_exceeded=cap_exceeded,
    )


def _dialog(title: str, class_name: str = "#32770") -> WindowInfo:
    """Build a WindowInfo with a given identity."""
    return WindowInfo(
        hwnd=0x1234,
        owner_pid=4242,
        owner_image="python.exe",
        class_name=class_name,
        title=title,
    )


# --------------------------------------------------------------------------- #
# pure rules (cross-platform)
# --------------------------------------------------------------------------- #


def test_redact_removes_the_canary_and_every_pwd_segment() -> None:
    """Every recorded string loses the fake canary and any ``PWD=`` segment."""
    cleaned = redact("Connect=ODBC;Database=remote.sqlite;PWD=canary-7f3a; note canary-7f3a")
    assert "canary-7f3a" not in cleaned
    assert "canary" not in cleaned
    assert "PWD=<REDACTED>" in cleaned
    assert "Database=remote.sqlite" in cleaned


def test_creation_order_guard_rejects_a_forged_parent_edge() -> None:
    """A pid that claims a raw parent it predates is rejected as recycled, not watched."""
    tree = {10: (0, "root.exe"), 20: (10, "child.exe"), 30: (10, "stale.exe")}
    values = {10: 100, 20: 200, 30: 50}
    accepted, rejected = descendants_of(tree, 10, creation_lookup=values.get)
    assert accepted == {20}
    assert [item["pid"] for item in rejected] == [30]
    assert rejected[0]["raw_parent_pid"] == 10
    assert "pid-reuse" in rejected[0]["reason"]


def test_unresolvable_creation_time_is_not_accepted_as_a_descendant() -> None:
    """A candidate whose creation time cannot be read is refused rather than trusted."""
    tree = {10: (0, "root.exe"), 20: (10, "child.exe")}
    accepted, rejected = descendants_of(tree, 10, creation_lookup={10: 100}.get)
    assert accepted == set()
    assert [item["pid"] for item in rejected] == [20]


def test_expected_driver_dialog_accepts_the_named_identity() -> None:
    """The named SQLite driver dialog, observed and closed, is accepted."""
    dialog = _dialog(EXPECTED_DRIVER_DIALOG_TITLE, EXPECTED_DRIVER_DIALOG_CLASS)
    result = _watch_result(dialogs=[dialog], dismissals=[Dismissal(hwnd=dialog.hwnd, posted_wm_close=True, elapsed_ms=250)])
    assert assert_expected_driver_dialog(result, "expected dialog") == dialog


def test_expected_driver_dialog_rejects_a_wrong_identity_and_records_it() -> None:
    """A wrong title/class fails the control and the recorded identity is in the message."""
    dialog = _dialog("Some Other Dialog")
    result = _watch_result(dialogs=[dialog], dismissals=[Dismissal(hwnd=dialog.hwnd, posted_wm_close=True, elapsed_ms=250)])
    with pytest.raises(AssertionError, match="Some Other Dialog") as excinfo:
        assert_expected_driver_dialog(result, "wrong title")
    assert "owner_pid=4242" in str(excinfo.value)


def test_missing_dialog_is_never_a_pass() -> None:
    """A control run that sees no dialog always fails loudly."""
    empty = _watch_result()
    with pytest.raises(AssertionError, match="observed no dialog"):
        validate_positive_control(empty)
    with pytest.raises(AssertionError, match="expected the deliberate locked read"):
        assert_expected_driver_dialog(empty, "control")
    assert_no_dialogs(empty, "clean step")


# --------------------------------------------------------------------------- #
# real Windows behavior
# --------------------------------------------------------------------------- #


@windows_only
def test_positive_control_messagebox_is_detected_and_closed(tmp_path: Path) -> None:
    """The watcher detects and closes a real in-process MessageBoxW dialog."""
    result = dialog_watcher.run_positive_control(cwd=tmp_path)
    verdict = validate_positive_control(result)
    assert "detected and closed" in verdict
    assert len(result.dialogs) == 1
    assert result.dialogs[0].title == POSITIVE_CONTROL_TITLE
    assert result.dialogs[0].class_name == "#32770"
    assert any(item.posted_wm_close for item in result.dismissals)
    assert result.clean is False  # it saw exactly the control dialog
    assert result.healthy is True
    assert result.returncode == 0


@windows_only
def test_raw_streams_keep_a_non_pwd_leak_verbatim_while_watcher_records_are_redacted(
    tmp_path: Path,
) -> None:
    """The credential the suites assert on must arrive raw, so a leak is never pre-masked.

    :func:`redact` masks the canary under *any* key, so a ``Password=canary-7f3a`` leak
    scrubbed inside :func:`run_watched` would make the qualification's absence assertion
    pass vacuously. The watcher therefore returns the raw streams to the caller and
    redacts only its own records: argv, window identities/control texts it captured, and
    the failure text it formats.
    """
    leak = f"Password={CANARY}"
    result = run_watched(
        [
            sys.executable,
            "-c",
            f"import sys; sys.stdout.write({leak!r}); sys.stderr.write({leak!r})",
        ],
        cwd=tmp_path,
        timeout_seconds=30.0,
    )
    assert result.returncode == 0
    assert leak in result.stdout, "the raw stdout was redacted before the caller could assert it"
    assert leak in result.stderr, "the raw stderr was redacted before the caller could assert it"
    assert CANARY not in "".join(result.argv), "the watcher's own argv record is not redacted"
    diagnostic = _watch_result(dialogs=[_dialog(leak)]).dialog_summary()
    assert CANARY not in diagnostic, "the watcher's own dialog diagnostic is not redacted"
    assert "<REDACTED>" in diagnostic


@windows_only
def test_non_descendant_windows_are_never_reported_or_closed(tmp_path: Path) -> None:
    """A window owned by an unrelated process is neither collected nor closed."""
    control = subprocess.Popen(  # noqa: S603 - exact argv, no shell
        positive_control_argv(),
        cwd=str(tmp_path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        hwnd = _wait_for_control_window(control.pid)
        assert window_exists(hwnd)
        result = run_watched(
            [sys.executable, "-c", "import time; time.sleep(1.5)"],
            cwd=tmp_path,
            timeout_seconds=30.0,
        )
        assert result.dialogs == []
        assert_no_dialogs(result, "unrelated sleeper")
        assert window_exists(hwnd), "the watcher closed a window it does not own"
    finally:
        terminate_tree(control.pid)
        control.wait(timeout=30)


@windows_only
def test_hung_step_is_killed_and_reported_as_failure(tmp_path: Path) -> None:
    """A step that never returns is terminated and reported instead of blocking forever."""
    result = run_watched(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        timeout_seconds=1.5,
        poll_seconds=0.05,
    )
    assert result.timed_out is True
    assert result.terminated_pids
    assert result.returncode is not None
    assert result.duration_ms < 30000
    with pytest.raises(AssertionError, match="timeout"):
        assert_no_dialogs(result, "hung sleeper")


@windows_only
def test_dialog_cap_is_bounded_and_reported(tmp_path: Path) -> None:
    """Repeats beyond the dismissal cap stop the step and report a failure."""
    result = run_watched(
        positive_control_argv(),
        cwd=tmp_path,
        max_dismissals=0,
        timeout_seconds=30.0,
    )
    assert result.cap_exceeded is True
    assert [dialog.title for dialog in result.dialogs] == [POSITIVE_CONTROL_TITLE]
    assert result.terminated_pids
    with pytest.raises(AssertionError, match="cap exceeded"):
        assert_no_dialogs(result, "repeat control")


def _wait_for_control_window(root_pid: int, timeout_seconds: float = 15.0) -> int:
    """Poll the watched tree for the positive-control dialog and return its hwnd."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for window in find_visible_windows(root_pid):
            if window.title == POSITIVE_CONTROL_TITLE:
                return window.hwnd
        time.sleep(0.05)
    raise AssertionError("positive-control dialog never appeared")
