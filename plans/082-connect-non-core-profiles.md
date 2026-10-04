# Plan 082: Connect the non-core MCP profiles (automation, ddl, analysis, versioning, maintenance)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/mcp_tools/ src/ms_access_mcp/application/_adapter_lifecycle.py src/ms_access_mcp/profiles.py`
> On any mismatch with "Current state", treat it as a STOP condition. Plans
> 079–081 will already have changed `mcp_tools/core.py`; that is expected —
> this plan builds ON those changes (reuse the `open_session` helper from
> plan 079).

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/079-*.md (session lifecycle helper), plans/080-*.md (contract truth)
- **Category**: bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

Every non-core MCP profile builds its use cases with `db_path=None` or never
opens the `path` argument its tools accept. Verified in the audit: adapter-
dependent calls in `automation`, `ddl`, `analysis`, and `versioning`
profiles deterministically return `"Backend unavailable: db_path is required
to create an adapter"` or `"Not connected"`. In `maintenance`,
`compact_repair` and `recover_access` share the defect (other maintenance
operations are filesystem-only and fine). The tools are registered,
documented in prompts, and advertised — but the entire opt-in surface
cannot touch a database.

## Current state

- `src/ms_access_mcp/application/_adapter_lifecycle.py` —
  `_ensure_adapter()` returns `Err("Backend unavailable: db_path is required
  to create an adapter")` when `_db_path is None` (lines ~41–46). This is
  the shared lazy-connect mechanism; it is correct — the wrappers never
  supply the path.
- `src/ms_access_mcp/mcp_tools/core.py` — the WORKING exemplar:
  `_open_or_err` (plan 045) opens the database before use; after plan 079,
  the `open_session` helper owns open+close per call.
- `src/ms_access_mcp/mcp_tools/automation.py` — `AutomationUseCases(...,
  db_path=None)` at module factory (~lines 20–21); tools accept `path` and
  ignore it (e.g. `list_objects` ~lines 26–38).
- `src/ms_access_mcp/mcp_tools/ddl.py` — `_new_db(readonly=False)`
  (~line 26) never opens; tools take `path` (e.g. `create_table`
  ~lines 36–54) and fail with "Not connected".
- `src/ms_access_mcp/mcp_tools/analysis.py` — tools `get_er_diagram`,
  `analyze_query`, `generate_sql` accept `path` (~lines 30, 58, 95) and
  never open it. (Their internal envelope bug is plan 086 — do not fix it
  here.)
- `src/ms_access_mcp/mcp_tools/versioning.py` —
  `VersioningUseCases(db_path=None)` (~lines 17–18) AND the tools take no
  `path` parameter at all (~lines 27–73) — this profile needs the parameter
  ADDED (schema change).
- `src/ms_access_mcp/mcp_tools/maintenance.py` —
  `MaintenanceUseCases(db_path=None)` (~lines 23–24). Verified broken:
  `compact_repair` (~line 407 in `application/maintenance.py`) and
  `recover_access` (~line 450) call `_ensure_adapter()`. Verified FINE
  without a db_path: `backup`, `diagnose_environment`,
  `repair_with_access_cli`, `salvage_database` (fresh adapter internally,
  ~lines 594–599), `convert_legacy_database`, `deploy_dev_copy`/
  `discard_dev_copy` (filesystem ops with their own path guards).
- `src/ms_access_mcp/profiles.py` — profile → module map; `server.py`
  registers per profile.
- Test masks, verified: `tests/unit/test_mcp_ddl.py` (~lines 44–66)
  monkeypatches `_new_db`; `tests/unit/test_mcp_analysis.py` (~lines 51–56)
  PINS the "Not connected" failure as the contract; versioning tests
  assert only "has content" (an `Err` envelope satisfies that);
  `tests/unit/test_mcp_maintenance.py` (~lines 126–142) mocks the use case
  class entirely.
- Conventions: tool schemas are typed (plan 045/075); path-guard checks use
  `safety.is_path_allowed` at wrapper level only where outputs are written;
  per-call lifecycle goes through plan 079's `open_session`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused tests | `uv run pytest tests/unit/test_mcp_ddl.py tests/unit/test_mcp_automation.py tests/unit/test_mcp_analysis.py tests/unit/test_mcp_versioning.py tests/unit/test_mcp_maintenance.py tests/unit/test_tool_schemas.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Budget | `uv run python scripts/tool_budget.py` | exit 0; per-profile counts unchanged except versioning (+path param, same count) |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/mcp_tools/automation.py`, `ddl.py`, `analysis.py`,
  `versioning.py`, `maintenance.py`
- `tests/unit/test_mcp_ddl.py`, `test_mcp_automation.py`,
  `test_mcp_analysis.py`, `test_mcp_versioning.py`,
  `test_mcp_maintenance.py`, `test_tool_schemas.py`

**Out of scope**:
- `mcp_tools/core.py` (already wired; only reuse its helper).
- `application/**` use-case internals — including the analysis envelope bug
  (plan 086) and versioning's missing backend methods (plan 085).
