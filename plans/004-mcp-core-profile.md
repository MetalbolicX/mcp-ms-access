# Plan 004: MCP core profile over stdio

> **Executor instructions**: Follow step by step; verify each command result.
> STOP conditions are hard. Update `plans/README.md` when done.
>
> **Drift check (run first)**: new repo has plans 001–003 DONE; oracle HEAD
> `c8beaab`. The MCP SDK API facts below were verified against installed
> `mcp 1.27.1` — re-verify `import mcp.server.fastmcp` works before starting.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/003-database-usecases-and-cli.md
- **Category**: feature
- **Planned at**: oracle commit `c8beaab`, 2026-09-08

## Execution mode

`@medium` + strict TDD. In-process MCP client tests first (RED), then server
wiring. Orchestrator personally runs the real stdio spawn test and the
tool-count gate.

## Why this matters

This is the AI-harness surface: OpenCode (and any MCP client) auto-spawns this
executable over stdio. It must expose a SMALL, typed, safe-by-default tool set
(≤ 30 tools) so tool schemas don't flood the model context — the exact
token-cost concern that drove local-default registration.

## Current state

- Use cases from plan 003: `application/database.py` with fixed operation names and oracle-compatible envelopes (`{"success", "error"?, ...}`).
- SDK (verified against `mcp 1.27.1` in the oracle venv):
  - `from mcp.server.fastmcp import FastMCP` — official SDK class; `@mcp.tool()` generates schemas from type hints; `mcp.run()` defaults to stdio (`run()` dispatches `transport="stdio"` → `anyio.run(run_stdio_async)`); `lifespan=` accepts an async context manager for startup/cleanup.
  - Structured output from typed return classes; plain `dict` returns are serialized as-is.
- Oracle tool names to reuse verbatim (parity muscle memory): `connect_access`, `disconnect_access`, `list_connections`, `get_tables`, `get_table_schema`, `get_indexes`, `get_relationships`, `get_queries`, `create_query`, `set_query_sql`, `delete_query`, `query_data`, `insert_data`, `update_data`, `delete_data`, `execute_raw_sql`, `export_data`, `get_database_statistics`, `get_system_tables`, `get_linked_tables`.
- Oracle error-envelope rule (plan-008 decision): `{success: false, error}` dicts → MCP `isError: true` with the JSON stringified as text content; never convert to thrown exceptions.
- Old server defect to avoid: `cli serve` defaulted to HTTP and printed an auto-generated API key to stdout (`cli/main.py:26-58`). Stdio mode requires NO key, prints NOTHING to stdout except protocol.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Unit tests | `uv run pytest tests/unit -q` | all pass |
| Stdio spawn | `uv run python scripts/stdio_smoke.py` | handshake + tools/list OK, < 10 s |
| Tool count | `uv run python scripts/tool_budget.py` | core ≤ 30 tools |
| Lint/Types | `uv run ruff check .` / `uv run pyright` | exit 0 / 0 errors |

## Scope

**In scope**: `src/ms_access_mcp/server.py`, `src/ms_access_mcp/mcp_tools/core.py`, `src/ms_access_mcp/profiles.py` (new), `src/ms_access_mcp/cli.py` (`mcp` subcommand), `scripts/stdio_smoke.py`, `scripts/tool_budget.py`, `tests/**`.

**Out of scope**: automation/maintenance/versioning tools (plans 006/007), installer/registration (plan 005), any HTTP/SSE transport.

## Git workflow

Branch `plan/004-mcp-core`. Conventional commits. No push.

## Steps

### Step 1: Profile registry

`profiles.py`: `PROFILES = {"core", "automation", "maintenance", "versioning", "full"}`; `load_profile(name) -> list[ToolModule]`; resolution order: explicit `--profile` arg > `ACCESS_MCP_PROFILE` env > `"core"`. `"full"` = union. Unknown name → error listing valid profiles.

Strict TDD: resolution-order and unknown-name tests first.

**Verify**: `uv run pytest tests/unit/test_profiles.py -q` → all pass.

### Step 2: Server composition

`server.py`:

