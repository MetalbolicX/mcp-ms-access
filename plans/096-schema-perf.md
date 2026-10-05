# Plan 096: Schema inspection performance — counts out, batched analysis, direct TableDefs lookup, salvage single-read

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 094–097").
>
> **Drift check (run first)**:
> `git diff --stat 3058688..HEAD -- src/ms_access_mcp/backends/schema_inspector.py src/ms_access_mcp/backends/dao.py src/ms_access_mcp/application/analysis.py src/ms_access_mcp/application/database.py`
> Plans 094/095 change `application/migration.py` (out of scope here);
> the four listed files should be otherwise unchanged — if they drifted,
> STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none (parallel to 094/095; sequence after 095 for a
  clean same-branch chain)
- **Category**: perf / contract change
- **Planned at**: commit `3058688`, 2026-10-04

## Why this matters

Re-verified cost profile at HEAD (scout evidence):
1. `schema_inspector.get_tables` issues **one `SELECT COUNT(*)` per user
   table** (`:72-96`, the COUNT at `:93`) — an N-table `get_tables` call
   opens every table. `get_object_metadata` repeats a COUNT (`:237`).
   **User decision (recorded in the ledger, this cycle): DROP
   `record_count` from these outputs** — Jet/DAO has no cheap row count;
   row counts remain reachable via an explicit `query_data("SELECT
   COUNT(*) ...")`.
2. `analyze_query` calls `get_table_schema` once per involved table
   (`application/analysis.py:299-301` → facade `database.py:305-311`) —
   K full `TableDefs` passes (each with COUNTs today).
3. Single-table sites linearly SCAN `TableDefs` to find one name
   (`schema_inspector.py:172` get_indexes, `:311` plan, others) —
   Jet's collection supports direct `db.TableDefs(name)` lookup.
4. `salvage_tables` opens the source table **twice** with `SELECT *`
   (`backends/dao.py:933` and `:953`) — double I/O per table.

## Current state (HEAD `3058688`)

- `backends/schema_inspector.py`: `get_tables` loop `:72-96`
  (COUNT at `:93`); system tables `:114`; `get_indexes` linear scan
  `:172`; `get_object_metadata` COUNT `:237`; plan scan `:311`;
  `generate_sql` scan `:395`; `get_database_statistics` `:484` uses
  `db.TableDefs.Count` (TABLE count — NOT row counts; unaffected).
- `application/analysis.py:299-301` per-table `get_table_schema` loop;
  facade `database.py:305-311` (one pass per call), ER path
  `:1280-1283` (verify whether it loops the same way).
- `backends/dao.py` salvage double-read `:933`/`:953`; linked-table
  scan `:761`.
- Tests pinning `record_count`: `tests/unit/test_schema_inspector.py:305-321`
  (`record_count == 5` from FakeRecordset) and
  `tests/integration/test_schema_extraction.py:91-92`
  (`record_count >= 0`). Consumers: MCP `get_tables` docstring
  (`mcp_tools/core.py` ~`:132`) and any CLI display — GREP
  `record_count` repo-wide first and list every consumer before
  dropping.
