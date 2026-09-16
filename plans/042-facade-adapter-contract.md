# Plan 042: Facade↔adapter contract alignment (signature drift, false-success propagation, readonly gates)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat dde0998..HEAD -- src/ms_access_mcp/application/database.py src/ms_access_mcp/backends/dao.py src/ms_access_mcp/backends/odbc.py src/ms_access_mcp/backends/ado.py src/ms_access_mcp/backends/access.py src/ms_access_mcp/backends/schema_inspector.py src/ms_access_mcp/application/protocols.py tests/unit/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/041-qualify-baseline.md (recommended — use its gate as
  the feedback loop; not a hard dependency)
- **Category**: bug
- **Planned at**: commit `dde0998`, 2026-09-16

## Why this matters

Three classes of contract bugs survive a fully green 1247-test suite because the
test fakes mirror the facade's assumptions instead of the real adapters'
signatures, and COM/ODBC integration tests are local-only:

1. **`update_data`/`delete_data` always fail on real backends** — the facade
   passes a `where=` keyword that no adapter accepts → `TypeError` on every call.
2. **Writes report false success** — adapters deliberately return
   `{"success": False, "error": ...}` dicts / `WriteResult(success=False)` on
   failure; the facade discards them and returns `Ok(rowcount=1)`. Failed
   CREATE TABLE / dropped indexes / rejected INSERTs surface as successes to
   the LLM client — the worst failure mode for a database tool.
3. **The readonly policy is bypassed** by `create_query`/`set_query_sql`/
   `delete_query`: they check only `confirm`, never `allow_write()`, so
   `confirm=True` persists objects into a database the server opened as
   read-only.

## Current state

All excerpts verified against `dde0998`.

`src/ms_access_mcp/application/database.py` — the drift (facade side):

```python
# :547 — update_data calls a kwarg no adapter has:
result = adapter.update_data(table_name, data, where=where)
# :579 — same for delete_data:
result = adapter.delete_data(table_name, where=where)
# :771 — create_relationship passes positionally in the WRONG ORDER:
result = adapter.create_relationship(relationship_name, source_table, source_column, dest_table, dest_column)
# :798 — delete_relationship passes ONE arg; DAO requires TWO:
result = adapter.delete_relationship(relationship_name)
```

`src/ms_access_mcp/backends/dao.py` — the real adapter contract:

```python
# :405  def update_data(self, table_name: str, set_dict: dict, where_dict: dict | str | None = None) -> WriteResult
# :445  def delete_data(self, table_name: str, where_dict: dict | str | None = None) -> WriteResult
# :279  def create_relationship(self, table_name, relationship_name, columns: list[str], foreign_table, foreign_columns: list[str]) -> RelationshipResult
# :303  def delete_relationship(self, table_name: str, relationship_name: str) -> RelationshipResult
```

(`odbc.py:124,149` and `ado.py:136,155` also use `where_dict` — verify the
relationship methods on every adapter in Step 0; `schema_inspector.py` may also
implement `create_relationship`/`delete_relationship` per plan 026.)

False-success sites (facade discards the adapter return):

```python
# database.py:389-390  adapter.create_query(query_name, sql)   -> return _ok(rowcount=1)
# database.py:412-413  adapter.set_query_sql(query_name, sql)  -> return _ok(rowcount=1)
# database.py:434-435  adapter.delete_query(query_name)        -> return _ok(rowcount=1)
# database.py:615-616, 650-651, 674-675, 709-710, 734-735 — same pattern for
#   create_table / alter_table / delete_table / create_index / drop_index
# database.py:513-515, 547-549, 579-581 — insert/update/delete unwrap only
#   dataclasses.asdict(result)["affected"], ignoring WriteResult.success
```

Adapters DO populate failure info — e.g. `dao.py:246`
`return {"success": False, "error": str(e)}` and `dao.py:401,441,477`
`return WriteResult(success=False, error=str(e), affected=0)`.

Readonly gate — present in `insert_data` (`database.py:507-508`):

```python
if not self._policy.allow_write():
    return _err("Write operations are disabled (readonly mode)")
```

… but ABSENT from `create_query` (:383), `set_query_sql` (:406),
`delete_query` (:428), which only call
`self._policy.require_acknowledgment(name, confirm)` (confirm-flag check,
`safety.py:61-69`). Additionally `dao.py:141` opens the DAO handle with
`read_only=False` unconditionally — engine-level backstop is out of scope here
(tracked in Maintenance notes).

