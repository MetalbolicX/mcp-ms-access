"""Reusable, validated Windows dialog watcher for test-launched process trees.

Why this exists
---------------
The x64 ``SQLite3 ODBC Driver`` raises a modal ``SQLite3 ODBC Driver Connect`` window
(class ``#32770``) as soon as a caller tries to reach a source it cannot open, which
means a human must dismiss it before the call returns. A qualification suite that can
be stopped by such a dialog is not unattended. This module watches only the process
tree it launched, records every visible top-level window owned by that tree, and closes
each one with ``WM_CLOSE`` after a short delay (bounded repeat cap). It never inspects
or closes a window of an unrelated process.

The watcher is itself validated: :func:`run_positive_control` launches a harmless child
that opens a real ``MessageBoxW`` in its own process, and
:func:`validate_positive_control` fails loudly unless that dialog was detected *and*
closed. A run that never sees the expected control dialog never passes as "no dialog".

How it stays honest
-------------------
* Descendants are resolved from a ``CreateToolhelp32Snapshot`` tree with creation-time
  guards (``GetProcessTimes``): an accepted parent->child edge must satisfy
  ``child_created >= parent_created``, so a recycled pid that a stale
  ``th32ParentProcessID`` now points at is rejected instead of watched.
* The launched root pid is only trusted while its creation time still matches the value
  observed at launch.
* Every text the watcher itself records is passed through :func:`redact`, which removes
  the fake canary and any ``PWD=`` segment: ``argv``, captured window titles and control
  texts, and the diagnostic/failure messages it formats.
* The subprocess ``stdout``/``stderr`` are returned **raw and unredacted**. Tests must
  assert credential absence on those bytes themselves; masking them here would turn a
  leak hidden under any key other than ``PWD=`` into a vacuous pass. A caller that shows
  captured output in a failure message formats that message through :func:`redact`.

The repository ``.venv/Scripts/python.exe`` is a uv shim that spawns the real
interpreter as a *child*, so window owners are normally descendants of the launched pid
rather than the launched pid itself; the tree resolution handles that without special
casing.

Public API
----------
``run_watched``               launch one command under the watcher and return everything.
``run_positive_control``      the MessageBoxW control that validates the watcher.
``validate_positive_control`` raise unless the control dialog was detected and closed.
``assert_no_dialogs``         raise on any dialog, timeout, or cap breach.
``assert_expected_driver_dialog``
                              require exactly the expected SQLite driver dialog.
``descendants_of``            pure creation-time-guarded tree resolution (injectable).
``collect_windows`` / ``find_visible_windows`` / ``window_exists`` / ``terminate_tree``
                              read-only Win32 helpers for tests and controls.
``redact``                    canary/``PWD`` redaction for every string the watcher itself records
                              (argv, window text, failure text); never applied to raw stdout/stderr.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

IS_WINDOWS = os.name == "nt"

CANARY = "canary-7f3a"
EXPECTED_DRIVER_DIALOG_TITLE = "SQLite3 ODBC Driver Connect"
EXPECTED_DRIVER_DIALOG_CLASS = "#32770"
POSITIVE_CONTROL_TITLE = "dialog-watcher-positive-control"

WM_CLOSE = 0x0010
POLL_SECONDS = 0.1
DISMISS_DELAY_SECONDS = 0.5
MAX_DISMISSALS = 5
DEFAULT_TIMEOUT_SECONDS = 300.0
CONTROL_TIMEOUT_SECONDS = 60.0
MAX_CHILD_CONTROLS = 40

_PWD_RE = re.compile(r"(?i)\bPWD=[^;]*")


def redact(text: str) -> str:
    """Remove the fake canary and any ``PWD=`` segment from a recorded string."""
    return _PWD_RE.sub("PWD=<REDACTED>", text.replace(CANARY, "<REDACTED>"))


@dataclass(frozen=True)
class WindowInfo:
    """A visible top-level window owned by the watched process tree."""

    hwnd: int
    owner_pid: int
    owner_image: str
    class_name: str
    title: str
    child_controls: tuple[dict[str, str], ...] = ()
    elapsed_ms: int = 0

    def describe(self) -> str:
        """Return the redacted title/class/owner identity used in failure messages."""
        return (
            f"title={redact(self.title)!r} class={self.class_name!r} owner_pid={self.owner_pid} "
            f"owner_image={self.owner_image!r} hwnd=0x{self.hwnd:X}"
        )


@dataclass(frozen=True)
class Dismissal:
    """One recorded ``WM_CLOSE`` attempt for a watched window."""

    hwnd: int
    posted_wm_close: bool
    elapsed_ms: int


@dataclass
class WatchResult:
    """Everything one watched command run produced."""

    argv: list[str]
    cwd: str
    returncode: int | None
    stdout: str  # raw, unredacted child stdout; callers assert credential absence on it
    stderr: str  # raw, unredacted child stderr; callers assert credential absence on it
    dialogs: list[WindowInfo] = field(default_factory=list)
    dismissals: list[Dismissal] = field(default_factory=list)
    timed_out: bool = False
    cap_exceeded: bool = False
    terminated_pids: list[int] = field(default_factory=list)
    rejected_pids: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def healthy(self) -> bool:
        """True when the step neither hung nor exceeded the dismissal cap."""
        return not self.timed_out and not self.cap_exceeded

    @property
    def clean(self) -> bool:
        """True when the step also observed zero dialogs."""
        return self.healthy and not self.dialogs

    def dialog_summary(self) -> str:
        """Return a redacted one-line summary of every observed dialog."""
        if not self.dialogs:
            return "none"
        return "; ".join(dialog.describe() for dialog in self.dialogs)


if IS_WINDOWS:  # pragma: no cover - exercised on Windows only
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_TERMINATE = 0x0001
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    class PROCESSENTRY32W(ctypes.Structure):
        """Win32 ``PROCESSENTRY32W``; only the parent pid and image name are used."""

        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
    user32.EnumChildWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL

    def creation_time(pid: int) -> int | None:
        """Return the process creation time in 100 ns ticks, or ``None`` if unavailable."""
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            creation, exit_time, kernel, user = (
                wintypes.FILETIME(),
                wintypes.FILETIME(),
                wintypes.FILETIME(),
                wintypes.FILETIME(),
            )
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not ok:
                return None
            return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        finally:
            kernel32.CloseHandle(handle)

    def process_snapshot() -> dict[int, tuple[int, str]]:
        """Return ``pid -> (raw_parent_pid, image_name)`` for every live process."""
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == INVALID_HANDLE_VALUE:
            return {}
        processes: dict[int, tuple[int, str]] = {}
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                processes[int(entry.th32ProcessID)] = (
                    int(entry.th32ParentProcessID),
                    str(entry.szExeFile),
                )
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return processes

    def process_image(pid: int) -> str:
        """Return the image name of ``pid`` from a fresh snapshot, or ``""``."""
        return process_snapshot().get(pid, (0, ""))[1]

    def terminate_tree(root_pid: int) -> list[int]:
        """Terminate every descendant of ``root_pid`` and then ``root_pid`` itself."""
        related, _ = descendants_of(process_snapshot(), root_pid)
        terminated: list[int] = []
        for pid in [*sorted(related, reverse=True), root_pid]:
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if not handle:
                continue
            try:
                if kernel32.TerminateProcess(handle, 1):
                    terminated.append(pid)
            finally:
                kernel32.CloseHandle(handle)
        return terminated

    def _window_text(hwnd: int) -> str:
        """Return the redacted text of a window or control."""
        length = user32.GetWindowTextLengthW(hwnd)
        size = length + 2 if length > 0 else 256
        buffer = ctypes.create_unicode_buffer(size)
        user32.GetWindowTextW(hwnd, buffer, size)
        return redact(buffer.value)

    def _class_name(hwnd: int) -> str:
        """Return the window class name."""
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value

    def _child_controls(hwnd: int) -> tuple[dict[str, str], ...]:
        """Return the class/text of the first bounded set of descendant controls."""
        controls: list[dict[str, str]] = []

        def _collect(child: int, _lparam: int) -> bool:
            if len(controls) < MAX_CHILD_CONTROLS:
                controls.append({"class": _class_name(child), "text": _window_text(child)})
            return True

        user32.EnumChildWindows(hwnd, WNDENUMPROC(_collect), 0)
        return tuple(controls)

    def collect_windows(target_pids: set[int]) -> list[WindowInfo]:
        """Return visible top-level windows owned by any pid in ``target_pids``.

        Only pids explicitly listed are inspected, so an unrelated process's window can
        never be reported (and therefore never closed) by mistake.
        """
        found: list[WindowInfo] = []

        def _collect(hwnd: int, _lparam: int) -> bool:
            if not hwnd or not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            owner = int(pid.value)
            if owner not in target_pids:
                return True
            found.append(
                WindowInfo(
                    hwnd=int(hwnd),
                    owner_pid=owner,
                    owner_image=process_image(owner),
                    class_name=_class_name(hwnd),
                    title=_window_text(hwnd),
                    child_controls=_child_controls(hwnd),
                )
            )
            return True

        user32.EnumWindows(WNDENUMPROC(_collect), 0)
        return found

    def watched_pids(root_pid: int) -> set[int]:
        """Return ``root_pid`` plus every creation-validated descendant."""
        related, _ = descendants_of(process_snapshot(), root_pid)
        return {root_pid} | related

    def find_visible_windows(root_pid: int) -> list[WindowInfo]:
        """Return every visible window owned by ``root_pid`` or its validated tree."""
        return collect_windows(watched_pids(root_pid))

    def window_exists(hwnd: int) -> bool:
        """Return True when ``hwnd`` is still a live window."""
        return bool(user32.IsWindow(hwnd))

    def post_close(hwnd: int) -> bool:
        """Post ``WM_CLOSE`` to ``hwnd``; return False when it is already gone."""
        if not user32.IsWindow(hwnd):
            return False
        return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))

else:  # pragma: no cover - non-Windows import safety
    user32 = kernel32 = None

    def _windows_only(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("dialog_watcher requires Windows")

    creation_time = process_snapshot = process_image = terminate_tree = _windows_only
    collect_windows = watched_pids = find_visible_windows = window_exists = post_close = _windows_only


def _require_windows() -> None:
    """Raise when the watcher is used outside Windows."""
    if not IS_WINDOWS:
        raise RuntimeError("dialog_watcher requires Windows")


def descendants_of(
    tree: dict[int, tuple[int, str]],
    root_pid: int,
    *,
    creation_lookup: Callable[[int], int | None] | None = None,
) -> tuple[set[int], list[dict[str, Any]]]:
    """Return creation-validated descendants of ``root_pid`` plus rejected ppid matches.

    ``creation_lookup`` defaults to :func:`creation_time`; tests inject a deterministic
    mapping to exercise the recycled-pid guard without real processes.
    """
    lookup = creation_lookup or creation_time
    accepted: set[int] = set()
    rejected: list[dict[str, Any]] = []
    frontier: list[tuple[int, int | None]] = [(root_pid, lookup(root_pid))]
    while frontier:
        current, current_creation = frontier.pop()
        for pid, (parent, image) in tree.items():
            if parent != current or pid in accepted:
                continue
            pid_creation = lookup(pid)
            if current_creation is not None and pid_creation is not None and pid_creation >= current_creation:
                accepted.add(pid)
                frontier.append((pid, pid_creation))
            else:
                rejected.append(
                    {
                        "pid": pid,
                        "image": image,
                        "raw_parent_pid": parent,
                        "reason": "pid-reuse: child creation time does not follow raw parent creation time",
                    }
                )
    return accepted, rejected


def _root_identity_intact(pid: int, expected_creation: int | None) -> bool:
    """Return True while ``pid`` is still the exact process launched at start."""
    if expected_creation is None:
        return False
    return creation_time(pid) == expected_creation


def _drain(stream: Any, sink: list[bytes]) -> None:
    """Read ``stream`` to EOF so the child can never block on a full pipe."""
    if stream is None:
        return
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            sink.append(chunk)
    except (OSError, ValueError):
        return
    finally:
        try:
            stream.close()
        except OSError:
            pass


def run_watched(
    argv: list[str],
    *,
    cwd: str | Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    dismiss_delay_seconds: float = DISMISS_DELAY_SECONDS,
    max_dismissals: int = MAX_DISMISSALS,
) -> WatchResult:
    """Launch ``argv`` and watch its process tree for dialogs until it exits.

    The launched process gets a null stdin, so it can never wait on console input. Every
    dialog owned by the tree is recorded and closed with ``WM_CLOSE`` after
    ``dismiss_delay_seconds``; more than ``max_dismissals`` distinct dialogs, or a run
    longer than ``timeout_seconds``, marks the result as failed and terminates the tree.
    The returned ``stdout``/``stderr`` are the child's raw bytes; only the watcher's own
    records (``argv``, window text, its failure messages) are redacted.
    """
    _require_windows()
    command = [str(part) for part in argv]
    start = time.monotonic()
    process = subprocess.Popen(  # noqa: S603 - argv is caller-controlled, never a shell string
        command,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_thread = threading.Thread(target=_drain, args=(process.stdout, stdout_chunks), daemon=True)
    stderr_thread = threading.Thread(target=_drain, args=(process.stderr, stderr_chunks), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    root_creation = creation_time(process.pid)
    seen: set[int] = set()
    dialogs: list[WindowInfo] = []
    dismissals: list[Dismissal] = []
    pending: dict[int, float] = {}
    rejected: dict[int, dict[str, Any]] = {}
    terminated: list[int] = []
    timed_out = False
    cap_exceeded = False

    while True:
        now = time.monotonic()
        exited = process.poll() is not None
        tree = process_snapshot()
        related, rejected_edges = descendants_of(tree, process.pid)
        for item in rejected_edges:
            rejected.setdefault(int(item["pid"]), item)
        targets = set(related)
        if not exited or _root_identity_intact(process.pid, root_creation):
            targets.add(process.pid)
        for window in collect_windows(targets):
            if window.hwnd in seen:
                continue
            seen.add(window.hwnd)
            dialogs.append(replace(window, elapsed_ms=round((now - start) * 1000)))
            if len(seen) > max_dismissals:
                cap_exceeded = True
                break
            pending[window.hwnd] = now + dismiss_delay_seconds
        if cap_exceeded:
            break
        for hwnd in list(pending):
            if now >= pending[hwnd]:
                dismissals.append(
                    Dismissal(hwnd=hwnd, posted_wm_close=post_close(hwnd), elapsed_ms=round((now - start) * 1000))
                )
                del pending[hwnd]
        if exited and not pending:
            break
        if now - start > timeout_seconds:
            timed_out = True
            break
        time.sleep(poll_seconds)

    if timed_out or cap_exceeded:
        terminated = terminate_tree(process.pid)
    try:
        returncode = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        terminated = terminate_tree(process.pid) + terminated
        try:
            returncode = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            returncode = process.wait(timeout=10)
    stdout_thread.join(timeout=10)
    stderr_thread.join(timeout=10)

    return WatchResult(
        argv=[redact(part) for part in command],
        cwd=str(cwd),
        returncode=returncode,
        stdout=b"".join(stdout_chunks).decode("utf-8", "replace"),
        stderr=b"".join(stderr_chunks).decode("utf-8", "replace"),
        dialogs=dialogs,
        dismissals=dismissals,
        timed_out=timed_out,
        cap_exceeded=cap_exceeded,
        terminated_pids=terminated,
        rejected_pids=sorted(rejected.values(), key=lambda item: int(item["pid"])),
        duration_ms=round((time.monotonic() - start) * 1000),
    )


_POSITIVE_CONTROL_CODE = (
    "import ctypes;"
    "ctypes.windll.user32.MessageBoxW(0, 'dialog-watcher-positive-control',"
    " 'dialog-watcher-positive-control', 0)"
)


def positive_control_argv() -> list[str]:
    """Return the argv that opens the watcher's own MessageBoxW positive control."""
    return [sys.executable, "-c", _POSITIVE_CONTROL_CODE]


