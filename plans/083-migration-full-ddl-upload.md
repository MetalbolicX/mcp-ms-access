# Plan 083: Migration `upload_schema` executes the complete per-dialect DDL (FK + indexes)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/application/migration.py src/ms_access_mcp/backends/migration/ tests/unit/test_application_migration.py tests/integration/test_migration_sqlite.py tests/unit/test_connector_ddl_golden.py`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none (independent of 079–082; sequence 084 after this — same file)
- **Category**: migration / bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

`upload_schema` renders the full DDL for each table (CREATE TABLE, then
`ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY`, then `CREATE INDEX …`) but
executes only element `[0]`. Verified in the audit with a fake connector:
two of three statements are silently discarded, so migrated targets lose
referential integrity and secondary indexes while the tool reports the
tables as created. Additionally, the SQLite dialect cannot execute the
phase-2 `ALTER … ADD CONSTRAINT` statement at all (SQLite rejects that
syntax), so simply iterating all statements is not enough: SQLite needs
inline FKs in the CREATE statement.

## Current state

- `src/ms_access_mcp/application/migration.py` — `upload_schema` table loop
  (~lines 446–459); the load-bearing line (~line 452), verbatim:

```python
                    ddl = render_schema_ddl(target_type, [table])[0]
                    connector.create_table(ddl)
```

  `tables_failed` collects pre-existing tables AND raised tables
  (indistinguishable — related test gap, do not fix here); the wrapper
  returns `_ok(...)` even when `tables_failed` is non-empty.
- `src/ms_access_mcp/backends/migration/__init__.py` — `render_schema_ddl`
  (~lines 96–133) returns `[CREATE TABLE, ALTER TABLE ADD CONSTRAINT FK…,
  CREATE INDEX …]` per table.
- `src/ms_access_mcp/backends/migration/connectors/dialect.py` —
  `render_table_ddl` (~lines 29–55) renders NO inline FK for ANY dialect;
  FKs are always phase-2 ALTERs.
- `src/ms_access_mcp/backends/migration/connectors/sqlite.py` —
  `create_table` (~lines 104–110) executes a single statement and commits
  per table; same shape in `connectors/postgres.py` (~127–133). SQLite
  cannot run `ALTER TABLE … ADD CONSTRAINT` (documented SQLite limitation).
- `tests/unit/test_connector_ddl_golden.py` — 24 byte-exact DDL goldens
  (plan 034); dialect changes must update goldens deliberately.
- `tests/integration/test_migration_sqlite.py` — (~lines 138–195) calls
  `render_schema_ddl` directly today; extend it to drive `upload_schema`.
- Conventions: DDL rendering is pure/dialect-driven (`DialectSpec` in
  `dialect.py`); connectors commit per table; audit-verified behavior is
  the spec for the new tests.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused unit | `uv run pytest tests/unit/test_application_migration.py tests/unit/test_connector_ddl_golden.py -q` | all pass |
