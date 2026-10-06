# Plan 088: Honor `limit` end-to-end and bound data movement on hot paths

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/application/database.py src/ms_access_mcp/backends/dao.py src/ms_access_mcp/backends/odbc.py src/ms_access_mcp/application/db_export.py`
> Plans 080/081/084 will have touched `database.py`/`migration.py` —
> expected drift; reconcile excerpts before starting.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/080-*.md (envelope work), plans/084-*.md (migration file sequencing)
- **Category**: perf / bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

`query_data(limit=N)` forwards `limit` only if the adapter's signature
declares it — and no adapter does — so every limit is silently dropped and
DAO/ODBC `fetchall` materializes unbounded rows; exports build the full row
list before writing. On a local Access database this is an OOM risk on
large tables and a lie in the tool contract. This plan makes `limit`
real on the query paths and streams exports; migration's source
materialization is explicitly deferred (it needs paging design, see
Maintenance notes).

## Current state

- `src/ms_access_mcp/application/database.py` — `query_data`
  (~lines 551–556): forwards `limit` conditionally on adapter signature
  introspection; DAO (`backends/dao.py:341`), ADO (`ado.py:104`), ODBC
  (`odbc.py:87`) all declare `execute_query(sql, params=None)` — the
  branch never fires.
- `src/ms_access_mcp/backends/dao.py` — `execute_query`
  (~lines 356–363): `while not rs.EOF` materializes every row into a list
  (also the known-deferred "empty result loses column metadata" site —
  NOT in scope).
- `src/ms_access_mcp/backends/odbc.py` — `execute_query`
  (~line 97 area): `cursor.fetchall()`.
- `src/ms_access_mcp/application/db_export.py` — `export_data`
  (~lines 155–172): fetches all rows, then writes CSV/JSON (the JSON path
  inherently needs the full payload; the CSV path can stream).
- Ledger context (do NOT re-litigate): unbounded `fetchall` family,
  N+1 `COUNT(*)` per table, linear `TableDefs` scans are recorded
  known-deferred; this plan owns the `limit` mechanism + export streaming
  only.
- Jet SQL note (from the deferred ledger): DAO dynaset `TOP n` semantics
  need LIVE verification before trusting injection — this plan treats that
  as a probe-gated decision, not an assumption.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused unit | `uv run pytest tests/unit/test_database.py tests/unit/test_backend_dao.py tests/unit/test_odbc_adapter.py tests/unit/test_db_export.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Integration (if Access present) | `uv run pytest tests/integration/test_access_adapter.py -q` | pass or marker-skip |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/application/database.py` (`query_data` limit plumbing)
- `src/ms_access_mcp/backends/dao.py`, `src/ms_access_mcp/backends/odbc.py`
  (`execute_query` accepting `limit`)
- `src/ms_access_mcp/application/db_export.py` (CSV streaming; bounded
  reads via the same limit plumbing where an export limit is offered)
- `tests/unit/test_database.py`, `tests/unit/test_backend_dao.py`,
  `tests/unit/test_odbc_adapter.py`, `tests/unit/test_db_export.py`
- `tests/integration/test_access_adapter.py` (probe-gated TOP test only if
  Access is available; else documented skip)

**Out of scope**:
- Migration source materialization chunking (`application/migration.py`
  `_transfer_table`) — deferred; needs keyset-paging design.
- Empty-result column metadata (deferred finding).
- N+1 COUNT / TableDefs scan performance family (deferred).
- Any UI/pagination contract change — `limit` keeps its current meaning.

## Git workflow

- Branch: `plan/088-limit-and-bounded-data` from `main` (after 084).
- Conventional commits: `feat(query): honor limit end-to-end via Jet TOP injection (probe-gated)`.

## Steps

### Step 1: RED — limit contract tests (unit)

- `tests/unit/test_database.py`: with a fake adapter RECORDING its call
  args, `query_data(sql, limit=5)` must reach the adapter with
  `limit=5` (today it doesn't — introspection branch never fires).
- `tests/unit/test_backend_dao.py` + `tests/unit/test_odbc_adapter.py`:
  fake engine/cursor — `execute_query(sql, limit=5)` returns at most 5
  rows and, for DAO, the rewritten SQL contains `TOP 5` after `SELECT`
  (inject only for plain `SELECT`/`SELECT DISTINCT` prefixes; anything
  else falls back to post-fetch slicing).

**Verify**: all three RED.

### Step 2: GREEN — adapters accept and apply `limit`

- DAO: add `limit: int | None = None` to `execute_query`; when set and the
  SQL matches `^SELECT (DISTINCT )?` (case-insensitive), rewrite to
  `SELECT TOP {n} ...`; execute on the STA thread as today; ALSO slice
  defensively after fetch (`rows[:limit]`) so non-SELECT shapes cannot
  exceed the limit.
- ODBC: same parameter; Access ODBC speaks Jet SQL, so the same TOP
  rewrite applies; keep `fetchall` but bounded by the rewrite + slice.
- Facade: `query_data` always passes `limit` through (drop the signature
  introspection; adapters now declare it).

**Verify**: Step-1 tests GREEN; `uv run pytest tests/unit/test_database.py -q` → pass.

### Step 3: Probe-gated live verification of TOP semantics

Add an integration test in `tests/integration/test_access_adapter.py`
(marker-gated like its siblings) that, WHEN Access/ACE is present: creates
a temp table with 20 rows, runs `query_data(limit=5)` through the real
DAO adapter, and asserts exactly 5 rows. If the executor environment has
no Access, the test self-skips per the existing conftest gates and the
plan row records "TOP semantics pending operator verification" — the unit
layer alone does not claim live proof.

**Verify**: `uv run pytest tests/integration/test_access_adapter.py -q` → pass or documented skip.

### Step 4: CSV export streaming

In `application/db_export.py`: stream CSV rows to the output file as they
are read (iterate the adapter result; write header then rows; no full-list
intermediate) while keeping the JSON path as-is (document why: single
document). If the export API exposes a row cap, wire it through the same
limit plumbing; if not, do not invent one here.

**Verify**: `uv run pytest tests/unit/test_db_export.py -q` → pass (byte-identical outputs; add a large-fake-rowset test asserting constant-ish memory is NOT required — correctness only).

### Step 5: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0; `uv run ruff check .` → 0.

## Test plan

- Limit plumbing (facade→adapter), TOP rewrite (DAO/ODBC unit), defensive
  slice on non-SELECT, CSV streaming byte-parity, probe-gated live TOP.
- Existing `test_odbc_adapter.py`/`test_backend_dao.py` fakes updated to
  the new signature (they are kwargs-forwarding after plan 080's shared
  fake — no compat shim needed).

## Done criteria

- [x] `query_data(limit=N)` reaches adapters (test) and DAO/ODBC honor it (tests)
- [x] Non-SELECT queries never exceed the limit (slice test)
- [x] CSV export streams (no full row-list intermediate; byte-parity test)
- [x] Probe-gated live TOP test present (pass or documented operator-pending skip)
- [x] Unit suite + qualify green; `plans/README.md` row updated

## STOP conditions

- If live TOP verification (operator run) contradicts the rewrite (e.g.
  `TOP` with `ORDER BY` ties returns more rows), keep the defensive slice
  as the contract, document the live finding, and stop for a product
  decision on exact-limit semantics.
- If `execute_query` signature changes ripple past the three call sites
  this plan covers (grep first), stop and report the caller list.
- If streaming CSV breaks the xlsx export path (shared code), stop — xlsx
  keeps its current buffering; only the CSV writer changes.

## Maintenance notes

- Migration chunking (deferred): when designed, keyset paging over ordered
  PKs composes with this plan's limit plumbing; do not bolt `TOP` onto
  `_transfer_table` blindly (ties + pagination hazards).
- The signature-introspection branch removed from `query_data` was the
  mechanism that hid this defect — any future "optional capability" check
  belongs on the protocol, discovered by tests.