def run_positive_control(*, cwd: str | Path, timeout_seconds: float = CONTROL_TIMEOUT_SECONDS) -> WatchResult:
    """Run the watcher's positive control: a child opens a real MessageBoxW in-process."""
    return run_watched(positive_control_argv(), cwd=cwd, timeout_seconds=timeout_seconds)


def validate_positive_control(result: WatchResult) -> str:
    """Raise unless the positive-control dialog was detected and closed; return the verdict."""
    if result.timed_out:
        raise AssertionError(
            f"dialog watcher positive control timed out after {result.duration_ms} ms; no dialog was closed"
        )
    if not result.dialogs:
        raise AssertionError(
            "dialog watcher positive control observed no dialog, so the watcher is not proven "
            "to detect real windows and 'no dialog' claims would be vacuous"
        )
    if not any(item.posted_wm_close for item in result.dismissals):
        raise AssertionError(
            f"dialog watcher positive control detected {result.dialog_summary()} but posted no WM_CLOSE"
        )
    return f"watcher detected and closed the control dialog ({result.dialog_summary()})"


def assert_no_dialogs(result: WatchResult, step: str) -> None:
    """Fail with the recorded dialog identity when a step is not dialog-free and bounded."""
    if result.timed_out:
        raise AssertionError(
            f"{step}: step hit the {result.duration_ms} ms timeout and was terminated "
            f"(pids {result.terminated_pids}); dialogs seen: {result.dialog_summary()}"
        )
    if result.cap_exceeded:
        raise AssertionError(
            f"{step}: dismissal cap exceeded and the tree was terminated (pids {result.terminated_pids}); "
            f"dialogs seen: {result.dialog_summary()}"
        )
    if result.dialogs:
        raise AssertionError(f"{step}: unexpected dialog(s) auto-closed: {result.dialog_summary()}")