Conventions to match: Result envelope `Ok`/`Err` from `..errors` (`_ok`/`_err`
aliases at `database.py:32-33`); the facade's existing success-check idiom at
`create_relationship` (:772-774) — `dataclasses.asdict(result).get("success")`
→ `_err(...)` — is the in-repo precedent for propagation.

## Commands you will need

| Purpose    | Command                                | Expected on success |
|------------|----------------------------------------|---------------------|
| Fast gate  | `uv run python scripts/qualify.py`     | exit 0 (after plan 041; else run the four commands individually) |
| Lint       | `uv run ruff check .`                  | exit 0              |
| Typecheck  | `uv run pyright src/ms_access_mcp/application/ src/ms_access_mcp/backends/` | 0 errors |
| Unit tests | `uv run pytest tests/unit/ -q`         | all pass            |
| Targeted   | `uv run pytest tests/unit/test_facade_adapter_contract.py tests/unit/test_backend_access.py -q` | all pass |

## Scope

**In scope**:
- `src/ms_access_mcp/application/database.py` — the call sites and gates listed above
- `src/ms_access_mcp/backends/dao.py`, `odbc.py`, `ado.py` — ONLY if a signature
  change is the chosen alignment (prefer facade-side fixes; see Step 2)
- `tests/unit/test_facade_adapter_contract.py` (create)
- Updates to existing fakes/tests that pinned the wrong contract (allowed —
  tests pinning a bug are not the contract; see plans/README.md lesson from
  plan 035)

**Out of scope**:
- `backends/access.py` COM adapter relationship methods (if its signatures also
  drift, fix identically — but do NOT refactor anything else there)
- `dao.py:141` `read_only=False` engine-level flag (MED-risk; deferred — see
  Maintenance notes)
- `query_data` action-SQL validation (deferred; needs live-Access verification)
- `cli.py` — it calls the same facade methods; if the facade signature changes,
  cli.py call sites may need mechanical updates, but no CLI behavior change

## Git workflow

- Branch: `plan/042-facade-adapter-contract`
- Conventional commits, e.g. `fix(application): align update_data where kwarg with adapters`
- One commit per step. Do NOT push unless instructed.

## Steps

### Step 0: Map the real call targets (recon, no edits)

For each of `update_data`, `delete_data`, `create_relationship`,
`delete_relationship`, `create_query`, `set_query_sql`, `delete_query`,
`create_table`, `alter_table`, `delete_table`, `create_index`, `drop_index`:
record which class the facade's `self._get_adapter(...)` can return and that
class's exact method signature (`DaoAdapter`, `OdbcAdapter`, `AdoAdapter`,
`AccessAdapter`, and check whether `SchemaInspector` is the real target for
relationship methods). Produce the table before touching code.

**Verify**: the table exists in your step report; every facade call site has a
confirmed target signature. If a facade call has NO matching adapter method at
all, STOP.

### Step 1: Fix the `where` kwarg drift (RED→GREEN)

Write failing tests first: a fake adapter whose `update_data`/`delete_data`
signatures are COPIED from `dao.py` (param name `where_dict`), asserting the
facade calls succeed and return the adapter's `affected` count. Then fix the
facade: call positionally — `adapter.update_data(table_name, data, where)` —
or rename the adapter params to `where` across dao/odbc/ado. Prefer the
facade-side positional call (smallest diff, no public adapter rename); if
`application/protocols.py` declares these methods, keep protocol and adapters
consistent either way.

**Verify**: `uv run pytest tests/unit/test_facade_adapter_contract.py -q` → pass;
`uv run ruff check .` → exit 0.

### Step 2: Fix relationship argument order/arity

Align the facade calls to the verified adapter contract from Step 0. Expected
shape for DAO: `adapter.create_relationship(source_table, relationship_name,
[source_column], dest_table, [dest_column])` (facade holds singular column
names; DAO wants lists) and `adapter.delete_relationship(<table>, relationship_name)`.
If the facade's `delete_relationship` lacks the table name, resolve by looking
up the relationship's owning table (DAO `Relations` collection is enumerable) —
do NOT change the facade's PUBLIC signature (CLI/MCP surface) without a STOP
and operator decision.

**Verify**: new failing-then-passing tests with signature-accurate fakes;
`uv run pytest tests/unit/ -q` → all pass.

### Step 3: Propagate adapter failures (no more false Ok)

Add one private helper in `database.py`, e.g.:

