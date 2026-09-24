# Plan 058: One shared CRUD SQL builder + a backend-side CRUD protocol for ado/odbc (dao documented as divergent)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 079d756..HEAD -- src/ms_access_mcp/backends/ado.py src/ms_access_mcp/backends/odbc.py src/ms_access_mcp/backends/dao.py src/ms_access_mcp/backends/protocols.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (SQL bytes are contract; byte-parity is pinned first)
- **Mode**: Characterization goldens → strict TDD (builder + protocol get RED→GREEN; refactor must keep goldens green unmodified)
- **Depends on**: none (042's contract-guard test is DONE and stays green)
- **Category**: tech-debt (DRY)
- **Planned at**: commit `079d756`, 2026-09-20

## Why this matters

`ado.py` and `odbc.py` carry a copy-paste-identical CRUD SQL layer: same
method set (`insert_data`/`update_data`/`delete_data`/`execute_query`/
`execute_script`), same signatures, same f-strings — verified near-verbatim
(`ado.py:110 ≡ odbc.py:110`). `dao.py` builds the same skeletons but with
literal value interpolation (`_format_dao_value`) and a sanitize-gated raw
string `WHERE` escape hatch — a *documented* divergence, not an accident.
Today a quoting or escaping fix must be applied 2-3 times; nothing
structurally enforces that the three adapters keep a shared surface (plan
013 typed the consumer side in `application/protocols.py`; the backend side
has no protocol — verified: `backends/protocols.py` does not declare the
CRUD surface).

## Current state

- `src/ms_access_mcp/backends/ado.py` — ADO adapter. CRUD SQL around
  lines 97-168:
  ```python
  f"INSERT INTO [{table_name}] ({', '.join(cols)}) VALUES ({placeholders})"   # :132
  f"[{k}] = ?"                                                                # :147
  f"UPDATE [{table_name}] SET {set_clause}"                                   # :150
  f"DELETE FROM [{table_name}]" + f" WHERE {' AND '.join(conditions)}"        # :164-168
  ```
  Values are `?`-parameterized; column names are interpolated **bare**
  (no brackets on `cols`).
- `src/ms_access_mcp/backends/odbc.py` — ODBC adapter, character-for-character
  the same construction style at `:110, :131-136, :155-159`; differs only in
  cursor/commit/rowcount plumbing.
- `src/ms_access_mcp/backends/dao.py` — DAO adapter. Same skeleton, different
  value path:
  ```python
  cols = ", ".join(f"[{c}]" for c in ...)          # :400 — brackets here
  vals = ", ".join(_format_dao_value(v) ...)       # :401 — literals, no ?
  f"INSERT INTO [{table_name}] ({cols}) VALUES ({vals})"   # :402
  sql += f" WHERE {where_dict}"                    # :438/:474 — raw string WHERE
  ```
  gated by `_sanitize_where_string` (`:421-427`, `:460-466`).
- `src/ms_access_mcp/backends/protocols.py` (78 lines) and
  `src/ms_access_mcp/application/protocols.py` (186 lines) exist but
  **neither declares the adapter CRUD surface** — the adapters are untyped
  plain classes (verified 2026-09-20).
- Repo constraints that bind this plan:
  - **Never name a new file with a leading underscore** — the architecture
    guard's private-import rule has an off-by-one that fires on it (lesson
    from plan 034). The new module is `backends/crud_sql.py`.
  - Plan 044's `quote_ident` lives in `backends/dao_sql.py`; the builder
    should route identifier quoting through it rather than re-escaping.
  - Plan 042's `tests/unit/test_facade_adapter_contract.py` pins facade↔
    adapter signatures — must stay green unmodified.
  - `application/sql_builder.py` is NOT used by backends (verified zero
    imports) — leave it alone; it is application-layer legacy.

## Commands you will need

| Purpose   | Command                  | Expected on success |
|-----------|--------------------------|---------------------|
| Install   | `uv sync --extra dev`    | exit 0              |
| Lint      | `uv run ruff check .`    | exit 0              |
| Typecheck | `uv run pyright src/ms_access_mcp/backends/` | 0 errors |
| Unit tests| `uv run pytest tests/unit/ -q` | all pass      |
| Contract  | `uv run pytest tests/unit/test_facade_adapter_contract.py tests/unit/test_boundary_serialization.py -q` | all pass |

## Scope

**In scope** (the only files you should modify):
- `src/ms_access_mcp/backends/crud_sql.py` (create)
- `src/ms_access_mcp/backends/protocols.py` (extend)
- `src/ms_access_mcp/backends/ado.py`, `src/ms_access_mcp/backends/odbc.py`
  (consume the builder)
- `src/ms_access_mcp/backends/dao.py` (protocol conformance only — its SQL
  construction stays as-is)
- `tests/unit/test_crud_sql.py` (create),
  `tests/unit/test_backends_protocols.py` (create or append),
  `tests/unit/test_ado_adapter.py` / `tests/unit/test_odbc_adapter.py`
  (append goldens)

**Out of scope** (do NOT touch):
- `application/protocols.py` (plan 013's consumer protocols — do not
  duplicate or merge).
- `application/sql_builder.py` — do not delete or rewire (separate decision).
- Column-name bracket-quoting behavior change in ado/odbc (bare `cols`
  today): goldens pin current bytes; quoting columns is a deliberate
  hardening step ONLY if goldens don't pin bare names — see STOP.
- dao.py's literal interpolation / string-WHERE semantics.

## Git workflow

- Branch: `plan/058-crud-sql-builder-protocol`
- Conventional commits mirroring plan 034's arc:
  `test(ado,odbc): golden-pin current CRUD SQL bytes` →
  `feat(backends): add crud_sql builder (RED→GREEN)` →
  `refactor(ado,odbc): consume crud_sql builder`. No AI attribution. Do not
  push.

