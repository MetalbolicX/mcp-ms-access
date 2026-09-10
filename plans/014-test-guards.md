# Plan 014: Strengthen architecture guard and close doctor/dao test gaps

> **Executor instructions**: Follow this plan step by step. This plan is
> test-only: it adds characterization and guard tests WITHOUT changing
> production behavior. If a new test FAILS against current production code,
> that is a discovered bug — stop and report it, do not "fix" production or
> weaken the test. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2178509..HEAD -- tests/unit/test_architecture_boundaries.py src/ms_access_mcp/doctor.py src/ms_access_mcp/backends/dao.py`
> On any mismatch with "Current state", treat as STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none (land AFTER plan 012 if both are queued — the
  architecture guard in Step 1 would flag the `_allowed_dirs_from_env`
  private import that 012 removes; write the guard to match the POST-012
  state, or land 012 first)
- **Category**: tests
- **Planned at**: commit `2178509`, 2026-09-09

## Execution mode

**Direct edit (test-only).** Characterization tests for existing behavior —
there is no RED→GREEN loop because production code does not change. SDD is
not warranted. The one TDD-flavored element: the architecture guard is
written to FAIL if a forbidden edge exists, so its first run is a genuine
verification of repo cleanliness.

## Why this matters

Three confirmed coverage holes:

1. `tests/unit/test_architecture_boundaries.py` (37 lines) only asserts that
   4 leaf modules don't import 6 web frameworks. It does NOT check layer
   direction, private cross-package imports, or circulars — the exact
   violations found in the audit (private `_allowed_dirs_from_env` import,
   Result envelope fan-in) would pass it.
2. `doctor.py` (329 lines) has NO unit tests — only subprocess runs in
   `test_cli_basic.py:29,43` (environment-dependent) and wiring tests in
   `test_cli.py`. Individual checks (`_is_windows_x64`, ODBC/registry
   probes, `_check_deep_access`) are untested; three silent swallows at
   `:111,232,236` are unobserved.
3. `backends/dao.py` (652 lines) has ZERO direct tests —
   `test_dao_sql.py:10` covers only the 4.6 KB `dao_sql` helper.

## Current state

- `tests/unit/test_architecture_boundaries.py:12-37` — subprocess-imports
  `ms_access_mcp`, `.safety`, `.models`, `.errors`; asserts absence of
  `fastapi, starlette, jinja2, typer, uvicorn, fastmcp`.
- `src/ms_access_mcp/doctor.py` — check functions plus `run_checks`;
  `tests/unit/test_application_maintenance.py:463-509` shows the existing
  pattern for monkeypatching `doctor.run_checks`.
- `src/ms_access_mcp/backends/dao.py` — COM/DAO adapter;
  `tests/unit/test_odbc_adapter.py` is the structural exemplar for testing a
  backend behind a fake boundary (fake driver/COM layer).
- Dependency rule this repo intends (from plans/README conventions):
  `mcp_tools/` + `cli.py` → `application/` → `backends/`; `errors.py`,
  `models.py`, `safety.py` are the shared kernel (import nothing from the
  layers above).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| New tests | `uv run pytest tests/unit/test_architecture_boundaries.py tests/unit/test_doctor.py tests/unit/test_backend_dao.py -q` | all pass |
| Full unit | `uv run pytest tests/unit/ -q` | all pass |
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | 0 errors |

## Scope

**In scope** (test files only):
- `tests/unit/test_architecture_boundaries.py` (extend)
- `tests/unit/test_doctor.py` (create)
- `tests/unit/test_backend_dao.py` (create)

**Out of scope**:
- Any production file. A failing characterization test = STOP and report.
- Rewriting the existing framework-absence check (keep it; add alongside).
- ODBC/ADO backend tests (already covered) and `access.py` (covered by
  `test_backend_access.py`).

## Git workflow

- Branch: `plan/014-test-guards`
- Commit per step; e.g.
  `test(architecture): AST layer-direction and private-import guard`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Architecture guard

Extend `test_architecture_boundaries.py` with a stdlib-`ast` scan of
`src/ms_access_mcp/**/*.py` asserting:
- No module under `backends/` imports from `application`, `mcp_tools`,
  `cli`, or `server`.
- Shared kernel (`errors.py`, `models.py`, `safety.py`, `profiles.py`,
  `capabilities.py`) imports nothing from `application`, `backends`,
  `mcp_tools`, `cli`, `server`.
- No module imports a `_private` name across package boundaries
  (`from ..server import _x`, `from .database import _err` style edges
  OUTSIDE the defining package). NOTE: if plan 012 is not yet DONE,
  `_allowed_dirs_from_env` and `_ok`/`_err` cross-package imports still
  exist — either land 012 first, or encode those exact edges in a temporary
  allowlist with a `# TODO(plan-012): remove` comment. Prefer landing 012
  first.

