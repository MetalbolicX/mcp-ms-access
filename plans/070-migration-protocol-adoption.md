# Plan 070: Migration protocol adoption — fix the QueryResult seam, add connector count_rows, retire the Any/ignore cluster

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/application/migration.py src/ms_access_mcp/application/protocols.py src/ms_access_mcp/backends/migration/`
> On any change, compare "Current state" excerpts against live code.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED (typing refactor over a working flow; pyright is the gate)
- **Depends on**: plan 069 (same file; lifecycle hoist must land first)
- **Category**: bug | tech-debt
- **Planned at**: commit `a7f2edb`, 2026-09-24
- **Supersedes**: the `migration.py:574` pyright-error item owned by TODO
  plan 050 (050 keeps its strays-deletion scope; this plan retires the
  `.get` error AND the "1 acceptable pre-existing error" carve-out every
  plan since 040 has carried).

## Why this matters

Migration is the only use-case family that bypasses `application/protocols.py`
entirely: adapters/connectors are typed `Any` behind ~14
`# type: ignore[assignment|attr-defined]` suppressions — which is exactly why
pyright never caught a REAL runtime break: `_transfer_table` calls
`query_result.get("rows", [])` (migration.py:574), but the DEFAULT Windows
backend (BackendSelector auto→dao) returns DAO's `QueryResult` DATACLASS
(`models/__init__.py:89`, no `.get`) → `AttributeError` on every per-table
transfer, swallowed into a `[sanitized]` error — the migration tool's core
promise silently fails on the default path. Additionally, verification
reaches into `connector._conn.cursor()` (SLF001-suppressed), never closes the
cursor, and converts every count failure to a silent `-1`.

## Current state

- `src/ms_access_mcp/application/migration.py:572-574` (post-069 line numbers
  may shift — match by symbol):
  ```python
  query_result = adapter.execute_query(query)  # type: ignore[attr-defined]
  rows = query_result.get("rows", []) if query_result else []
  ```
  `DaoAdapter.execute_query` returns `QueryResult`
  (`models/__init__.py:89-95`: `success/rows/count/columns/error` — a plain
  dataclass, no `.get`). ODBC/ADO return dicts — the seam divergence plan 071
  finishes; THIS plan normalizes at the migration boundary.
- `src/ms_access_mcp/application/migration.py:611-620`:
  ```python
  def _count_target_rows(self, connector: Any, table: str) -> int:
      """Count rows in target table (for verification)."""
      try:
          cursor = connector._conn.cursor()  # noqa: SLF001
          cursor.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}")
          count = cursor.fetchone()[0]
          return count
      except Exception:
          return -1
  ```
- `src/ms_access_mcp/application/migration.py:230-270, 384-403, 493, 556-573`
  — the `Any` + `type: ignore` cluster (`_extract_dao`, `_extract_odbc`,
  `_upload_schema`, `_transfer_table`).
- `src/ms_access_mcp/application/protocols.py:14-57` — existing protocol
  pattern: `AdapterLifecycleProtocol` (with `connected: bool` — now real via
  plan 065) + per-use-case adapter protocols. THIS is the pattern to extend.
- `src/ms_access_mcp/backends/migration/connectors/__init__.py` —
  `BaseConnector` ABC (factory registry in `backends/migration/__init__.py`).
- `src/ms_access_mcp/models/migration.py` — pydantic models
  (`ExtractedSchema`:30, `TableResult`:52, `MigrationJob`:78,
  `TableTransferConfig`:106).
- `DatabaseUseCases` shows the normalization exemplar at
  `application/database.py:556-557`:
  ```python
  if isinstance(result, QueryResult):
      result = dataclasses.asdict(result)
  ```
- Test exemplars: `tests/unit/test_application_migration.py` —
  `FakeConnector(BaseConnector)`:29, `SpyCursor`:620, `SpyConnection`:634.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Types baseline | `uv run pyright` | record count BEFORE (expect the known migration.py item) |
| Lint | `uv run ruff check .` | exit 0 |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Ignore census | `rg -c "type: ignore" src/ms_access_mcp/application/migration.py` | count must DROP (target: 0 in migration.py) |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS |

## Scope

**In scope**:
- `src/ms_access_mcp/application/protocols.py` (new MigrationAdapterProtocol + connector protocol)
- `src/ms_access_mcp/application/migration.py` (normalization + typing + count_rows)
- `src/ms_access_mcp/backends/migration/connectors/__init__.py` (BaseConnector.count_rows default method)
- `src/ms_access_mcp/backends/migration/connectors/{sqlite,postgres,mysql,sqlserver}.py` (only if the default needs an override)
- `tests/unit/test_application_migration.py` (extend)

**Out of scope**:
- Backend envelope unification (plan 071 — odbc/ado dict→dataclass).
- Vacuous-success-on-zero-tables; `_JOBS` registry (deferred).
- sql_builder/quoting (settled by plans 044/057).

## Git workflow

- Branch: `plan/070-migration-protocol-adoption`
- RED → GREEN per step; the protocol/typing steps are gate-verified
  (pyright) with characterization tests unmodified. Do NOT push.

## Steps

### Step 1: RED — transfer succeeds with a REAL QueryResult from the adapter

Extend `tests/unit/test_application_migration.py`: fake adapter whose
`execute_query` returns an actual `ms_access_mcp.models.QueryResult`
(populated `rows`, `columns`) — NOT a dict; drive a 1-table `transfer_data`;
assert the `TableResult` is `success=True` with the right `rows_transferred`.

**Verify**: FAILS today — `AttributeError: 'QueryResult' object has no
attribute 'get'` swallowed into a sanitized per-table error → success=False.
(If it unexpectedly PASSES, the default adapter path isn't what the fake
exercises — STOP and report what you found.)

