# Plan 080: Facade↔backend contract truth (failure detection and call shapes)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/application/database.py src/ms_access_mcp/backends/dao.py src/ms_access_mcp/backends/odbc.py src/ms_access_mcp/backends/schema_inspector.py tests/unit/test_facade_adapter_contract.py`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/077-*.md (green baseline)
- **Category**: bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

`DatabaseUseCases` (the facade used by CLI and MCP) and the concrete adapters
disagree on call shapes and result types, and the facade's failure detector
recognizes only one result class. Consequences, all verified in the audit
with fake adapters: DDL/relationship failures that arrive as plain dicts or
`RelationshipResult` are reported as `Ok(rowcount=1)` (false success);
`create_table` passes a `{name: type}` dict where DAO expects a
`list[dict]`; `alter_table` passes `action=`/`column_name=`/`sql_type=`
keywords that no adapter accepts (guaranteed `TypeError` → `Err`). The
existing contract tests pass because their fakes mirror the facade's own
(wrong) expectations.

## Current state

- `src/ms_access_mcp/application/database.py` — the facade (1300+ lines).
  - `_write_failed(result)` (~lines 51–58) returns an `Err` only when
    `isinstance(result, WriteResult)` and `result.success is False`; any
    other shape (dict with `"success": False`, `RelationshipResult`)
    falls through as success.
  - `create_table` (~line 707) sends `columns` as a `{name: type}` dict;
    `backends/dao.py` `create_table` (~line 550) iterates
    `col["name"]`/`col["sql_type"]` over a `list[dict]`.
  - `alter_table` (~lines 740–741) calls
    `adapter.alter_table(table, action=..., column_name=..., sql_type=...)`;
    DAO (~line 655) and ODBC (~line 312) implement
    `alter_table(table_name, operations: list[dict])`. `AccessAdapter` has
    no such method (unreachable for these capabilities — not a scope item).
  - `create_relationship` (~lines 875–878) passes a `RelationshipResult`
    producer path (DAO ~lines 300/314; `schema_inspector.py` ~566/579).
- `src/ms_access_mcp/errors.py` — `Ok`/`Err` envelopes (facade `_err`/`_ok`).
- `tests/unit/test_facade_adapter_contract.py` — fakes that all return
  `WriteResult` with the facade's signature (~lines 76–130); no test drives
  a real adapter through the facade.
- `src/ms_access_mcp/backends/dao.py` — `_do(engine=engine)` compat shim
  (~lines 884–887) exists solely because some test fakes invoke `_do()`
  bare (comment says so).
- Test-fake drift (audit TEST-06): `tests/unit/test_dao_recordset_safety.py`
  (~line 50) `def call(self, fn, engine=None): return fn()` drops kwargs;
  `tests/unit/test_dao_sql.py` (~line 278) `def call(self, fn)` accepts
  none. Real contract: `dispatcher.py` `call(fn, *args, **kwargs)` forwards
  everything. A kwarg-dropping fake masked the plan-071 salvage regression.
- Conventions: adapters may return typed results (`WriteResult`,
  `RelationshipResult`, `QueryResult` — plan 037/071) OR plain dicts
  (`{"success": bool, ...}`) on the DAO path; the facade must treat every
  shape's `success is False` as failure.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused tests | `uv run pytest tests/unit/test_facade_adapter_contract.py tests/unit/test_database.py tests/unit/test_backend_dao.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/application/database.py`
- `src/ms_access_mcp/backends/dao.py`, `src/ms_access_mcp/backends/odbc.py`
  (only if a signature legitimately needs normalizing — prefer facade-side
  translation)
- `tests/unit/test_facade_adapter_contract.py`,
  `tests/unit/test_database.py`, `tests/unit/test_dao_recordset_safety.py`,
  `tests/unit/test_dao_sql.py`, `tests/unit/conftest.py` (shared fake)

**Out of scope**:
- `mcp_tools/**` (wiring is plan 082) and `backends/access/**` (COM adapter
  capabilities are a different surface).
- `application/db_admin.py` stub honesty (plan 081).
- Migrating DAO's remaining dict-returning methods to typed results
  wholesale (only the failure-detection seam changes here).

## Git workflow

- Branch: `plan/080-facade-contract-truth` from `main`.
- Conventional commits: characterization first
  (`test: pin real-adapter DDL failure propagation (RED)`), then fix.

## Steps

### Step 1: Characterize the real contracts (tests first, expected RED)

Extend `tests/unit/test_facade_adapter_contract.py` with cases that drive
the REAL `DaoAdapter` (fake dispatcher/engine, pattern:
`tests/unit/test_backend_dao.py`) through `DatabaseUseCases`:

