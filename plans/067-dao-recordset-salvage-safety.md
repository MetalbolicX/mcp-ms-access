# Plan 067: DAO recordset safety + salvage failure envelope (finally-close recordsets; never strand a fresh salvage DB)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/backends/dao.py src/ms_access_mcp/application/maintenance.py`
> On any change, compare "Current state" excerpts against live code.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW-MED (salvage is a recovery path — changes are failure-path only)
- **Depends on**: none (plan 065 recommended first but not required)
- **Category**: bug
- **Planned at**: commit `a7f2edb`, 2026-09-24

## Why this matters

DAO recordsets leak on every error path in `execute_query` and
`salvage_tables` (`rs.Close()` only on success branches): each failed query
or mid-salvage raise strands a COM Recordset on the STA thread — server-side
cursors and page locks accumulate on the shared long-lived Database handle
until restart. Worse, `salvage_database` never disconnects the fresh-DB
adapter and calls `salvage_tables` OUTSIDE any try: if it raises, the
original DB is already renamed to `.corrupt-<ts>`, `path` holds an EMPTY
database, and the caller gets a raw exception with no report — re-running
salvage then "salvages" the empty file while the real data sits in the first
`.corrupt-*` file. And because the fresh adapter is never disconnected, the
user's very next `open_database` on `path` fails with the lock error the
salvage was supposed to fix.

## Current state

- `src/ms_access_mcp/backends/dao.py:348-385` — `execute_query`'s `_do`
  closure (recordset opened at :351; `rs.Close()` only at :353 EOF branch and
  :371 success; the `except` at :378-385 returns without closing):
  ```python
  def _do() -> QueryResult:
      try:
          db = self._dispatcher.current_db
          rs = db.OpenRecordset(sql)
          if rs.EOF:
              rs.Close()
              return QueryResult(success=True, rows=[], count=0, columns=[])
          columns = [rs.Fields(i).Name for i in range(rs.Fields.Count)]
          results: list[dict] = []
          while not rs.EOF:
              row = {...}
              results.append(row)
              rs.MoveNext()
          rs.Close()
          return QueryResult(success=True, rows=results, count=len(results), columns=columns)
      except Exception as e:
          return QueryResult(success=False, rows=[], count=0, columns=[], error=str(e))
  ```
- `src/ms_access_mcp/backends/dao.py:896-935` — `salvage_tables` first
  recordset (`src_rs` opened :903, closed :909 happy path only), then a
  SECOND identical `OpenRecordset` at :920 (`src_rs2`) for field metadata,
  then a destination recordset later (~:973-989 `dst_rs`); any raise
  mid-table escapes to the per-table handler at :997 with recordsets open:
  ```python
  src_rs = corrupt_db.OpenRecordset(f"SELECT * FROM [{tname}]", 2)
  field_count = src_rs.Fields.Count
  rows: list[list[Any]] = []
  while not src_rs.EOF:
      rows.append([src_rs.Fields(i).Value for i in range(field_count)])
      src_rs.MoveNext()
  src_rs.Close()
  ...
  src_rs2 = corrupt_db.OpenRecordset(f"SELECT * FROM [{tname}]", 2)
  ```
- `src/ms_access_mcp/application/maintenance.py:582-600` —
  `salvage_database` tail:
  ```python
  fresh_adapter = BackendSelector.get_adapter(
      db_path=path,
      backend=cast(Literal["odbc", "com", "dao", "auto"] | None, "dao"),
      capabilities=DATA_READ_CAPS,
  )
  cast(Any, fresh_adapter).create_database(path, format=file_format)
  except Exception as e:
      return _err(f"Cannot create fresh database at {path}: {e}")

  # Salvage tables from corrupt DB using DAO backend seam
  table_reports = cast(Any, fresh_adapter).salvage_tables(corrupt_path, path)
  ```
  No `disconnect()` anywhere in the function; `salvage_tables` call is
  outside any try.
- Test exemplars: `tests/unit/test_maintenance_recovery.py` —
  `TestSalvageDatabase` at :201 with method-nested fakes (`_FakeDB`:220,
  `_FakeDBEngine`:225, `_FakeApp`:231, `_FakeDispatcher`:234, `_FakeRS`:299,
  `_FakeField`:268, `_FakeFields`:275). DAO tests also live in
  `tests/unit/test_boundary_serialization.py` and
  `test_architecture_boundaries` fixtures; `tests/integration/test_jet4_mdb.py`
  is the real-COM salvage test (local-only).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | ≤ baseline |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS |
| COM hygiene | `taskkill /F /IM MSACCESS.EXE` (ignore "not found") around any live check | — |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/dao.py` (`execute_query` + `salvage_tables` finally-close)
- `src/ms_access_mcp/application/maintenance.py` (`salvage_database` envelope + disconnect)
- `tests/unit/test_dao_recordset_safety.py` (create)
- `tests/unit/test_maintenance_recovery.py` (extend `TestSalvageDatabase`)

**Out of scope**:
- The double-scan in `salvage_tables` (PERF-04: reuse `src_rs.Fields`
  metadata instead of re-opening at :920) — tempting while in there, but it
  changes the happy path of a recovery routine; separate plan.
- Result-envelope typing (plan 071).
- Jet-3 bridge salvage variants.

## Git workflow

- Branch: `plan/067-dao-recordset-salvage-safety`
- RED → GREEN per step. Do NOT push.

## Steps

### Step 1: RED — execute_query closes the recordset on mid-loop failure

Create `tests/unit/test_dao_recordset_safety.py` using a fake dispatcher/db
shaped like `test_maintenance_recovery.py`'s method-nested fakes: a fake
recordset whose `MoveNext` raises `RuntimeError("COM boom")` after 2 rows and
which RECORDS `Close()` calls; drive `DaoAdapter.execute_query` and assert
the returned `QueryResult.success is False` AND `rs.close_calls == 1`.

**Verify**: `uv run pytest tests/unit/test_dao_recordset_safety.py -q` → FAILS
(close_calls == 0 — the leak).

### Step 2: GREEN — try/finally in `execute_query._do`

```python
rs = None
try:
    db = self._dispatcher.current_db
    rs = db.OpenRecordset(sql)
    ...
