# Plan 079: Explicit session ownership and dispatcher teardown

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/mcp_tools/_session_registry.py src/ms_access_mcp/mcp_tools/core.py src/ms_access_mcp/server.py src/ms_access_mcp/backends/dao.py src/ms_access_mcp/backends/dispatcher.py`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/077-*.md (green baseline)
- **Category**: bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

Every core-profile MCP tool call builds a fresh `DatabaseUseCases` and tracks
it in a `weakref.WeakSet`. The use case is garbage-collected when the tool
returns, so the registry entry vanishes and `close_all_sessions()` (server
lifespan and `disconnect_access`) can no longer reach it. On the default DAO
backend, `disconnect()` closes the database but never calls
`dispatcher.shutdown()`, and the dispatcher's daemon STA thread holds the COM
engine alive — a leaked thread (and, until GC'd handles release, a database
lock) per call in a long-lived server. Plan 066's registry is structurally
unable to do its job for per-call sessions.

## Current state

- `src/ms_access_mcp/mcp_tools/_session_registry.py` — the registry
  (~line 19) is a `weakref.WeakSet` plus `track()`/`close_all_sessions()`.
- `src/ms_access_mcp/mcp_tools/core.py` — `_new_db()` (~lines 66–68)
  constructs `DatabaseUseCases(...)` and `registry.track(...)`s it per tool
  call; `_open_or_err` (from plan 045) opens the adapter. Nothing closes the
  use case when the call ends.
- `src/ms_access_mcp/server.py` — lifespan teardown (~lines 68–83) calls
  `close_all_sessions()`.
- `src/ms_access_mcp/backends/dao.py` — `connect()` starts the dispatcher
  STA thread (~line 158, `self._dispatcher.start()`); `disconnect()`
  (~lines 156–162) calls `close_dao_database()` only — no `shutdown()`.
  Contrast `src/ms_access_mcp/backends/access/__init__.py` (~line 116),
  whose `AccessAdapter.disconnect()` does call `shutdown()`.
- `src/ms_access_mcp/backends/dispatcher.py` — `start()` spawns a daemon
  thread (~line 85); the thread's bound `_run` keeps the dispatcher and its
  `_current_db` COM reference alive; `shutdown()` joins with a 15 s timeout
  (~line 266). After a `join` timeout, `_started` is reset (~lines 80–87)
  so a later `start()` can spawn a second thread over the same queue —
  restart-safety is untested (audit TEST-10).
- Verified in the audit (in-memory): `track(obj); del obj; gc.collect()` →
  0 tracked; `close_all_sessions()` no-ops.
- Existing tests hold strong local references (`tests/unit/test_mcp_core.py`
  ~lines 134–140, `tests/unit/test_server_build.py` ~lines 100–108), which
  is exactly why the suite stays green.
- Conventions: result envelopes `Ok`/`Err` from `src/ms_access_mcp/errors.py`
  (see `mcp_tools/core.py` for the `_err` usage pattern); COM teardown rules
  from plans 030/065 (all COM calls on the dispatcher's STA thread; teardown
  ordered and idempotent).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused tests | `uv run pytest tests/unit/test_session_registry.py tests/unit/test_dispatcher.py tests/unit/test_mcp_core.py tests/unit/test_server_build.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Budget | `uv run python scripts/tool_budget.py` | exit 0, core count unchanged (21) |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/mcp_tools/_session_registry.py`
- `src/ms_access_mcp/mcp_tools/core.py`
- `src/ms_access_mcp/server.py` (only where it calls the registry)
- `src/ms_access_mcp/backends/dao.py` (disconnect/shutdown only)
- `src/ms_access_mcp/backends/dispatcher.py` (restart-safety of `start()` only)
- `tests/unit/test_session_registry.py`, `tests/unit/test_dispatcher.py`,
  `tests/unit/test_mcp_core.py`, `tests/unit/test_server_build.py`

**Out of scope**:
- Any other `mcp_tools` module (plan 082 wires them; it reuses this plan's
  helper but must not be edited here).
- The COM adapter (`backends/access/**`) — its teardown already calls
  `shutdown()`.
- Async redesign of the dispatcher (rejected 2026-09-14; STA serialization is
  a hard constraint).

## Git workflow

- Branch: `plan/079-session-ownership` from `main`.
- Conventional commits per step: `test: ...` → `fix(mcp): ...`.

## Steps

### Step 1: RED — ownership and close semantics

In `tests/unit/test_session_registry.py`, add tests capturing the contract:
1. `track(obj)` followed by `del obj; gc.collect()` then
   `close_all_sessions()` must still close any session that was open at
   track time — i.e. the registry must hold a strong reference while a
   session is open (design: `track` returns a handle/context; entering a
   call keeps it strongly referenced until the call exits).
2. A `close_all_sessions()` run must be idempotent (second call is a no-op).
3. Factory wiring (audit TEST-09): invoking `_new_db()` in
   `mcp_tools/core.py` results in a tracked session while in flight (fake
   adapter injection, no real COM).

Expected: all RED (today the WeakSet drops the entry and nothing closes).

### Step 2: GREEN — explicit lifecycle helper

Implement in `_session_registry.py` an explicit open-session helper, e.g.
`open_session(path, **uc_kwargs)` used by `mcp_tools/core.py`:

- constructs the use case, opens the adapter (reusing `_open_or_err`
  semantics), registers it in a STRONG set for the duration of the call,
  and guarantees `use_case.close()` (adapter `disconnect()`; for DAO also
  dispatcher `shutdown()`) on exit — context-manager shape preferred;
- `close_all_sessions()` iterates a snapshot of the strong set plus any
  leaked entries, closing idempotently;
- keep `track()` available for genuinely long-lived sessions
  (`connect_access`'s persistent session) but make it hold a strong
  reference until `close_session(...)` is called; `disconnect_access`
  closes by path.

Migrate `mcp_tools/core.py` tool handlers from `_new_db()` + `_open_or_err`
to the helper, EXCEPT `connect_access`/`disconnect_access`, which keep the
persistent-session semantics (strongly tracked until disconnect).

**Verify**: `uv run pytest tests/unit/test_session_registry.py tests/unit/test_mcp_core.py -q` → pass.

### Step 3: DAO dispatcher shutdown on disconnect + restart-safe start

- `backends/dao.py` `disconnect()`: after `close_dao_database()`, call
  `self._dispatcher.shutdown()` when this adapter owns the dispatcher
  (mirror `AccessAdapter.disconnect`). Confirm `connect()` after
  `disconnect()` re-`start()`s cleanly (AdapterLifecycle reconnect path,
  `application/_adapter_lifecycle.py:35-40`).
- `backends/dispatcher.py` `start()`: guard against double-thread spawn —
  if `self._thread is not None and self._thread.is_alive()`, return instead
  of spawning a second thread (audit: after a `join(15)` timeout,
  `_started=False` currently allows a second STA thread over one queue).
- `tests/unit/test_dispatcher.py`: add the audit-TEST-10 pins —
  (a) timeout on `future.result` leaves the queue consistent and a later
  `shutdown()` still terminates; (b) `start(); shutdown-with-timeout;
  start()` never produces two live worker threads (patch the timeout small
  with a slow fake callable).

**Verify**: `uv run pytest tests/unit/test_dispatcher.py tests/unit/test_backend_dao.py -q` → pass.

### Step 4: Server teardown wiring

`server.py` lifespan and `disconnect_access` continue to call
`close_all_sessions()`; with Step 2 this now actually closes in-flight and
persistent sessions. Extend `tests/unit/test_server_build.py` so the
teardown test drops local strong refs before asserting closure (removing the
mask the audit found).

**Verify**: `uv run pytest tests/unit/test_server_build.py -q` → pass.

### Step 5: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → exit 0; `uv run ruff check .` → exit 0; `uv run python scripts/tool_budget.py` → exit 0, core tool count unchanged.

## Test plan

- `test_session_registry.py`: strong-ref-while-open, idempotent close,
  `_new_db`-factory wiring, GC-after-close.
- `test_dispatcher.py`: timeout-consistency, no double STA thread on restart.
- `test_mcp_core.py` / `test_server_build.py`: refreshed teardown pins that
  no longer retain strong refs (document the refresh in the commit message —
  plan-035 precedent).
- Model new tests on existing fakes: `tests/unit/test_dispatcher.py` fake
  callables; `tests/unit/test_backend_dao.py` fake dispatcher/engine.

## Done criteria

- [ ] `track(); del; gc.collect(); close_all_sessions()` closes an open session (test passes)
- [ ] `grep -n "WeakSet" src/ms_access_mcp/mcp_tools/_session_registry.py` shows the weak path only for already-closed sessions (or is gone)
- [ ] `DaoAdapter.disconnect()` calls `dispatcher.shutdown()` (test asserts via fake dispatcher)
- [ ] dispatcher restart test proves ≤1 live worker thread
- [ ] `uv run pytest tests/unit/ -q` all pass; qualify exit 0; core budget 21
- [ ] `plans/README.md` row updated

## STOP conditions

- If closing the per-call DAO adapter visibly slows tools in the smoke
  scripts (`scripts/stdio_smoke.py`) beyond their existing budgets, stop and
  report measurements — the open/close policy may need a session cache, which
  is a product decision.
- If `shutdown()` during disconnect breaks the AdapterLifecycle reconnect
  contract (`connected` flips unexpectedly in existing tests), stop and
  report; do not weaken plan-065's one-open invariant gates.
- If live COM behavior is needed to judge (unit fakes insufficient), report
  the exact gate to run — do not fake evidence.

## Maintenance notes

- Plan 082 reuses `open_session` for the non-core profiles; its helper
  signature here is load-bearing.
- If a session cache is ever added for performance, the strong-registry
  invariants in Step 1 are the contract it must preserve.
- Reviewer focus: teardown ordering (DB close before dispatcher shutdown) and
  the one-open invariant gates from plan 065 staying green.