**Verify**: `uv run pytest tests/unit/test_architecture_boundaries.py -q` →
all pass (if the guard fails on a real violation not listed above, STOP —
you found a new boundary break)

### Step 2: Doctor characterization tests

Create `tests/unit/test_doctor.py`. Model the monkeypatching style on
`test_application_maintenance.py:463-509`. Cover:
- `_is_windows_x64` truth table via patched `sys`/`platform` values.
- Each registry/ODBC probe with the registry accessor patched to
  present/absent/error — assert the `Check` result shape (name, ok, detail
  fields) rather than exact environment outcomes.
- `run_checks` aggregation: all-pass and one-failure cases (patch
  individual checks).
- The silent swallows at `:111,232,236`: characterize CURRENT behavior
  (exception → check marked not-ok or skipped, per whatever the code does)
  with a comment `# characterization: documents current swallow behavior`.
  Do not assert what you WISH it did.

**Verify**: `uv run pytest tests/unit/test_doctor.py -q` → all pass

### Step 3: DAO adapter characterization tests

Create `tests/unit/test_backend_dao.py` modeling `test_odbc_adapter.py`'s
fake-boundary approach: a fake DAO/DBEngine layer injected the same way the
odbc tests fake their driver. Cover the highest-risk public methods first:
`alter_table` (71 lines, `:581`), connect/disconnect lifecycle, and one
happy-path + one COM-failure-path per method group. Characterize current
behavior — including any error swallowing — with the same
`# characterization:` comment convention.

**Verify**: `uv run pytest tests/unit/test_backend_dao.py -q` → all pass

### Step 4: Full gate

**Verify**: `uv run pytest tests/unit/ -q` → all pass (count increases by
the new tests); `uv run ruff check .` → exit 0; `uv run pyright` → 0 errors.

## Test plan

This plan IS the test plan. Structural patterns:
- Guard: existing `test_architecture_boundaries.py`.
- Doctor: `test_application_maintenance.py:463-509` monkeypatch style.
- DAO: `test_odbc_adapter.py` fake-boundary style.
Verification: `uv run pytest tests/unit/ -q` → all pass.

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0; new guard + doctor + dao
      tests exist and pass
- [ ] Guard test demonstrably fails when a forbidden edge is temporarily
      introduced (prove it once by adding a fake edge in a scratch file
      under src/, run, observe failure, revert)
- [ ] `uv run ruff check .` exits 0; `uv run pyright` 0 errors
- [ ] Zero production files modified (`git status` shows tests only)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- A characterization test fails against current production code (discovered
  bug — report with the failing output; do not patch production).
- The guard finds a forbidden edge NOT listed in Step 1 (new violation).
- Faking the DAO boundary proves impossible without production changes
  (means the adapter lacks a seam — report; do not add one here).

## Maintenance notes

- The guard's forbidden-edge list is the repo's architectural contract in
  executable form; when new layers appear, extend the guard, don't bypass it.
- `# characterization:` comments mark tests that document questionable
  current behavior — future bug-fix plans should grep for them and convert
  to intended-behavior tests.
- Deferred: `mcp_tools/automation.py` (860 lines vs 168 test lines) and
  `application/database.py` (746 vs 160) coverage ratios — assessed but not
  gapped in this plan; revisit after plans 009–013 land.
