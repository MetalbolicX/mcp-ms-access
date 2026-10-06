# Plan 084: Migration verification gates success (counts enforced, dialect-aware quoting)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/application/migration.py src/ms_access_mcp/backends/migration/connectors/ tests/unit/test_application_migration.py tests/unit/test_migration_connectors.py`
> Plans 083 will already have modified `application/migration.py` — that is
> expected drift; reconcile against its "Current state" before starting.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: MED
- **Depends on**: plans/083-*.md (same-file sequencing)
- **Category**: migration / bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

`verification_mode="full"` records source/target row counts and then sets
`success = True` unconditionally — a transfer that loses rows still reports
success, and a failed count (returned as `-1` and only logged) is ignored.
`count-only` mode records nothing at all (it does not count). On top of
that, `BaseConnector.count_rows` quotes table names with Jet brackets
(`[table]`) for EVERY dialect — postgres and mysql reject that syntax, so
their counts always fail and silently return `-1`. Migration is the
product's flagship feature; its verification is currently decorative.

## Current state

- `src/ms_access_mcp/application/migration.py` — `_transfer_table`
  (~lines 620–688): executes the query, inserts in batches of 1000, then:

```python
            # Verification
            if verification_mode == "full":
                # Simple count verification
                target_count = self._count_target_rows(connector, table.name)
                result.verification = {
                    "source_rows": result.source_rows,
                    "target_rows": target_count,
                    "mode": "full",
                }
            else:
                result.verification = {"mode": "count-only"}

            result.success = True
```

  `_count_target_rows` (~lines 676–682) returns `-1` on any exception (log
  only). `transfer_data` finalizes with `all_success = all(r.success for r
  in results)` — the gate to make counts matter already exists downstream
  (failed tables trigger `rollback_table`).
- `src/ms_access_mcp/backends/migration/connectors/__init__.py` —
  `BaseConnector.count_rows` (~line 54) uses `dao_sql.quote_ident`
  (Access brackets) for all dialects. No connector overrides it.
  Dialect-correct quoting exists implicitly per connector's DDL rendering
  (`dialect.py` knows each dialect's identifier style).
- `tests/unit/test_application_migration.py` — (~lines 686–730) covers
  count-only mode; no test pins a mismatch outcome. Audit-verified with a
  fake connector: mismatch (source 2 / target 999) → `success=True`;
  count failure → `success=True`.
- Zero-table vacuous success (`all([])` at ~line 588) is a KNOWN-DEFERRED
  finding — see STOP conditions for the boundary of this plan.
- Conventions: `TableResult.verification` dict shape is consumed by
  `models/migration.py` serialization (plan 024) — keep keys stable.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused unit | `uv run pytest tests/unit/test_application_migration.py tests/unit/test_migration_connectors.py tests/unit/test_mcp_migration.py -q` | all pass |
| SQLite integration | `uv run pytest tests/integration/test_migration_sqlite.py tests/integration/test_migration_e2e.py -q` | pass/skip per driver gates |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/application/migration.py` (`_transfer_table` verification block, `_count_target_rows`)
- `src/ms_access_mcp/backends/migration/connectors/__init__.py` (+ per-connector `quote_identifier` overrides as needed)
- `tests/unit/test_application_migration.py`, `tests/unit/test_migration_connectors.py`

**Out of scope**:
- Zero-table transfer success semantics (deferred finding — do not change
  `all([])` behavior here; it is pinned as deferred in `plans/README.md`).
- Sampling/row-content verification beyond counts (mode stays count-based).
- `models/migration.py` serialization shape changes.

## Git workflow

- Branch: `plan/084-migration-verification` from `main` (after 083).
- Conventional commits: `fix(migration): fail transfers on count mismatch and count errors`.

## Steps

### Step 1: Characterize, then RED

- Characterize: add a test pinning TODAY's outcome for `count-only`
  (records `{"mode": "count-only"}` and no counts) so the deliberate
  behavior change is visible.
- RED: two new tests in `test_application_migration.py` with a fake
  connector: (a) full mode, `count_rows` returns a value ≠ `source_rows`
  → `result.success is False` with an error naming both counts;
  (b) full mode, `count_rows` raises → `success is False` with a
  "verification unavailable" error. Then make `count-only` actually count:
  (c) count-only records `source_rows`/`target_rows` in `verification`
  without gating success.

**Verify**: (a)/(b)/(c) RED as applicable; characterization passes.

### Step 2: GREEN — gate on counts in full mode

In `_transfer_table`: full mode computes `target_count`; if
`target_count != result.source_rows` → `result.success = False` and
`result.error = f"verification failed: source {n} != target {m}"`; if the
count errored (`-1`) → `success = False` with
`"row-count verification unavailable"`. Count-only mode calls the counter
and records both numbers without gating. Keep the `verification` dict keys
stable (add nothing that `models/migration.py` cannot serialize).

**Verify**: Step-1 tests pass; `uv run pytest tests/unit/test_mcp_migration.py -q` → pass (wrapper tests unaffected).

### Step 3: Dialect-aware `count_rows` quoting (strict TDD)

- RED: in `tests/unit/test_migration_connectors.py`, per-connector tests
  asserting the COUNT SQL uses dialect-correct quoting: sqlite `"table"`,
  postgres `"table"`, mysql `` `table` ``, sqlserver `[table]`.
- GREEN: add `quote_identifier(name)` to `BaseConnector` (default: Jet
  brackets, preserving today's sqlserver behavior) and override in
  sqlite/postgres/mysql connectors (they already know their dialect —
  reuse the `dialect.py` spec's identifier quoting if exposed; otherwise a
  one-line per-connector override). `count_rows` uses it.

**Verify**: `uv run pytest tests/unit/test_migration_connectors.py -q` → pass.

### Step 4: Integration sanity

Run the sqlite migration integration (mismatch path if practical: easiest
is a table override with a `where` clause so target < source, asserting
the job reports the table failed).

**Verify**: `uv run pytest tests/integration/test_migration_sqlite.py -q` → pass (or documented driver-gated skip).

### Step 5: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0; `uv run ruff check .` → 0.

## Test plan

- Unit: mismatch gates failure; count error gates failure; count-only
  counts without gating; per-dialect COUNT quoting (4 connectors).
- Integration: sqlite transfer with a row-losing override reports failure.
- Existing `test_models_migration.py` serialization tests must stay green
  (no shape change).

## Done criteria

- [x] Full-mode mismatch → `success=False` with named counts (test)
- [x] Full-mode count error → `success=False` (test)
- [x] count-only records both counts (test)
- [x] COUNT SQL is dialect-quoted for all four connectors (tests)
- [x] Unit suite + qualify green; `plans/README.md` row updated

## STOP conditions

- If existing pinned behavior (tests or documented user expectations)
  requires count failures to be non-fatal, stop and present the trade-off —
  flipping the default is a product decision.
- If any connector's driver makes `SELECT COUNT(*)` semantics differ
  (e.g. approximate counts), stop and report per-dialect findings.
- If changing `verification` dict contents breaks `models/migration.py`
  serialization tests, stop — shape stability is a hard boundary.

## Maintenance notes

- The zero-table vacuous success stays deferred by design; when it is
  revisited, the `Err("no tables in schema")` fail-fast belongs at
  `transfer_data` entry, before job registration.
- Plan 088 (bounded data movement) touches the same `_transfer_table`
  materialization; sequence after this plan.
