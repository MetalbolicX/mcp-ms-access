"""Documentation-only runtime facade: supervised, deadline-bounded inspection.

This facade is the single entry point the CLI and MCP documentation frontends use for
inspection. It deliberately performs **no** source open of its own: the parent records a
wall-clock deadline, spawns one owned child process (``documentation_worker``), hands it a
bounded structured request, and reads one bounded structured response. All source-path
validation, credential resolution, engine-level read-only open, passive extraction, and
owned-handle cleanup happen inside that child, so a parent's active CRUD session is never
touched and a hung engine cannot keep the parent's worker alive past the deadline.

Design guarantees:

* **Owned isolation.** The child is a fresh Python process (spawn, never fork): it builds
  its own DAO STA / ODBC read-only factory and shares no COM proxy or live handle. The
  supervisor terminates only that exact ``Popen``. An Access instance the child announces
  as its own (armed app-logic read) is reaped by PID through the fail-closed verifier in
  ``application/process_identity.py``: there is no baseline-diff kill and no ``/IM``
  ``taskkill``.
* **Owned Access reap.** The child announces each owned ``MSACCESS.EXE`` as one flushed
  ``@@ACCESS_IDENTITY@@`` frame before it opens a source; the supervisor records those
  identities and, after reaping the Python child, terminates each one only when the PID is
  outside the pre-spawn baseline, the live creation time matches, and the image is
  ``MSACCESS.EXE``. A verified Access process that survives the bounded grace (or an
  announced identity that stays unreadable while alive) fails cleanup explicitly instead
  of orphaning Access.
* **Whole-inspection deadline.** The 120-second budget is measured from before the spawn
  and covers child startup, source open, extraction, and serialization. On expiry the
  child is killed and reaped within an explicit, documented cleanup grace; a timer-style
  ``Err`` that leaves STA work running is not produced.
* **Bounded IPC.** Both request and response are read in fixed-size chunks with a hard
  byte limit, and both stdout and stderr are drained continuously so a flooding child
  can never block on a full pipe. The one-shot request write also runs on a daemon
  thread, so a child that never drains its stdin cannot stall the caller outside the
  deadline. An oversized or malformed response is rejected; raw stderr is never
  reflected because it may carry credentials.
* **Honest failure.** Any failure -- deadline, oversized/malformed response, non-zero
  child exit, or an un-establishable cleanup -- yields a safe ``AccessMcpError`` with no
  partial evidence. Cleanup that cannot be established raises explicitly rather than
  silently orphaning the child, and never blocks on a child pipe still held open by a
  surviving child: a stream whose reader/writer thread is still blocked is left to its
  own daemon thread instead of being closed.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..errors import AccessMcpError
from . import documentation_limits
from .documentation import EvidenceDoc, redact_connection_secrets
from .documentation_limits import CLEANUP_GRACE_SECONDS, DEADLINE_SECONDS, DIAGNOSTIC_MAX_BYTES
from .documentation_worker import (
    AccessIdentity,
    build_request,
    decode_response_with_identities,
    encode_request,
    split_access_identity_frames,
)
from .process_identity import AccessTermination, list_msaccess_pids, terminate_verified_access

# Poll cadence for the deadline/oversize supervisor loop. Small enough to notice a
# terminated child promptly, large enough not to busy-spin.
_POLL_INTERVAL_SECONDS = 0.02
# One bounded read chunk for both pipes; the accumulated buffer never exceeds its limit.
_READ_CHUNK_BYTES = 65536

Launcher = Callable[[], "subprocess.Popen[bytes]"]
AccessPidLister = Callable[[], set[int]]


class AccessTerminator(Protocol):
    """Fail-closed owned-Access termination seam (faked in unit tests)."""

    def __call__(
        self,
        *,
        pid: int,
        creation: int,
        image: str,
        baseline_pids: frozenset[int],
        grace_seconds: float,
    ) -> AccessTermination: ...


def _default_launcher() -> subprocess.Popen[bytes]:
    """Spawn the real owned worker as a fresh interpreter inheriting cwd and environment."""
    return subprocess.Popen(  # noqa: S603 - fixed interpreter + fixed module, no shell
        [sys.executable, "-m", "ms_access_mcp.application.documentation_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class _BoundedPipeReader(threading.Thread):
    """Drain a child pipe continuously, storing at most ``limit`` bytes.

    Reading happens in a daemon thread so a child that writes more than the limit cannot
    block on a full pipe; the accumulated buffer is bounded, and ``oversized`` signals the
    supervisor to terminate the child instead of allocating an unbounded blob.
    """

    def __init__(self, stream: Any, limit: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self.data = bytearray()
        self.oversized = False

    @property
    def stream(self) -> Any:
        """The child pipe this reader drains; exposed so a concurrent close can be skipped."""
        return self._stream

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                if len(self.data) + len(chunk) > self._limit:
                    self.oversized = True
                    break
                self.data.extend(chunk)
        except Exception:  # noqa: BLE001 - a closed pipe at teardown is not an error
            pass
        finally:
            with contextlib.suppress(Exception):
                self._stream.close()


class DocumentationRuntime:
    """Supervise one owned child per inspection; private injection is for tests only."""

    def __init__(
        self,
        *,
        launcher: Launcher | None = None,
        deadline_seconds: float | None = None,
        cleanup_grace_seconds: float | None = None,
        access_pid_lister: AccessPidLister | None = None,
        access_terminator: AccessTerminator | None = None,
    ) -> None:
        self._launcher: Launcher = launcher or _default_launcher
        self._deadline_seconds = deadline_seconds
        self._cleanup_grace_seconds = cleanup_grace_seconds
        self._access_pid_lister: AccessPidLister = access_pid_lister or list_msaccess_pids
        self._access_terminator: AccessTerminator = access_terminator or terminate_verified_access
        # Owned Access instances announced by the current child, plus the pre-spawn
        # baseline the fail-closed verifier must never kill.
        self._owned_access: dict[int, AccessIdentity] = {}
        self._access_baseline: frozenset[int] = frozenset()

    @property
    def deadline_seconds(self) -> float:
        return DEADLINE_SECONDS if self._deadline_seconds is None else self._deadline_seconds

    @property
    def cleanup_grace_seconds(self) -> float:
        return CLEANUP_GRACE_SECONDS if self._cleanup_grace_seconds is None else self._cleanup_grace_seconds

    def inspect(
        self,
        db_path: str,
        *,
        backend: str,
        allowed_dirs: list[Path],
        tables: list[str] | None,
        queries: list[str] | None,
        include_query_sql: bool = False,
        include_app_logic: bool = False,
        include_app_logic_source: bool = False,
    ) -> EvidenceDoc:
        """Run one whole inspection in an owned child, or raise a safe ``AccessMcpError``."""
        if include_app_logic_source and not include_app_logic:
            msg = (
                "Documentation inspection requires include_app_logic=True when "
                "include_app_logic_source=True; raw source text is only meaningful with "
                "the app-logic catalogue opt-in"
            )
            raise AccessMcpError(msg)
        request = build_request(
            db_path=db_path,
            backend=backend,
            allowed_dirs=allowed_dirs,
            tables=tables,
            queries=queries,
            include_query_sql=include_query_sql,
            include_app_logic=include_app_logic,
            include_app_logic_source=include_app_logic_source,
        )
        request_bytes = encode_request(request)
        output_limit = documentation_limits.worker_output_limit()
        deadline_seconds = self.deadline_seconds
        grace = self.cleanup_grace_seconds

        self._owned_access.clear()
        # Snapshot already-running Access processes *before* the child exists, so a
        # caller-owned session can never be announced as this child's own and killed.
        self._access_baseline = self._capture_access_baseline()
        started = time.monotonic()
        process = self._launcher()
        stdout_reader = _BoundedPipeReader(process.stdout, output_limit)
        stderr_reader = _BoundedPipeReader(process.stderr, DIAGNOSTIC_MAX_BYTES)
        stdout_reader.start()
        stderr_reader.start()
        # The one-shot request write runs on a daemon thread: a child that stays alive
        # without draining stdin would otherwise block this caller in ``write`` outside
        # any deadline (Windows pipe capacity is far below the bounded 1 MiB request cap).
        request_writer = threading.Thread(
            target=self._write_request,
            args=(process, request_bytes),
            name="documentation-runtime-request-writer",
            daemon=True,
        )
        request_writer.start()
        try:
            timed_out = self._await(process, stdout_reader, started, deadline_seconds)
            cleanup_established = self._terminate_and_reap(process, grace)
            self._join(thread=stdout_reader, grace=grace)
            self._join(thread=stderr_reader, grace=grace)
            self._join(thread=request_writer, grace=grace)
            self._record_owned_access(stdout_reader)
            access_cleanup_established = self._terminate_registered_access(grace)
            if (
                not cleanup_established
                or not access_cleanup_established
                or stdout_reader.is_alive()
                or stderr_reader.is_alive()
                or request_writer.is_alive()
            ):
                msg = (
                    f"Documentation inspection worker cleanup could not be established within "
                    f"the {grace:g}-second cleanup grace; the owned worker may still be running "
                    "and no evidence was produced"
                )
                raise AccessMcpError(msg)
            if stdout_reader.oversized:
                msg = (
                    f"Documentation inspection worker response exceeded the allowed "
                    f"{output_limit} byte output limit; the owned worker was terminated and no "
                    "evidence was produced"
                )
                raise AccessMcpError(msg)
            if timed_out:
                msg = (
                    f"Documentation inspection exceeded the {deadline_seconds:g}-second deadline; "
                    f"the owned worker was terminated and reaped within the {grace:g}-second "
                    "cleanup grace and no evidence was produced"
                )
                raise AccessMcpError(msg)
            return self._decode_evidence(process.returncode, bytes(stdout_reader.data))
        finally:
            # A stream whose reader/writer thread is still blocked inside a pipe call is
            # never closed here: see ``_close_streams`` for why that close would hang.
            live_streams: list[Any] = [
                reader.stream for reader in (stdout_reader, stderr_reader) if reader.is_alive()
            ]
            if request_writer.is_alive():
                live_streams.append(process.stdin)
            self._close_streams(process, skip=live_streams)

    def _write_request(self, process: subprocess.Popen[bytes], request_bytes: bytes) -> None:
        """Write the request once and close stdin; a child that already exited is handled.

        Runs on a daemon thread (never inline) so a full child stdin pipe cannot stall
        the deadline loop; a writer that is still blocked after the bounded cleanup is
        reported as an un-establishable cleanup instead of an unbounded wait.
        """
        stream = process.stdin
        if stream is None:
            return
        with contextlib.suppress(BrokenPipeError, OSError):
            stream.write(request_bytes)
            stream.flush()
        with contextlib.suppress(Exception):
            stream.close()

    def _await(
        self,
        process: subprocess.Popen[bytes],
        stdout_reader: _BoundedPipeReader,
        started: float,
        deadline_seconds: float,
    ) -> bool:
        """Return True when the whole-inspection deadline expired before the child exited."""
        deadline = started + deadline_seconds
        while True:
            if process.poll() is not None:
                return False
            if stdout_reader.oversized:
                return False
            if time.monotonic() >= deadline:
                return True
            time.sleep(_POLL_INTERVAL_SECONDS)

    def _terminate_and_reap(self, process: subprocess.Popen[bytes], grace: float) -> bool:
        """Terminate only this owned child and wait for it within the bounded grace."""
        if process.poll() is not None:
            return True
        with contextlib.suppress(Exception):
            process.kill()
        if self._wait(process, grace):
            return True
        # One bounded retry: a process that ignored the first termination gets a second
        # kill and the same bounded grace, never an unbounded join.
        with contextlib.suppress(Exception):
            process.kill()
        return self._wait(process, grace)

    @staticmethod
    def _wait(process: subprocess.Popen[bytes], grace: float) -> bool:
        try:
            process.wait(timeout=grace)
            return True
        except subprocess.TimeoutExpired:
            return False

    @staticmethod
    def _join(*, thread: threading.Thread, grace: float) -> None:
        """Join one helper thread within the bounded grace; never an unbounded join."""
        if thread.ident is not None:
            thread.join(timeout=grace)

    @staticmethod
    def _close_streams(process: subprocess.Popen[bytes], *, skip: list[Any] | None = None) -> None:
        """Close the child streams, except one still owned by a blocked helper thread.

        ``io.BufferedReader.read`` and ``BufferedReader.close`` take the same internal
        lock, so closing a stream while another thread is blocked inside ``read`` waits
        for that read -- and a pipe read only returns at end-of-file, which requires
        every writer of that pipe to be gone. When cleanup could not be established the
        owned child (or another handle holder) can still be alive, so closing anyway
        would block the caller forever instead of reporting the explicit cleanup
        failure. Such a stream is left open; its daemon reader/writer thread and handle
        are released when this process exits.
        """
        skipped = [stream for stream in (skip or []) if stream is not None]
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is None or any(stream is item for item in skipped):
                continue
            with contextlib.suppress(Exception):
                stream.close()

    def _capture_access_baseline(self) -> frozenset[int]:
        """Snapshot the Access PIDs already running before this child was spawned.

        A failing enumeration yields an empty baseline: the verifier still requires a live
        creation-time and image match before any kill, and the baseline only ever *denies*
        a kill.
        """
        with contextlib.suppress(Exception):
            return frozenset(self._access_pid_lister())
        return frozenset()

    def _record_owned_access(self, stdout_reader: _BoundedPipeReader) -> None:
        """Record every complete identity frame the child announced before its response.

        A damaged frame is left in the response bytes and rejected by the response
        decoder, but the frames parsed before it are still recorded so their Access PIDs
        are not lost to cleanup.
        """
        identities, _body = split_access_identity_frames(bytes(stdout_reader.data))
        for identity in identities:
            self._owned_access[identity.pid] = identity

    def _terminate_registered_access(self, grace: float) -> bool:
        """Reap every recorded owned Access PID through the fail-closed verifier.

        Runs after the Python child has been reaped, because a dead child can no longer
        close its own ``Access.Application``. Returns False when a verified owned Access
        process survives its bounded grace, or when an announced identity stays unreadable
        while the process is still alive, so the caller raises the existing
        un-establishable-cleanup failure instead of silently orphaning Access. Verifier
        refusals (baseline PID, creation or image mismatch) never kill and are reported
        through ``AccessTermination.detail``.
        """
        if not self._owned_access:
            return True
        deadline = time.monotonic() + grace
        established = True
        for identity in self._owned_access.values():
            outcome = self._access_terminator(
                pid=identity.pid,
                creation=identity.creation,
                image=identity.image,
                baseline_pids=self._access_baseline,
                grace_seconds=max(deadline - time.monotonic(), 0.0),
            )
            if not outcome.exited:
                established = False
        return established

    def _decode_evidence(self, returncode: int | None, output: bytes) -> EvidenceDoc:
        if returncode != 0:
            msg = (
                f"Documentation inspection worker failed (exit code {returncode!r}) and was "
                "reaped; no evidence was produced"
            )
            raise AccessMcpError(msg)
        # Identity frames were recorded before cleanup; the decoder skips them so the v1
        # no-frame path decodes byte-identically.
        _identities, response = decode_response_with_identities(output)
        if response["ok"] is not True:
            message = redact_connection_secrets(str(response.get("error") or "Documentation inspection failed"))
            raise AccessMcpError(message)
        evidence = response.get("evidence")
        if not isinstance(evidence, dict):
            msg = "Documentation worker response did not include an evidence object"
            raise AccessMcpError(msg)
        document = EvidenceDoc.from_dict(evidence)
        final = document.to_dict()
        final["status"] = document.status
        documentation_limits.ensure_evidence_within_limit(final)
        return document


def run_documentation_inspection(
    db_path: str,
    *,
    backend: str,
    allowed_dirs: list[Path],
    tables: list[str] | None = None,
    queries: list[str] | None = None,
    include_query_sql: bool = False,
    include_app_logic: bool = False,
    include_app_logic_source: bool = False,
) -> EvidenceDoc:
    """Run one inspection through the default supervised runtime facade."""
    return DocumentationRuntime().inspect(
        db_path,
        backend=backend,
        allowed_dirs=allowed_dirs,
        tables=tables,
        queries=queries,
        include_query_sql=include_query_sql,
        include_app_logic=include_app_logic,
        include_app_logic_source=include_app_logic_source,
    )
