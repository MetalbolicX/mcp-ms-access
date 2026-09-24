# Plan 066: Honest server session teardown — track live use cases, make lifespan shutdown and disconnect_access real

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/server.py src/ms_access_mcp/mcp_tools/ src/ms_access_mcp/application/database.py`
> On any change, compare "Current state" excerpts against live code.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED (adds shutdown calls to paths that currently "work" by leaking)
- **Depends on**: plan 065 (adapter `connected` contract must be real first)
- **Category**: bug | architecture
- **Planned at**: commit `a7f2edb`, 2026-09-24
- **Owns the deferred 2026-09-16 finding** "per-call connection lifecycle"
  (plans/README.md "Audit findings intentionally NOT planned" — unblocked now
  that plan 045 is DONE).

## Why this matters

The server lifespan (`server.py:52-70`) constructs `state.db =
DatabaseUseCases(...)` and `state.dispatcher = ComDispatcher()`, and on exit
closes `state.db` and shuts down `state.dispatcher` — but NO tool ever uses
`state.db`: every MCP tool call builds its own use case via `_new_db(...)`
(mcp_tools/core.py:65, used at :95, :102, :109, :120-121, :134-135, … :442-443
— one fresh instance per tool call) and every adapter constructs a PRIVATE
`ComDispatcher()` (`backends/__init__.py:172,194`). The lifespan teardown is
theater: it shuts down an inert dispatcher while the real ones are released
only at process death — hidden MSACCESS.EXE instances, DAO handles, and STA
threads persist for the server's lifetime, pinning file locks across
sessions. `disconnect_access` is structurally a no-op for the same reason
(it closes a NEW empty instance, not the ones live calls created).

## Current state

- `src/ms_access_mcp/server.py:47-70`:
  ```python
  @asynccontextmanager
  async def lifespan(server: FastMCP):
      state = _ServerState()
      state.db = DatabaseUseCases(readonly=True, allowed_dirs=allowed_dirs_from_env())
      state.dispatcher = ComDispatcher()

      _log.debug("Server lifespan: starting")
      try:
          yield state
      finally:
          _log.debug("Server lifespan: shutting down")
          # Close active connection
          if state.db is not None:
              try:
                  state.db.close_database()
              except Exception as exc:
                  _log.warning("Error closing database on shutdown: %s", exc)
          # Shutdown dispatcher (STA teardown)
          if state.dispatcher is not None:
              try:
                  state.dispatcher.shutdown()
              except Exception as exc:
                  _log.warning("Error shutting down dispatcher on shutdown: %s", exc)
  ```
- `src/ms_access_mcp/mcp_tools/core.py:65` — `def _new_db(...)` (fresh
  `DatabaseUseCases` per call; never closed). Sibling factories exist per
  profile module (`_new_migration`, `_new_db(readonly=False)` in
  `mcp_tools/ddl.py`/`maintenance.py`, automation/versioning equivalents —
  locate each with `rg -n "def _new_" src/ms_access_mcp/mcp_tools/`).
- `DatabaseUseCases.close_database()` (application/database.py:~210-228) is
  the per-instance teardown (disconnects adapter + session-backup cleanup);
  `disconnect_access` tool wraps a fresh instance's close — no-op for live
  instances.
- Architecture guard lesson: importing an underscore-prefixed module is safe
  via `from ._x import ...` but NOT via `from . import _x` in non-`__init__`
  modules (`mcp_tools/_registry.py` already uses the from-import form —
  match it).
- COM hygiene: `taskkill /F /IM MSACCESS.EXE` before/after live checks.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | ≤ baseline |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS (no tool surface change) |
| Arch guard | `uv run pytest tests/unit/test_architecture_boundaries.py -q` | pass |

## Scope

**In scope**:
- `src/ms_access_mcp/mcp_tools/` — new `session_registry.py` (NO leading
  underscore in the FILE name? see lesson below) + edits to each `_new_*`
  factory
- `src/ms_access_mcp/server.py` (lifespan finally calls registry close)
- `src/ms_access_mcp/mcp_tools/core.py` (`disconnect_access` closes tracked)
- `tests/unit/test_session_registry.py` (create), `tests/unit/test_mcp_core.py`
  (extend — locate the actual core-tool test file first with
  `rg -ln "disconnect_access" tests/unit/`)

**Out of scope**:
- Rewriting tool bodies to context-manager sessions (deferred follow-up —
  see Maintenance notes).
- `cli.py` — CLI commands are short-lived processes; process exit reclaims.
- Adapter internals (plan 065 owns the connect contract).

Naming lesson (plans 033–038): files with a leading underscore trip the
architecture guard when imported as `from . import _x` from a non-`__init__`
module. Use `from ._session_registry import ...` (from-import form) OR name
the file without the underscore. Prefer `mcp_tools/_session_registry.py` with
from-imports only.

## Git workflow

- Branch: `plan/066-session-registry-teardown`
- RED → GREEN commits per step. Do NOT push.

## Steps

### Step 1: RED — registry closes tracked use cases

Create `tests/unit/test_session_registry.py`:

```python
"""SessionRegistry tracks live per-call use cases and closes them all."""

from ms_access_mcp.mcp_tools._session_registry import SessionRegistry


