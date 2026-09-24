# Plan 065: Make the adapter `connected` contract real (stop reconnecting every call; quit orphaned Access on failed connect)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/application/_adapter_lifecycle.py src/ms_access_mcp/application/protocols.py src/ms_access_mcp/backends/dao.py src/ms_access_mcp/backends/odbc.py src/ms_access_mcp/backends/ado.py src/ms_access_mcp/backends/access/__init__.py`
> On any change, compare "Current state" excerpts against live code.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED (changes connect frequency for all COM/ODBC paths)
- **Depends on**: none (but land BEFORE plans 066–068 — they build on the contract)
- **Category**: bug | architecture
- **Planned at**: commit `a7f2edb`, 2026-09-24

## Why this matters

`application/protocols.py:16` DECLARES `connected: bool` on
`AdapterLifecycleProtocol`, and `_adapter_lifecycle.py:42` probes
`getattr(self._adapter, "connected", False)` — but NO adapter defines a
`connected` attribute (verified across dao.py:121/185, access/__init__.py:77/112,
odbc.py:37/66, ado.py:33/62: all expose only private `_connected` +
`is_connected()`). The getattr default `False` therefore fires on EVERY call:
every maintenance/automation/versioning tool call re-invokes `connect()`.
For `DaoAdapter` that closes and reopens the database each call (lock churn);
for `AccessAdapter` (forced for maintenance caps) `connect()` re-`Dispatch`es
`Access.Application` and overwrites `_dispatcher._access_app` WITHOUT quitting
the previous instance — likely one new hidden MSACCESS.EXE per tool call, the
old one leaked holding the DB. Separately, when `OpenCurrentDatabase` raises
(locked file, bad password — exactly the crash-recovery scenarios), the
freshly Dispatched `app` was never stored nor Quit, stranding an orphan
MSACCESS.EXE that then CAUSES the next lock failure.

## Current state

- `src/ms_access_mcp/application/_adapter_lifecycle.py:36-44` — the probe:
  ```python
  def _ensure_adapter(self) -> Err | None:
      if self._adapter is not None:
          if not getattr(self._adapter, "connected", False):
              self._adapter.connect(self._db_path)
          return None
  ```
- `src/ms_access_mcp/application/protocols.py:14-22` — the protocol already
  declares the member:
  ```python
  class AdapterLifecycleProtocol(Protocol):
      """Lifecycle members every injected backend adapter must expose."""

      connected: bool

      def connect(self, db_path: str | None = None, /) -> Any:
          ...
  ```
- `src/ms_access_mcp/backends/dao.py:185-187`:
  ```python
  def is_connected(self) -> bool:
      """Return True if this adapter holds an open DAO handle."""
      return self._connected
  ```
  (identical shape in `access/__init__.py:112-114`, `odbc.py`, `ado.py` —
  each guards with a private `_connected` bool; `access` sets it True only
  after `dispatcher.call(_do)` succeeds at :103.)
- `src/ms_access_mcp/backends/access/__init__.py:89-102` — the leak on
  failure:
  ```python
  def _do() -> None:
      import win32com.client  # type: ignore[import-not-found]

      app = win32com.client.Dispatch("Access.Application")
      app.Visible = False
      app.OpenCurrentDatabase(target, False, password)
      self._dispatcher._access_app = app
      self._dispatcher.capture_access_pid()  # plan 030: record PID for post-shutdown kill
      ...
  ```
  If `OpenCurrentDatabase` raises, `app` was never stored (`_access_app` set
  only at :95, after the open succeeds), so `_release_com_safe`
  (dispatcher.py:396-408) never quits it and `capture_access_pid` (:96) never
  ran, so the plan-030 PID kill net cannot see it.
- Correct finally-Quit exemplar: `dispatcher.create_dao_database` fallback
  (dispatcher.py:227-245) quits the app in `finally`.
