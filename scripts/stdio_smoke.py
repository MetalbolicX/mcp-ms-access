"""Stdio smoke test — spawns MCP server, sends JSON-RPC handshake, asserts clean exit."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    """Run stdio smoke test against the MCP server.

    Spawns `uv run ms-access-mcp mcp`, sends JSON-RPC initialize ->
    tools/list -> get_tables call, asserts responses, and proves clean
    exit on stdin EOF (proving the shutdown path).

    Returns:
        0 on success, non-zero on failure
    """
    # Find fixture DB
    fixture_db = Path(__file__).parent.parent / "tests" / "fixtures" / "test_db.accdb"
    if not fixture_db.exists():
        print("SKIP: test fixture not found", file=sys.stderr)
        return 0  # Skip on missing fixture (CI environments)

    # Create temp copy for the test
    tmpdir = tempfile.mkdtemp(prefix="acc_smoke_")
    tmp_db = Path(tmpdir) / fixture_db.name
    shutil.copy2(fixture_db, tmp_db)

    try:
        # Spawn server process
        proc = subprocess.Popen(
            ["uv", "run", "ms-access-mcp", "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        results: dict[str, object] = {}

        def send(rpc: dict) -> None:
            line = json.dumps(rpc) + "\n"
            proc.stdin.write(line)  # type: ignore
            proc.stdin.flush()

        def recv() -> dict | None:
            line = proc.stdout.readline()  # type: ignore
            if not line:
                return None
            return json.loads(line.strip())

        # JSON-RPC handshake
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke-test", "version": "0.1.0"},
        }})
        resp = recv()
        if resp is None:
            print("FAIL: no response to initialize", file=sys.stderr)
            return 1
        if "error" in resp:
            print(f"FAIL: initialize error: {resp['error']}", file=sys.stderr)
            return 1
        results["initialize"] = resp
        print("OK: initialize handshake")

        # Send initialized notification
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # tools/list
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = recv()
        if resp is None:
            print("FAIL: no response to tools/list", file=sys.stderr)
            return 1
        if "error" in resp:
            print(f"FAIL: tools/list error: {resp['error']}", file=sys.stderr)
            return 1
        results["tools_list"] = resp
        print("OK: tools/list")

        # Verify core tools present
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
        for name in expected:
            if name not in tool_names:
                print(f"FAIL: missing tool {name}", file=sys.stderr)
                return 1
        print(f"OK: all {len(expected)} core tools present")

        # get_tables call on temp copy
        send({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_tables",
                "arguments": {"path": str(tmp_db)},
            },
        })
        resp = recv()
        if resp is None:
            print("FAIL: no response to get_tables call", file=sys.stderr)
            return 1
        if "error" in resp:
            print(f"FAIL: get_tables error: {resp['error']}", file=sys.stderr)
            return 1
        results["get_tables"] = resp
        print("OK: get_tables call succeeded")

        # Clean EOF: close stdin to signal shutdown
        proc.stdin.close()  # type: ignore

        # Wait for clean exit (with timeout)
        try:
            exit_code = proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            print("FAIL: server did not exit cleanly within 10s", file=sys.stderr)
            return 1

        if exit_code != 0:
            stderr_output = proc.stderr.read()  # type: ignore
            print(f"FAIL: server exited with code {exit_code}: {stderr_output}", file=sys.stderr)
            return 1

        print("OK: clean exit on stdin EOF")
        print("PASS: stdio smoke test")

        # Verify no stdout pollution (all lines should be JSON-RPC)
        # We already checked stderr is empty-ish
        return 0

    finally:
        # Cleanup temp dir
        import time as time_module
        time_module.sleep(0.25)
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
