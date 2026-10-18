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
from ms_access_mcp.application.documentation import (
    Coverage,
    EvidenceDoc,
    RunMetadata,
    SourceEvidence,
)
from ms_access_mcp.application.documentation_runtime import DocumentationRuntime
from ms_access_mcp.application.documentation_worker import (
    build_request,
    decode_request,
    decode_response,
    decode_response_with_identities,
    emit_access_identity,
    encode_request,
    encode_response,
    split_access_identity_frames,
)
from ms_access_mcp.application.process_identity import (
    AccessTermination,
    list_msaccess_pids,
    terminate_verified_access,
)
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


def test_request_round_trip_carries_the_sql_opt_in_and_defaults_absent_to_false() -> None:
    """The worker request carries ``include_query_sql``; absent means false, non-bool is rejected."""
    opted_in = build_request(
        db_path="core.accdb",
        backend="dao",
        allowed_dirs=[Path("C:/db")],
        tables=None,
        queries=None,
        include_query_sql=True,
    )
    assert opted_in["include_query_sql"] is True
    assert decode_request(encode_request(opted_in))["include_query_sql"] is True

    default = build_request(
        db_path="core.accdb",
        backend="dao",
        allowed_dirs=[Path("C:/db")],
        tables=None,
        queries=None,
    )
    assert default["include_query_sql"] is False

    legacy = {key: value for key, value in opted_in.items() if key != "include_query_sql"}
    assert decode_request(encode_request(legacy))["include_query_sql"] is False

    with pytest.raises(AccessMcpError, match="include_query_sql"):
        decode_request(encode_request({**opted_in, "include_query_sql": "yes"}))


