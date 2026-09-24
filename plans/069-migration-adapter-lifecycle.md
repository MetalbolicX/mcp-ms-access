# Plan 069: Migration source-adapter lifecycle — connect once per transfer, disconnect in finally

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/application/migration.py`
> On any change, compare "Current state" excerpts against live code.

## Status

- **Priority**: P1
- **Effort**: M (three small strict-TDD steps)
- **Risk**: LOW (happy path preserved; hoist + finally)
- **Depends on**: none (land BEFORE plan 070 — same file)
- **Category**: bug | perf
- **Planned at**: commit `a7f2edb`, 2026-09-24

## Why this matters

`_transfer_table` creates a FRESH source adapter + connection PER TABLE
(inside the per-table loop), and disconnects only on the success path — so an
N-table migration creates N connections (ODBC/COM engine churn), and any
per-table failure (bad column, query error) leaks a DAO Database handle that
pins the `.ldb/.laccdb` lock on the source DB — the exact "file in use"
condition the recovery ladder exists to fix. The same leak shape exists in
`_extract_dao`/`_extract_odbc`: any exception returns `None, None` without
`adapter.disconnect()`.

## Current state

- `src/ms_access_mcp/application/migration.py:544-609` — `_transfer_table`
  (def at :544; called per table from the loop around :512):
  ```python
  try:
      # Get source data via backend adapter
      adapter = BackendSelector.get_adapter(database_path)  # type: ignore[assignment]
      adapter.connect(database_path)
      ...
      result.success = True
      adapter.disconnect()          # ← line ~601, success path ONLY
  except Exception as e:
      result.success = False
      result.error = _sanitize_error(str(e), f"transfer_table({table.name})")
  ```
- `src/ms_access_mcp/application/migration.py:530-542` — the transfer loop's
  outer `finally: connector.disconnect()` (target side is handled; source is
  not).
- `src/ms_access_mcp/application/migration.py:230-270` — `_extract_dao` /
  `_extract_odbc`:
  ```python
  try:
      adapter = BackendSelector.get_adapter(database_path, backend="dao")  # type: ignore[assignment]
      adapter.connect(database_path)
      tables, unknown_flags = adapter.get_table_schema_plan()  # type: ignore[attr-defined]
      adapter.disconnect()
      return tables, unknown_flags
  except Exception:
      return None, None        # ← leaked adapter on any failure
  ```
- Test exemplar: `tests/unit/test_application_migration.py` — module-level
  `FakeConnector(BaseConnector)` at :29; test classes at :78+; use these for
  connector fakes. For adapter fakes, check how existing tests stub
  `BackendSelector.get_adapter` (`rg -n "get_adapter" tests/unit/test_application_migration.py`).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | ≤ baseline (this plan does NOT remove the type:ignores — 070 does) |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS |

## Scope

**In scope**:
- `src/ms_access_mcp/application/migration.py` (transfer_data/_transfer_table + _extract_dao/_extract_odbc)
- `tests/unit/test_application_migration.py` (extend)

**Out of scope**:
- The `Any`/`type: ignore` cluster and `.get("rows")` seam (plan 070).
- Vacuous-success-on-zero-tables + module-level `_JOBS` (deferred findings —
  see plans/README).
- Connector-side cursor leaks (plan 070 via `count_rows`).

## Git workflow

- Branch: `plan/069-migration-adapter-lifecycle`
- RED → GREEN per step. Do NOT push.

## Steps

### Step 1: RED — one source adapter per transfer, not per table

Extend `tests/unit/test_application_migration.py`: patch/stub
`BackendSelector.get_adapter` (match the existing test style) with a fake
adapter factory counting `get_adapter`/`connect`/`disconnect` calls; drive a
2-table `transfer_data` against a `FakeConnector`; assert `get_adapter`
called exactly ONCE and `connect` exactly ONCE.

**Verify**: new test FAILS (called twice — per table).

### Step 2: GREEN — hoist adapter into transfer_data

In `transfer_data`: before the table loop, create + connect the source
adapter once; pass it into `_transfer_table(..., adapter)` (signature change,
private method — safe); in the SAME `finally` that disconnects the connector,
also `adapter.disconnect()` (guarded: adapter may be None if connect itself
failed). Remove the per-table `get_adapter`/`connect`/`disconnect` from
`_transfer_table`.

**Verify**: Step-1 test passes; existing transfer tests green (fakes that
don't expect the adapter kwarg need updating — that's legitimate RED-refresh
only where they pinned per-table construction; note it in the commit).

### Step 3: RED — mid-table failure still disconnects the source

Test: fake adapter; second table's `execute_query` raises → transfer
completes with a failed TableResult; assert `adapter.disconnect` was called
exactly once.

**Verify**: FAILS first (leak), passes after confirming Step 2's finally
covers it (if the finally from Step 2 already makes this GREEN, write the
test FIRST as the RED driver before Step 2's implementation — order the
commits so the test exists before the fix; if you implemented Step 2 first,
add this test and verify it passes, marking the RED expectation in the commit
message as covered-by-construction).

### Step 4: RED → GREEN — _extract_dao/_extract_odbc disconnect on failure

RED: test that a fake adapter whose `get_table_schema_plan` (resp. granular
getters) raises still gets `disconnect()` called; `_extract_*` returns
`(None, None)`.
GREEN: wrap in `try/finally` (disconnect best-effort in finally; keep the
`except Exception: return None, None` semantics).

**Verify**: new tests pass; full suite green.

### Step 5: Full gates

**Verify**: ruff / pyright / unit / tool_budget green.

## Test plan

- New: single-adapter-per-transfer (1), disconnect-on-mid-table-failure (1),
  extract-disconnect-dao (1), extract-disconnect-odbc (1).
- Existing migration tests: refresh ONLY those that pinned per-table adapter
  construction (separate commit, per plan-035 lesson).

## Done criteria

- [ ] `transfer_data` connects the source adapter exactly once per job
- [ ] Source adapter disconnected in `finally` (success AND failure)
- [ ] `_extract_dao`/`_extract_odbc` disconnect in `finally`
- [ ] `rg -n "get_adapter" src/ms_access_mcp/application/migration.py` →
      no per-table call inside `_transfer_table`
- [ ] All gates green; `plans/README.md` row updated

## STOP conditions

- Drift in migration.py (plan 065/066 landings shift line numbers — excerpts
  are structural, match by symbol).
- Existing tests fail for reasons OTHER than per-table-construction pins.
- The adapter kwarg change to `_transfer_table` ripples beyond
  migration.py (it must not — private method).

## Maintenance notes

- Plan 070 builds directly on this file next: protocol adoption + the
  `.get("rows")` seam fix — keep this plan's diff minimal and mechanical.
- The full-table materialization (`rows` list per table) is the known
  unbounded-fetch family — deferred with the other fetchall findings.