1. `create_table` with a failing engine → facade must return `Err`
   (today: `Ok(rowcount=1)`).
2. Adapter returning `{"success": False, "error": "..."}` (dict) via any
   write path → `Err`.
3. Adapter returning `RelationshipResult(success=False, ...)` via
   `create_relationship` → `Err`.
4. `alter_table` facade call reaching DAO's `operations` parameter with the
   documented operation dicts (today: `TypeError` swallowed into a generic
   `Err`).
5. `create_table` sends `list[dict]` columns with `name`/`sql_type` keys
   (assert on the fake engine's captured DDL/args).

**Verify**: `uv run pytest tests/unit/test_facade_adapter_contract.py -q` → new cases fail (RED), old cases pass.

### Step 2: Generalize `_write_failed`

In `application/database.py`, extend `_write_failed` (add a helper
`_result_failed(result)` if cleaner) to detect failure across shapes:
`WriteResult.success is False`, `RelationshipResult.success is False`, and
`isinstance(result, dict) and result.get("success") is False`. Route every
write method through it. Keep messages: prefer the adapter's
`error`/`message` field when present.

**Verify**: RED cases 2 and 3 now pass.

### Step 3: Fix `create_table` / `alter_table` call shapes (facade-side)

- `create_table`: translate the public `columns` argument (keep the public
  facade signature — it is the CLI/MCP contract) into the adapter's
  `list[dict]` shape (`{"name": ..., "sql_type": ...}`); accept BOTH the
  dict form and list form at the boundary, normalize internally.
- `alter_table`: translate the facade's `action`/`column_name`/`sql_type`
  into DAO/ODBC `operations` dicts inside the facade before dispatch.
  Confirm the exact operation dict keys by reading
  `backends/dao.py:655` and `backends/odbc.py:312` and pin them in the
  characterization tests.

**Verify**: all Step-1 cases pass; `uv run pytest tests/unit/test_database.py -q` → pass (refresh any pinned false-success expectations with rationale in the commit message).

### Step 4: Shared kwargs-forwarding dispatcher fake

- Add one conformance fake in `tests/unit/conftest.py`:
  `class FakeDispatcher: def call(self, fn, *args, **kwargs): return fn(*args, **kwargs)`.
- Migrate the drifted fakes (`test_dao_recordset_safety.py:50`,
  `test_dao_sql.py:278`, and the third variant in
  `test_maintenance_recovery.py` noted by the audit) to it (or to the same
  signature).
- Add one test asserting the shared fake forwards kwargs exactly like
  `ComDispatcher.call` (compare against the real method's behavior with a
  recording function).

**Verify**: `uv run pytest tests/unit/test_dao_recordset_safety.py tests/unit/test_dao_sql.py tests/unit/test_maintenance_recovery.py -q` → pass.

### Step 5: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → exit 0; `uv run ruff check .` → exit 0.

## Test plan

- New characterization cases in `test_facade_adapter_contract.py` (five
  above), modeled on the real-adapter fake-dispatcher pattern from
  `test_backend_dao.py` — NOT on the existing facade-mirroring fakes.
- Shared-fake conformance test in `conftest.py`-adjacent test module.
- Any refreshed expectations (false-success pins) must be listed in the
  commit message with the reason (plan-035 precedent).

## Done criteria

- [ ] Failing dict / `RelationshipResult` / `WriteResult` all produce `Err` through the facade (tests exist and pass)
- [ ] `alter_table` reaches adapters with correct `operations` (characterization test)
- [ ] `create_table` sends adapter-native column dicts (test asserts captured args)
- [ ] All migrated dispatcher fakes forward `**kwargs` (conformance test passes)
- [ ] `uv run pytest tests/unit/ -q` all pass; qualify exit 0
- [ ] `plans/README.md` row updated

## STOP conditions

- If the public CLI/MCP parameter contract for `create_table`/`alter_table`
  cannot be preserved while fixing the shapes (i.e. the fix requires
  changing tool schemas), stop and report — tool schema changes belong to a
  decision, not a silent rewrite.
- If ODBC's `alter_table`/`create_table` semantics differ materially from
  DAO's operation-dict keys, stop and report the divergence instead of
  inventing a third shape.
- If a verification fails twice after a reasonable fix attempt.

## Maintenance notes

- Plan 081 (raw-SQL routing, db-property honesty) and plan 082 (profile
  wiring) build on this contract work; land this first.
- The `_do(engine=...)` compat shim in `dao.py:884-887` may be tightened in
  plan 093 once all fakes conform; do not remove it here.
- Reviewer focus: no write path may return `Ok` when any adapter result
  shape says failure; and the facade's public signatures are unchanged.