- Test exemplar: `tests/unit/test_backend_access.py:11` —
  `class FakeDispatcher` ("Fake dispatcher that invokes _do functions directly
  for unit testing."). win32com is imported INSIDE `_do`, so unit tests
  exercise it by injecting a fake `win32com.client` module into
  `sys.modules` (check how existing tests in that file handle it and match;
  if none do, the fake-module injection pattern below is the way).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | ≤ baseline (this plan should REDUCE ignores/risks, not add) |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS |
| Arch guard | `uv run pytest tests/unit/test_architecture_boundaries.py -q` | pass |
| COM hygiene | `taskkill /F /IM MSACCESS.EXE` before/after any live-COM check (ignore "not found") | — |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/dao.py`, `odbc.py`, `ado.py` (add `connected` property)
- `src/ms_access_mcp/backends/access/__init__.py` (add `connected` property; fix connect-failure leak)
- `src/ms_access_mcp/application/_adapter_lifecycle.py` (probe via the protocol member)
- `tests/unit/test_adapter_lifecycle.py` (create or extend — check existence first)
- `tests/unit/test_backend_access.py` (extend for the connect-failure Quit)

**Out of scope**:
- `server.py` lifespan wiring (plan 066).
- ODBC/ADO result-envelope changes (plan 071).
- Any change to `is_connected()` signatures (public API preserved).

## Git workflow

- Branch: `plan/065-adapter-connected-contract`
- RED commit(s) `test: ...` then GREEN `fix: ...` per step; conventional
  commits, no AI attribution. Do NOT push.

## Steps

### Step 1: RED — reuse must not reconnect (dao-shaped fake)

Create/extend `tests/unit/test_adapter_lifecycle.py` with a fake that mirrors
the REAL adapters' shape (private `_connected` + `is_connected()`, NO
`connected` attribute yet) and a consumer exercising `AdapterLifecycle`:

```python
class DaoLikeFake:  # mirrors DaoAdapter's current public shape
    def __init__(self) -> None:
        self._connected = False
        self.connect_calls = 0

    def connect(self, db_path: str | None = None, /) -> None:
        self.connect_calls += 1
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected
```

Test: build a minimal `AdapterLifecycle` consumer (or use
`MaintenanceUseCases` with the fake injected the way existing tests inject
`FakeAdapter` — see `tests/unit/test_maintenance_recovery.py:31`), call the
guarded operation twice, assert `connect_calls == 1`.

**Verify**: `uv run pytest tests/unit/test_adapter_lifecycle.py -q` → FAILS
(`connect_calls == 2` — the getattr default reconnects every call).

### Step 2: GREEN — add `connected` to all four adapters

Add to `DaoAdapter`, `AccessAdapter`, `OdbcAdapter`, `AdoAdapter`:

```python
@property
def connected(self) -> bool:
    """Protocol member (AdapterLifecycleProtocol.connected)."""
    return self._connected
```

and reduce `is_connected()` to `return self.connected` (single source).

**Verify**: Step-1 test passes (probe now sees `connected`); full unit suite
passes; `uv run pyright` no new errors.

### Step 3: RED — AccessAdapter.connect must Quit the app when the open fails

Extend `tests/unit/test_backend_access.py`. Inject a fake `win32com.client`
into `sys.modules` (via `monkeypatch.setitem`) whose `Dispatch` returns a
recording fake app:

```python
class FakeAccessApp:
    def __init__(self) -> None:
        self.visible_set = False
        self.quit_calls = 0

    def __setattr__...  # or plain attribute: Visible = False
```

Test: `OpenCurrentDatabase` raises `RuntimeError("file already in use")` →
`AccessAdapter.connect(...)` must re-raise AND `app.quit_calls == 1`.

**Verify**: new test FAILS (`quit_calls == 0` today — the orphan leak).

### Step 4: GREEN — Quit-and-reraise in the `_do` closure

In `access/__init__.py` `connect`:

```python
def _do() -> None:
    import win32com.client  # type: ignore[import-not-found]

    app = win32com.client.Dispatch("Access.Application")
    app.Visible = False
    try:
        app.OpenCurrentDatabase(target, False, password)
    except Exception:
        try:
            app.Quit()
        except Exception:
            pass
        raise
    self._dispatcher._access_app = app
    ...
```

(Mirrors the `create_dao_database` finally-Quit discipline at
dispatcher.py:227-245. `self._connected` must stay False on the failure path.)

**Verify**: Step-3 test passes; existing `test_backend_access.py` suite
passes (FakeDispatcher contract unchanged).

### Step 5: REFACTOR + full gates

- Confirm no other `getattr(..., "connected", ...)` probes exist:
  `rg -n 'getattr\(.*"connected"' src/` → 0 matches.
- Confirm `connected:` remains declared on `AdapterLifecycleProtocol` and now
  honestly implemented — `uv run pyright` clean.

**Verify**: all gates green (ruff, pyright, unit, tool_budget, arch guard).

### Step 6 (operator/local-only, com_integration): one-open invariant

Add to `tests/integration/test_access_adapter.py` (marker-gated, CI-skipped
by design): connect twice consecutively via a use case; assert only one
MSACCESS.EXE process exists afterwards (`tasklist` count). Document in the
test docstring that this pins the plan-065 invariant. If no Access runtime is
available locally, mark the plan DONE with this step explicitly deferred in
the README row (it is an operator gate, not a CI gate).

## Test plan

- New unit tests: reconnect-count (1), connect-failure-Quit (1); plus
  regression coverage from the existing suite.
- Optional integration: single-MSACCESS.EXE invariant (local-only).
- Verification: `uv run pytest tests/unit/ -q` all pass.

## Done criteria

- [ ] All four adapters expose `connected` property backed by `_connected`
- [ ] `is_connected()` delegates to `connected` (or is unchanged and equivalent)
- [ ] `_adapter_lifecycle.py` probe resolves via the real attribute
- [ ] `rg -n 'getattr\(.*"connected"' src/` → 0 matches
- [ ] AccessAdapter.connect failure path Quits the dispatched app and re-raises
- [ ] All gates green; no new pyright errors
- [ ] `plans/README.md` status row updated

## STOP conditions

- Drift at any cited location.
- Making `connected` real surfaces behavior that DEPENDS on the old
  reconnect-every-call semantics (e.g. tests that only pass because state is
  rebuilt per call) — report which ones before touching them.
- Step 3's fake-win32com injection conflicts with how
  `test_backend_access.py` currently stubs COM — match the file's existing
  pattern; if the file has no such pattern and injection is unreliable,
  report instead of improvising a new harness.
- A verification fails twice after a reasonable fix attempt.

## Maintenance notes

- This plan un-breaks the protocol seam plans 013/016 built; plan 066
  (lifespan teardown) and plan 070 (migration protocol) build on it.
- The single-open invariant (Step 6) should be re-run after plans 066–068
  land — they all touch adapter ownership.
- If a future adapter is added, `AdapterLifecycleProtocol.connected` is now a
  hard contract — pyright will catch a missing property.