def test_worker_run_request_passes_the_flag_to_the_extraction_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Actual-worker survival: both decoded output opt-ins reach inspection, not just the request dict.

    The strict extraction-seam fake pins the full keyword contract of
    ``inspect_open_database``; it was refreshed because the seam gained
    ``include_app_logic`` (T6), so a dropped keyword fails this test instead of
    silently defaulting at the seam.
    """
    from ms_access_mcp.application import documentation_worker as worker
    from ms_access_mcp.application.database import Ok
    from ms_access_mcp.application.documentation import (
        Coverage,
        EvidenceDoc,
        RunMetadata,
        SourceEvidence,
    )

    seen: dict[str, Any] = {}

    class _FakeDatabase:
        def __init__(self, *, backend: str, readonly: bool, allowed_dirs: Any) -> None:
            seen["database"] = (backend, readonly)

        def open_database(self, _path: str) -> Any:
            return Ok(data={})

        def close_database(self) -> Any:
            seen["closed"] = True
            return Ok()

    def fake_inspect(
        database: Any,
        *,
        tables: Any,
        queries: Any,
        include_query_sql: bool = False,
        include_app_logic: bool = False,
    ) -> Any:
        seen["include_query_sql"] = include_query_sql
        seen["include_app_logic"] = include_app_logic
        return EvidenceDoc(
            run=RunMetadata("run-1", "1.0", [], []),
            source=SourceEvidence("core.accdb", "accdb", "DAO", "offline", "closed"),
            coverage=Coverage(),
        )

    monkeypatch.setattr(worker, "DatabaseUseCases", _FakeDatabase)
    monkeypatch.setattr(worker, "inspect_open_database", fake_inspect)
    request = decode_request(
        encode_request(
            build_request(
                db_path="core.accdb",
                backend="dao",
                allowed_dirs=[Path("C:/db")],
                tables=None,
                queries=None,
                include_query_sql=True,
                include_app_logic=True,
            )
        )
    )

    payload = worker._run_request(request)

    assert seen["include_query_sql"] is True
    assert seen["include_app_logic"] is True
    assert seen["closed"] is True
    assert payload["schema"] == "access.documentation.evidence/1"


def test_worker_run_request_defaults_app_logic_off_at_the_extraction_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRESERVE: a v1 request without the key still passes an explicit ``False`` to inspection."""
    from ms_access_mcp.application import documentation_worker as worker
    from ms_access_mcp.application.database import Ok
    from ms_access_mcp.application.documentation import (
        Coverage,
        EvidenceDoc,
        RunMetadata,
        SourceEvidence,
    )

    seen: dict[str, Any] = {}

    class _FakeDatabase:
        def __init__(self, *, backend: str, readonly: bool, allowed_dirs: Any) -> None:
            return None

        def open_database(self, _path: str) -> Any:
            return Ok(data={})

        def close_database(self) -> Any:
            return Ok()

    def fake_inspect(_database: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return EvidenceDoc(
            run=RunMetadata("run-1", "1.0", [], []),
            source=SourceEvidence("core.accdb", "accdb", "DAO", "offline", "closed"),
            coverage=Coverage(),
        )

    monkeypatch.setattr(worker, "DatabaseUseCases", _FakeDatabase)
    monkeypatch.setattr(worker, "inspect_open_database", fake_inspect)
    v1_request = {
        "db_path": "core.accdb",
        "backend": "dao",
        "allowed_dirs": [],
        "tables": None,
        "queries": None,
        "include_query_sql": False,
    }

    worker._run_request(decode_request(encode_request(v1_request)))

    assert seen["include_app_logic"] is False


def test_runtime_inspect_threads_the_sql_opt_in_into_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """The supervisor facade must not drop the flag between frontend and worker request."""
    from ms_access_mcp.application import documentation_runtime as runtime_module

    real_build_request = runtime_module.build_request
    seen: dict[str, Any] = {}

    def capture(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return real_build_request(**kwargs)

    monkeypatch.setattr(runtime_module, "build_request", capture)
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("ok", records), deadline_seconds=30)

    runtime.inspect(
        "fixture.accdb",
        backend="dao",
        allowed_dirs=[Path("C:/db")],
        tables=None,
        queries=None,
        include_query_sql=True,
    )

    assert seen["include_query_sql"] is True


def test_runtime_inspect_threads_the_app_logic_opt_in_into_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """The supervisor facade must not drop the app-logic opt-in before the worker request."""
    from ms_access_mcp.application import documentation_runtime as runtime_module

    real_build_request = runtime_module.build_request
    seen: dict[str, Any] = {}

    def capture(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return real_build_request(**kwargs)

    monkeypatch.setattr(runtime_module, "build_request", capture)
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("ok", records), deadline_seconds=30)

    runtime.inspect(
        "fixture.accdb",
        backend="dao",
        allowed_dirs=[Path("C:/db")],
        tables=None,
        queries=None,
        include_app_logic=True,
    )

    assert seen["include_app_logic"] is True


def test_runtime_inspect_defaults_app_logic_off_in_the_worker_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRESERVE: an unset opt-in still produces a request that carries ``False``."""
    from ms_access_mcp.application import documentation_runtime as runtime_module

    real_build_request = runtime_module.build_request
    seen: dict[str, Any] = {}

    def capture(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return real_build_request(**kwargs)

    monkeypatch.setattr(runtime_module, "build_request", capture)
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(launcher=_launcher("ok", records), deadline_seconds=30)

    _inspect(runtime)

    assert seen["include_app_logic"] is False


def test_runtime_facades_expose_the_app_logic_opt_in_defaulting_off() -> None:
    """The facade signatures pin the default-off contract at the seam itself."""
    import inspect as inspect_module

    from ms_access_mcp.application import documentation_runtime as runtime_module

    for callable_object in (runtime_module.DocumentationRuntime.inspect, runtime_module.run_documentation_inspection):
        parameter = inspect_module.signature(callable_object).parameters["include_app_logic"]
        assert parameter.default is False


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


# ---------------------------------------------------------------------------
# 6. Worker -> supervisor Access ownership: ack frames plus the fail-closed kill net
# ---------------------------------------------------------------------------

ACCESS_CREATION = 133_456_789_012_345_678
ACCESS_PID = 41_000
ACCESS_IMAGE = "MSACCESS.EXE"
PINNED_CLEANUP_ERROR = (
    "Documentation inspection worker cleanup could not be established within "
    "the {grace:g}-second cleanup grace; the owned worker may still be running "
    "and no evidence was produced"
)


def _frame_bytes(
    *,
    pid: int = ACCESS_PID,
    creation: int = ACCESS_CREATION,
    image: str = ACCESS_IMAGE,
) -> bytes:
    """Build one flushed identity frame with the production writer."""
    stream = io.BytesIO()
    emit_access_identity(pid, creation, image, stream=stream)
    return stream.getvalue()


def _evidence_response_bytes() -> bytes:
    """Build the minimal valid worker response the fixture child emits."""
    document = EvidenceDoc(
        run=RunMetadata("fixture-stub", "1.0", [], []),
        source=SourceEvidence("fixture.accdb", "ACE", "DAO", "offline", "caller-managed"),
        coverage=Coverage(),
    )
    payload = document.to_dict()
    payload["status"] = document.status
    return encode_response({"ok": True, "evidence": payload})


class _FrameProcess:
    """In-process fake child whose stdout already carries frames plus a response.

    ``poll`` reports the child as already exited, so the supervisor never kills it and
    the test observes only the Access kill net. No subprocess, COM, or pipeline runs.
    """

    returncode: int | None = 0

    def __init__(self, output: bytes) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(output)
        self.stderr = io.BytesIO(b"")
        self.kills = 0

    def poll(self) -> int:
        return 0

    def kill(self) -> None:
        self.kills += 1

    def wait(self, timeout: float | None = None) -> int:
        return 0


class _RecordingAccessTerminator:
    """Fake fail-closed verifier: records every call and reports a scripted outcome."""

    def __init__(self, *, exited: bool) -> None:
        self.calls: list[dict[str, Any]] = []
        self._exited = exited

    def __call__(
        self,
        *,
        pid: int,
        creation: int,
        image: str,
        baseline_pids: frozenset[int],
        grace_seconds: float,
    ) -> AccessTermination:
        self.calls.append(
            {
                "pid": pid,
                "creation": creation,
                "image": image,
                "baseline_pids": baseline_pids,
                "grace_seconds": grace_seconds,
            }
        )
        return AccessTermination(
            pid=pid,
            verified=True,
            terminated=True,
            exited=self._exited,
            detail="stub",
        )


def _owned_access_runtime(
    process: _FrameProcess,
    terminator: _RecordingAccessTerminator,
    *,
    baseline: set[int] | None = None,
    grace: float = 0.05,
) -> DocumentationRuntime:
    return DocumentationRuntime(
        launcher=lambda: process,  # type: ignore[arg-type,return-value]
        deadline_seconds=30,
        cleanup_grace_seconds=grace,
        access_pid_lister=lambda: {4242} if baseline is None else baseline,
        access_terminator=terminator,
    )


def test_ack_frame_is_recorded_and_the_owned_access_pid_is_terminated() -> None:
    process = _FrameProcess(_frame_bytes() + _evidence_response_bytes())
    terminator = _RecordingAccessTerminator(exited=True)

    document = _inspect(_owned_access_runtime(process, terminator, baseline={999}))

    assert document.status == "complete"
    assert len(terminator.calls) == 1
    call = terminator.calls[0]
    assert call["pid"] == ACCESS_PID
    assert call["creation"] == ACCESS_CREATION
    assert call["image"] == ACCESS_IMAGE
    assert call["baseline_pids"] == frozenset({999})
    assert 0.0 < call["grace_seconds"] <= 0.05


def test_v1_response_without_ack_frames_decodes_unchanged_and_never_fires_the_kill_net() -> None:
    body = _evidence_response_bytes()
    terminator = _RecordingAccessTerminator(exited=True)

    document = _inspect(_owned_access_runtime(_FrameProcess(body), terminator))

    assert document.status == "complete"
    assert terminator.calls == []
    frames, remaining = split_access_identity_frames(body)
    assert frames == []
    assert remaining == body
    assert decode_response(body) == decode_response_with_identities(body)[1]


def test_verified_access_survivor_after_the_grace_fails_cleanup_with_the_pinned_error() -> None:
    process = _FrameProcess(_frame_bytes() + _evidence_response_bytes())
    terminator = _RecordingAccessTerminator(exited=False)

    with pytest.raises(AccessMcpError) as excinfo:
        _inspect(_owned_access_runtime(process, terminator, grace=0.05))

    assert len(terminator.calls) == 1
    assert str(excinfo.value) == PINNED_CLEANUP_ERROR.format(grace=0.05)


# --- process_identity: the fail-closed verifier itself (scripted runner, no live kill) ---


def _tasklist_line(pid: int = ACCESS_PID) -> str:
    return f'"MSACCESS.EXE","{pid}","Console","1","100,000 K"\n'


class _CommandRunner:
    """Scripted ``subprocess`` double: records argv and returns fixed probe output."""

    def __init__(
        self,
        *,
        creation: int = ACCESS_CREATION,
        process_name: str = "MSACCESS",
        alive: bool = True,
        alive_after_kill: bool = False,
        powershell_ok: bool = True,
    ) -> None:
        self.calls: list[list[str]] = []
        self.creation = creation
        self.process_name = process_name
        self.alive = alive
        self.alive_after_kill = alive_after_kill
        self.powershell_ok = powershell_ok
        self.killed = False

    def __call__(self, args: Any, timeout: float) -> subprocess.CompletedProcess[str]:
        call = [str(argument) for argument in args]
        self.calls.append(call)
        if call[0] == "taskkill":
            self.killed = True
            return subprocess.CompletedProcess(call, 0, "", "")
        if call[0] == "powershell":
            if not self.powershell_ok:
                return subprocess.CompletedProcess(call, 1, "", "probe failed")
            return subprocess.CompletedProcess(call, 0, f"{self.creation}|{self.process_name}", "")
        if call[0] == "tasklist":
            alive = self.alive_after_kill if self.killed else self.alive
            if not alive:
                return subprocess.CompletedProcess(call, 0, "INFO: No tasks are running\n", "")
            return subprocess.CompletedProcess(call, 0, _tasklist_line(), "")
        msg = f"unexpected probe command: {call[0]}"
        raise AssertionError(msg)


def _taskkill_calls(runner: _CommandRunner) -> list[list[str]]:
    return [call for call in runner.calls if call[0] == "taskkill"]


def test_list_msaccess_pids_parses_tasklist_csv_and_never_kills() -> None:
    runner = _CommandRunner()
    runner.calls.clear()

    pids = list_msaccess_pids(runner=runner)  # type: ignore[arg-type]

    assert pids == {ACCESS_PID}
    assert _taskkill_calls(runner) == []
    assert runner.calls[0][:3] == ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE"]


def test_verified_identity_kills_exactly_the_announced_pid_and_never_uses_im() -> None:
    runner = _CommandRunner(alive_after_kill=False)

    outcome = terminate_verified_access(
        pid=ACCESS_PID,
        creation=ACCESS_CREATION,
        image=ACCESS_IMAGE,
        baseline_pids=frozenset(),
        runner=runner,  # type: ignore[arg-type]
        grace_seconds=0.2,
    )

    assert outcome.verified is True
    assert outcome.terminated is True
    assert outcome.exited is True
    assert _taskkill_calls(runner) == [["taskkill", "/F", "/PID", str(ACCESS_PID)]]
    assert "/IM" not in {argument for call in runner.calls for argument in call}


def test_verified_survivor_after_the_grace_is_reported_as_not_exited() -> None:
    runner = _CommandRunner(alive_after_kill=True)

    outcome = terminate_verified_access(
        pid=ACCESS_PID,
        creation=ACCESS_CREATION,
        image=ACCESS_IMAGE,
        runner=runner,  # type: ignore[arg-type]
        grace_seconds=0.0,
    )

    assert outcome.terminated is True
    assert outcome.exited is False
    assert "grace" in outcome.detail
    assert len(_taskkill_calls(runner)) == 1


def test_creation_mismatch_reports_and_never_calls_taskkill() -> None:
    runner = _CommandRunner(creation=ACCESS_CREATION + 1)

    outcome = terminate_verified_access(
        pid=ACCESS_PID,
        creation=ACCESS_CREATION,
        image=ACCESS_IMAGE,
        runner=runner,  # type: ignore[arg-type]
        grace_seconds=0.0,
    )

    assert outcome.verified is False
    assert outcome.terminated is False
    assert outcome.exited is True
    assert _taskkill_calls(runner) == []
    assert "creation" in outcome.detail


def test_live_image_mismatch_reports_and_never_calls_taskkill() -> None:
    runner = _CommandRunner(process_name="WINWORD")

    outcome = terminate_verified_access(
        pid=ACCESS_PID,
        creation=ACCESS_CREATION,
        image=ACCESS_IMAGE,
        runner=runner,  # type: ignore[arg-type]
        grace_seconds=0.0,
    )

    assert outcome.terminated is False
    assert outcome.exited is True
    assert _taskkill_calls(runner) == []
    assert "WINWORD.EXE" in outcome.detail


def test_announced_image_must_be_msaccess_exe_before_any_probe() -> None:
    runner = _CommandRunner()

    outcome = terminate_verified_access(
        pid=ACCESS_PID,
        creation=ACCESS_CREATION,
        image="WINWORD.EXE",
        runner=runner,  # type: ignore[arg-type]
        grace_seconds=0.0,
    )

    assert outcome.terminated is False
    assert outcome.exited is True
    assert runner.calls == []


def test_a_baseline_pid_is_never_probed_or_killed() -> None:
    runner = _CommandRunner()

    outcome = terminate_verified_access(
        pid=ACCESS_PID,
        creation=ACCESS_CREATION,
        image=ACCESS_IMAGE,
        baseline_pids=frozenset({ACCESS_PID}),
        runner=runner,  # type: ignore[arg-type]
        grace_seconds=0.0,
    )

    assert outcome.terminated is False
    assert outcome.exited is True
    assert runner.calls == []
    assert _taskkill_calls(runner) == []


@pytest.mark.parametrize("pid", [0, -1, True])
def test_an_invalid_announced_pid_is_never_probed_or_killed(pid: Any) -> None:
    runner = _CommandRunner()

    outcome = terminate_verified_access(
        pid=pid,
        creation=ACCESS_CREATION,
        image=ACCESS_IMAGE,
        runner=runner,  # type: ignore[arg-type]
        grace_seconds=0.0,
    )

    assert outcome.terminated is False
    assert outcome.exited is True
    assert runner.calls == []
    assert _taskkill_calls(runner) == []


def test_an_unreadable_identity_that_is_still_alive_is_reported_unresolved() -> None:
    runner = _CommandRunner(powershell_ok=False, alive=True)

    outcome = terminate_verified_access(
        pid=ACCESS_PID,
        creation=ACCESS_CREATION,
        image=ACCESS_IMAGE,
        runner=runner,  # type: ignore[arg-type]
        grace_seconds=0.0,
    )

    assert outcome.verified is False
    assert outcome.terminated is False
    assert outcome.exited is False
    assert _taskkill_calls(runner) == []
    assert "unreadable" in outcome.detail


def test_an_unreadable_identity_that_already_exited_needs_no_kill() -> None:
    runner = _CommandRunner(powershell_ok=False, alive=False)

    outcome = terminate_verified_access(
        pid=ACCESS_PID,
        creation=ACCESS_CREATION,
        image=ACCESS_IMAGE,
        runner=runner,  # type: ignore[arg-type]
        grace_seconds=0.0,
    )

    assert outcome.terminated is False
    assert outcome.exited is True
    assert _taskkill_calls(runner) == []
