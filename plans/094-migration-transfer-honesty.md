# Plan 094: Migration transfer honesty — zero-table fail-fast and tables_failed conflation

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 094–097").
>
> **Drift check (run first)**:
> `git diff --stat 3058688..HEAD -- src/ms_access_mcp/application/migration.py src/ms_access_mcp/mcp_tools/migration.py`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: MED
- **Depends on**: plans 077–093 (landed; migration.py carries plans 083/084)
- **Category**: migration / bug
- **Planned at**: commit `3058688`, 2026-10-04

## Why this matters

Two deferred integrity defects, re-verified at HEAD:
1. `transfer_data` with **zero tables** registers a job, sets it
   `completed`, and returns `success: true` — `all([]) is True`. A
   mistaken/empty schema uploads nothing and reports success. (Plan 084's
   maintenance note prescribed the fix location: fail fast at entry,
   before job registration.)
2. `upload_schema`'s `tables_failed` conflates **"table already exists"**
   (an idempotent skip) with **"creation genuinely failed"**, and the
   wrapper returns `Ok` even when creations failed.

## Current state (HEAD `3058688`)

- `src/ms_access_mcp/application/migration.py`:
  - `:519-529` schema obtained (`ExtractedSchema.model_validate` or
    `extract_schema`) BEFORE `:531-539` `register_job(job)` — the count
    is knowable before registration.
  - `:555` `results = []`; `:560` `for i, table in enumerate(schema.tables)`;
    `:562` `int((i / total_tables) * 100)` (latent ZeroDivision —
    unreachable today because a zero-table run never reaches it, but the
    entry guard makes it structurally impossible); `:587`
    `all_success = all(r.success for r in results)`; `:588` job status;
    `:595` `return _ok(data={"success": all_success, ...})`.
  - `upload_schema` `:447-456`: `if connector.table_exists(table.name):
    tables_failed.append(table.name); continue` vs
    `except Exception: tables_failed.append(table.name)` — same list;
    `:458` `_ok(...)` regardless.
- Consumers of `tables_failed`: `mcp_tools/migration.py:89` (docstring)
  and `:110-116` (`_result_to_dict` passthrough). NO CLI consumer
  (repo-wide grep: zero `cli.py` hits). `models/migration.py`
  serialization — verify it passes data keys through (existing
  `test_models_migration.py` must stay green; an added key is additive).
- Test pins to refresh deliberately (plan-035 precedent):
  `tests/unit/test_application_migration.py:352` (Ok with empty failed),
  `:380` (Ok with a failed table — will become Err), and
  `tests/integration/test_migration_sqlite.py:307-308`.
- NO existing test pins zero-table success (scout-verified) — the fix
  adds tests rather than refreshing any.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused unit | `PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest tests/unit/test_application_migration.py -q -p no:cacheprovider` | all pass |
| SQLite integration | `... -B -m pytest tests/integration/test_migration_sqlite.py -q -p no:cacheprovider` | all pass |
| Unit suite | `... -B -m pytest tests/unit/ -q -p no:cacheprovider` | all pass |
| Lint / types / budget | ruff `--no-cache .`; pyright `--pythonpath .venv/Scripts/python.exe`; `scripts/tool_budget.py` | exit 0 / 0 errors / PASS |

## Scope

**In scope**:
- `src/ms_access_mcp/application/migration.py` (transfer_data entry + upload_schema)
- `src/ms_access_mcp/mcp_tools/migration.py` (docstrings only)
- `tests/unit/test_application_migration.py`
- `tests/unit/test_models_migration.py` (only if the added key needs a pin)
- `tests/integration/test_migration_sqlite.py`

**Out of scope**:
- Verification gating semantics (plan 084 — landed).
- transfer streaming (plan 095 — lands next, same file).
- Any other envelope change.

## Git workflow

- Branch: `plan/094-migration-honesty` from `3058688`.
- Conventional commits per unit.

## Steps

### Step 1: RED — zero-table + conflation tests

1. `transfer_data` with `schema.tables == []` → returns `Err`
   mentioning no tables, and **no job is registered** (assert the job
   registry / `_jobs` dict gained nothing — follow how existing tests
   observe jobs).
2. `upload_schema` where the table already exists → result data has
   `tables_existing: [name]` and `tables_failed: []`, envelope `Ok`.
3. `upload_schema` where creation genuinely raises → envelope `Err`
   naming the failed table(s) (today: `Ok` with the table in
   `tables_failed` — pin first as characterization if that clarifies,
   then flip).

**Verify**: all three RED (1 and 3 failing, 2 failing on the new key).

### Step 2: GREEN — entry guard + split lists + gate

- `transfer_data`: at entry, after schema resolution and BEFORE
  `register_job`: `if not schema.tables: return _err("no tables in
  schema to transfer")` (exact house-style message; plan-084 maintenance
  note). This also removes the `:562` latent ZeroDivision structurally.
- `upload_schema`: split the pre-existing branch into
  `tables_existing`; keep `tables_failed` for genuine failures; after
  the loop, if `tables_failed` is non-empty return
  `_err(f"{n} table(s) failed to create: {', '.join(failed)}")` —
  mirroring plan 084's gating philosophy (partial failure never reports
  `Ok`); `Ok` data gains `tables_existing` (additive key) alongside the
  existing lists.
- Update `mcp_tools/migration.py` docstrings to the new contract.
- Refresh the listed pins with rationale in the commit message
  (plan-035 precedent).

**Verify**: Step-1 tests GREEN; `test_models_migration.py` still green.

### Step 3: Full gates

Focused unit + integration + suite + ruff + pyright + budget (commands
table).

## Test plan

- Zero-table Err + no-registration; existing-table → `tables_existing`;
  genuine failure → Err naming tables; models serialization untouched
  green; sqlite integration's existing `tables_failed` pins refreshed.

## Done criteria

- [ ] Zero tables → `Err`, job never registered (test)
- [ ] Pre-existing table lands in `tables_existing`, not `tables_failed` (test)
- [ ] Genuine creation failure → `Err` naming the tables (test + refreshed pins)
- [ ] `grep -n "all(r.success for r in results)" src/ms_access_mcp/application/migration.py` still exists but is unreachable with zero tables (entry guard precedes registration)
- [ ] Full gates green; `plans/README.md` row updated

## STOP conditions

- If `models/migration.py` serialization rejects the added key (tests
  fail on shape), stop and report — envelope shape is a hard boundary.
- If any existing test pins `Ok`-on-genuine-failure for a DELIBERATE
  documented reason (read adjacent comments first), stop and present it.
- If the job registry cannot be observed from tests to prove
  non-registration, stop and report the observation seam instead of
  asserting indirectly.

## Maintenance notes

- Plan 095 touches the same file next — land this first; keep the entry
  guard and the upload split in separate commits for clean review.