class FakeUseCase:
    def __init__(self) -> None:
        self.closed = False

    def close_database(self) -> None:
        self.closed = True


def test_close_all_closes_tracked() -> None:
    reg = SessionRegistry()
    a, b = FakeUseCase(), FakeUseCase()
    reg.track(a)
    reg.track(b)
    reg.close_all()
    assert a.closed and b.closed


def test_weakref_gone_instance_is_skipped() -> None:
    reg = SessionRegistry()
    reg.track(FakeUseCase())  # immediately garbage
    reg.close_all()  # must not raise


def test_close_all_swallows_individual_failures() -> None:
    # one raising close must not prevent the others
    ...
```

**Verify**: `uv run pytest tests/unit/test_session_registry.py -q` → FAILS
(module doesn't exist).

### Step 2: GREEN — implement `mcp_tools/_session_registry.py`

A module-level registry holding `weakref.WeakSet` of live use cases +
`track()`/`close_all()`/`close_for_path(path)`; `close_all()` iterates a
snapshot, calls `close_database()` best-effort (swallow + DEBUG log each
failure — plan-056 swallow-logging convention), clears the set. Use the
existing `logging.getLogger(__name__)` pattern.

**Verify**: Step-1 tests pass.

### Step 3: Wire every `_new_*` factory to track its instance

In `mcp_tools/core.py` and each profile module factory
(`rg -n "def _new_" src/ms_access_mcp/mcp_tools/`): after constructing the
use case, `registry.track(uc)` before returning. No behavior change beyond
tracking.

**Verify**: `uv run pytest tests/unit/ -q` → all pass (tracking is inert);
`uv run python scripts/tool_budget.py` → PASS.

### Step 4: RED — lifespan shutdown closes tracked instances

Extend the server tests (locate with `rg -ln "lifespan" tests/unit/`; if none
exist, create `tests/unit/test_server_lifespan.py` driving the asynccontextmanager
with `asyncio.run` or pytest-asyncio if already a dev dep — check
`pyproject.toml` first; if no async test support exists, test the extracted
helper synchronously instead): assert that a tracked fake use case gets
`close_database()` called when the lifespan exits.

**Verify**: new test FAILS (lifespan only touches its own inert state).

### Step 5: GREEN — lifespan calls the registry

In `server.py` lifespan `finally`, after the existing state teardown, add:

```python
from .mcp_tools._session_registry import close_all_sessions

close_all_sessions()  # plan 066: real teardown for per-call use cases
```

Keep the existing state.db/state.dispatcher teardown (harmless; state.db may
be removed in a follow-up — do NOT remove in this plan).

**Verify**: Step-4 test passes; full unit suite passes.

### Step 6: RED → GREEN — `disconnect_access` closes tracked sessions

RED: test that `disconnect_access` (the MCP tool function, via its unit test
harness) closes a pre-tracked fake use case. Currently it constructs a fresh
instance and closes nothing tracked → FAILS.
GREEN: tool handler calls `close_for_path(path)` (or `close_all_sessions()`
if per-path filtering isn't derivable — inspect what `close_database` needs;
choose the narrower one that works and document the choice in the docstring).

**Verify**: new test passes; existing disconnect tests pass (refresh pins if
they asserted the no-op behavior — per the plan-035 lesson, tests pinning the
bug are refreshed in a separate commit with rationale).

### Step 7: Full gates + COM hygiene

**Verify**: ruff / pyright / unit / tool_budget / arch guard all green;
`taskkill /F /IM MSACCESS.EXE` (expect "not found") before and after the
full unit run.

## Test plan

- New: registry unit tests (3+), lifespan-close test, disconnect_access
  closes-tracked test.
- Existing suite must stay green unmodified EXCEPT tests that pinned the
  no-op disconnect (refresh in a dedicated commit).
- Local-only sanity (operator, optional): run the server, open a DB via a
  tool, kill the client, confirm no orphan MSACCESS.EXE after lifespan exit.

## Done criteria

- [ ] `mcp_tools/_session_registry.py` exists with track/close_all (+ per-path if chosen)
- [ ] Every `_new_*` factory tracks its use case
- [ ] Lifespan finally closes all tracked sessions
- [ ] `disconnect_access` closes live tracked sessions (no-op fix)
- [ ] All gates green; `plans/README.md` row updated (note the deferred
      2026-09-16 finding as owned+closed by this plan)

## STOP conditions

- Drift at cited locations (especially if plan 045's rewrite changed
  `_new_db`'s shape).
- Any evidence that closing a use case mid-conversation breaks a SUBSEQUENT
  tool call (i.e. some tool keeps using an instance after the call returns) —
  report; that would require the context-manager rewrite, out of scope.
- close_database raising in ways the best-effort wrapper can't contain.
- Step's verification fails twice.

## Maintenance notes

- The fuller design — one context-manager session per tool call
  (`with _using_db(path) as db:`) — remains the follow-up once this
  registry proves the teardown path; do not grow this plan into it.
- Plan 047 recorded that `disconnect_access` being a no-op forced backup
  cleanup onto the lifespan path; after this plan, both paths are real —
  keep both wired (defense in depth).
- If FastMCP ever gains per-call middleware/hooks, the registry can shrink to
  a thin adapter around it.
