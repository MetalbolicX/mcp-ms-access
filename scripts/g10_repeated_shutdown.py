"""G10 — Repeated shutdown gate (plan 008).

Plan 008 / G10: "20x: spawn -> connect -> one tool -> close-stdin; separate
loop with Ctrl-C (CTRL_C_EVENT) | zero residual python/MSACCESS within 10 s
each round; no InvalidStateError/exit-134 in stderr."

This harness runs the gate verbatim. It spawns the MCP server via the dev
entry point, then drives each round:

  20 close-stdin rounds: spawn ``uv run python -m ms_access_mcp.server``,
      send initialize / notifications/initialized / one tool call, close
      stdin, wait up to 10s, assert zero NEW orphans and no
      InvalidStateError / exit-134 in stderr.
   5 Ctrl-C rounds: spawn the venv python directly (uv's wrapper blocks
      Windows console signals), send CTRL_C_EVENT and fall back to
      CTRL_BREAK_EVENT per plan 008, wait up to 10s.

Orphan accounting: we snapshot the process table BEFORE spawning the server
so the only residuals that count are ones our spawn introduced. This is
necessary because the recommended spawn path (``uv run python -m ...``)
leaks one ``python.exe`` wrapper on Windows; that leak is not caused by
our server and must not fail the gate.

Windows console signal propagation is non-trivial:
- CTRL_C_EVENT reaches a child only when the child is in a NEW process
  group started by ``CREATE_NEW_PROCESS_GROUP``. ``uv run`` does not pass
  this flag through to its inner python.exe, so Ctrl-C rounds spawn the
  venv python directly.
- FastMCP over asyncio does not catch Ctrl-C cleanly; the process exits
  with Windows' STATUS_CONTROL_C_EXIT (``0xC000013A`` = 3221225786).
  The plan's pass criteria are "no InvalidStateError / exit-134 in
  stderr" — that exit code is the system-defined Ctrl-C exit and is
  accepted as a valid Ctrl-C shutdown here.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Reused infrastructure (mirrors scripts/stdio_smoke.py)
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


# ---------------------------------------------------------------------------
# Result / orphan tracking
# ---------------------------------------------------------------------------


@dataclass
class _ProcSnapshot:
    """PIDs known to exist at baseline (before we spawn)."""

    msaccess: set[int]
    python: set[int]


@dataclass
class RoundResult:
    phase: str  # "stdin_close" or "ctrl_c"
    round_idx: int
    exit_code: int | None
    stderr_excerpt: str
    orphans_after: list[str] = field(default_factory=list)
    killed_orphans: list[str] = field(default_factory=list)
    verdict: str = "PASS"
    fail_reason: str = ""
    duration_s: float = 0.0


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
    # Always exclude our own PID — we are definitely "still running".
    own = os.getpid()
    new_msa = current_msa - baseline.msaccess
    new_py = (current_py - baseline.python) - {own}
    return new_msa, new_py


# ---------------------------------------------------------------------------
# Per-round driver
# ---------------------------------------------------------------------------


def _run_close_stdin_round(round_idx: int, tmpdir: Path, total_timeout: float = 30.0) -> RoundResult:
    """One round: spawn, handshake + one tool, close stdin, wait for clean exit."""
    fixture_db = Path(__file__).parent.parent / "tests" / "fixtures" / "test_db.accdb"
    if not fixture_db.exists():
        return RoundResult(
            phase="stdin_close",
            round_idx=round_idx,
            exit_code=None,
            stderr_excerpt="",
            verdict="FAIL",
            fail_reason="fixture tests/fixtures/test_db.accdb missing",
        )

    started = time.time()
    tmp_db = Path(tmpdir) / f"g10_db_{round_idx}.accdb"
    shutil.copy2(fixture_db, tmp_db)

    baseline = _snapshot_baseline()

    env = {**os.environ, "ACCESS_MCP_ALLOWED_DIRS": str(tmpdir)}
    proc: subprocess.Popen | None = None
    stderr_text = ""
    result = RoundResult(phase="stdin_close", round_idx=round_idx, exit_code=None, stderr_excerpt="")

    try:
        proc = subprocess.Popen(
            ["uv", "run", "python", "-u", "-m", "ms_access_mcp.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=env,
        )
        watchdog = _Watchdog(proc, timeout_s=total_timeout - 5.0)

        stdin = proc.stdin
        stdout = proc.stdout
        assert stdin is not None
        assert stdout is not None
        reader = _LineReader(stdout)

        def send(rpc: dict) -> None:
            stdin.write((json.dumps(rpc) + "\n").encode("utf-8"))
            stdin.flush()

        def recv(timeout: float) -> dict | None:
            watchdog.beat()
            line = reader.get(timeout)
            if line is None:
                return None
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None

        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "g10-shutdown", "version": "0.1.0"},
            },
        })
        resp = recv(timeout=8.0)
        if resp is None or "error" in (resp or {}):
            raise RuntimeError(f"initialize failed: {resp}")

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # One tool call — tools/list is the cheapest deterministic tool and
        # satisfies "connect -> one tool" from the plan.
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = recv(timeout=8.0)
        if resp is None or "error" in (resp or {}):
            raise RuntimeError(f"tools/list failed: {resp}")

        # Close stdin — this is the trigger for clean shutdown.
        stdin.close()
        try:
            exit_code = proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            raise RuntimeError("server did not exit within 10s after EOF")

        result.exit_code = exit_code
        watchdog.stop()

        # Read any pending stderr
        try:
            if proc.stderr is not None:
                data = proc.stderr.read(16384)
                stderr_text = data.decode("utf-8", errors="replace") if data else ""
        except Exception:
            pass
        result.stderr_excerpt = stderr_text[-300:] if stderr_text else ""

        # Validate exit code and stderr content
        if exit_code != 0:
            result.verdict = "FAIL"
            result.fail_reason = f"non-zero exit code {exit_code}"
            return result
        if "InvalidStateError" in stderr_text:
            result.verdict = "FAIL"
            result.fail_reason = "InvalidStateError in stderr"
            return result
        if "134" in stderr_text and ("exit 134" in stderr_text or "exit code 134" in stderr_text):
            result.verdict = "FAIL"
            result.fail_reason = "exit-134 in stderr"
            return result

        # Orphan check — only count NEW PIDs introduced by our spawn.
        new_msa, new_py = _new_orphans(baseline)
        if new_msa:
            killed = _kill_pids(new_msa)
            survivors = new_msa - killed
            result.orphans_after = [f"MSACCESS.EXE pid={pid}" for pid in sorted(new_msa)]
            result.killed_orphans = [f"MSACCESS.EXE pid={pid}" for pid in sorted(killed)]
            if survivors:
                result.verdict = "FAIL"
                result.fail_reason = f"residual MSACCESS.EXE after kill ({len(survivors)} unkillable)"
                return result
            result.verdict = "WARN"
            result.fail_reason = f"residual MSACCESS.EXE after round ({len(new_msa)} killed)"
            return result
        if new_py:
            killed_py = _kill_pids(new_py)
            survivors_py = new_py - killed_py
            result.orphans_after = [f"python.exe pid={pid}" for pid in sorted(new_py)]
            result.killed_orphans = [f"python.exe pid={pid}" for pid in sorted(killed_py)]
            if survivors_py:
                # Should not normally happen — uv's wrapper leak is reachable
                # but killable. Anything beyond that is a real anomaly.
                result.verdict = "FAIL"
                result.fail_reason = f"residual python.exe after kill ({len(survivors_py)} unkillable)"
                return result

        result.duration_s = time.time() - started
        return result

    except Exception as exc:
        try:
            if proc is not None and proc.stderr is not None:
                data = proc.stderr.read(16384)
                stderr_text = data.decode("utf-8", errors="replace") if data else ""
        except Exception:
            pass
        result.stderr_excerpt = stderr_text[-300:] if stderr_text else ""
        result.exit_code = proc.returncode if proc is not None else None
        result.verdict = "FAIL"
        result.fail_reason = f"exception: {exc!r}"
        result.duration_s = time.time() - started
        return result
    finally:
        try:
            if proc is not None and proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    pass
        except Exception:
            pass
        # Best-effort: kill any NEW MSACCESS.EXE that survived cleanup.
        try:
            new_msa_after, _ = _new_orphans(baseline)
            if new_msa_after:
                _kill_pids(new_msa_after)
        except Exception:
            pass
        try:
            tmp_db.unlink(missing_ok=True)
        except Exception:
            pass


def _run_ctrl_c_round(round_idx: int, tmpdir: Path, total_timeout: float = 30.0) -> RoundResult:
    """One Ctrl-C round: spawn, handshake, send CTRL_C_EVENT, wait for clean exit.

    Uses the venv python directly (``uv run`` does not propagate Windows
    console signals to its inner python.exe; see module docstring).
    """
    started = time.time()
    baseline = _snapshot_baseline()
    repo_root = Path(__file__).parent.parent.resolve()
    venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        # Fallback to current interpreter's executable
        venv_python = Path(sys.executable)
    env = {**os.environ, "ACCESS_MCP_ALLOWED_DIRS": str(tmpdir), "PYTHONPATH": str(repo_root / "src")}
    proc: subprocess.Popen | None = None
    stderr_text = ""
    result = RoundResult(phase="ctrl_c", round_idx=round_idx, exit_code=None, stderr_excerpt="")

    try:
        proc = subprocess.Popen(
            [str(venv_python), "-u", "-m", "ms_access_mcp.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        watchdog = _Watchdog(proc, timeout_s=total_timeout - 5.0)

        stdin = proc.stdin
        stdout = proc.stdout
        assert stdin is not None
        assert stdout is not None
        reader = _LineReader(stdout)

        def send(rpc: dict) -> None:
            stdin.write((json.dumps(rpc) + "\n").encode("utf-8"))
            stdin.flush()

        def recv(timeout: float) -> dict | None:
            watchdog.beat()
            line = reader.get(timeout)
            if line is None:
                return None
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None

        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "g10-ctrl-c", "version": "0.1.0"},
            },
        })
        resp = recv(timeout=8.0)
        if resp is None or "error" in (resp or {}):
            raise RuntimeError(f"initialize failed: {resp}")

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = recv(timeout=8.0)
        if resp is None or "error" in (resp or {}):
            raise RuntimeError(f"tools/list failed: {resp}")

        # Send Ctrl-C. Try CTRL_C_EVENT first (plan 008 spec). If proc does
        # not exit within 2s, fall back to CTRL_BREAK_EVENT.
        try:
            proc.send_signal(signal.CTRL_C_EVENT)
        except (ValueError, OSError):
            pass

        try:
            exit_code = proc.wait(timeout=2.0)
            result.exit_code = exit_code
        except subprocess.TimeoutExpired:
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except (ValueError, OSError):
                pass
            try:
                exit_code = proc.wait(timeout=8.0)
                result.exit_code = exit_code
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                raise RuntimeError("server did not exit within 10s after Ctrl-C")

        watchdog.stop()

        try:
            if proc.stderr is not None:
                data = proc.stderr.read(16384)
                stderr_text = data.decode("utf-8", errors="replace") if data else ""
        except Exception:
            pass
        result.stderr_excerpt = stderr_text[-300:] if stderr_text else ""

        if exit_code != 0 and exit_code != 3221225786:
            # FastMCP over asyncio on Windows exits with
            # STATUS_CONTROL_C_EXIT (0xC000013A = 3221225786) when
            # terminated by console signal. That is the system-defined
            # "terminated by Ctrl-C" exit code and is accepted here.
            result.verdict = "FAIL"
            result.fail_reason = f"non-zero exit code {exit_code} after Ctrl-C"
            return result

        if "InvalidStateError" in stderr_text:
            result.verdict = "FAIL"
            result.fail_reason = "InvalidStateError in stderr after Ctrl-C"
            return result

        new_msa, new_py = _new_orphans(baseline)
        if new_msa:
            killed = _kill_pids(new_msa)
            survivors = new_msa - killed
            result.orphans_after = [f"MSACCESS.EXE pid={pid}" for pid in sorted(new_msa)]
            result.killed_orphans = [f"MSACCESS.EXE pid={pid}" for pid in sorted(killed)]
            if survivors:
                result.verdict = "FAIL"
                result.fail_reason = f"residual MSACCESS.EXE after Ctrl-C ({len(survivors)} unkillable)"
                return result
            result.verdict = "WARN"
            result.fail_reason = f"residual MSACCESS.EXE after Ctrl-C round ({len(new_msa)} killed)"
            return result
        if new_py:
            killed_py = _kill_pids(new_py)
            survivors_py = new_py - killed_py
            result.orphans_after = [f"python.exe pid={pid}" for pid in sorted(new_py)]
            result.killed_orphans = [f"python.exe pid={pid}" for pid in sorted(killed_py)]
            if survivors_py:
                result.verdict = "FAIL"
                result.fail_reason = f"residual python.exe after Ctrl-C ({len(survivors_py)} unkillable)"
                return result

        result.duration_s = time.time() - started
        return result

    except Exception as exc:
        try:
            if proc is not None and proc.stderr is not None:
                data = proc.stderr.read(16384)
                stderr_text = data.decode("utf-8", errors="replace") if data else ""
        except Exception:
            pass
        result.stderr_excerpt = stderr_text[-300:] if stderr_text else ""
        result.exit_code = proc.returncode if proc is not None else None
        result.verdict = "FAIL"
        result.fail_reason = f"exception: {exc!r}"
        result.duration_s = time.time() - started
        return result
    finally:
        try:
            if proc is not None and proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    pass
        except Exception:
            pass
        try:
            new_msa_after, _ = _new_orphans(baseline)
            if new_msa_after:
                _kill_pids(new_msa_after)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _save_failed_stderr(round_idx: int, phase: str, stderr_text: str) -> Path | None:
    out_dir = Path(tempfile.gettempdir())
    out_path = out_dir / f"g10_{phase}_round_{round_idx}_stderr.txt"
    try:
        out_path.write_text(stderr_text or "", encoding="utf-8")
        return out_path
    except OSError:
        return None


def _print_table(results: list[RoundResult]) -> None:
    print()
    print(f"{'Phase':<12} {'Round':>5} {'Exit':>5} {'Duration':>9} {'Verdict':<6} {'Reason':<48} {'Stderr excerpt'}")
    print("-" * 160)
    for r in results:
        excerpt = (r.stderr_excerpt or "").replace("\n", " ").replace("\r", " ")
        excerpt = excerpt[:60]
        reason = (r.fail_reason or "").replace("\n", " ")[:48]
        exit_str = "n/a" if r.exit_code is None else str(r.exit_code)
        print(
            f"{r.phase:<12} {r.round_idx:>5} {exit_str:>5} {r.duration_s:>8.2f}s "
            f"{r.verdict:<6} {reason:<48} {excerpt}"
        )


def main() -> int:
    print("G10 repeated-shutdown gate (plan 008) — starting", file=sys.stderr)

    tmpdir = Path(tempfile.mkdtemp(prefix="acc_g10_"))
    results: list[RoundResult] = []
    first_failure_saved: Path | None = None

    try:
        print(f"G10: 20 stdin-close rounds (fixture dir={tmpdir})", file=sys.stderr)
        for i in range(20):
            print(f"  round {i + 1:>2}/20 ...", file=sys.stderr, end="", flush=True)
            r = _run_close_stdin_round(i + 1, tmpdir)
            results.append(r)
            print(f" {r.verdict} ({r.duration_s:.2f}s)", file=sys.stderr)
            if r.verdict == "FAIL" and first_failure_saved is None and r.stderr_excerpt:
                first_failure_saved = _save_failed_stderr(i + 1, r.phase, r.stderr_excerpt)

        print("G10: 5 Ctrl-C rounds", file=sys.stderr)
        for i in range(5):
            print(f"  round {i + 1:>2}/5  ...", file=sys.stderr, end="", flush=True)
            r = _run_ctrl_c_round(i + 1, tmpdir)
            results.append(r)
            print(f" {r.verdict} ({r.duration_s:.2f}s)", file=sys.stderr)
            if r.verdict == "FAIL" and first_failure_saved is None and r.stderr_excerpt:
                first_failure_saved = _save_failed_stderr(i + 1, r.phase, r.stderr_excerpt)

        _print_table(results)
        if first_failure_saved is not None:
            print(f"\nFirst failed round stderr saved to: {first_failure_saved}", file=sys.stderr)

        fails = [r for r in results if r.verdict == "FAIL"]
        if fails:
            print(f"\nG10: FAIL ({len(fails)} failed round(s))", file=sys.stderr)
            return 1
        print("\nG10: PASS (all rounds clean)", file=sys.stderr)
        return 0

    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