## Steps

### Step 1: Characterization — golden-pin the current SQL bytes

Add golden tests (new `tests/unit/test_crud_sql.py` may start as
`test_crud_goldens.py`-style cases or live in the adapter test files) that
call each adapter's `insert_data`/`update_data`/`delete_data` with a fake
connection/cursor capturing executed SQL, and assert the exact strings for:
simple table+cols, multi-col `WHERE`, empty `where_dict` (no WHERE clause),
and list-of-dicts inserts. Pin both ado and odbc.

**Verify**: `uv run pytest tests/unit/test_ado_adapter.py tests/unit/test_odbc_adapter.py -q`
→ new goldens pass against CURRENT code (these are pins, not RED).

### Step 2: RED — builder contract tests

Create `src/ms_access_mcp/backends/crud_sql.py` as an empty module and write
`tests/unit/test_crud_sql.py` against its intended API:

```python
def build_insert(table: str, data: dict | list[dict]) -> tuple[str, list]: ...
def build_update(table: str, set_dict: dict, where_dict: dict | None) -> tuple[str, list]: ...
def build_delete(table: str, where_dict: dict | None) -> tuple[str, list]: ...
```

Tests assert the golden bytes from Step 1 (same inputs → same SQL, params
returned separately) — these FAIL (module empty).

**Verify**: `uv run pytest tests/unit/test_crud_sql.py -q` → all new tests
fail (ImportError/NameError is acceptable RED).

### Step 3: GREEN — implement the builder

Implement the three functions reproducing the pinned bytes exactly
(`?` placeholders, `[table]` brackets, bare column names, ` AND `-joined
WHERE). Route table quoting through the existing bracket convention;
do NOT introduce new escaping here.

**Verify**: `uv run pytest tests/unit/test_crud_sql.py -q` → all pass.

### Step 4: Refactor ado.py + odbc.py onto the builder

Replace the inline f-string construction in both adapters with builder
calls. No plumbing changes (cursor/commit/rowcount code untouched).

**Verify**: `uv run pytest tests/unit/ -q` → ALL pass, including Step 1
goldens UNMODIFIED (this is the byte-parity proof) and plan 042's contract
tests.

### Step 5: RED→GREEN — backend CRUD protocol

Add to `backends/protocols.py`:

```python
@runtime_checkable
class CrudBackendProtocol(Protocol):
    def execute_query(self, sql: str, params: tuple | None = None) -> Any: ...
    def insert_data(self, table_name: str, data: dict | list[dict]) -> Any: ...
    def update_data(self, table_name: str, set_dict: dict, where_dict: dict | None = None) -> Any: ...
    def delete_data(self, table_name: str, where_dict: dict | None = None) -> Any: ...
```

RED: `tests/unit/test_backends_protocols.py` asserts
`isinstance(AdoAdapter(...), CrudBackendProtocol)` etc. for ado/odbc/dao
using fakes — run BEFORE declaring the protocol to see it fail
(NameError), then declare → GREEN. Include a drift-guard test: any adapter
exposing the CRUD surface must conform (enumerate via import of the three
classes explicitly — do not introspect the package).

**Verify**: `uv run pytest tests/unit/test_backends_protocols.py -q` → pass;
`uv run pytest tests/unit/ -q` → all pass.

### Step 6: Gate re-verification

**Verify**: `uv run ruff check .` → exit 0;
`uv run pyright src/ms_access_mcp/backends/` → 0 errors.

## Test plan

- Golden pins (Step 1): exact SQL bytes for insert/update/delete ×
  ado/odbc × 4 shape cases each.
- Builder unit tests (Steps 2-3): same bytes from pure functions.
- Protocol conformance (Step 5): 3 adapters conform; drift-guard present.
- Structural pattern: fake-cursor capture as in
  `tests/unit/test_ado_adapter.py`; characterization discipline as in plan
  034's connector goldens.
- Verification: full unit suite + contract/boundary tests green.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `rg -n "INSERT INTO \[\{table_name\}\]" src/ms_access_mcp/backends/ado.py src/ms_access_mcp/backends/odbc.py` → 0 matches (construction lives in `crud_sql.py`)
- [ ] Step 1 goldens pass UNMODIFIED after Step 4
- [ ] `uv run pytest tests/unit/ -q` exits 0
- [ ] `uv run ruff check .` and `uv run pyright src/ms_access_mcp/backends/` clean
- [ ] `tests/unit/test_facade_adapter_contract.py` unmodified and green
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- Goldens in Step 1 FAIL against current code (means SQL construction is
  not uniform — re-scope before proceeding).
- Any existing test pins bare/unquoted column names in a way the builder
  cannot reproduce byte-exactly.
- `dao.py`'s method signatures drift from the protocol (plan 042 fixed
  `where_dict=`; if re-drifted, STOP — separate defect).
- The architecture guard (`test_architecture_boundaries.py`) fails on the
  new `crud_sql.py` import (re-check the no-leading-underscore rule first).

## Maintenance notes

- `application/sql_builder.py` remains unused by backends — future cleanup
  candidate (delete or fold into `crud_sql.py`) once nothing in
  `application/` imports it; verify with
  `rg -n "sql_builder" src/ tests/`.
- Column-name quoting hardening (bare `cols` in ado/odbc INSERT/UPDATE) is
  now a ONE-site change in `crud_sql.py` — schedule it as a follow-up with
  its own RED tests rather than slipping it into this plan.
- Reviewer: the byte-parity proof is "Step 1 goldens green, unmodified,
  after Step 4". If any golden was edited, the refactor is not
  behavior-preserving.
