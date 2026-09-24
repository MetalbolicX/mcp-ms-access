# Plan 071: Unify backend result envelopes on the models dataclasses — no more post-commit TypeErrors

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/backends/odbc.py src/ms_access_mcp/backends/ado.py src/ms_access_mcp/application/database.py`
> On any change, compare "Current state" excerpts against live code.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED (touches all backends + the shared facade; characterization first)
- **Depends on**: plan 070 (protocol seam landed; migration normalization already tolerates both shapes)
- **Category**: bug | tech-debt
- **Planned at**: commit `a7f2edb`, 2026-09-24

## Why this matters

The three backends return THREE different envelope conventions: DAO returns
`QueryResult`/`WriteResult` dataclasses (`models/__init__.py:89`, `:259`),
while ADO and ODBC return ad-hoc dicts. The application layer compensates in
three places — and one compensation is a live bug: `insert_data`/
`update_data`/`delete_data` in `DatabaseUseCases` unconditionally do
`dataclasses.asdict(result)["affected"]` (database.py:601,637,671), which
raises `TypeError` on the dicts ADO/ODBC return — AFTER the write has
committed — converting a successful write into an Err on any
`ACCESS_MCP_BACKEND=odbc` (or ado) session. Every adapter author must know
three conventions; two dict↔dataclass seams are failure traps CI cannot see
(COM/ODBC gates are local-only).

## Current state

- `src/ms_access_mcp/backends/odbc.py:82-160` — dict envelopes:
  ```python
  def execute_query(self, sql: str, params: Any = None) -> dict:
      if not self._connected or self._conn is None:
          return {"success": False, "rows": [], "count": 0, "columns": [], "error": "Not connected"}
      try:
          ...
          return {"success": True, "rows": results, "count": len(results), "columns": columns}
      except Exception as e:
          return {"success": False, "rows": [], "count": 0, "columns": [], "error": str(e)}

  def insert_data(self, table_name: str, data: dict | list[dict]) -> dict:
      ...
      return {"success": True, "affected": total}     # ← dict, keys match WriteResult
  ```
  (update/delete same shape with `"affected"`.)
- `src/ms_access_mcp/backends/ado.py:98-159` — same dict shapes; error paths
  already scrubbed via `_strip_password(str(e))`; NOTE `self._conn.Execute(sql, vals)`
  passes `vals` where ADODB `Connection.Execute(CommandText, RecordsAffected,
  Options)` expects RecordsAffected and hardcodes `"affected": 1` — that is
  finding DEBT-06, explicitly OUT of this plan's scope: reshape the
  envelope, do NOT change Execute call semantics.
- `src/ms_access_mcp/models/__init__.py:89-95`:
  ```python
  @dataclass
  class QueryResult:
      success: bool
      rows: list[dict]
      count: int
      columns: list[str]
      error: str | None = None
  ```
  and `WriteResult` at `:259` (fields: `success`, `error`, `affected` —
  verify exact field order before use).
- `src/ms_access_mcp/application/database.py`:
  - `_write_failed` (:51-62) — duck-types dataclass-or-dict;
  - `query_data` (:555-557) — `if isinstance(result, QueryResult): result =
    dataclasses.asdict(result)`;
  - insert/update/delete (:601, :637, :671) — `rowcount =
    dataclasses.asdict(result)["affected"]` ← TypeError on dict results;
  - ODBC path reachable via `ACCESS_MCP_BACKEND=odbc`
    (`backends/__init__.py:129`).
- Plan 037 precedent: typed-backend-boundary conversion for dao.py/
  schema_inspector.py with `test_boundary_serialization.py` as the pinning
  harness — THIS plan is the odbc/ado+consumer continuation of that pattern.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | ≤ baseline (expect improvement) |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS |
| Boundary pins | `uv run pytest tests/unit/test_boundary_serialization.py -q` | pass |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/odbc.py`, `src/ms_access_mcp/backends/ado.py` (returns → models dataclasses)
- `src/ms_access_mcp/application/database.py` (delete the three compensations)
- `src/ms_access_mcp/application/migration.py` (ONLY to shrink the dict branch if trivial — optional)
- `tests/unit/test_odbc_adapter.py`, `tests/unit/test_ado_adapter.py` (locate actual names via `rg -ln "OdbcAdapter|AdoAdapter" tests/unit/`) — extend/refresh
- `tests/unit/test_boundary_serialization.py` (extend with odbc/ado pins)

**Out of scope**:
- DEBT-06: ADO `Execute(sql, vals)` parameter-slot misuse + fabricated
  `affected: 1` — stays EXACTLY as-is (envelope only); separate investigate
  plan.