- No test pins scan COUNTS or the salvage double-read (scout-verified).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused unit | `PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest tests/unit/test_schema_inspector.py tests/unit/test_backend_dao.py tests/unit/test_analysis_envelope.py tests/unit/test_mcp_analysis.py tests/unit/test_database.py -q -p no:cacheprovider` | all pass |
| Integration | `... -B -m pytest tests/integration/test_schema_extraction.py -q -p no:cacheprovider` | pass or documented skip |
| Unit suite / lint / types / budget | house commands | green |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/schema_inspector.py`
- `src/ms_access_mcp/backends/dao.py` (salvage single-read + shared
  scan helpers)
- `src/ms_access_mcp/application/analysis.py` (batch schema fetch)
- `src/ms_access_mcp/application/database.py` (facade: add batch
  schema method; drop count plumbing)
- `src/ms_access_mcp/mcp_tools/core.py` (docstrings re counts only)
- `tests/unit/test_schema_inspector.py`, `test_backend_dao.py`,
  `test_analysis_envelope.py`, `test_mcp_analysis.py`,
  `test_database.py`, `tests/integration/test_schema_extraction.py`

**Out of scope**:
- `get_database_statistics` table-count semantics (different metric).
- Migration paths (094/095 own them).
- Adding row counts anywhere new.

## Git workflow

- Branch: `plan/096-schema-perf` from `plan/095-transfer-streaming`.
- Conventional commits per unit (counts-out first — it is the contract
  change; then batch; then lookups; then salvage).

## Steps

### Step 1: RED→GREEN — drop `record_count` (contract change)

- RED: refresh `test_schema_inspector.py:305-321` and the integration
  pin to the no-count contract (plan-035 rationale: user-approved
  contract change, ledger record).
- GREEN: remove the per-table COUNT from `get_tables` and the COUNT in
  `get_object_metadata`; remove `record_count` from the returned table
  schemas everywhere it was produced; delete dead count plumbing;
  update the `get_tables` MCP docstring; grep `record_count` repo-wide
  and clear every consumer found (report the list).

**Verify**: focused + integration green; `grep -rn "COUNT(\*)" src/ms_access_mcp/backends/schema_inspector.py` → no hits.

### Step 2: RED→GREEN — batch analysis schema (K passes → 1)

- RED: with a fake inspector counting `TableDefs` iterations,
  `analyze_query` over a K-table statement performs ONE inspector pass
  (today: K).
- GREEN: facade `get_schemas_for(table_names)` single-pass batch method;
  `application/analysis.py` uses it (replace the `:299-301` loop);
  check the ER path `database.py:1280-1283` and use it there too if it
  loops identically.

**Verify**: RED→GREEN; existing analysis tests green.

### Step 3: RED→GREEN — direct TableDefs lookup for single-name sites

- RED: fake `TableDefs` supporting `__getitem__(name)` — assert
  `get_indexes` (and the other single-name sites you convert) use
  direct lookup, not a full scan (count `__iter__` calls).
- GREEN: convert `schema_inspector.py:172` and the other single-name
  scan sites (verify each site's semantics first; listing functions
  keep iterating). Update fakes as needed.

**Verify**: focused green; behavior pins unchanged.

### Step 4: RED→GREEN — salvage single-read

- RED: fake recordset — `salvage_tables` opens each source table ONCE
  (today: twice at `:933`/`:953`).
- GREEN: read the two purposes in code first; if both consume row data
  (validation + copy), one read feeds both. If the second read is
  cursor-position-dependent (write-side re-read), STOP and report
  rather than change semantics.

**Verify**: focused green; existing salvage tests green.

### Step 5: Full gates

Unit suite + ruff + pyright + budget; integration re-run.

## Test plan

- Count-removal refreshes (list every pin in the commit message);
  batch-pass counting test; direct-lookup counting test; salvage
  single-open test; regression: all pre-existing schema/analysis pins.

## Done criteria

- [ ] `schema_inspector` issues zero `COUNT(*)` (grep + tests)
- [ ] `analyze_query` over K tables = 1 inspector pass (test)
- [ ] Single-name sites use direct lookup (test counts scans)
- [ ] Salvage reads each table once (test) — or STOP reported
- [ ] Full gates green; `plans/README.md` row updated

## STOP conditions

- If `record_count` has a consumer outside the listed surfaces (CLI
  output, docs promising it), report the list before dropping (the
  user approved the drop — but hidden consumers change the work list).
- If the dispatcher/STA shape blocks the salvage single-read
  (Step 4), report rather than restructure.
- If direct `TableDefs(name)` lookup cannot be faked by the existing
  test doubles, adapt the fakes (allowed) — but if a real-behavior
  difference appears (Jet lookup semantics vs scan), STOP.

## Maintenance notes

- Row counts on demand: `query_data("SELECT COUNT(*) FROM [t]")` now
  that plan 088's limit path and 090's identifier escaping make it
  correct; document that in the `get_tables` docstring.
