# Plan 097: Foreign-key orientation — one contract, proven against live DAO

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. This plan contains the cycle's LIVE COM probe (user-authorized:
> fixture-scoped, read-only inspection of a test/scratch database). If
> anything in the "STOP conditions" section occurs, stop and report — do not
> improvise. When done, update the status row for this plan in
> `plans/README.md` (section "Plans 094–097").
>
> **Drift check (run first)**:
> `git diff --stat 3058688..HEAD -- src/ms_access_mcp/backends/schema_inspector.py src/ms_access_mcp/backends/odbc_schema_reader.py src/ms_access_mcp/application/migration.py src/ms_access_mcp/backends/sql_generator.py`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED (live probe + reader alignment)
- **Depends on**: none (parallel; sequence last — it is the live-gated plan)
- **Category**: bug / data integrity
- **Planned at**: commit `3058688`, 2026-10-04

## Why this matters

Foreign-key orientation is inconsistent across readers, and at least two
DAO paths inside the SAME module disagree:

- `get_relationships` (`schema_inspector.py:194-217`) produces
  `table=rel.Table, foreign_table=rel.ForeignTable, columns=Name,
  foreign_columns=ForeignName`.
- `get_table_schema_plan` (`schema_inspector.py:294-308`) attaches the FK
  to `rel.ForeignTable` with `referenced_table=rel.Table` and
  `referenced_columns` from `Fields(j).Name`.
- The ODBC reader (`odbc_schema_reader.py:99-114`) maps
  `child_table, child_col, parent_table, parent_col` explicitly.
- Migration extraction therefore differs by backend:
  DAO path (`migration.py:283`) vs ODBC path (`migration.py:310,
  335-343`).
- A fourth reader exists (`schema_inspector.py:436-466`, used by
  `generate_sql`; consumer `sql_generator.py:110-125`).

The offline scout could not settle which side DAO's
`Relation.Table`/`ForeignTable` designates — the model contract
(`models/__init__.py:45-48`) and each consumer's assumption must be
mapped first, then ground truth established LIVE (user authorized this
cycle's live DB check). The defect is already proven internally (two
DAO readers disagree); the probe decides which one, if any, is right.

## Current state (HEAD `3058688`)

- Producers/consumers matrix to build verbatim in Step 1 (the scout's
  partial matrix above is the starting point — verify every cell).
- Test pins to consider (plan-035 precedent when refreshing):
  `tests/unit/test_schema_inspector.py:365-380` (DAO get_relationships
  pass-through, `columns == ["id"] # child`), `:396-427`
  (`TestGetTableSchemaPlan` — currently uses NO relations),
  `tests/unit/test_application_migration.py:475-513`
  (`referenced_table == "Orders"` — ODBC path),
  `tests/unit/test_odbc_schema_reader.py:23`,
  `tests/integration/test_schema_extraction.py:210,228` round-trip.
- Live environment: this machine has Access COM (proven this cycle);
  DAO integration tests run here. Scratch DB creation is available via
  the DAO adapter's `create_database`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused unit | `PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest tests/unit/test_schema_inspector.py tests/unit/test_odbc_schema_reader.py tests/unit/test_application_migration.py -q -p no:cacheprovider` | all pass |
| LIVE probe (marker-gated) | `... -B -m pytest tests/integration/test_fk_orientation.py -q -p no:cacheprovider` | pass (or documented skip if no Access) |
| Unit suite / lint / types / budget | house commands | green |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/schema_inspector.py` (all four reader
  paths)
- `src/ms_access_mcp/backends/odbc_schema_reader.py` (only if the
  contract decision changes its mapping)
- `src/ms_access_mcp/application/migration.py` (DAO extract consumer
  only — plans 094/095 land first; reconcile their drift)
- `src/ms_access_mcp/backends/sql_generator.py`,
  `src/ms_access_mcp/application/db_analysis.py` (consumers, only if
  alignment requires)
- `tests/unit/test_schema_inspector.py`, `test_odbc_schema_reader.py`,
  `test_application_migration.py`, `tests/integration/test_schema_extraction.py`
- `tests/integration/test_fk_orientation.py` (create — the live probe)

**Out of scope**:
- Adding new relationship features; ER rendering changes beyond
  orientation-driven arrow direction; versioning's export_schema_ddl
  callback path (production callers pass no callbacks — note only).

## Git workflow

- Branch: `plan/097-fk-orientation` from `plan/096-schema-perf`.
- Conventional commits: matrix+probe first (characterization), then fix.

## Steps

### Step 1: Orientation matrix (offline, evidence)

Read verbatim and tabulate: model field definitions
(`models/__init__.py` ForeignKeySchema region), every producer's exact
mapping, every consumer's assumption (`sql_generator.py:110-125`,
`db_analysis.py:57-58`, `migration.py:283/310/335-343`,
`mcp_tools/core.py` get_relationships path). Output the matrix in the
report — this defines the single target contract.

**Verify**: matrix complete (no producer or consumer left unmapped).

### Step 2: LIVE probe (characterization)

Create `tests/integration/test_fk_orientation.py` (marker-gated like
its siblings):
1. Create a scratch `.accdb` via the DAO adapter's `create_database`
   (tmp path).
2. Create two tables with a known FK via DAO (relationships created
   with an explicit child→parent direction the test itself defines).
3. Through DAO, dump `Relation.Table`, `Relation.ForeignTable`, and each
   `Field`'s `Name`/`ForeignName` — the side whose `Name`s are the FK
   columns IS the child. This is ground truth.
4. Assert the readers (`get_relationships`, `get_table_schema_plan`)
   against the proven ground truth and the Step-1 contract.
   RED = whichever reader is wrong.

**Verify**: probe RED identifies the wrong reader(s); if the fixture
cannot be built via DAO DDL, STOP and report (do not fake ground truth).

### Step 3: GREEN — align readers to the proven contract

Fix the wrong reader(s) so ALL producers emit the Step-1 contract;
adjust consumers only if the matrix shows them compensating for the
inconsistency (report each). Refresh the listed pins with rationale.

**Verify**: probe GREEN; focused unit tests GREEN.

### Step 4: Full gates

Unit suite + ruff + pyright + budget; existing integration files
(`test_schema_extraction.py`) re-run.

## Test plan

- The live probe (ground-truth-based assertions, marker-gated, skips
  without Access — runs on THIS machine);
- Unit alignment pins per refreshed listed test;
- Matrix as review evidence in the first commit message.

## Done criteria

- [ ] Orientation matrix documented in the first commit message
- [ ] Live probe proves ground truth and asserts every reader against it
- [ ] All four readers emit one contract (probe + unit tests green)
- [ ] Full gates green; `plans/README.md` row updated (live gate status)

## STOP conditions

- If the live probe contradicts the model CONTRACT itself (model
  fields' documented semantics are what's wrong), STOP and present the
  contract decision — that is a schema/API change, not a reader fix.
- If creating a known-direction relationship via DAO DDL fails in a way
  the test cannot express, STOP and report an operator-gated fallback
  (existing fixture with a known FK — locate and name it).
- If aligning a reader would change a documented public tool output
  (get_relationships field meanings) beyond orientation, stop and
  present the surface change.

## Maintenance notes

- Versioning's export_schema_ddl relationship callbacks are wired only
  in tests — if they are ever activated, they must consume the same
  contract; note this in the matrix commit.
