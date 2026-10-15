"""Supervisor contract tests for the isolated documentation runtime facade.

These use the repository-owned fixture child (``tests/helpers/documentation_deadline_worker.py``)
so the supervisor's bounded IPC, deadline, kill/reap, and scrubbing behavior can be
observed without an Access fixture. The real production child is exercised separately by
the success tests (CLI/MCP) and the integration deadline suite.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from ms_access_mcp.application import documentation_limits
from ms_access_mcp.application.documentation_runtime import DocumentationRuntime
from ms_access_mcp.application.documentation_worker import decode_response
from ms_access_mcp.errors import AccessMcpError

HELPER = Path(__file__).resolve().parents[1] / "helpers" / "documentation_deadline_worker.py"
CANARY_SECRET = "canary-secret-9f3c"


def _launcher(mode: str, records: list[subprocess.Popen[bytes]]):
    def launch() -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, str(HELPER), "--mode", mode],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        records.append(process)
        return process

    return launch


def _inspect(runtime: DocumentationRuntime, *, tables: list[str] | None = None) -> Any:
    return runtime.inspect(
        "fixture.accdb",
        backend="dao",
        allowed_dirs=[Path("C:/db")],
        tables=tables,
        queries=None,
    )


class _UnreapableProcess:
    """Fake owned child that never exits and ignores termination."""

    returncode: int | None = None

    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"")
        self.stderr = io.BytesIO(b"")
        self.kills = 0

    def poll(self) -> None:
        return None

    def kill(self) -> None:
        self.kills += 1

    def wait(self, timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired("worker", timeout)


class _SurvivingPipeProcess:
    """Fake owned child that never exits, ignores termination, and owns a real pipe end.

    A real ``os.pipe`` end (kept open by the test) is wired as ``stdout`` or ``stdin``, so
    the supervisor's reader/writer thread stays blocked in a pipe call exactly as it does
    for a real owned child that survived termination while still holding its end.
    """

    returncode: int | None = None

    def __init__(self, *, stdin: Any = None, stdout: Any = None) -> None:
        self.stdin = io.BytesIO() if stdin is None else stdin
        self.stdout = io.BytesIO(b"") if stdout is None else stdout
        self.stderr = io.BytesIO(b"")
        self.kills = 0

    def poll(self) -> None:
        return None

    def kill(self) -> None:
        self.kills += 1

    def wait(self, timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired("worker", timeout)


def _call_bounded(
    runtime: DocumentationRuntime, *, tables: list[str] | None = None, timeout: float = 20.0
) -> list[BaseException]:
    """Run one inspect on a helper thread and return the error it raised.

    The supervisor must always return; running it on its own thread turns an unbounded
    wait into an observed assertion failure instead of a hung test process.
    """
    errors: list[BaseException] = []

    def call() -> None:
        try:
            _inspect(runtime, tables=tables)
        except BaseException as error:  # noqa: BLE001 - captured for the caller's assertions
            errors.append(error)

    caller = threading.Thread(target=call, daemon=True)
    caller.start()
    caller.join(timeout=timeout)
    assert not caller.is_alive(), "inspect never returned: the supervisor blocked on a child pipe"
    return errors


# ---------------------------------------------------------------------------
# 1. Bounded negotiation and request validation
# ---------------------------------------------------------------------------


def test_request_over_the_ipc_cap_is_rejected_before_spawning() -> None:
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("ok", records), deadline_seconds=30)
    huge_tables = ["t" * 512] * 4096

    with pytest.raises(AccessMcpError, match="IPC request limit"):
        _inspect(runtime, tables=huge_tables)
    assert records == []


def test_unknown_backend_is_rejected_before_spawning() -> None:
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("ok", records), deadline_seconds=30)

    with pytest.raises(AccessMcpError, match="backend"):
        runtime.inspect(
            "fixture.accdb",
            backend="com",
            allowed_dirs=[Path("C:/db")],
            tables=None,
            queries=None,
        )
    assert records == []


def test_decode_response_rejects_empty_and_unknown_shapes() -> None:
    with pytest.raises(AccessMcpError, match="empty"):
        decode_response(b"")
    with pytest.raises(AccessMcpError, match="malformed"):
        decode_response(b"{not json")
    with pytest.raises(AccessMcpError, match="boolean 'ok'"):
        decode_response(b'{"evidence": {}}')


# ---------------------------------------------------------------------------
# 2. Success path: evidence plus exact-child reap
# ---------------------------------------------------------------------------


def test_success_returns_evidence_and_reaps_the_owned_child() -> None:
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("ok", records), deadline_seconds=30)

    document = _inspect(runtime)

    assert document.tables == []
    assert document.status == "complete"
    assert len(records) == 1
    assert records[0].poll() is not None


# PRESERVE: the request is still delivered whole on the driver thread and stdin is still
# closed afterwards, exactly as the previous inline write did for a normal child.
def test_request_is_delivered_whole_and_stdin_is_still_closed() -> None:
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("ok", records), deadline_seconds=30)

    document = _inspect(runtime)

    # The "ok" fixture echoes the request's db_path, so this proves a complete request.
    assert document.source.path == "fixture.accdb"
    assert records[0].stdin is not None and records[0].stdin.closed is True
    assert records[0].poll() is not None


# ---------------------------------------------------------------------------
# 3. Deadline: kill only the owned child, no partial evidence
# ---------------------------------------------------------------------------


def test_deadline_kills_and_reaps_only_the_owned_child() -> None:
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("block_before_open", records), deadline_seconds=0.5)

    with pytest.raises(AccessMcpError, match="deadline"):
        _inspect(runtime)
    assert len(records) == 1
    assert records[0].poll() is not None


# ---------------------------------------------------------------------------
# 4. Bounded reading: oversize, malformed, non-zero, scrubbing
# ---------------------------------------------------------------------------


def test_oversized_response_is_rejected_without_truncated_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documentation_limits, "EVIDENCE_MAX_BYTES", 4096)
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("oversize", records), deadline_seconds=30)

    with pytest.raises(AccessMcpError, match="output limit") as excinfo:
        _inspect(runtime)
    assert "xxxx" not in str(excinfo.value)
    assert records[0].poll() is not None


def test_malformed_response_is_rejected() -> None:
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("malformed", records), deadline_seconds=30)

    with pytest.raises(AccessMcpError, match="malformed"):
        _inspect(runtime)
    assert records[0].poll() is not None


def test_nonzero_child_exit_is_generic_and_never_reflects_stderr() -> None:
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("nonzero", records), deadline_seconds=30)

    with pytest.raises(AccessMcpError) as excinfo:
        _inspect(runtime)
    message = str(excinfo.value)
    assert "exit code 2" in message
    assert CANARY_SECRET not in message
    assert records[0].poll() is not None


def test_application_error_message_is_credential_scrubbed() -> None:
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("secret", records), deadline_seconds=30)

    with pytest.raises(AccessMcpError) as excinfo:
        _inspect(runtime)
    message = str(excinfo.value)
    assert "PWD=***" in message
    assert CANARY_SECRET not in message
    assert records[0].poll() is not None


# ---------------------------------------------------------------------------
# 5. Cleanup that cannot be established is an explicit failure
# ---------------------------------------------------------------------------


def test_unestablishable_cleanup_returns_an_explicit_failure() -> None:
    process = _UnreapableProcess()
    runtime = DocumentationRuntime(
        launcher=lambda: process,  # type: ignore[arg-type,return-value]
        deadline_seconds=0.1,
        cleanup_grace_seconds=0.05,
    )

    with pytest.raises(AccessMcpError, match="cleanup could not be established"):
        _inspect(runtime)
    assert process.kills == 2


# RED/GREEN guards for the observed T4b hang: a surviving child must never make the
# supervisor wait without a bound on that child's pipe.
def test_surviving_child_with_a_live_reader_pipe_still_returns() -> None:
    """A child that outlives termination must fail fast instead of blocking in ``close``.

    ``_BoundedPipeReader`` blocks inside ``BufferedReader.read`` and ``BufferedReader``
    shares one internal lock between ``read`` and ``close``, so closing ``process.stdout``
    while that read is pending waits for a read that only returns at end-of-file -- which
    never comes while the owned child is still alive. Before the guard this call never
    returned (observed: an outer 20-second bound expired with ``inspect`` still blocked).
    The supervisor must instead report the explicit cleanup failure and return, leaving
    that one stream to its own daemon reader thread.
    """
    read_fd, write_fd = os.pipe()
    process = _SurvivingPipeProcess(stdout=os.fdopen(read_fd, "rb"))
    runtime = DocumentationRuntime(
        launcher=lambda: process,  # type: ignore[arg-type,return-value]
        deadline_seconds=0.1,
        cleanup_grace_seconds=0.05,
    )
    try:
        errors = _call_bounded(runtime)
        assert process.kills == 2
        assert len(errors) == 1 and isinstance(errors[0], AccessMcpError)
        assert "cleanup could not be established" in str(errors[0])
    finally:
        # Release the reader so its daemon thread can finish and close its own stream.
        os.close(write_fd)


def test_request_write_to_a_child_that_never_reads_stdin_is_bounded() -> None:
    """A request larger than the pipe capacity must not stall the deadline loop.

    The request was written inline before the deadline loop, so a child that stayed alive
    without draining its stdin blocked the caller inside ``write`` with no deadline
    coverage at all (observed: an outer bound expired with ``inspect`` still inside the
    write). The write now runs on a daemon thread joined within the cleanup grace, and a
    still-blocked write is the same explicit cleanup failure; no evidence is produced.
    """
    read_fd, write_fd = os.pipe()
    process = _SurvivingPipeProcess(stdin=os.fdopen(write_fd, "wb"))
    runtime = DocumentationRuntime(
        launcher=lambda: process,  # type: ignore[arg-type,return-value]
        deadline_seconds=0.1,
        cleanup_grace_seconds=0.05,
    )
    # 1024 x 512-character names stay under the 1 MiB request cap but far above the
    # operating system's anonymous-pipe capacity, so the write cannot complete.
    oversized_for_a_pipe = ["t" * 512] * 1024
    try:
        errors = _call_bounded(runtime, tables=oversized_for_a_pipe)
        assert process.kills == 2
        assert len(errors) == 1 and isinstance(errors[0], AccessMcpError)
        assert "cleanup could not be established" in str(errors[0])
    finally:
        # Close the read end so the blocked writer thread ends with a broken pipe.
        os.close(read_fd)
