"""G12 — Portable stdio smoke gate (plan 032).

Plan 032 / G12: "Portable win64 zip distribution" — parameterized stdio smoke.

This script verifies the portable zip runtime (embeddable python.exe + -m
ms_access_mcp) answers the JSON-RPC stdio handshake, exposes the core profile
tool set, performs one real tool call against a fixture DB copy, and exits
cleanly on EOF.

Accepts the full command as arguments (after a `--` separator), e.g.:
    python scripts/g12_portable_stdio_smoke.py -- runtime\\python.exe -m ms_access_mcp mcp --profile core

Runs from any cwd — does not depend on the source tree being importable.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Reused infrastructure (mirrors scripts/stdio_smoke.py and g11)
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
# Main
# ---------------------------------------------------------------------------


def _drain_stderr(proc: subprocess.Popen) -> str:
    """Read any pending stderr bytes from the subprocess without blocking."""
    if proc.stderr is None:
        return ""
    try:
        data = proc.stderr.read(8192)
    except Exception:
        return ""
    return data.decode("utf-8", errors="replace") if data else ""


def main() -> int:
    """Run the G12 stdio smoke against the provided command."""
    parser = argparse.ArgumentParser(
        prog="g12_portable_stdio_smoke",
        description="Portable stdio smoke gate for the ms-access-mcp portable zip.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help=(
            "Full command list after `--`, e.g.: "
            "runtime\\python.exe -m ms_access_mcp mcp --profile core"
        ),
    )
    args = parser.parse_args()

    # Strip a leading `--` if the caller passed it (common pattern)
    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    if not cmd:
        print("FAIL: no command provided; pass the runtime command after `--`", file=sys.stderr)
        return 1

    print(f"G12: running command: {' '.join(cmd)}", file=sys.stderr)

    fixture_db = Path(__file__).parent.parent / "tests" / "fixtures" / "test_db.accdb"
    if not fixture_db.exists():
        print("SKIP: test fixture not found", file=sys.stderr)
        return 0

    tmpdir = tempfile.mkdtemp(prefix="acc_g12_")
    tmp_db = Path(tmpdir) / fixture_db.name
    shutil.copy2(fixture_db, tmp_db)

    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env={**os.environ, "ACCESS_MCP_ALLOWED_DIRS": str(tmpdir)},
        )
        watchdog = _Watchdog(proc, timeout_s=20.0)

        stdin = proc.stdin
        stdout = proc.stdout
        assert stdin is not None  # noqa: S101 - Popen PIPE setup establishes the stream invariant
        assert stdout is not None  # noqa: S101 - Popen PIPE setup establishes the stream invariant
        reader = _LineReader(stdout)

        def send(rpc: dict) -> None:
            stdin.write((json.dumps(rpc) + "\n").encode("utf-8"))
            stdin.flush()

        def recv(timeout: float = 8.0) -> dict | None:
            watchdog.beat()
            line = reader.get(timeout)
            if line is None:
                return None
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None

        # initialize
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "g12-portable-smoke", "version": "0.2.0"},
            },
        })
        resp = recv(timeout=10.0)
        if resp is None:
            err = _drain_stderr(proc)
            print(f"FAIL: no initialize response; stderr={err[:600]}", file=sys.stderr)
            proc.kill()
            return 1
        if "error" in resp:
            err = _drain_stderr(proc)
            print(f"FAIL: initialize error: {resp['error']}; stderr={err[:600]}", file=sys.stderr)
            proc.kill()
            return 1
        print("OK: initialize handshake against portable runtime", file=sys.stderr)

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # tools/list
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = recv(timeout=10.0)
        if resp is None:
            err = _drain_stderr(proc)
            print(f"FAIL: no tools/list response; stderr={err[:600]}", file=sys.stderr)
            proc.kill()
            return 1
        if "error" in resp:
            err = _drain_stderr(proc)
            print(f"FAIL: tools/list error: {resp['error']}; stderr={err[:600]}", file=sys.stderr)
            proc.kill()
            return 1
        tools = resp.get("result", {}).get("tools", [])
        tool_names = [t["name"] for t in tools]
        if len(tool_names) < 20:
            print(f"FAIL: expected >= 20 core tools, got {len(tool_names)}", file=sys.stderr)
            proc.kill()
            return 1
        print(f"OK: portable runtime exposes {len(tool_names)} core tools", file=sys.stderr)

        # get_tables
        send({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "get_tables", "arguments": {"path": str(tmp_db)}},
        })
        resp = recv(timeout=15.0)
        if resp is None:
            err = _drain_stderr(proc)
            print(f"FAIL: no get_tables response; stderr={err[:600]}", file=sys.stderr)
            proc.kill()
            return 1
        if "error" in resp:
            err = _drain_stderr(proc)
            print(f"FAIL: get_tables error: {resp['error']}; stderr={err[:600]}", file=sys.stderr)
            proc.kill()
            return 1
        result = resp.get("result", {})
        if result.get("isError"):
            err = _drain_stderr(proc)
            print(f"FAIL: get_tables isError: {result}; stderr={err[:600]}", file=sys.stderr)
            proc.kill()
            return 1
        print("OK: get_tables call succeeded against portable runtime", file=sys.stderr)

        # Clean EOF
        stdin.close()
        try:
            exit_code = proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            print("FAIL: portable runtime did not exit within 10s after EOF", file=sys.stderr)
            return 1
        if exit_code != 0:
            err = _drain_stderr(proc)
            print(f"FAIL: portable runtime exit={exit_code}; stderr={err[:600]}", file=sys.stderr)
            return 1
        watchdog.stop()
        print("OK: portable runtime exited cleanly on stdin EOF", file=sys.stderr)
        print("PASS: G12 portable stdio smoke")
        return 0

    finally:
        try:
            if proc is not None and proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        time.sleep(0.25)
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
