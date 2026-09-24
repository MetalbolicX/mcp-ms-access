# Plan 059: Route the maintenance salvage through the DAO backend seam — no raw win32com in `application/`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 079d756..HEAD -- src/ms_access_mcp/application/maintenance.py src/ms_access_mcp/backends/dao.py tests/unit/test_architecture_boundaries.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (touches a crash-recovery code path; semantics must be preserved exactly)
- **Mode**: Strict TDD (architecture-guard RED first, salvage contract RED→GREEN)
- **Depends on**: none
- **Category**: tech-debt (architecture)
- **Planned at**: commit `079d756`, 2026-09-20

## Why this matters

The architecture is hexagonal with an AST guard
(`tests/unit/test_architecture_boundaries.py`) enforcing layer direction —
but `application/maintenance.py` bypasses the backend layer with a raw
`win32com.client.Dispatch("DAO.DBEngine.120")` inside a nested class,
verified at lines 608-610. The same function already constructs a proper
backend via `BackendSelector.get_adapter(..., backend="dao")` at `:588`, so
the abstraction exists and is simply not used for the salvage walk. Cost:
the salvage path is untestable without real COM, invisible to the guard,
and duplicates engine-lifecycle concerns the DAO backend owns.

## Current state

- `src/ms_access_mcp/application/maintenance.py` — maintenance use cases
  (640 lines). The salvage implementation (verified verbatim at `079d756`):

  ```python
  588:    ... BackendSelector.get_adapter(... backend="dao") ...   # proper seam exists
  600: class _Do:
  601:     """Callable that runs the salvage logic on a provided DAO engine."""
  ...
  607:     def __call__(self, engine: Any = None) -> list[dict[str, Any]]:
  608:         import win32com.client
  609:         if engine is None:
  610:             engine = win32com.client.Dispatch("DAO.DBEngine.120")
  ...
  617:         corrupt_db = engine.OpenDatabase(corrupt_path)
  623:         fresh_db = engine.OpenDatabase(path)
  ...
  635:             src_rs = corrupt_db.OpenRecordset(f"SELECT * FROM [{tname}]", 2)
  ```

  `_Do` is defined INSIDE the calling function (nested class), making it
  doubly hard to test/replace. The `engine` parameter looks like an
  injection seam but the default branch defeats it — no caller passes a
  non-None engine.
- `src/ms_access_mcp/backends/dao.py` — the DAO adapter (720 lines); the
  sanctioned owner of `DAO.DBEngine.120` usage (its own `Dispatch` at
  `:71`).
- `tests/unit/test_architecture_boundaries.py` — AST layer-direction +
  private-import guard (5 tests, from plan 014). It does NOT currently
  forbid `win32com` imports in `application/**` — that's the gap this plan
  closes first (RED).
- Known sibling: `doctor.py` also calls `Dispatch` (root-level diagnostics
  module, `:73/:108/:146/:214`) — explicitly OUT of scope (see below).
- Test exemplar for the recovery path: `tests/unit/test_maintenance_recovery.py`
  and the salvage coverage inside `tests/unit/test_application_maintenance.py`
  (locate with `rg -ln "salvage" tests/unit/`).

## Commands you will need

| Purpose   | Command                  | Expected on success |
|-----------|--------------------------|---------------------|
| Install   | `uv sync --extra dev`    | exit 0              |
| Lint      | `uv run ruff check .`    | exit 0              |
| Typecheck | `uv run pyright src/ms_access_mcp/application/maintenance.py src/ms_access_mcp/backends/dao.py` | 0 errors |
| Unit tests| `uv run pytest tests/unit/ -q` | all pass      |
| Arch guard| `uv run pytest tests/unit/test_architecture_boundaries.py -q` | all pass |

## Scope

**In scope** (the only files you should modify):
- `tests/unit/test_architecture_boundaries.py` (extend)
- `src/ms_access_mcp/backends/dao.py` (add salvage method)
- `src/ms_access_mcp/application/maintenance.py` (call the seam; delete `_Do`)
- Salvage tests (new class in the existing maintenance test module)

**Out of scope** (do NOT touch):
- `src/ms_access_mcp/doctor.py` — root-level diagnostics module, NOT part of
  the `application/` layer; its direct COM probes are a separate (deliberate)
  pattern. The new architecture rule must be scoped to `application/**` only.
- `backends/dispatcher.py` STA semantics, `convert_legacy_database`, PID-kill
  helpers (plan 031 owns the kill shape).
- Any behavioral change to salvage results (table list, skip set, error
  strings).

## Git workflow

