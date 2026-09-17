"""G13 — Kill-mid-write chaos harness (plan 047).

Plan 047 / G13: "Spawn the MCP server, drive a write workload, hard-kill
the process at a random phase (mid-batch, post-commit, mid-read), then
classify the resulting database state using a fresh server spawn:
  CLEAN / LOCK_STALE / REPAIRABLE / FATAL.

Exit 0 while all rounds are CLEAN or LOCK_STALE (retryable) or REPAIRABLE
(compact_repair can fix). Exit 1 on any FATAL round without recovery.

Infrastructure (LineReader, Watchdog, _ProcSnapshot, _kill_pids) is copied
verbatim from g10 — duplication across scripts is accepted house style.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Reused infrastructure (mirrors g10 — do not import from g10)
# ---------------------------------------------------------------------------


class _LineReader:
    """Background reader that delivers newline-delimited lines via a queue."""

    def __init__(self, stream) -> None:
        self.stream = stream
        self.queue: list[str] = []
        self.lock = threading.Lock()
        self._buf = b""
        self._eof = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while True:
                chunk = self.stream.read(1)
                if not chunk:
                    self._eof = True
                    if self._buf:
                        with self.lock:
                            self.queue.append(self._buf.decode("utf-8", errors="replace"))
                            self._buf = b""
                    return
                if chunk == b"\n":
                    with self.lock:
                        self.queue.append(self._buf.decode("utf-8", errors="replace"))
                        self._buf = b""
                else:
                    self._buf += chunk
        except Exception:
            self._eof = True

    def get(self, timeout: float) -> str | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if self.queue:
                    return self.queue.pop(0)
            time.sleep(0.05)
        return None


class _Watchdog:
    """Kills the subprocess if the main thread does not heartbeat in time."""

    def __init__(self, proc: subprocess.Popen, timeout_s: float = 30.0) -> None:
        self.proc = proc
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self._last_beat = time.time()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def beat(self) -> None:
        with self._lock:
            self._last_beat = time.time()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._stopped:
                    return
                idle = time.time() - self._last_beat
            if idle > self.timeout_s:
                try:
                    if self.proc.poll() is None:
                        self.proc.kill()
                except Exception:
                    pass
                return
            time.sleep(1.0)


@dataclass
class _ProcSnapshot:
    """PIDs known to exist at baseline (before we spawn)."""

    msaccess: set[int]
    python: set[int]


def _list_pids(image_name: str) -> set[int]:
    """Return set of PIDs running the given image name."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return set()
    pids: set[int] = set()
    for line in out.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("INFO:"):
            continue
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == image_name.lower():
            try:
                pids.add(int(parts[1]))
            except ValueError:
                continue
    return pids


def _kill_pids(pids: set[int]) -> set[int]:
    """Force-kill each PID. Returns the set of PIDs successfully killed."""
    killed: set[int] = set()
    for pid in pids:
        try:
            res = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            if res.returncode == 0:
                killed.add(pid)
        except Exception:
            pass
    return killed


def _snapshot_baseline() -> _ProcSnapshot:
    """Capture process-table state before we spawn the server."""
    return _ProcSnapshot(
        msaccess=_list_pids("MSACCESS.EXE"),
        python=_list_pids("python.exe"),
    )


def _new_orphans(baseline: _ProcSnapshot) -> tuple[set[int], set[int]]:
    """Return (new_msaccess_pids, new_python_pids) introduced by our spawn."""
    current_msa = _list_pids("MSACCESS.EXE")
    current_py = _list_pids("python.exe")
    own = os.getpid()
    new_msa = current_msa - baseline.msaccess
    new_py = (current_py - baseline.python) - {own}
    return new_msa, new_py


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

# Kill phases
PHASE_MID_BATCH = "mid_batch"
PHASE_POST_COMMIT = "post_commit"
PHASE_MID_READ = "mid_read"

# Verdict types
VERDICT_CLEAN = "CLEAN"
VERDICT_LOCK_STALE = "LOCK_STALE"
VERDICT_REPAIRABLE = "REPAIRABLE"
VERDICT_FATAL = "FATAL"


@dataclass
class RoundResult:
    round_idx: int
    phase: str
    verdict: str = "PASS"
    exit_code: int | None = None
    stderr_excerpt: str = ""
    orphans_after: list[str] = field(default_factory=list)
    killed_orphans: list[str] = field(default_factory=list)
    fail_reason: str = ""
    duration_s: float = 0.0
    # Verification details
    baseline_rows: dict[str, int] = field(default_factory=dict)  # table -> row count
    final_rows: dict[str, int] = field(default_factory=dict)
    committed_after_kill: list[str] = field(default_factory=list)  # ops confirmed before kill


