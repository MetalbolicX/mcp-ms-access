# Plan 081: Action-SQL routing and database-property honesty

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/mcp_tools/core.py src/ms_access_mcp/application/database.py src/ms_access_mcp/application/db_admin.py src/ms_access_mcp/backends/db_operations.py`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: MED
- **Depends on**: plans/080-*.md (contract work in `application/database.py`)
- **Category**: bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

Two surfaces report the wrong thing about writes:

1. The MCP tool `execute_raw_sql` — a confirm-gated WRITE tool — routes
   through `query_data` (the SELECT-oriented path) instead of the adapters'
   action-SQL method, so action statements take a read path with
   backend-dependent commit/error behavior.
2. `set_database_property` is backed by a stub that always returns `False`,
   but the application layer ignores the boolean and returns
   `Ok(rowcount=1)` — a property write that did nothing is reported as a
   success. `get_database_properties` similarly returns an empty `Ok`.

## Current state

- `src/ms_access_mcp/mcp_tools/core.py` — `execute_raw_sql` wrapper
  (~line 268) calls `db.query_data(sql)`. There is a redundant re-check of
  the acknowledgment gate (~line 263) that the typed schema already
  enforces (dead code per audit). `insert_data`/`update_data`/`delete_data`
  wrappers nearby show the confirm-gating pattern to match.
- `src/ms_access_mcp/application/database.py` — `query_data` (~lines
  528–556) executes via `execute_query` (recordset semantics).
- `src/ms_access_mcp/backends/dao.py` — `execute_raw_sql`
  (~line 497): the real action-SQL method (used today only by
  `application/db_export.py:147`). DAO executes action SQL immediately
  (no explicit commit needed).
- `src/ms_access_mcp/backends/odbc.py` — `execute_raw_sql`
  (~lines 449–451) raises "not supported".
- `src/ms_access_mcp/backends/db_operations.py` — stub module, verbatim:

```python
    def get_database_properties(self, names: list[str] | None = None) -> dict:
        """Return database properties, or an empty mapping when unavailable."""
        return {}

    def set_database_property(self, name: str, value: str, type: str | None = None) -> bool:
        """Return whether setting a database property succeeded."""
        return False
