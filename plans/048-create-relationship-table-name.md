# Plan 048: Pass the real source_table to create_relationship — fix empty Relation.Table

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 655f712..HEAD -- src/ms_access_mcp/application/database.py src/ms_access_mcp/backends/dao.py tests/unit/test_facade_adapter_contract.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1 (data bug on a registered write tool)
- **Effort**: S
- **Risk**: MED (changes the argument a live write path sends to DAO)
- **Depends on**: none (branch from `plan/047-crash-resilience-recovery` tip
  `655f712`, or from `main` after 047 merges — both trees contain the bug;
  it predates plan 047, blame `cbae99c` / plan 042)
- **Category**: bug
- **Planned at**: commit `655f712` (plan-047 branch tip), 2026-09-16

## Why this matters

The facade `DatabaseUseCases.create_relationship` accepts `source_table`
but sends an **empty string** to the backend as the table name. The DAO
backend builds a `Relation` whose `Table` property is therefore `""` —
every relationship created through the facade or the MCP
`create_relationship` tool (ddl profile) is malformed or rejected by ACE.
The MCP layer passes `source_table` correctly, so the bug is invisible to
callers and silently corrupts the one argument that anchors the relation to
a real table. Worse, an existing unit test **pins the bug as the contract**
(`args == ("", ...)`), so the fix must also refresh that pin — the same
class of decision recorded for plan 035 ("tests pinning a known bug are not
in scope — update them").

## Current state

- `src/ms_access_mcp/application/database.py:842-879` — the facade method.
  Signature: `(relationship_name, source_table, source_column, dest_table,
  dest_column, connection_name="default", confirm=False)`. The bug is at
  line 874:
  ```python
  result = adapter.create_relationship("", relationship_name, [source_column], dest_table, [dest_column])
  ```
  Line 845 carries a suppression that exists ONLY because of this bug:
  ```python
  source_table: str,  # noqa: ARG002 - facade parity; adapter resolves via column list
  ```
  The comment is wrong — the adapter does NOT resolve the table from the
  column list; it receives `table_name` as its first positional.
- Backend contract — `src/ms_access_mcp/backends/dao.py:279-286` and
  `backends/schema_inspector.py:557-564`:
  ```python
  def create_relationship(self, table_name: str, relationship_name: str,
                          columns: list[str], foreign_table: str,
                          foreign_columns: list[str]) -> RelationshipResult:
  ```
  The DAO implementation builds the Relation from `table_name`; an empty
  string yields `Relation.Table == ""`.
- ODBC backend (`backends/odbc.py:386-392`) raises
  `BackendCapabilityMismatchError` for relationships by design — unaffected.
- MCP tool `src/ms_access_mcp/mcp_tools/ddl.py:81-98` — passes
  `source_table` correctly by keyword; nothing to change there.
- **The pin to refresh** — `tests/unit/test_facade_adapter_contract.py:179-186`:
  ```python
  def test_create_relationship_five_args(self):
      """Facade calls adapter.create_relationship(table_name, rel_name, [col], ft, [fcol])."""
      spy = SpyAdapter()
      db = DatabaseUseCases(readonly=False)
      db._connections["default"] = spy
      db.create_relationship("FK_Users", "Users", "id", "Orders", "user_id", confirm=True)
      _, args, kwargs = spy._calls[-1]
      assert args == ("", "FK_Users", ["id"], "Orders", ["user_id"])
  ```
  The `SpyAdapter.create_relationship` at :90-92 records
  `(table_name, relationship_name, columns, foreign_table, foreign_columns)`.
-   **Out-of-scope asymmetry, do not "fix" it**: the facade
  `delete_relationship` (database.py:881+) sends `""` as `table_name` too,
  but the backend ignores it — `schema_inspector.py:592` deletes by
  `db.Relations.Delete(relationship_name)` alone (relation names are unique
  per database). Harmless; leave as is.
- **RED step convention (locked by reviewer)**: modify the existing test
  in place — do not snapshot the old pin to a separate file. The git
  history itself is the baseline (one commit for RED, one for GREEN, one
  for the `# noqa` cleanup); no `xfail` artifact needed.
- Repo conventions: Result envelope (`_ok`/`_err`), strict TDD RED→GREEN,
  conventional commits, branch `plan/048-create-relationship-table-name`.

## Commands you will need

| Purpose     | Command                                          | Expected on success |
|-------------|--------------------------------------------------|---------------------|
| Lint        | `uv run ruff check .`                            | exit 0              |
| Typecheck   | `uv run pyright src/ms_access_mcp/`              | 0 errors            |
| Unit tests  | `uv run pytest tests/unit/ -q`                   | all pass            |
| Targeted    | `uv run pytest tests/unit/test_facade_adapter_contract.py -q` | all pass |
| Budget      | `uv run python scripts/tool_budget.py`           | PASS                |

## Scope

**In scope** (the only files you should modify):

- `src/ms_access_mcp/application/database.py` — line 874 call + line 845
  noqa removal
- `tests/unit/test_facade_adapter_contract.py` — refresh
  `test_create_relationship_five_args` pin
- `tests/integration/test_relationship_roundtrip.py` (create — see Step 3)

**Out of scope** (do NOT touch):

- `src/ms_access_mcp/backends/**` — the backend contract is correct; the
  facade is the defect.
- `src/ms_access_mcp/mcp_tools/ddl.py` — already correct.
- Facade `delete_relationship` `""` table_name — backend ignores it
  (verified `schema_inspector.py:592`); changing it would break the
  kwarg-pin test for zero behavioral gain.
- Any other write method in `database.py`.

## Git workflow

- Branch: `plan/048-create-relationship-table-name` (from plan-047 tip
  `655f712` or `main` post-merge).
- Three commits (one per step):
  1. `test(database): pin source_table passthrough in create_relationship`
     (RED: refresh the existing pin; target test FAILS)
  2. `fix(database): pass source_table to adapter in create_relationship`
     (GREEN: facade + remove stale noqa; target test PASSES)
  3. `test(integration): probe-gated DAO relationship roundtrip`
     (Step 3: probe-gated integration test that auto-activates where DAO
     COM is available and skips cleanly elsewhere)
- Do NOT push unless instructed.

## Steps

### Step 1: RED — refresh the pin

Edit `tests/unit/test_facade_adapter_contract.py:179-186`: change the
asserted tuple to the CORRECT contract and fix the example to make the
distinction visible:

```python
def test_create_relationship_five_args(self):
    """Facade calls adapter.create_relationship(source_table, rel_name, [col], ft, [fcol])."""
    spy = SpyAdapter()
    db = DatabaseUseCases(readonly=False)
    db._connections["default"] = spy
    db.create_relationship("FK_Users", "Users", "id", "Orders", "user_id", confirm=True)
    _, args, kwargs = spy._calls[-1]
    assert args == ("Users", "FK_Users", ["id"], "Orders", ["user_id"])
```

**Verify**: `uv run pytest tests/unit/test_facade_adapter_contract.py::TestSignatureConformance::test_create_relationship_five_args -q`
→ **FAILS** with `assert ("", "FK_Users", ...) == ("Users", "FK_Users", ...)`.
(If the class name differs, locate the test by name; quote the RED output
in your report.)

### Step 2: GREEN — one-line fix + noqa removal

In `src/ms_access_mcp/application/database.py`:

- Line 874 →
  `result = adapter.create_relationship(source_table, relationship_name, [source_column], dest_table, [dest_column])`
- Line 845 → remove the `# noqa: ARG002 ...` comment (the argument is now
  used; ruff will flag a stale noqa with Ruf100 if left).

**Verify**:
- `uv run pytest tests/unit/test_facade_adapter_contract.py -q` → all pass
- `uv run ruff check .` → exit 0

### Step 3: Integration probe (probe-gated — pattern from plan 027/028)

Create `tests/integration/test_relationship_roundtrip.py` using the same
probe-gated pattern as `tests/integration/test_export_xlsx.py` (see
plan-027/028 precedent): the test auto-activates on machines where the DAO
COM provider + Access are available, and skips cleanly elsewhere.

Structure:

```python
"""DAO relationship roundtrip — proves source_table reaches the DAO layer.

PROBE: DAO COM provider reachable (pywin32 + Access OR direct ACE).
When unavailable, the test skips cleanly. When available, it asserts the
relation created via DatabaseUseCases reports the real source table, not "".
"""
from __future__ import annotations

import os
import pytest

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.com_integration,
]


def _dao_relation_available() -> bool:
    """Return True if DAO relationship creation is reachable on this host."""
    if os.name != "nt":
        return False
    try:
        pytest.importorskip("win32com")
    except pytest.skip.Exception:
        return False
    try:
        import win32com.client  # type: ignore[import-not-found]

        db = win32com.client.Dispatch("DAO.DBEngine.36").CreateDatabase(
            "", "", ";LANGID=0x0409;CP=1252;COUNTRY=0"
        )
        rel = db.CreateRelation("FK_Probe", "TableA", "TableB")
        rel.Fields.Append(rel.CreateField("a_id"))
        rel.Fields[0].ForeignName = "b_id"
        db.Relations.Append(rel)
        created = db.Relations["FK_Probe"].Table
        db.Close()
        return created == "TableA"
    except Exception:
        return False


class TestRelationshipRoundtrip:
    @pytest.mark.skipif(
        not _dao_relation_available(),
        reason="DAO relationship COM roundtrip not reachable on this host",
    )
    def test_relation_source_table_roundtrip(self, temp_db_copy: str) -> None:
        from ms_access_mcp.application.database import DatabaseUseCases

        # Discover real table/column names via get_tables + get_table_schema.
        # (Fixture has zero pre-existing relationships — plan 026 evidence.)
        with DatabaseUseCases(
            readonly=False, allowed_dirs=[str(Path(temp_db_copy).parent)]
        ) as db:
            tables = db.get_tables()
            assert len(tables) >= 2, "fixture needs >=2 tables for FK probe"
            src_table, src_col = tables[0]["name"], tables[0]["columns"][0]["name"]
            dst_table, dst_col = tables[1]["name"], tables[1]["columns"][0]["name"]

            rel_name = "FK_Probe_RT"
            result = db.create_relationship(
                rel_name, src_table, src_col, dst_table, dst_col, confirm=True
            )
            assert result.success, f"create_relationship failed: {result.error}"

            rels = db.get_relationships()
            hit = next((r for r in rels if r["name"] == rel_name), None)
            assert hit is not None, f"relation {rel_name} not visible"
            assert hit["table"] == src_table, (
                f"BUG REGRESSED: relation.Table == {hit['table']!r}, "
                f"expected {src_table!r}"
            )
            assert hit["table"] != "", "BUG REGRESSED: relation.Table is empty"
```

**Verify**:
- `uv run pytest tests/integration/test_relationship_roundtrip.py -q` →
  passes on Windows+ACE (DAO COM probe succeeds), skips cleanly elsewhere
  (probe returns False). On hosts where the probe fails the test MUST
  still `pytest.exit(0)` via skip, not error.
- Cross-check: with the Step 2 fix reverted (facade passes `""` again), the
  roundtrip's final assertion `hit["table"] == src_table` MUST fail — that
  is the integration-level proof the bug was real. (Optional; do not run
  on this Windows host by default. Note in the DONE summary whether you
  cross-checked or skipped it.)

## Test plan

- Refreshed pin (Step 1) is the regression test: positional passthrough of
  `source_table`.
- Existing coverage that must stay green untouched:
  `test_create_relationship_false_success` (:257-260),
  `test_create_relationship_readonly` (:326-329) — both use positional args
  that remain valid.
- Verification: full unit suite + targeted file, ruff, pyright, budget.

## Done criteria

- [ ] `uv run pytest tests/unit/test_facade_adapter_contract.py -q` all pass
      with the refreshed `("Users", ...)` pin
- [ ] `uv run pytest tests/unit/ -q` all pass
- [ ] `uv run pytest tests/integration/test_relationship_roundtrip.py -q`
      passes on hosts where DAO COM is reachable, skips cleanly elsewhere
      (probe returns False → skip, no error)
- [ ] `uv run ruff check .` exit 0 (no stale noqa)
- [ ] `uv run pyright src/ms_access_mcp/` 0 errors
- [ ] `uv run python scripts/tool_budget.py` PASS
- [ ] `rg -n 'create_relationship\(""' src/ms_access_mcp/` returns no matches
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` row 048 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The excerpts above don't match live code (drift since `655f712`).
- The RED test in Step 1 does NOT fail (the bug was fixed independently —
  re-verify and close the plan as DONE-by-other).
- The integration probe (Step 3) reveals the DAO backend ALSO needs
  `foreign_table` semantics beyond what the facade sends — that is a new
  finding, not this plan's scope.
- A verification fails twice after a reasonable fix attempt.

## Maintenance notes

- After this lands, `create_relationship` is the exemplar for auditing the
  remaining 12 write facades for silent argument drops — the audit already
  deferred that sweep; do not expand this plan into it.
- A reviewer should scrutinize exactly two lines: the :874 call and the
  refreshed pin. Everything else is ceremony.