def assert_expected_driver_dialog(
    result: WatchResult,
    step: str,
    *,
    expected_title: str = EXPECTED_DRIVER_DIALOG_TITLE,
    expected_class: str = EXPECTED_DRIVER_DIALOG_CLASS,
) -> WindowInfo:
    """Require exactly the expected driver dialog, detected and auto-closed.

    A missing dialog is a loud control failure, never a pass: without it neither the lock
    tripwire nor the watcher's ability to see real driver dialogs is validated.
    """
    if result.timed_out:
        raise AssertionError(f"{step}: timed out after {result.duration_ms} ms; dialogs seen: {result.dialog_summary()}")
    if not result.dialogs:
        raise AssertionError(
            f"{step}: expected the deliberate locked read to raise the {expected_title!r} driver dialog "
            f"(class {expected_class!r}), but no dialog was observed; the tripwire and the watcher "
            f"are therefore NOT validated"
        )
    if len(result.dialogs) != 1:
        raise AssertionError(f"{step}: expected exactly one driver dialog, saw {result.dialog_summary()}")
    dialog = result.dialogs[0]
    if dialog.title != expected_title or dialog.class_name != expected_class:
        raise AssertionError(
            f"{step}: expected {expected_title!r} (class {expected_class!r}) but got {dialog.describe()}"
        )
    if not any(item.posted_wm_close for item in result.dismissals):
        raise AssertionError(f"{step}: dialog {dialog.describe()} was observed but never closed")
    return dialog
