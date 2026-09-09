"""Stdio smoke test — spawns MCP server, sends JSON-RPC handshake, asserts clean exit."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def _drain_stderr(proc: subprocess.Popen) -> str:
    """Read any pending stderr bytes from the subprocess without blocking."""
    if proc.stderr is None:
        return ""
    try:
        data = proc.stderr.read(8192)
    except Exception:
        return ""
    return data.decode("utf-8", errors="replace") if data else ""


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

    def drain_stderr(self) -> str:
        """Best-effort read of available stderr from the underlying process."""
        try:
            data = self.stream.read(4096)
        except Exception:
            return ""
        return data.decode("utf-8", errors="replace") if data else ""


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


def main() -> int:
    """Run stdio smoke test against the MCP server."""
    fixture_db = Path(__file__).parent.parent / "tests" / "fixtures" / "test_db.accdb"
    if not fixture_db.exists():
        print("SKIP: test fixture not found", file=sys.stderr)
        return 0

    tmpdir = tempfile.mkdtemp(prefix="acc_smoke_")
    tmp_db = Path(tmpdir) / fixture_db.name
    shutil.copy2(fixture_db, tmp_db)

    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            ["uv", "run", "python", "-u", "-m", "ms_access_mcp.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env={**__import__("os").environ, "ACCESS_MCP_ALLOWED_DIRS": str(tmpdir)},
        )
        watchdog = _Watchdog(proc, timeout_s=15.0)

        stdin = proc.stdin
        stdout = proc.stdout
        assert stdin is not None
        assert stdout is not None
        reader = _LineReader(stdout)

        def send(rpc: dict) -> None:
            line = (json.dumps(rpc) + "\n").encode("utf-8")
            stdin.write(line)
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
                "clientInfo": {"name": "smoke-test", "version": "0.1.0"},
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
        print("OK: initialize handshake", file=sys.stderr)

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
        expected = [
            "connect_access", "disconnect_access", "list_connections",
            "get_tables", "get_table_schema", "get_indexes", "get_relationships",
            "get_queries", "create_query", "set_query_sql", "delete_query",
            "query_data", "insert_data", "update_data", "delete_data",
            "execute_raw_sql", "export_data", "get_database_statistics",
            "get_system_tables", "get_linked_tables",
        ]
        missing = [n for n in expected if n not in tool_names]
        if missing:
            print(f"FAIL: missing tools: {missing}", file=sys.stderr)
            proc.kill()
            return 1
        print(f"OK: all {len(expected)} core tools present", file=sys.stderr)

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
        print("OK: get_tables call succeeded", file=sys.stderr)

        # Clean EOF
        stdin.close()
        try:
            exit_code = proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            print("FAIL: server did not exit within 10s after EOF", file=sys.stderr)
            return 1
        if exit_code != 0:
            err = _drain_stderr(proc)
            print(f"FAIL: server exit={exit_code}; stderr={err[:600]}", file=sys.stderr)
            return 1
        watchdog.stop()
        print("OK: clean exit on stdin EOF", file=sys.stderr)
        print("PASS: stdio smoke test")
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
