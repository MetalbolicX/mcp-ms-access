# Plan 068: Guard open_database against silently replacing a live connection

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/application/database.py`
> On any change, compare "Current state" excerpts against live code.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `a7f2edb`, 2026-09-24

## Why this matters

A second `open_database` without an intervening `close_database` (an easy MCP
client mistake across a long session) unconditionally overwrites
`self._adapter` and `self._connections[self._active_connection]` — leaking
the previous adapter's COM/pyodbc handle and keeping the OLD database file
locked. Because `self._db_path` then points at the new DB, `close_database`'s
session-backup cleanup also operates on the wrong session's snapshot context.
Auto-disconnecting the previous adapter before replacing is strictly safer
and friendlier than erroring: MCP clients open/close casually.

## Current state

- `src/ms_access_mcp/application/database.py:113-177` — `open_database`
  (excerpt, def at :113):
  ```python
  def open_database(self, db_path: str) -> Result:
      ...
      if not is_path_allowed(db_path, self._allowed_dirs):
          return _err(f"Path not allowed: {db_path}")

      from ..credentials import resolve_password
      password = resolve_password(db_path)

      orphan_data: dict | None = None
      if not self._policy.readonly:
          orphan_data = _session_backup.detect_orphaned_backup(db_path)

      try:
          self._adapter = BackendSelector.get_adapter(...)   # ← overwrites, no disconnect
          ...
          self._db_path = db_path
          self._connections[self._active_connection] = self._adapter   # ← overwrites
  ```
  The ONLY disconnect in the neighborhood is `close_database`
  (database.py:~218). `_active_connection` is always `"default"` (the
  multi-connection dict is currently unreachable generality).
- Test exemplar: `tests/unit/test_database.py` (large; locate the
  open/close tests with `rg -n "open_database" tests/unit/test_database.py`).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | ≤ baseline |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS |

## Scope

**In scope**:
- `src/ms_access_mcp/application/database.py` (`open_database` guard)
- `tests/unit/test_database.py` (extend)

**Out of scope**:
- Multi-connection support (`connection_name` machinery) — the dict stays.
- Session-backup semantics on switch (cleanup still belongs to
  `close_database`; this plan only prevents the silent leak).
- mcp_tools layers.

## Git workflow

- Branch: `plan/068-open-database-replace-guard`
- RED then GREEN commit. Do NOT push.

## Steps

### Step 1: RED — open→open disconnects the first adapter

Extend `tests/unit/test_database.py` with a test using a recording fake
adapter (pattern: existing fake adapters in that file —
`rg -n "class Fake" tests/unit/test_database.py`): open db A (fake adapter
records `disconnect` calls), then open db B; assert adapter-A's
`disconnect()` was called before adapter B is stored.

**Verify**: `uv run pytest tests/unit/test_database.py -q -k open` → new test
FAILS (no disconnect).

### Step 2: GREEN — auto-disconnect before replace

At the top of `open_database` (after the path check, before
`BackendSelector.get_adapter`):

```python
# Plan 068: never silently replace a live connection — the old handle
# would leak and keep the previous file locked.
previous = self._connections.get(self._active_connection)
if previous is not None:
    try:
        previous.disconnect()
    except Exception:
        pass  # best-effort: stale handle, proceed with the new open
```

**Verify**: new test passes; full `tests/unit/` green (some tests open twice
sequentially — they must stay green because the disconnect is best-effort on
fakes).

### Step 3: Full gates

**Verify**: ruff / pyright / unit / tool_budget green.

## Test plan

- New: open→open disconnects first (1); optionally: first adapter's
  disconnect raising does not block the second open (1).
- Existing open/close/retry tests unmodified and green.

## Done criteria

- [ ] `open_database` disconnects any previous live adapter before replacing
- [ ] Best-effort: a raising disconnect never blocks the new open
- [ ] All gates green; `plans/README.md` row updated

## STOP conditions

- Drift in `open_database`'s shape (e.g. plan 066 changed `_connections`
  handling — coordinate ordering if so).
- Evidence that some caller RELIES on open-replacing without disconnect
  (a test that fails for that reason and looks intentional) — report it.

## Maintenance notes

- After plan 066's session registry, this guard is second-order defense;
  keep both.
- If real multi-connection support ever lands, this guard becomes
  per-connection-name — note it there.
