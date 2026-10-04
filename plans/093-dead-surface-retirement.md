# Plan 093: Retire dead surfaces (duplicate backend selector, AdoAdapter, dead trusted-locations opt-in)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/capabilities.py src/ms_access_mcp/backends/ado.py src/ms_access_mcp/backends/access/trusted_locations.py src/ms_access_mcp/backends/trusted_locations.py src/ms_access_mcp/backends/dao.py`
> Plans 080–092 may have touched `dao.py` — expected; this plan's `dao.py`
> item is only the test-fake compat shim.

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: MED (external-import compatibility) / LOW (internal)
- **Depends on**: plans/080-*.md, plans/081-*.md (contracts settled first)
- **Category**: tech-debt
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

Three surfaces are production-dead but still cost maintenance and invite
wrong fixes:

1. `src/ms_access_mcp/capabilities.py` duplicates the live backend
   selector with DIVERGENT behavior — its "win32com importable" probe has
   an empty try-body (the import never happens), and its
   `BackendCapabilities` names `ado`/`access` where the live selector says
   `com`. Zero production importers; only two test files pin its behavior.
   A future fix applied to the wrong copy silently changes nothing.
2. `AdoAdapter` is never constructed in production
   (`BackendSelector` yields only dao/com/odbc), and its `Execute(sql,
   vals)` passes parameters in the RecordsAffected slot while fabricating
   `affected: 1` — the deferred DEBT-06 finding, now proven unreachable.
   Keeping a wrong-by-construction adapter on an importable path is a
   trap.
3. The trusted-locations "preserve" opt-in is dead: the flag is hardcoded
   False, `ACCESS_MCP_PRESERVE_TRUSTED_LOCATIONS` is read by NOTHING
   (docstring mentions only), and the capture/restore code it would enable
   has a real defect (merged HKLM+HKCU restore — audit finding S4). Dead
   opt-in code with a latent bug is worse than no opt-in.

## Current state

- `src/ms_access_mcp/capabilities.py` — duplicate selector + capabilities;
  empty try-body at ~lines 96–102 (`except Exception` is unreachable
  decoration); `ado`/`access` value sets at ~line 106. Importers:
  `tests/unit/test_capabilities.py:8`,
  `tests/unit/test_exception_unification.py:3` only. The live selector:
  `src/ms_access_mcp/backends/__init__.py` `BackendSelector`
  (~lines 101–108 hold the REAL win32com probe).
- `src/ms_access_mcp/backends/ado.py` — 166 LOC; zero production
  constructions (`BackendSelector` imports Dao/Access/Odbc only,
  `backends/__init__.py:170-189`); `Execute` RecordsAffected-slot defect
  at ~lines 137–162. Test importers exist (`test_ado_adapter.py`,
  `test_backends_access.py`, `test_migration_connectors.py` per audit).
  Public import path `ms_access_mcp.backends.ado` — EXTERNAL users cannot
  be ruled out from inside the repo.
- `src/ms_access_mcp/backends/access/trusted_locations.py` —
  `_TRUSTED_LOCATIONS_PRESERVE = False` hardcoded (~lines 16–33); env var
  never read anywhere (audit grep). `src/ms_access_mcp/backends/
  trusted_locations.py` — capture merges HKLM+HKCU (~lines 70–73), restore
  writes the merged list to BOTH hives (~lines 119–124), no stale-key
  deletion; `write_location` swallows all exceptions (~line 118). Wrapper
  is invoked from `__init__.py:34,263` and is therefore a permanent
  passthrough.
- `src/ms_access_mcp/backends/dao.py` — `_do(engine=engine)` compat shim
  (~lines 884–887) whose comment says it exists for test fakes that call
  `_do()` bare; plan 080 made the shared fakes kwargs-correct, so the shim
  may be tighten-able.