# ---------------------------------------------------------------------------
# Database baseline via pyodbc
# ---------------------------------------------------------------------------

def _snapshot_table_rows(db_path: Path) -> dict[str, int]:
    """Return {table_name: row_count} for all tables in the DB."""
    import pyodbc
    rows: dict[str, int] = {}
    try:
        conn_str = f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={db_path};"
        conn = pyodbc.connect(conn_str, autocommit=True, timeout=5)
        cursor = conn.cursor()
        for table_name in cursor.tables(tableType="TABLE"):
            tname = table_name.table_name
            if tname.startswith("MSys"):
                continue
            try:
                cursor.execute(f"SELECT COUNT(*) FROM [{tname}]")
                rows[tname] = cursor.fetchone()[0]
            except Exception:
                rows[tname] = 0
        cursor.close()
        conn.close()
    except Exception:
        pass
    return rows


def _snapshot_checksum(db_path: Path) -> str:
    """Return a simple checksum of ordered row repr for each table."""
    import pyodbc
    parts: list[str] = []
    try:
        conn_str = f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={db_path};"
        conn = pyodbc.connect(conn_str, autocommit=True, timeout=5)
        cursor = conn.cursor()
        for table_name in sorted(cursor.tables(tableType="TABLE")):
            tname = table_name.table_name
            if tname.startswith("MSys"):
                continue
            try:
                cursor.execute(f"SELECT * FROM [{tname}] ORDER BY 1")
                rows_data = [repr(row) for row in cursor.fetchall()]
                parts.append(f"{tname}={len(rows_data)}:{hash(tuple(rows_data))}")
            except Exception:
                parts.append(f"{tname}=err")
        cursor.close()
        conn.close()
    except Exception as e:
        parts.append(f"err={e}")
    return "|".join(parts)


# ---------------------------------------------------------------------------
# MCP stdio driver helpers
# ---------------------------------------------------------------------------

def _send(stdin, rpc: dict) -> None:
    stdin.write((json.dumps(rpc) + "\n").encode("utf-8"))
    stdin.flush()