- Adding `path` parameters to any tool that already has one.
- `maintenance` operations verified to work without an adapter.

## Git workflow

- Branch: `plan/082-connect-profiles` from `main` (after 079/080/081).
- Conventional commits per profile: `fix(mcp): connect ddl tools to the requested database path`.

## Steps

### Step 1: RED — one success-path test per profile

For each of `automation`, `ddl`, `analysis`, `versioning`: add a test that
injects a FAKE adapter via `monkeypatch.setattr(BackendSelector,
"get_adapter", ...)` (pattern: `tests/unit/test_database.py`
`TestCreateLinkedTableErrorScrub`'s injection, minus the leak), calls one
representative tool with a `path`, and asserts an `Ok`-equivalent success
(not "has content"). For `maintenance`: `compact_repair` and
`recover_access` get the same treatment. Expected: all RED with the
"db_path is required"/"Not connected" errors.

**Verify**: `uv run pytest tests/unit/test_mcp_ddl.py tests/unit/test_mcp_automation.py tests/unit/test_mcp_analysis.py tests/unit/test_mcp_versioning.py tests/unit/test_mcp_maintenance.py -q` → new cases fail, old cases pass.

### Step 2: GREEN — wire `path` through the factories

For each module, construct the use case with the tool's `path`:

- `automation.py` / `analysis.py` / `ddl.py` / `maintenance.py`: build the
  use case per call with `db_path=path` (or open via plan 079's
  `open_session(path, ...)` so teardown is owned — REQUIRED for adapter-
  creating profiles; do not construct adapters that nothing closes).
- `versioning.py`: ADD the `path` parameter to all six tools' schemas and
  pass `db_path=path`. Update `tests/unit/test_tool_schemas.py` pins and
  any docstrings that describe the parameter list. `mcp_tools/prompts.py`
  mentions tool names only — verify no signature text needs updating
  (`grep -n "versioning" src/ms_access_mcp/mcp_tools/prompts.py`).
- Keep the existing wrapper-level guards (confirm gates, `is_path_allowed`
  for outputs) exactly as they are.

**Verify**: Step-1 tests pass per profile as each module lands.

### Step 3: Refresh the pinning/masking tests

- `test_mcp_analysis.py` (~51–56): replace the "Not connected is the
  contract" pin with the connected-contract pin (fake adapter returns
  whatever the use case yields — even if plan 086's envelope fix is not
  landed yet, assert the error is NOT "Not connected"). Document the
  refresh in the commit message (plan-035 precedent).
- `test_mcp_ddl.py`: replace `_new_db` monkeypatching with the fake-adapter
  injection where the test intends to exercise real dispatch.
- `test_mcp_versioning.py`: assert `success: true/false` explicitly, never
  "has content".
- `test_mcp_maintenance.py`: keep the mocked-class tests that verify
  wrapper plumbing; add the two real-dispatch tests from Step 1.

**Verify**: `uv run pytest tests/unit/test_mcp_ddl.py tests/unit/test_mcp_automation.py tests/unit/test_mcp_analysis.py tests/unit/test_mcp_versioning.py tests/unit/test_mcp_maintenance.py -q` → all pass.

### Step 4: Full gates + budget

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run python scripts/tool_budget.py` → exit 0 (tool COUNTS unchanged); `uv run pyright` → 0; `uv run ruff check .` → 0.

## Test plan

- Per profile: one connected success-path test (fake adapter), one
  disconnected-failure test asserting the honest error, existing
  confirm-gate tests untouched.
- Versioning: schema test updated for the new required `path` param on all
  six tools.
- Maintenance: `compact_repair` and `recover_access` connected tests;
  filesystem-only operations stay on their existing tests.

## Done criteria

- [ ] Every adapter-dependent tool in the five modules reaches its use case with a connected adapter (tests assert success paths)
- [ ] `versioning` tools all declare `path` and pass `db_path`
- [ ] No test asserts "Not connected" as the expected contract for a connected call
- [ ] `uv run python scripts/tool_budget.py` exit 0, counts unchanged
- [ ] Unit suite + qualify green; `plans/README.md` row updated

## STOP conditions

- If wiring a profile requires choosing BETWEEN per-call open/close and a
  persistent session (e.g. automation tools that assume a warm session),
  stop and present the trade-off — that is a product decision, not an
  implementation detail.
- If `versioning` tools cannot accept `path` without breaking the
  FastMCP schema validation (plan 075 models), stop and report the exact
  validation error.
- If any live-COM behavior is required to make a test meaningful, mark it
  as an operator gate and keep the unit test on fakes — never fake
  success.

## Maintenance notes

- After this plan, `ddl` tools expose plan 080's fixed contracts and
  `versioning` tools will still fail on missing backend methods until
  plan 085 lands — sequence 085 immediately after.
- The analysis profile's envelope bug (schema `{}`, no `timing`) remains
  and is owned by plan 086; connected tests here must not accidentally pin
  the degraded envelope as correct (assert only that the call dispatches).