except Exception as e:
    return QueryResult(..., error=str(e))
finally:
    if rs is not None:
        try:
            rs.Close()
        except Exception:
            pass  # best-effort: recordset may already be dead
```

(Keep the early `rs.Close()`+return EOF branch or let finally own it —
either is fine; do not double-close a fake that explodes on second Close —
guard the fake accordingly, the finally pattern must tolerate
already-closed.)

**Verify**: Step-1 test passes; `uv run pytest tests/unit/ -q` → all pass
(the boundary-serialization characterization tests pin execute_query's
success shapes — they must stay green unmodified).

### Step 3: RED — salvage_tables closes all opened recordsets on raise

Extend the new test file with a salvage-shaped fake (reuse
`_FakeRS`-style objects from `test_maintenance_recovery.py:299` as the
pattern): fake `corrupt_db` whose second table's `OpenRecordset` returns a
recordset that raises on first `Fields(i).Value` read; assert every opened
recordset got `Close()` called (track opens/closes on the fakes) even though
the per-table error is reported in `table_reports`.

**Verify**: new test FAILS (src_rs2 left open on the raising table).

### Step 4: GREEN — finally-close each recordset in `salvage_tables`

Wrap `src_rs`, `src_rs2`, and `dst_rs` each in the
`rs = None; try: ... finally: close-if-not-None` pattern (same helper shape
as Step 2 — a tiny local `_close_rs(rs)` module function in dao.py is
acceptable and keeps the diff small).

**Verify**: Step-3 test passes; existing salvage unit tests
(`TestSalvageDatabase`) pass.

### Step 5: RED — salvage_database disconnects the fresh adapter and wraps salvage_tables

Extend `TestSalvageDatabase` (test_maintenance_recovery.py:201) with two
tests:
1. Failure envelope: fake adapter whose `salvage_tables` raises → the use
   case must return an Err (not propagate) whose message INCLUDES the
   renamed corrupt path (inspect how the rename is performed above :582 —
   likely a `.corrupt-<ts>` sibling — and surface that path in the error
   data so the user can find their data).
2. Disconnect: assert `fresh_adapter.disconnect()` was called on BOTH the
   success and failure paths (track calls on the fake).

**Verify**: both tests FAIL (no disconnect, no envelope).

### Step 6: GREEN — try/finally + envelope in maintenance.py

```python
fresh_adapter = ...  # unchanged construction
try:
    cast(Any, fresh_adapter).create_database(path, format=file_format)
    try:
        table_reports = cast(Any, fresh_adapter).salvage_tables(corrupt_path, path)
    except Exception as e:
        return _err(
            f"Salvage failed after the corrupt database was renamed to {corrupt_path!r} "
            f"and an empty database was created at {path!r}: {e}. "
            "Your data is in the .corrupt file — do not re-run salvage against the empty DB."
        )
    ...  # existing report assembly
finally:
    try:
        fresh_adapter.disconnect()
    except Exception:
        pass  # best-effort teardown
```

(Adapt to the function's actual structure/excerpts; keep the existing
"Cannot create fresh database" error branch.)

**Verify**: Step-5 tests pass; full unit suite passes.

### Step 7: Full gates

**Verify**: ruff / pyright / unit / tool_budget green; arch guard green.

## Test plan

- New: recordset-close on query failure (1), salvage recordset-close on
  raise (1), salvage envelope + renamed-path surfacing (1), salvage
  disconnect success+failure (2) — 5 tests.
- Pattern source: method-nested fakes in `test_maintenance_recovery.py`.
- All existing characterization/boundary tests must stay green unmodified.

## Done criteria

- [ ] `execute_query` closes its recordset on every path
- [ ] `salvage_tables` closes src/src2/dst recordsets on every path
- [ ] `salvage_database` always disconnects the fresh adapter (finally)
- [ ] `salvage_tables` failure returns an Err naming the `.corrupt-*` path
- [ ] All gates green; `plans/README.md` row updated

## STOP conditions

- Drift at cited lines (esp. if plan 065 already touched dao.py connect
  paths — the recordset code should be unaffected).
- Any evidence the finally-Close breaks the boundary-serialization pins
  (double-close semantics) — report rather than weakening the pins.
- Salvage's rename mechanics differ from the excerpt (e.g. no `.corrupt-<ts>`
  sibling) — adapt the envelope message to reality and note it.
- Verification fails twice.

## Maintenance notes

- PERF-04 (double scan) is the natural next plan for `salvage_tables` —
  these finally-closes make that refactor safer; sequence it after this.
- The empty-DB-after-failure state is now at least REPORTED; a follow-up
  could refuse salvage if `path` looks like a prior salvage's empty output
  (marker in the report) — deferred.
- Integration coverage for salvage lives only in local-only
  `test_jet4_mdb.py` — run it manually when this plan lands (operator gate).