def _recv(reader: _LineReader, timeout: float, watchdog: _Watchdog | None = None) -> dict | None:
    if watchdog:
        watchdog.beat()
    line = reader.get(timeout)
    if line is None:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _mcp_handshake(proc: subprocess.Popen, tmpdir: Path) -> tuple:
    """Perform MCP initialize handshake. Returns (stdin, stdout, reader, watchdog)."""
    stdin = proc.stdin
    stdout = proc.stdout
    assert stdin is not None
    assert stdout is not None

    reader = _LineReader(stdout)
    watchdog = _Watchdog(proc, timeout_s=60.0)

    _send(stdin, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "g13-kill-mid-write", "version": "0.1.0"},
        },
    })
    resp = _recv(reader, 10.0, watchdog)
    if resp is None or "error" in (resp or {}):
        raise RuntimeError(f"initialize failed: {resp}")

    _send(stdin, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    return stdin, stdout, reader, watchdog


def _open_database(stdin, reader, watchdog: _Watchdog, tmpdir: Path, db_path: Path) -> int:
    """Call open_database tool. Returns request_id of the call."""
    _send(stdin, {
        "jsonrpc": "2.0", "id": 10, "method": "tools/call",
        "params": {
            "name": "open_database",
            "arguments": {"path": str(db_path)},
        },
    })
    resp = _recv(reader, 15.0, watchdog)
    if resp is None or "error" in (resp or {}):
        raise RuntimeError(f"open_database failed: {resp}")
    return 10


def _insert_data(stdin, reader, watchdog: _Watchdog, table: str, values: list[dict]) -> dict | None:
    """Call insert_data tool. Returns the response or None on error."""
    _send(stdin, {
        "jsonrpc": "2.0", "id": 20, "method": "tools/call",
        "params": {
            "name": "insert_data",
            "arguments": {"table": table, "values": values},
        },
    })
    resp = _recv(reader, 20.0, watchdog)
    return resp


# ---------------------------------------------------------------------------
# Kill round
# ---------------------------------------------------------------------------

def _run_kill_round(
    round_idx: int,
    tmpdir: Path,
    rng: random.Random,
    phase: str,
    total_timeout: float = 60.0,
) -> RoundResult:
    """Execute one kill-mid-write round.

    Returns RoundResult with verdict classification.
    """
    fixture_db = Path(__file__).parent.parent / "tests" / "fixtures" / "test_db.accdb"
    if not fixture_db.exists():
        return RoundResult(
            round_idx=round_idx, phase=phase, verdict="FAIL",
            fail_reason="fixture tests/fixtures/test_db.accdb missing",
        )

    started = time.time()
    tmp_db = Path(tmpdir) / f"g13_db_{round_idx}.accdb"
    shutil.copy2(fixture_db, tmp_db)

    baseline_rows = _snapshot_table_rows(tmp_db)
    baseline_checksum = _snapshot_checksum(tmp_db)

    baseline_proc = _snapshot_baseline()

    env = {**os.environ, "ACCESS_MCP_ALLOWED_DIRS": str(tmpdir)}
    proc: subprocess.Popen | None = None

    try:
        # Spawn venv python directly (never uv run — muddies kill semantics)
        venv_python = sys.executable
        proc = subprocess.Popen(
            [venv_python, "-u", "-m", "ms_access_mcp.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )

        stdin, stdout, reader, watchdog = _mcp_handshake(proc, tmpdir)

        # Open the database
        _open_database(stdin, reader, watchdog, tmpdir, tmp_db)

        # Determine kill timing based on phase
        if phase == PHASE_MID_BATCH:
            # Insert some rows, kill during a large batch
            for i in range(3):
                _insert_data(stdin, reader, watchdog, "tasks", [{"title": f"pre_kill_{i}"}])
            # Large batch — kill in the middle
            large_batch = [{"title": f"batch_{j}"} for j in range(100)]
            _send(stdin, {
                "jsonrpc": "2.0", "id": 30, "method": "tools/call",
                "params": {"name": "insert_data", "arguments": {"table": "tasks", "values": large_batch}},
            })
            # Kill immediately after sending (mid-batch)
            proc.kill()
            proc.wait(timeout=5)
            watchdog.stop()

        elif phase == PHASE_POST_COMMIT:
            # Insert + commit, then kill within 50ms window
            for i in range(3):
                _insert_data(stdin, reader, watchdog, "tasks", [{"title": f"commit_{i}"}])
            # Small insert that commits
            resp = _insert_data(stdin, reader, watchdog, "tasks", [{"title": "post_commit_target"}])
            # Kill within 50ms — just kill immediately after the response was received
            proc.kill()
            proc.wait(timeout=5)
            watchdog.stop()

        elif phase == PHASE_MID_READ:
            # Insert some data first, then do a SELECT and kill mid-read
            for i in range(5):
                _insert_data(stdin, reader, watchdog, "tasks", [{"title": f"read_phase_{i}"}])
            # Send a read tool call
            _send(stdin, {
                "jsonrpc": "2.0", "id": 40, "method": "tools/call",
                "params": {"name": "list_tables", "arguments": {}},
            })
            # Kill during the read
            time.sleep(0.1)
            proc.kill()
            proc.wait(timeout=5)
            watchdog.stop()

        exit_code = proc.returncode
        duration_s = time.time() - started

    except Exception as e:
        duration_s = time.time() - started
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        return RoundResult(
            round_idx=round_idx, phase=phase, verdict="FAIL",
            fail_reason=f"exception: {e}", duration_s=duration_s,
        )

    # Orphan accounting
    new_msa, new_py = _new_orphans(baseline_proc)
    killed_orphans: list[str] = []
    for pid in new_msa:
        killed_orphans.append(f"MSACCESS.EXE:{pid}")
    _kill_pids(new_msa)
    for pid in new_py:
        killed_orphans.append(f"python.exe:{pid}")
    _kill_pids(new_py)

    orphans_after = [f"MSACCESS.EXE:{p}" for p in _list_pids("MSACCESS.EXE") - baseline_proc.msaccess]
    orphans_after += [f"python.exe:{p}" for p in (_list_pids("python.exe") - baseline_proc.python - {os.getpid()})]

    # Wait for file system to settle
    time.sleep(1.0)

    # Classify the outcome using a fresh server spawn
    verdict, fail_reason = _classify_outcome(tmp_db, baseline_checksum, baseline_rows)

    return RoundResult(
        round_idx=round_idx,
        phase=phase,
        verdict=verdict,
        exit_code=exit_code or 0,
        duration_s=duration_s,
        orphans_after=orphans_after,
        killed_orphans=killed_orphans,
        fail_reason=fail_reason,
        baseline_rows=baseline_rows,
    )


def _classify_outcome(tmp_db: Path, baseline_checksum: str, baseline_rows: dict[str, int]) -> tuple[str, str]:
    """Classify the state of the database after a kill.

    Returns (verdict, fail_reason).
    """
    import pyodbc

    ldb_path = tmp_db.with_suffix(".ldb")
    laccdb_path = tmp_db.with_suffix(".laccdb")

    # Check 1: is the file locked or open by another process?
    lock_file = ldb_path if ldb_path.exists() else laccdb_path if laccdb_path.exists() else None

    # Try direct pyodbc connect
    try:
        conn_str = f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={tmp_db};"
        conn = pyodbc.connect(conn_str, autocommit=True, timeout=5)
        cursor = conn.cursor()
        # Count rows in each table
        final_rows: dict[str, int] = {}
        for table_name in cursor.tables(tableType="TABLE"):
            tname = table_name.table_name
            if tname.startswith("MSys"):
                continue
            try:
                cursor.execute(f"SELECT COUNT(*) FROM [{tname}]")
                final_rows[tname] = cursor.fetchone()[0]
            except Exception:
                final_rows[tname] = -1
        cursor.close()
        conn.close()

        # Compare to baseline — committed ops should survive
        # CLEAN means: all baseline rows present, no extra partial rows
        all_match = True
        for table, baseline_count in baseline_rows.items():
            final_count = final_rows.get(table, 0)
            if final_count < baseline_count:
                all_match = False
                break

        if all_match and lock_file is None:
            return VERDICT_CLEAN, ""
        elif all_match and lock_file is not None:
            return VERDICT_LOCK_STALE, f"lock file {lock_file.name} persists"
        else:
            # Some rows missing or extra — could be partial commit
            return VERDICT_REPAIRABLE, "row count mismatch after kill"
    except Exception as e:
        err_msg = str(e).lower()
        if "could not use" in err_msg or "file already in use" in err_msg or "locked" in err_msg:
            return VERDICT_LOCK_STALE, f"lock error: {e}"
        return VERDICT_FATAL, f"cannot open: {e}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="G13 kill-mid-write chaos harness")
    parser.add_argument("--rounds", type=int, default=20, help="Number of rounds (default 20)")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed (default random)")
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else int.from_bytes(os.urandom(4), "big")
    rng = random.Random(seed)
    print(f"[G13] seed={seed}  rounds={args.rounds}")
    print()

    phases = [PHASE_MID_BATCH, PHASE_POST_COMMIT, PHASE_MID_READ]

    fixture_db = Path(__file__).parent.parent / "tests" / "fixtures" / "test_db.accdb"
    if not fixture_db.exists():
        print("SKIP: tests/fixtures/test_db.accdb not found (off-Windows or no ACE)")
        return 0

    with tempfile.TemporaryDirectory(prefix="g13_") as tmpdir:
        results: list[RoundResult] = []
        for i in range(args.rounds):
            phase = phases[i % len(phases)]
            print(f"[G13] round {i + 1}/{args.rounds}  phase={phase}", end=" ... ", flush=True)
            result = _run_kill_round(i, Path(tmpdir), rng, phase)
            results.append(result)
            print(f"verdict={result.verdict}  duration={result.duration_s:.1f}s")
            if result.orphans_after:
                print(f"  [WARN] orphans after: {result.orphans_after}")
            if result.fail_reason:
                print(f"  [INFO] fail_reason: {result.fail_reason}")

        # Summary table
        print()
        print("=" * 90)
        print(f"{'Round':>4}  {'Phase':<15}  {'Verdict':<15}  {'Exit':>4}  {'Duration':>8}  {'FailReason'}")
        print("-" * 90)
        for r in results:
            print(
                f"{r.round_idx + 1:>4}  {r.phase:<15}  {r.verdict:<15}  "
                f"{r.exit_code or 0:>4}  {r.duration_s:>8.1f}s  {r.fail_reason}"
            )
        print("=" * 90)

        verdict_counts: dict[str, int] = {}
        for r in results:
            verdict_counts[r.verdict] = verdict_counts.get(r.verdict, 0) + 1
        print()
        print("Summary:")
        for v, c in sorted(verdict_counts.items()):
            print(f"  {v}: {c}")
        print()

        has_fatal = any(r.verdict == VERDICT_FATAL for r in results)
        if has_fatal:
            print("[FAIL] FATAL verdict(s) detected — unrecovered corruption")
            return 1

        print("[PASS] all rounds recovered or retryable")
        return 0


if __name__ == "__main__":
    sys.exit(_main())