- `access/` package envelopes (~68 dict returns, deferred 2026-09-14).
- Any MCP-layer shape change (`_result_to_dict` consumes Ok/Err, unaffected).

## Git workflow

- Branch: `plan/071-backend-result-envelopes`
- Characterization commit FIRST (pins on current dict payloads), then RED →
  GREEN. Do NOT push.

## Steps

### Step 1: Characterization — pin today's odbc/ado payload shapes

Extend `test_boundary_serialization.py` with pins asserting the CURRENT
serialization of OdbcAdapter/AdoAdapter CRUD results (drive with fake
pyodbc/ADO connections — check existing odbc/ado unit tests for the fake
pattern and reuse it). These pins will be REFRESHED in Step 3's commit (the
plan-037/042 precedent for intentional contract changes).

**Verify**: pins pass against current code.

### Step 2: RED — dataclass envelopes + no post-commit TypeError

Two RED layers:
1. Adapter level: tests asserting `OdbcAdapter.insert_data(...)` returns an
   instance of `ms_access_mcp.models.WriteResult` (and
   `execute_query` → `QueryResult`); same for `AdoAdapter`.
2. Facade level: `DatabaseUseCases.insert_data` driven by a fake ODBC-shaped
   adapter (monkeypatched backend) returns Ok with the right rowcount —
   today the `asdict(result)["affected"]` line raises TypeError AFTER the
   fake commit; assert `.success is True`.

**Verify**: new tests FAIL (dicts returned; TypeError raised).

### Step 3: GREEN — odbc.py/ado.py return models

- Swap every CRUD return in odbc.py/ado.py for `QueryResult`/`WriteResult`
  constructions (import from `..models` — check dao.py's import form and
  match). Key mapping is 1:1 (`success/rows/count/columns/error`;
  `success/affected/error`). Update the method return annotations
  (`-> QueryResult` / `-> WriteResult`).
- Do NOT touch: `Execute(sql, vals)` call shapes, `"affected": 1` semantics
  (become `affected=1` in the dataclass), `compact_repair`/schema methods
  outside CRUD (unless trivially adjacent).
- Refresh the Step-1 characterization pins to the dataclass attribute
  contract (separate commit, message explains the intentional change).

**Verify**: Step-2 tests pass; odbc/ado unit suites pass; boundary pins
(green after refresh).

### Step 4: GREEN — delete the facade compensations

In `application/database.py`:
- `_write_failed`: drop the dict branch (keep the dataclass branch; if a
  caller still passes dicts after this plan, pyright should say so — that's
  the payoff).
- `query_data`: remove the `isinstance(result, QueryResult)` asdict
  special-case — the dict branch below still handles legacy adapters ONLY
  if pyright-clean; otherwise access `.rows/.count` directly (match what
  plan 037 did for dao).
- insert/update/delete: `rowcount = result.affected` (no asdict).

**Verify**: full unit suite green (plan-042 contract tests cover the write
paths); `uv run pyright` ≤ baseline.

### Step 5: Full gates

**Verify**: ruff / pyright / unit / tool_budget / arch guard green.

## Test plan

- Characterization pins (refreshed once, deliberately), adapter envelope
  tests (4), facade no-TypeError test (1), plus the existing suite as the
  regression net.
- Local-only note: `ACCESS_MCP_BACKEND=odbc` end-to-end check is an operator
  gate (odbc_integration) — run if a driver is available.

## Done criteria

- [ ] odbc.py/ado.py CRUD return `QueryResult`/`WriteResult`
- [ ] `rg -n "asdict\(result\)" src/ms_access_mcp/application/database.py` → 0 matches
- [ ] `_write_failed` has no dict branch
- [ ] ADO `Execute(sql, vals)` call sites UNCHANGED (DEBT-06 preserved for its own plan)
- [ ] All gates green; `plans/README.md` row updated

## STOP conditions

- Drift (esp. plan 070 touching database.py normalization — coordinate).
- A consumer OTHER than database.py/migration.py depends on odbc/ado dict
  returns (`rg -n "\[.success.\]" src/` over odbc/ado results) — report the
  list before breaking them.
- Step 3 needs schema-method envelope changes beyond CRUD — out of scope.
- Verification fails twice.

## Maintenance notes

- DEBT-06 (ADO Execute slot misuse + fabricated affected) is now the LAST
  known correctness question on the ado CRUD path — its investigate plan
  should land after this (envelope-stable, semantics-focused).
- After this plan, `AdapterLifecycleProtocol`-style typing for the CRUD
  surface (`CrudBackendProtocol` from plan 058) can grow return types from
  models — cheap follow-up.