- Architecture guard: `tests/unit/test_architecture_boundaries.py` lists
  `capabilities` in the KERNEL tuple (~line 25) — must be updated in the
  same commit as the deletion.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Guard | `uv run pytest tests/unit/test_architecture_boundaries.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Dead-code greps | see Done criteria | no matches |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- DELETE: `src/ms_access_mcp/capabilities.py`, `src/ms_access_mcp/backends/ado.py`
- `src/ms_access_mcp/backends/access/trusted_locations.py` (reduce to an honest no-op wrapper) and, if it becomes unreferenced, `src/ms_access_mcp/backends/trusted_locations.py`
- `src/ms_access_mcp/backends/dao.py` (shim tightening only)
- `tests/unit/test_capabilities.py`, `tests/unit/test_exception_unification.py`,
  `tests/unit/test_ado_adapter.py`, `tests/unit/test_trusted_locations.py`,
  `tests/unit/test_architecture_boundaries.py` (guard tuple)
- `README.md` (only if it mentions `ado` as a backend value)

**Out of scope**:
- Any live backend behavior change (dao/com/odbc untouched).
- Implementing a CORRECT trusted-locations preserve feature (that is a
  future opt-in decision; the cross-hive defect is recorded in the ledger
  as the precondition).
- `pyproject.toml` backend value lists that gate CLI validation
  (`_VALID_BACKEND_VALUES` — check and align only if `ado` appears).

## Git workflow

- Branch: `plan/093-dead-surface-retirement` from `main` (after 080/081).
- Conventional commits: `refactor: retire dead capabilities selector and AdoAdapter`.

## Steps

### Step 0: External-compatibility check (STOP gate)

Search for out-of-repo consumers before deleting public import paths:
`grep -rn "backends.ado\|capabilities" README.md docs/ openspec/ scripts/`
and confirm `pip show`/distribution metadata exposes no documented ADO
usage. If README or docs instruct users to import either module, STOP and
report — deletion then needs a deprecation cycle instead.

### Step 1: Retire `capabilities.py`

- Delete `src/ms_access_mcp/capabilities.py`.
- Move any test case worth keeping (e.g. backend-value validation) onto
  the LIVE `BackendSelector` in `tests/unit/test_backends_protocols.py`
  or the existing backend selection tests; delete the rest.
- Fix `tests/unit/test_exception_unification.py:3`'s import (it used the
  dead module only as an import surface — retarget to `backends`).
- Update the guard's KERNEL tuple in
  `tests/unit/test_architecture_boundaries.py:25`.

**Verify**: `uv run pytest tests/unit/test_architecture_boundaries.py tests/unit/test_backends_protocols.py -q` → pass; `grep -rn "from ..capabilities\|import capabilities" src/` → no hits.

### Step 2: Retire `AdoAdapter`

- Delete `src/ms_access_mcp/backends/ado.py` and `tests/unit/test_ado_adapter.py`.
- Sweep remaining references: `grep -rn "ado" src/ tests/ scripts/` —
  keep only unrelated hits (e.g. `dialog` contains "alog"); align
  `_VALID_BACKEND_VALUES` / doctor text / README if `ado` appears as a
  user-facing value (it was never selectable in production — removing it
  from docs is a correction, not a break).
- Note in the commit message: the DEBT-06 RecordsAffected defect is
  retired WITH the module; external importers (if any surface later) get
  the git history as reference.

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `grep -rn "AdoAdapter" src/ tests/` → no hits.

### Step 3: Reduce the trusted-locations opt-in to an honest no-op

- In `src/ms_access_mcp/backends/access/trusted_locations.py`: delete the
  dead flag + env-var plumbing; the wrapper becomes a documented
  passthrough ("no registry mutation is performed; the preserve opt-in
  was never enabled — see plans ledger"). Fix the module docstring's
  wrong "PowerShell" reference (`backends/trusted_locations.py:14-16`).
- If `src/ms_access_mcp/backends/trusted_locations.py` becomes
  unreferenced (check `__init__.py:34,263` callers), delete it and its
  tests too; otherwise keep it with a module-level comment recording the
  cross-hive restore defect as the reason it stays disabled.
- Refresh `tests/unit/test_trusted_locations.py` accordingly (its
  both-hive assertions document disabled behavior — delete or convert to
  a no-op pin with rationale).

**Verify**: `uv run pytest tests/unit/test_trusted_locations.py tests/unit/test_backends_access.py -q` → pass.

### Step 4: Tighten the DAO test-fake shim (conditional)

With plan 080's shared kwargs-forwarding fakes landed, try removing the
`engine=` default from `dao.py`'s `_do` shim (~lines 884–887) so kwargs
misuse fails loudly. If any remaining fake still calls `_do()` bare, fix
THAT fake (test-side) rather than keeping the shim.

**Verify**: `uv run pytest tests/unit/test_backend_dao.py tests/unit/test_dao_sql.py tests/unit/test_dao_recordset_safety.py -q` → pass.

### Step 5: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0; `uv run ruff check .` → 0; `uv run python scripts/tool_budget.py` → exit 0.

## Test plan

- Deletion-driven: guard tuple update, retargeted imports, swept greps.
- No new production tests; retirement plans are verified by absence
  (greps in Done criteria) plus the full suite.

## Done criteria

- [ ] `test -f src/ms_access_mcp/capabilities.py` → file gone; guard green
- [ ] `grep -rn "AdoAdapter" src/ tests/ scripts/` → no hits
- [ ] Trusted-locations wrapper is an honest documented no-op (or module deleted with callers cleaned)
- [ ] `grep -n "engine: .* = engine" src/ms_access_mcp/backends/dao.py` → no shim default remains (or the remaining fake fix is documented)
- [ ] Unit suite + qualify + budget green; `plans/README.md` row updated

## STOP conditions

- Step 0 finds any documented external consumer of `capabilities` or
  `backends.ado` — report; retirement becomes a deprecation cycle.
- Any live selection path turns out to reference `ado` after the sweep
  (doctor, CLI validation, docs) — report the list before deleting.
- If removing the trusted-locations modules breaks the access package's
  import graph (circular or guard violation), keep the honest-no-op form
  and record why.

## Maintenance notes

- If a preserve-trusted-locations feature is ever requested, the ledger's
  S4 finding (hive-provenance restore) is the mandatory precondition —
  the audit record in `plans/README.md` points at it.
- ADO support, if ever wanted for real, should be built against the
  current adapter protocol family, not resurrected from git history with
  the RecordsAffected-slot defect.
