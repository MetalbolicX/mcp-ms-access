from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def g13_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "g13_kill_mid_write.py"
    spec = importlib.util.spec_from_file_location("g13_kill_mid_write", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_g13_calls_conform_to_core_registry():
    from ms_access_mcp.server import build_server

    g13 = g13_module()
    # The harness spawns the server with --profile full (it needs core
    # connect/insert plus DDL create_table for the workload bootstrap);
    # conformance must check the profile the harness actually spawns.
    server = build_server("full")
    tools = server._tool_manager._tools

    violations = []
    for call_name, (tool_name, argument_keys) in g13.TOOL_CALLS.items():
        if tool_name not in tools:
            violations.append(f"{call_name}: unknown MCP tool {tool_name!r}")
            continue
        schema = tools[tool_name].parameters
        properties = set(schema["properties"])
        required = set(schema.get("required", []))
        unexpected = set(argument_keys) - properties
        missing = required - set(argument_keys)
        if unexpected:
            violations.append(f"{call_name}: unsupported arguments {unexpected} for {tool_name}")
        if missing:
            violations.append(f"{call_name}: missing required arguments {missing} for {tool_name}")

    assert not violations, "G13 registry mismatches:\n" + "\n".join(violations)


def test_round_without_successful_writes_is_not_clean():
    g13 = g13_module()
    assert g13._classify_round(g13.VERDICT_CLEAN, successful_writes=0) == "FAIL"
