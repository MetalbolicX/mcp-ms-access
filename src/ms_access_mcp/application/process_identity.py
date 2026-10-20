"""Fail-closed identity verification for Access processes owned by the documentation worker.

An armed (app-logic) documentation read instantiates ``Access.Application`` inside the
spawned Python child. A child that dies -- or is killed at the whole-inspection deadline --
can therefore orphan a real ``MSACCESS.EXE`` holding a lock on the disposable source copy.
The child announces every Access instance it owns as one ``@@ACCESS_IDENTITY@@`` frame
before it opens a source (``documentation_worker.emit_access_identity``); the parent
supervisor reaps that exact instance after it has reaped the Python child.

A recorded PID is never taken at face value. :func:`terminate_verified_access` kills
exactly one PID, and only when **all** of the following hold:

* the PID is not part of the supervisor's pre-spawn baseline,
* the live process creation time (100 ns ticks, 1601 epoch) equals the announced one,
* the live image name is ``MSACCESS.EXE``.

Every other outcome -- invalid PID, baseline PID, creation mismatch, image mismatch, or an
unreadable live identity -- performs no kill and is reported through
:attr:`AccessTermination.detail`. Kills use ``taskkill /F /PID`` and never ``/IM``; the
enumeration and kill shapes follow the established pattern in
``application/maintenance.py``. Nothing here opens a database, touches COM, or runs Access.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ACCESS_IMAGE_NAME = "MSACCESS.EXE"

# A process probe is a bounded, read-only helper call; the grace is the bounded wait for a
# verified kill to take effect and is supplied by the supervisor's cleanup budget.
_DEFAULT_COMMAND_TIMEOUT_SECONDS = 10.0
_POLL_INTERVAL_SECONDS = 0.1

# One bounded command invocation: argv (never a shell), timeout -> completed process.
CommandRunner = Callable[[Sequence[str], float], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class AccessProcessIdentity:
    """One live process identity read back from the operating system."""

    pid: int
    creation: int
    image: str


@dataclass(frozen=True)
class AccessBaseline:
    """Outcome of one pre-spawn ``MSACCESS.EXE`` baseline listing.

    ``pids`` is the baseline every kill decision is made against and is exactly the set
    :func:`list_msaccess_pids` returns. ``error_class`` is the class name of the swallowed
    enumeration error, or ``None`` when the listing succeeded -- so a genuinely empty
    baseline stays distinguishable from an unreadable one without changing either the
    baseline or its kill-denying semantics.
    """

    pids: frozenset[int]
    error_class: str | None


@dataclass(frozen=True)
class AccessTermination:
    """Outcome of one fail-closed owned-Access termination attempt.

    ``verified`` is True only when the live identity matched the announced identity.
    ``terminated`` is True only when a ``taskkill /F /PID`` was actually issued through
    this verified path. ``exited`` answers the supervisor's cleanup question: no owned
    Access instance of the announced identity is known to still be running (a verified
    kill that survived its grace, or an unreadable-but-alive identity, reports False).
    ``detail`` names the mismatch for the audit trail; it never carries credentials.
    """

    pid: int
    verified: bool
    terminated: bool
    exited: bool
    detail: str


def _default_runner(args: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run one bounded, shell-free process probe."""
    return subprocess.run(  # noqa: S603 - fixed argv built in this module, never a shell
        list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _resolve_runner(runner: CommandRunner | None) -> CommandRunner:
    return _default_runner if runner is None else runner


def list_msaccess_pids(
    *,
    runner: CommandRunner | None = None,
    timeout: float = _DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> set[int]:
    """Return the PIDs of the ``MSACCESS.EXE`` processes already running.

    This is the supervisor's pre-spawn baseline: any PID on it belongs to a caller-owned
    Access session and must never be killed by the documentation cleanup. Enumeration
    failure yields an empty set -- it never authorizes a kill by itself.

    A caller that must *report* the failure instead of only acting on the set uses
    :func:`read_msaccess_baseline`, which returns the same PIDs plus the swallowed error
    class. This function keeps its exact public behavior: a failed probe is an empty set.
    """
    return set(read_msaccess_baseline(runner=runner, timeout=timeout).pids)


def read_msaccess_baseline(
    *,
    runner: CommandRunner | None = None,
    timeout: float = _DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> AccessBaseline:
    """List the pre-spawn ``MSACCESS.EXE`` baseline and say whether listing itself failed.

    ``AccessBaseline.pids`` is byte-for-byte the PID set :func:`list_msaccess_pids` returns
    (and therefore the set every kill decision is made against): a failed enumeration is
    still the empty baseline, which only ever *denies* a kill. ``error_class`` carries the
    class name of the swallowed enumeration error so a genuinely empty baseline stays
    distinguishable from an unreadable one; the error message itself is never kept, because
    it can carry paths or credentials.
    """
    run = _resolve_runner(runner)
    try:
        result = run(
            ["tasklist", "/FI", f"IMAGENAME eq {ACCESS_IMAGE_NAME}", "/FO", "CSV", "/NH"],
            timeout,
        )
    except Exception as error:  # noqa: BLE001 - enumeration failure is reported as "no baseline"
        return AccessBaseline(pids=frozenset(), error_class=type(error).__name__)
    return AccessBaseline(
        pids=frozenset(_baseline_pids_from_tasklist(result.stdout or "")),
        error_class=None,
    )


def _baseline_pids_from_tasklist(stdout: str) -> set[int]:
    """Parse the PID column of every ``MSACCESS.EXE`` row in one ``tasklist /FO CSV`` output."""
    pids: set[int] = set()
    for line in stdout.splitlines():
        if ACCESS_IMAGE_NAME.casefold() not in line.casefold():
            continue
        fields = [field.strip().strip('"') for field in line.split(",")]
        if len(fields) >= 2 and fields[1].isdigit():
            pids.add(int(fields[1]))
    return pids


def read_process_identity(
    pid: int,
    *,
    runner: CommandRunner | None = None,
    timeout: float = _DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> AccessProcessIdentity | None:
    """Read one live process creation time and image name, or ``None`` when unreadable.

    ``tasklist`` exposes no creation time, so the probe uses PowerShell's
    ``StartTime.ToFileTime()``: the same 1601-epoch 100 ns ticks the worker announces.
    ``ProcessName`` carries no extension, so the image is normalized to ``NAME.EXE``.
    A PID that is not a positive integer is refused before any probe runs.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        msg = f"process identity probe requires a positive integer PID; got {pid!r}"
        raise ValueError(msg)
    run = _resolve_runner(runner)
    script = (
        "$p = Get-Process -Id " + str(pid) + " -ErrorAction Stop; "
        "'{0}|{1}' -f $p.StartTime.ToFileTime(), $p.ProcessName"
    )
    try:
        result = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout)
    except Exception:  # noqa: BLE001 - an unreadable identity is a reported refusal
        return None
    output = (result.stdout or "").strip()
    ticks_text, separator, name = output.partition("|")
    if not separator:
        return None
    ticks_text = ticks_text.strip()
    name = name.strip()
    if not ticks_text.isdigit() or not name:
        return None
    image = name if name.casefold().endswith(".exe") else f"{name}.EXE"
    return AccessProcessIdentity(pid=pid, creation=int(ticks_text), image=image)


def process_is_alive(
    pid: int,
    *,
    runner: CommandRunner | None = None,
    timeout: float = _DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> bool:
    """Return True when a PID is still observable.

    A failed probe reports True (still alive): cleanup cannot be *proved* established by
    an unreadable liveness check, and the supervisor treats that as an explicit failure.
    The parse is field-aware -- the PID must sit in the PID column next to an
    ``MSACCESS.EXE`` image column -- so a banner or another numeric column that merely
    contains the digits is never read as liveness.
    """
    run = _resolve_runner(runner)
    try:
        result = run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout)
    except Exception:  # noqa: BLE001 - see the docstring: unknown liveness is not "gone"
        return True
    return _tasklist_row_matches_access_pid(result.stdout or "", pid)


def _tasklist_row_matches_access_pid(stdout: str, pid: int) -> bool:
    """True when one row carries exactly ``pid`` in the PID column of ``MSACCESS.EXE``.

    Accepts the ``/FO CSV`` shape this module requests and the default table shape:
    column 1 is the image and column 2 is the PID, so digits appearing anywhere else
    (memory footprint, a "No tasks" banner) can never be mistaken for liveness.
    """
    expected_pid = str(pid)
    for line in stdout.splitlines():
        # ``/FO CSV /NH`` always quotes the image column; the default table shape does not,
        # and the table's memory column contains a comma, so a bare comma is not a delimiter.
        stripped = line.strip()
        fields = (
            [field.strip().strip('"') for field in stripped.split(",")]
            if stripped.startswith('"')
            else stripped.split()
        )
        if len(fields) < 2:
            continue
        if fields[0].casefold() != ACCESS_IMAGE_NAME.casefold():
            continue
        if fields[1] == expected_pid:
            return True
    return False


def terminate_verified_access(
    *,
    pid: int,
    creation: int,
    image: str,
    baseline_pids: frozenset[int] = frozenset(),
    runner: CommandRunner | None = None,
    grace_seconds: float = _DEFAULT_COMMAND_TIMEOUT_SECONDS,
    timeout: float = _DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> AccessTermination:
    """Kill exactly one identity-verified owned Access PID, or report why no kill happened.

    Fail-closed ordering: an invalid PID, a baseline PID, or a non-``MSACCESS.EXE``
    announced image is refused before any probe; a live identity that is unreadable or
    that does not match the announced creation time/image is refused without a kill. The
    only ``taskkill`` ever issued is ``taskkill /F /PID <pid>``, and the verified process
    is then waited for within ``grace_seconds``.
    """
    run = _resolve_runner(runner)
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return AccessTermination(
            pid=-1 if not isinstance(pid, int) else pid,
            verified=False,
            terminated=False,
            exited=True,
            detail="announced Access PID is not a positive integer; no kill was attempted",
        )
    if pid in baseline_pids:
        return AccessTermination(
            pid=pid,
            verified=False,
            terminated=False,
            exited=True,
            detail=(
                f"announced Access PID {pid} is part of the pre-spawn baseline of already "
                "running Access processes; no kill was attempted"
            ),
        )
    if Path(str(image)).name.casefold() != ACCESS_IMAGE_NAME.casefold():
        return AccessTermination(
            pid=pid,
            verified=False,
            terminated=False,
            exited=True,
            detail=(
                f"announced image {image!r} is not {ACCESS_IMAGE_NAME}; no kill was attempted"
            ),
        )
    live = read_process_identity(pid, runner=run, timeout=timeout)
    if live is None:
        alive = process_is_alive(pid, runner=run, timeout=timeout)
        return AccessTermination(
            pid=pid,
            verified=False,
            terminated=False,
            exited=not alive,
            detail=(
                f"live identity of announced Access PID {pid} is unreadable and the process "
                f"is{' still running' if alive else ' not observable'}; no kill was attempted"
            ),
        )
    if live.creation != creation or live.image.casefold() != ACCESS_IMAGE_NAME.casefold():
        return AccessTermination(
            pid=pid,
            verified=False,
            terminated=False,
            exited=True,
            detail=(
                f"live process identity (creation={live.creation}, image={live.image!r}) does "
                f"not match the announced identity (creation={creation}, image={image!r}); "
                "no kill was attempted"
            ),
        )
    try:
        run(["taskkill", "/F", "/PID", str(pid)], timeout)
    except Exception as error:  # noqa: BLE001 - a failed kill is reported, never retried blindly
        alive = process_is_alive(pid, runner=run, timeout=timeout)
        return AccessTermination(
            pid=pid,
            verified=True,
            terminated=False,
            exited=not alive,
            detail=(
                f"identity-verified Access PID {pid} termination failed "
                f"({type(error).__name__}); the process is"
                f"{' still running' if alive else ' not observable'}"
            ),
        )
    exited = _wait_for_exit(pid, grace_seconds, run, timeout)
    return AccessTermination(
        pid=pid,
        verified=True,
        terminated=True,
        exited=exited,
        detail=(
            f"identity-verified Access PID {pid} terminated by PID-scoped taskkill"
            if exited
            else (
                f"identity-verified Access PID {pid} survived the {grace_seconds:g}-second "
                "cleanup grace after taskkill /F /PID"
            )
        ),
    )


def _wait_for_exit(pid: int, grace_seconds: float, run: CommandRunner, timeout: float) -> bool:
    """Wait for a terminated PID to disappear within a bounded grace, never unbounded."""
    deadline = time.monotonic() + max(float(grace_seconds), 0.0)
    while True:
        if not process_is_alive(pid, runner=run, timeout=timeout):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_INTERVAL_SECONDS)