| SQLite integration | `uv run pytest tests/integration/test_migration_sqlite.py -q` | all pass (sqlite target; runs without Access) |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/application/migration.py` (`upload_schema` only)
- `src/ms_access_mcp/backends/migration/connectors/dialect.py`
  (inline-FK rendering for SQLite)
- `src/ms_access_mcp/backends/migration/connectors/sqlite.py` (multi-
  statement execution if needed)
- `tests/unit/test_application_migration.py`,
  `tests/unit/test_connector_ddl_golden.py`,
  `tests/integration/test_migration_sqlite.py`

**Out of scope**:
- `transfer_data` / verification semantics (plan 084).
- Distinguishing "already exists" from "create failed" in `tables_failed`
  (recorded in `plans/README.md` deferred list).
- Any dialect other than sqlite/postgres/mysql/sqlserver behavior change
  beyond what multi-statement execution requires.

## Git workflow

- Branch: `plan/083-migration-full-ddl` from `main`.
- Conventional commits: `fix(migration): execute FK and index DDL during upload_schema`.

## Steps

### Step 1: RED — full-DDL execution unit test

In `tests/unit/test_application_migration.py`, add a test using a recording
fake connector (existing fakes in the file show the pattern): build a
`TableSchema` with one FK and one index, call `upload_schema`, assert the
connector received the CREATE, the FK statement, AND the index statement
(today only CREATE arrives). Add a second case asserting a statement failure
appends the table to `tables_failed` and continues with the next table.

**Verify**: `uv run pytest tests/unit/test_application_migration.py -q` → new cases RED.

### Step 2: GREEN — execute all statements per table

In `upload_schema`, iterate `render_schema_ddl(target_type, [table])`
executing every statement (via `connector.create_table(ddl)` per statement
or a connector-level multi-execute; keep per-table failure isolation).
Surfacing per-statement error text into the failure record is allowed if it
does not change the `Ok` envelope shape (`tables_created`/`tables_failed`
lists stay lists of names).

**Verify**: Step-1 tests pass.

### Step 3: SQLite inline FK (strict TDD, goldens updated deliberately)

- RED: golden test for sqlite now expects FK constraints inline in the
  CREATE TABLE statement (`FOREIGN KEY ("col") REFERENCES "parent"("id")`
  inside CREATE; NO `ALTER … ADD CONSTRAINT` statement for sqlite).
- GREEN: in `dialect.py`, add inline-FK rendering gated on the dialect
  (sqlite only; postgres/mysql/sqlserver keep phase-2 ALTERs — audit
  confirmed they support `ADD CONSTRAINT`).
- Update every affected golden in `test_connector_ddl_golden.py` in the
  same commit, with the byte diffs visible for review.

**Verify**: `uv run pytest tests/unit/test_connector_ddl_golden.py -q` → pass.

### Step 4: Integration — FK and index actually land in SQLite

Extend `tests/integration/test_migration_sqlite.py`: after `upload_schema`
of a schema with FK + index, query `sqlite_master` (or `PRAGMA
foreign_key_list` / `PRAGMA index_list`) and assert the constraint and
index exist. This test runs without Access (sqlite target; follow the
existing file's connector guard).

**Verify**: `uv run pytest tests/integration/test_migration_sqlite.py -q` → pass.

### Step 5: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0; `uv run ruff check .` → 0.

## Test plan

- Unit: full-DDL execution (recording connector), per-statement failure
  isolation, sqlite inline-FK goldens.
- Integration: `upload_schema` → FK + index present in sqlite catalog.
- Existing `test_migration_e2e.py` (postgres/mysql gated) untouched.

## Done criteria

- [x] `upload_schema` executes every rendered statement per table (unit test)
- [x] SQLite CREATEs carry inline FKs; no sqlite `ALTER … ADD CONSTRAINT` is emitted (goldens)
- [x] Integration test proves FK + index in the sqlite catalog after upload
- [x] `uv run pytest tests/unit/ -q` all pass; qualify exit 0
- [x] `plans/README.md` row updated

## STOP conditions

- If postgres/mysql/sqlserver connectors reject multi-statement execution
  (driver-level), stop and report the per-dialect behavior — the fix must
  stay per-dialect, not degrade the common path.
- If inline-FK for sqlite breaks `render_schema_ddl` consumers other than
  upload (grep for callers first), stop and report the caller list.
- If the sqlite integration test cannot run in the executor environment
  (missing driver), run the unit goldens and mark the integration check as
  an operator gate in the plan row — do not delete it.

## Maintenance notes

- Plan 084 lands next on the same file; keep commits small and focused.
- The `tables_failed` conflation (exists vs failed) is deliberately
  deferred; note it in review.
- When a new dialect is added, the dialect spec must now answer TWO
  questions: inline FK or ALTER-phase FK, and multi-statement support.