- Branch: `plan/059-maintenance-salvage-seam`
- Conventional commits: `test(arch): RED — forbid win32com in application/**`
  → `feat(dao): salvage_tables backend method` →
  `refactor(maintenance): route salvage through DAO backend`. No AI
  attribution. Do not push.

## Steps

### Step 1: RED — extend the architecture guard

Add a test (or assertion block) to
`tests/unit/test_architecture_boundaries.py`: walk the AST of every module
under `src/ms_access_mcp/application/` and fail if any `import win32com`
/ `from win32com import ...` node exists at ANY nesting level (function
bodies included — that's where `:608` hides).

**Verify**: `uv run pytest tests/unit/test_architecture_boundaries.py -q`
→ NEW test fails, naming `maintenance.py`. Quote the failure.

### Step 2: RED — salvage contract test with injected engine

In the existing maintenance test module, add a salvage test using a fake
engine object (record `OpenDatabase` calls per path; return fake
`TableDefs`/`OpenRecordset` objects; one healthy table + one unreadable
table). Assert:

- returns per-table results in the same shape as today,
- the unreadable table is skipped/salvaged-best-effort exactly as the
  current `_Do` does (read its loop at `:629-640` and pin that behavior).

This test FAILS today: the production code accepts `engine` but nothing
threads a fake through the public use-case entry point.

**Verify**: new test fails (salvage entry point takes no engine / calls real
Dispatch).

### Step 3: GREEN — move the salvage into `backends/dao.py`

Add to the DAO adapter a method (suggested shape — match the adapter's
existing `_do()` dispatcher idiom):

```python
def salvage_tables(self, corrupt_path: str, path: str, engine: Any = None) -> list[dict[str, Any]]:
```

Move the `_Do.__call__` body (maintenance.py `:607-640`) into it VERBATIM
(loop structure, skip semantics, recordset flags, error strings). The
`engine=None` default may keep its internal `Dispatch` — this is the
backends layer, where COM belongs. Update maintenance.py to obtain the DAO
adapter (it already does at `:588`) and call `adapter.salvage_tables(...)`;
delete the nested `_Do` class.

**Verify**: Step 1 + Step 2 tests now pass;
`uv run pytest tests/unit/ -q` → all pass.

### Step 4: Wire the injection seam for tests

Ensure the use-case entry point exposes the engine override (e.g. optional
`engine: Any = None` kwarg passed through to `salvage_tables`) OR the test
constructs the adapter and calls the backend method directly — whichever
matches the existing maintenance test patterns. No public MCP/CLI surface
change: the kwarg is test-only plumbing on internal seams.

**Verify**: `uv run pytest tests/unit/ -q` → all pass.

### Step 5: Gate re-verification

**Verify**: `uv run ruff check .` → exit 0; pyright on both changed files →
0 errors; `rg -n "win32com" src/ms_access_mcp/application/` → 0 matches.

## Test plan

- Architecture guard extension (Step 1): `application/**` bans win32com at
  any nesting level — includes a canary assertion that would have caught
  `:608`.
- Salvage contract (Step 2): fake engine, healthy + unreadable tables,
  result shape pinned to current behavior.
- Regression: full unit suite + existing
  `test_maintenance_recovery.py` green unmodified.
- Verification: commands table above, all green.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `rg -n "win32com" src/ms_access_mcp/application/` → no matches
- [ ] `rg -n "class _Do" src/ms_access_mcp/application/maintenance.py` → no matches
- [ ] `uv run pytest tests/unit/ -q` exits 0 (incl. new guard + salvage tests)
- [ ] `uv run ruff check .` exits 0; pyright on changed files 0 errors
- [ ] No public MCP tool / CLI surface change (`scripts/tool_budget.py` not
      required — no tool count change — but running it is cheap insurance)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The salvage body at `:600-640` doesn't match the excerpt (drift — e.g.
  crash-resilience plan 047 touched it).
- Moving the code requires touching `dispatcher.py` (STA) semantics — the
  salvage engine is used OFF the STA dispatcher today; if the DAO adapter's
  `_do()` idiom would force it onto the STA thread, keep the direct-engine
  path and report for a dispatcher decision.
- Existing recovery tests pin `maintenance.py` internals (`_Do` name,
  nested location) — refresh pins per the plan-035 lesson ONLY for the
  relocation, never for behavior.

## Maintenance notes

- The guard added in Step 1 permanently protects the seam — future raw-COM
  additions to `application/` will fail CI.
- `doctor.py`'s direct COM usage remains allowed by scope; if the operator
  later wants it behind the backend seam too, that's a separate plan
  (diagnostics ≠ use cases).
- Reviewer: diff should be a pure MOVE of `:607-640` plus call-site +
  guard changes; any logic edit inside the moved block is a red flag.