```

- `src/ms_access_mcp/application/db_admin.py` — (~lines 66–73) calls
  `adapter.set_database_property(name, value)`, discards the `False`, and
  returns `_ok(rowcount=1)` unconditionally. DAO delegates to the stub
  (`backends/dao.py` ~lines 329–335); ODBC raises honestly.
- `src/ms_access_mcp/cli.py` — `set_database_property`/`get_database_properties`
  subcommands (~lines 122–127) expose the false success; MCP `ddl` profile
  exposes the same use case (`mcp_tools/ddl.py` ~233–256) — note the MCP
  tools become reachable after plan 082.
- Conventions: `Ok`/`Err` from `errors.py`; write tools are confirm-gated at
  the MCP boundary (`mcp_tools/core.py` exemplar).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused tests | `uv run pytest tests/unit/test_mcp_core.py tests/unit/test_database.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/application/database.py` (a facade method routing raw SQL to the action contract)
- `src/ms_access_mcp/mcp_tools/core.py` (wrapper re-routing; dead re-check removal)
- `src/ms_access_mcp/application/db_admin.py`
- `src/ms_access_mcp/backends/db_operations.py` (docstring/honesty only — no new COM implementation)
- `tests/unit/test_mcp_core.py`, `tests/unit/test_database.py`,
  `tests/unit/test_cli_maintenance.py` (if property-write pins exist)

**Out of scope**:
- Implementing real DAO database-property reads/writes via COM (a separate,
  live-gated feature; recorded in `plans/README.md` deferred list).
- `backends/odbc.py` gaining action support (its honest "not supported"
  error is correct behavior today).
- Anything in `mcp_tools/ddl.py` (plan 082 wires reachability).

## Git workflow

- Branch: `plan/081-action-sql-honesty` from `main` (after 080).
- Conventional commits: `fix(mcp): route execute_raw_sql through the action-SQL contract`.

## Steps

### Step 1: RED — action routing

Add to `tests/unit/test_mcp_core.py` a test invoking the `execute_raw_sql`
tool with a fake `DatabaseUseCases` (monkeypatch pattern used by the
existing `insert_data` tests) asserting the facade's ACTION method is
called (e.g. `execute_action_sql`), not `query_data`. Add the matching
facade test in `tests/unit/test_database.py`: with a fake adapter exposing
`execute_raw_sql`, the facade returns `Ok` with the adapter's rowcount;
with an adapter lacking the method (or raising "not supported"), the
facade returns `Err("backend does not support raw action SQL")`.

**Verify**: both RED.

### Step 2: GREEN — facade method + wrapper re-route

- In `application/database.py`, add an action façade method (name it
  `execute_action_sql`) that applies the write policy/acknowledgment gate
  the same way other write methods do, dispatches to
  `adapter.execute_raw_sql(sql)` when present, and returns
  `Err("Backend does not support raw action SQL: ...")` when the adapter
  raises `NotImplementedError`/`AttributeError`.
- In `mcp_tools/core.py`, point `execute_raw_sql` at it; remove the dead
  duplicate gate check at ~line 263 (the schema-level gate remains).

**Verify**: Step-1 tests pass; `uv run pytest tests/unit/test_mcp_core.py -q` → pass.

### Step 3: RED→GREEN — property-write honesty

- RED: in `tests/unit/test_database.py`, pin that
  `set_database_property` returns `Err` (not `Ok(rowcount=1)`) when the
  adapter reports `False`, and `Ok` with the property map when the adapter
  succeeds (fake adapter both ways).
- GREEN: in `application/db_admin.py`, branch on the adapter result:
  truthy → `_ok(...)`; falsy → `_err("Database property write is not
  supported by this backend")`. In `backends/db_operations.py`, fix the
  module/method docstrings to state plainly this is an unimplemented stub
  (keep behavior).

**Verify**: focused tests pass; `uv run pytest tests/unit/ -q` → all pass.

### Step 4: Full gates

**Verify**: `uv run pytest tests/unit/ -q` all pass; `uv run pyright` → 0; `uv run ruff check .` → 0; `uv run python scripts/tool_budget.py` → exit 0 (core count unchanged).

## Test plan

- `test_mcp_core.py`: action-routing case (fake use case), plus existing
  confirm-gate tests still green.
- `test_database.py`: facade action method success/unsupported cases;
  property-write `Err` on `False`; property-write `Ok` on success.
- Check `test_cli_maintenance.py`/CLI tests for pinned `Ok` property writes
  and refresh with rationale if any pin the false success.

## Done criteria

- [x] `execute_raw_sql` MCP tool calls the action contract (test asserts)
- [x] Adapters without action support get an explicit `Err` (test asserts)
- [x] `set_database_property` never returns `Ok` when the backend returns falsy (test asserts)
- [x] `grep -n "query_data" src/ms_access_mcp/mcp_tools/core.py` shows no hit inside the `execute_raw_sql` handler
- [x] Unit suite + qualify green; `plans/README.md` row updated

## STOP conditions

- If DAO's `execute_raw_sql` semantics on live Access (commit boundaries,
  rowcount) cannot be established from code/tests alone, implement the
  routing but mark the live behavior gate as an operator follow-up in the
  plan row — do not assert unverified engine behavior in tests.
- If removing the duplicate gate at `core.py:263` breaks the typed-schema
  gate tests, stop and report (the gate must stay exactly once).

## Maintenance notes

- A future plan may implement real DAO database properties
  (`CurrentDb.Properties` via COM); the `Err` contract added here is the
  seam it will replace.
- Plan 082 makes the `ddl`-profile property tools reachable; their honest
  `Err` behavior depends on this plan landing first.
