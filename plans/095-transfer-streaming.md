# Plan 095: Stream migration transfers (bounded per-batch memory)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 094–097").
>
> **Drift check (run first)**:
> `git diff --stat 3058688..HEAD -- src/ms_access_mcp/application/migration.py src/ms_access_mcp/backends/dao.py`
> Plan 094 will already have changed `application/migration.py` (entry
> guard + tables split) — expected drift; build on it.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/094 (same file sequencing)
- **Category**: perf / migration
- **Planned at**: commit `3058688`, 2026-10-04

## Why this matters

The audit deferred this as "needs a keyset-paging design". Re-analysis at
HEAD changed the design: DAO already **streams** row-by-row from the open
recordset — the memory cost is purely the consumer-side list. Exposing an
iterator and consuming it incrementally bounds transfer memory to the
batch size with no SQL-paging complexity (Jet has no OFFSET; DAO has no
parameterized `GetRows`; multi-column keyset SQL would be fragile). The
adapter without an iterator keeps the current full-fetch path as a
documented fallback.

## Current state (HEAD `3058688`)

- `src/ms_access_mcp/application/migration.py` — `_transfer_table`
  (~`:626-650`): source query built (plus `config.order_by` at
  `:626,:632-633`), `adapter.execute_query(query)` → `rows =
  query_result.rows` (complete list), then batch inserts
  (`batch_size = 1000`, `:645-650`). `result.source_rows` is set from
  the fetched rows — LOCATE THE EXACT ASSIGNMENT FIRST and preserve its
  semantics exactly (plan 084's verification gates on it).
- `src/ms_access_mcp/backends/dao.py` — `execute_query` (~`:381-393`):
  `while not rs.EOF: results.append(row)` — row-by-row reads already;
  only the accumulation is unbounded. `params` is ignored (`_ = params`)
  — unchanged by this plan.
- `models/__init__.py:90-96` — `QueryResult.rows: list[dict]`.
- Connector API: `insert_batch(table, columns, list[tuple])`,
  `count_rows(table)` (`migration.py:678-683`).
- Test pins: `tests/unit/test_application_migration.py:725-765`
  (QueryResult dataclass/dict fakes), `:766-840` (single adapter, job
  failed), `:987-1004` (verification gating/count-only); integration
  `tests/integration/test_migration_sqlite.py:99-119` plus the
  row-loss case (plan 084).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused unit | `PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest tests/unit/test_application_migration.py tests/unit/test_backend_dao.py -q -p no:cacheprovider` | all pass |
| SQLite integration | `... -B -m pytest tests/integration/test_migration_sqlite.py -q -p no:cacheprovider` | all pass |
| Unit suite | `... -B -m pytest tests/unit/ -q -p no:cacheprovider` | all pass |
| Lint / types / budget | ruff / pyright / tool_budget per house commands | green |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/dao.py` (add `iter_query`; keep
  `execute_query` behavior byte-identical)
- `src/ms_access_mcp/application/migration.py` (`_transfer_table` only)
- `tests/unit/test_backend_dao.py`
- `tests/unit/test_application_migration.py`
- `tests/integration/test_migration_sqlite.py`

**Out of scope**:
- ODBC/other adapters gaining iterators (fallback covers them; note it
  as a future extension in the plan row).
- Verification semantics (plan 084) — except preserving
  `source_rows` computation exactly.
- The source-query `params` gap (`_ = params`) — separate finding.

## Git workflow

- Branch: `plan/095-transfer-streaming` from `plan/094-migration-honesty`.
- Conventional commits per unit.

## Steps

### Step 1: RED — adapter iterator + consumer fallback

1. `DaoAdapter.iter_query(sql, chunk=1000)` yields row-batches in order,
   covering all rows (fake recordset producing e.g. 2500 rows → chunks
   of 1000/1000/500); assert it yields INCREMENTALLY — the consumer must
   be able to act on chunk 1 before chunk 2 exists (drive with a
   consumer that records per-yield insert calls).
2. `_transfer_table` with a fake adapter exposing `iter_query`:
   `insert_batch` called per chunk (cross-chunk batch buffer flushes at
   ≥1000), `result.source_rows` equals the total row count (exact
   prior semantics), verification still gates (plan-084 pins green).
3. Fallback: fake adapter WITHOUT `iter_query` → current full-fetch
   behavior still works (compat pin).

**Verify**: all RED.

### Step 2: GREEN — iterator + streaming consumer

- `dao.py`: `iter_query(sql, params=None, chunk: int = 1000) ->
  Iterator[list[dict]]` — same column capture and error shape as
  `execute_query`; extract a shared row-reader helper ONLY if it keeps
  `execute_query` behavior identical (characterization tests must not
  move). No cross-thread changes — the recordset read stays inside the
  existing dispatcher call pattern: decide the threading shape from the
  live code (a generator cannot outlive its STA call naively — if the
  dispatcher's `call()` returns results rather than executing lazily,
  implement chunking INSIDE one `_do` that returns successive batch
  results driven by the caller, or re-dispatch per chunk with the
  recordset kept on the STA thread — STOP and report if apartment
  rules prevent a clean design; do NOT execute COM lazily from a
  generator frame on the caller thread).
- `migration.py`: `_transfer_table` uses
  `getattr(adapter, "iter_query", None)` when available (else current
  path), buffers into the existing 1000-row batches across chunk
  boundaries, accumulates `source_rows` with the SAME semantics as the
  current code, keeps `config.order_by` on the query (unchanged — one
  recordset, no keyset SQL needed).

**Verify**: Step-1 tests GREEN; focused + integration green.

### Step 3: Full gates

Unit suite + ruff + pyright + budget; integration row-loss case green.

## Test plan

- Iterator chunks/incrementality; cross-chunk batch flush; source_rows
  exact; fallback compat; plan-084 verification pins untouched;
  sqlite integration unchanged behavior.

## Done criteria

- [x] `iter_query` yields incremental batches (test drives insert before later chunks exist)
- [x] `_transfer_table` never holds more than chunk+batch rows (test observes per-yield inserts)
- [x] Fallback path pinned; verification/source_rows semantics unchanged
- [x] Full gates green; `plans/README.md` row updated (note ODBC-iterator as future extension)

## STOP conditions

- If the dispatcher/STA design prevents lazy per-chunk COM reads
  without breaking apartment rules (see Step 2), STOP and report —
  memory-bound streaming must not come at the cost of cross-thread COM.
- If `source_rows` semantics cannot be reproduced exactly from the
  streamed path (find a differing consumer), stop and report.
- If any test pins whole-list materialization behavior for a deliberate
  reason, present it.

## Maintenance notes

- If a session cache or reuse lands later, `iter_query` is the seam for
  cursor lifetime; ODBC can adopt the same iterator contract.