```python
from mcp.server.fastmcp import FastMCP
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(server):
    yield
    # cleanup: close active connection, shutdown dispatcher (STA teardown)

def build_server(profile: str = "core") -> FastMCP:
    mcp = FastMCP("ms-access", lifespan=lifespan)
    for module in load_profile(profile):
        module.register(mcp)
    return mcp

def main(argv=None):
    profile = parse(argv)  # --profile / env
    build_server(profile).run()  # stdio default
```

Rules: stdout = protocol ONLY (no banners); all logs to stderr via `logging`; no API key logic anywhere.

**Verify**: `uv run pytest tests/unit/test_server_build.py -q` → pass (build with fakes; lifespan cleanup invoked on exit).

### Step 3: Core tools (strict TDD)

`mcp_tools/core.py` — register the ~20 verbatim-named tools wrapping plan-003 use cases 1:1. Typed signatures (str paths, `params: dict[str, Any] | None`, `confirm: bool = False` where the use case gates). Error envelope: return the use-case dict unchanged; `success: false` must surface as `isError: true` (adapter in one helper, oracle plan-008 rule). `execute_raw_sql` is IN core but requires `confirm=True` and the write policy (readonly default blocks it — error explains how to enable).

Tests (RED first, in-process client — model after oracle `test_server.py` stdio refs): initialize handshake; `tools/list` exact names; per-tool round-trip against use-case fakes; error-dict → `isError`.

**Verify**: `uv run pytest tests/unit/test_mcp_core.py -q` → all pass.

### Step 4: CLI wiring

`ms-access-mcp mcp [--profile core]` → `server.main(argv)`. `serve`/HTTP does NOT exist. `ms-access-mcp mcp --help` documents profiles and the token-cost rationale.

**Verify**: `uv run ms-access-mcp mcp --help` → exit 0.

### Step 5: Real stdio smoke + budget gate (orchestrator runs these)

`scripts/stdio_smoke.py`: spawn `uv run ms-access-mcp mcp` via subprocess, pipe JSON-RPC `initialize` → `tools/list` → one `get_tables` call on a temp fixture copy, assert responses and clean exit on stdin EOF (this also proves the shutdown path). `scripts/tool_budget.py`: build server for each profile, count tools; assert core ≤ 30; assert disabled profiles contribute ZERO tools (build "core" and diff tool names vs full registry).

**Verify**: both scripts green; `Get-Process MSACCESS` empty after smoke; 5 consecutive spawn/EOF rounds leave zero orphan python/MSACCESS children.

### Step 6: CLI/MCP equivalence

Extend the plan-003 harness: for 5 representative operations, CLI `--json` output == direct use-case JSON == MCP tool result JSON (normalized key order).

**Verify**: `uv run pytest tests/integration/test_cli_mcp_equivalence.py -q` → pass.

## Test plan

- `tests/unit/test_profiles.py`, `test_server_build.py`, `test_mcp_core.py` (fakes + in-process client).
- `tests/integration/test_stdio_spawn.py` — subprocess handshake/EOF/cleanup.
- `tests/integration/test_cli_mcp_equivalence.py`.
- Gate scripts under `scripts/` (usable in plan 008 unchanged).

## Done criteria

- [ ] `tools/list` on core profile ≤ 30 tools, verbatim oracle names
- [ ] Disabled profiles contribute zero schemas (automated diff)
- [ ] `isError: true` mapping proven for `{success: false}` envelopes
- [ ] Stdio smoke: handshake, tool call, clean EOF exit, 5/5 rounds zero orphans
- [ ] No stdout pollution (test asserts stdout lines are all JSON-RPC)
- [ ] ruff + pyright clean; `plans/README.md` row 004 updated

## STOP conditions

- Core profile cannot fit 30 tools without dropping a verbatim-name tool → report the overflow list, don't silently exceed.
- SDK API differs from the verified facts above (drift in `mcp` package).
- Clean EOF shutdown leaves orphan processes after one fix attempt.

## Maintenance notes

- Plan 005 registers the command `ms-access-mcp.exe mcp --profile core` — keep that invocation contract stable.
- Reviewer focus: tool signatures (they are the LLM contract) and the isError adapter.