### Step 2: GREEN — normalize the query result at the seam

In `_transfer_table` (mirroring database.py:556):

```python
from ..models import QueryResult  # (top-level import; models is a stable dep)

query_result = adapter.execute_query(query)
if isinstance(query_result, QueryResult):
    rows = query_result.rows
elif isinstance(query_result, dict):
    rows = query_result.get("rows", [])
else:
    rows = []
```

**Verify**: Step-1 test passes; existing dict-fake tests still pass (both
shapes supported until plan 071 collapses them); `uv run pyright` — the
known `migration.py:574` error is GONE.

### Step 3: RED — count_rows on the connector, cursor closed, failure logged

Three assertions in new tests using `FakeConnector`/`SpyCursor`:
1. `_count_target_rows` (or its replacement call site) invokes a public
   `count_rows(table)` method on the connector (spy records the call) — NOT
   `connector._conn`.
2. The cursor the default implementation opens gets `.close()` called.
3. A counting failure returns -1 AND logs a WARNING (use `caplog`) instead
   of silence.

**Verify**: all three FAIL.

### Step 4: GREEN — BaseConnector.count_rows + delegation

- In `connectors/__init__.py` `BaseConnector`, add a concrete default:

  ```python
  def count_rows(self, table: str) -> int:
      """Count rows in *table* (used by migration verification)."""
      cursor = self._conn.cursor()
      try:
          cursor.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}")
          return int(cursor.fetchone()[0])
      finally:
          try:
              cursor.close()
          except Exception:
              pass
  ```

  (Import `quote_ident` per the connectors' existing import style — check
  where plan 044/057 put it; if connectors already have a quoting helper,
  use that one.)
- In migration.py: `_count_target_rows` becomes a thin delegate:
  ```python
  def _count_target_rows(self, connector: MigrationConnectorProtocol, table: str) -> int:
      try:
          return connector.count_rows(table)
      except Exception as e:
          _log.warning("Row-count verification failed for %s: %s", table, e)
          return -1
  ```
  (`_log` = module logger; check migration.py's existing logging setup and
  match.) The `noqa: SLF001` line disappears.
- Only touch a dialect connector if the default breaks it (sqlite/postgres/
  mysql/sqlserver all speak `SELECT COUNT(*)`; placeholder styles don't
  apply — no params here).

**Verify**: Step-3 tests pass; connector tests green
(`uv run pytest tests/unit/test_migration_connectors.py -q` if present).

### Step 5: Protocol adoption — type the seam, delete the ignores

- Add to `application/protocols.py` (following the existing pattern):

  ```python
  class MigrationAdapterProtocol(AdapterLifecycleProtocol, Protocol):
      """Backend adapter surface used by ``MigrationUseCases``."""

      def get_table_schema_plan(self, /) -> Any: ...
      def get_tables(self, /) -> Any: ...
      def get_columns(self, table_name: str, /) -> Any: ...
      def get_indexes(self, table_name: str, /) -> Any: ...
      def get_relationships(self, /) -> Any: ...
      def execute_query(self, sql: str, /) -> Any: ...


  class MigrationConnectorProtocol(Protocol):
      """Connector surface used by ``MigrationUseCases``."""

      def insert_batch(self, table: str, columns: list[str], rows: list[tuple]) -> None: ...
      def rollback_table(self, table: str, /) -> None: ...
      def disconnect(self, /) -> None: ...
      def count_rows(self, table: str, /) -> int: ...
  ```

  (Verify each member against what migration.py ACTUALLY calls — adjust the
  list to reality before writing; the adapter `connect/disconnect/connected`
  come from AdapterLifecycleProtocol.)
- Type migration.py's locals/params against these protocols; remove the
  `type: ignore` suppressions one cluster at a time, running
  `uv run pyright` after each removal.

**Verify**: `rg -c "type: ignore" src/ms_access_mcp/application/migration.py`
→ 0 (or a documented residue ≤2 with reasons); `uv run pyright` → error
count ≤ baseline MINUS the migration item (target: 0 project-wide if the
baseline was only that item).

### Step 6: Full gates

**Verify**: ruff / pyright / unit / tool_budget / arch guard green.

## Test plan

- New: QueryResult-seam transfer success (1), count_rows delegation (1),
  cursor-closed (1), count-failure-logged (1).
- Existing migration suite green; connector characterization (plan-034
  goldens) untouched.

## Done criteria

- [ ] QueryResult-returning adapters transfer successfully (Step-1 test)
- [ ] `connector._conn` reach-in gone; `count_rows` public + cursor closed + failures logged
- [ ] MigrationAdapterProtocol + MigrationConnectorProtocol in protocols.py
- [ ] `type: ignore` count in migration.py = 0 (or ≤2 documented)
- [ ] `uv run pyright` — the historical migration.py error is retired
- [ ] All gates green; `plans/README.md` row updated (note the plan-050 scope
      split: 050 now owns only strays)

## STOP conditions

- Step 1 unexpectedly passes (fake isn't exercising the DAO-shaped path).
- Removing a `type: ignore` surfaces a REAL interface mismatch (adapter
  method missing/different signature) — fix the adapter call or report if
  it's a design question; do NOT force-cast to make pyright quiet.
- More than 5 new pyright diagnostics appear during Step 5 — report the
  list.
- Drift in protocols.py/connectors from concurrent plans.

## Maintenance notes

- Plan 071 finishes this seam: once odbc/ado return dataclasses, the dict
  branch in Step 2's normalization can shrink (keep it until then).
- The `_JOBS` tracker and vacuous-success findings remain deferred — this
  plan makes them MORE visible (typed seams) but does not fix them.
- Record in README that the "1 pre-existing pyright error" carve-out ends
  here.