```python
def _write_failed(result: Any) -> str | None:
    """Return the adapter's error message if a write result reports failure."""
    d = dataclasses.asdict(result) if dataclasses.is_dataclass(result) else result
    if isinstance(d, dict) and d.get("success") is False:
        return str(d.get("error", "write failed"))
    return None
```

Apply it at every site listed in Current state: query CRUD, the five DDL
facades, and insert/update/delete (`WriteResult.success`). On failure return
`_err(message)`; on success keep the current `Ok` shape (rowcount semantics
unchanged — MCP payloads must not change shape, only correctness).

**Verify**: tests where the fake returns `{"success": False, "error": "boom"}`
assert the facade returns `Err("boom")` for each of the 11 sites; full suite green.

### Step 4: Add the readonly gate to query-CRUD facades

Insert the standard block (copy from `insert_data:507-508`) into
`create_query`, `set_query_sql`, `delete_query`, immediately after the existing
`require_acknowledgment` check. Audit the whole facade for any OTHER mutating
method missing the gate while you are there (`save_query` path included) and
gate those too; list what you found in the step report.

**Verify**: tests asserting readonly `DatabaseUseCases(readonly=True)` +
`confirm=True` returns Err for the three query tools; suite green.

### Step 5: Add the recurrence guard — fake-signature conformance test

In `tests/unit/test_facade_adapter_contract.py`, add a test that takes every
fake adapter class used by the database-facade tests and, for each public
method name the facade calls, binds the fake's signature against the REAL
adapter signature via `inspect.signature`:

```python
import inspect
from ms_access_mcp.backends.dao import DaoAdapter

def test_fake_matches_real_adapter_signature():
    real = inspect.signature(DaoAdapter.update_data)
    fake = inspect.signature(FakeAdapter.update_data)
    assert list(real.parameters) == list(fake.parameters)
```

Cover every adapter method the facade calls. This test is the actual fix for
the systemic cause — fakes drifting from real signatures.

**Verify**: deliberately rename a fake param, watch the test fail, revert.
`uv run pytest tests/unit/ -q` → all pass.

## Test plan

- New file `tests/unit/test_facade_adapter_contract.py`: signature-conformance
  test (Step 5), where-kwarg regression tests (Step 1), relationship order/arity
  tests (Step 2).
- Extend existing facade tests (locate the file covering `DatabaseUseCases`
  write methods — likely `tests/unit/test_database*.py`): false-success → Err
  cases for all 11 sites (Step 3); readonly-gate cases (Step 4).
- Structural pattern to follow: `tests/unit/test_architecture_boundaries.py`
  (guard-style test) and the existing dao characterization tests from plan 014.

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0 (≥ baseline count; no deletions)
- [ ] `uv run ruff check .` exit 0; `uv run pyright src/ms_access_mcp/application/ src/ms_access_mcp/backends/` 0 errors
- [ ] `rg -n "where=where" src/ms_access_mcp/application/` → no matches
- [ ] Signature-conformance test exists and provably fails on drift
- [ ] Query-CRUD facades reject writes under readonly policy (tested)
- [ ] No MCP/CLI tool result-shape changes (`uv run python scripts/tool_budget.py` PASS)
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- An adapter method the facade calls does not exist on any real backend (the
  drift is worse than documented here) — stop, report the full Step-0 table.
- Fixing `delete_relationship` requires changing the facade's PUBLIC signature
  (CLI args / MCP params) — stop; that is an API decision for the operator.
- `application/protocols.py` types the adapter seam and the correct fix would
  require changing the protocol in a way that breaks `AccessAdapter` — stop and
  report the conflict.
- The existing unit suite is red before you start — stop.

## Maintenance notes

- Reviewers: scrutinize Step 3's `_write_failed` for adapters that return
  success dicts WITHOUT a `success` key (absence must mean success, matching
  today's behavior — only explicit `False` fails).
- Deferred (not this plan): `dao.py:141` `read_only=False` engine-level
  backstop — thread `self._policy.readonly` into `DaoAdapter.connect` in a
  follow-up; linked-table refresh paths may legitimately need write handles,
  so that change needs live-Access verification first. Also deferred:
  `query_data` non-SELECT rejection under readonly (verify DAO `OpenRecordset`
  action-query semantics on real Access before gating).
- Future cohort extractions (deferred `DatabaseUseCases` split, plans/README
  backlog) must carry these gates and the propagation helper with the methods
  they move.
