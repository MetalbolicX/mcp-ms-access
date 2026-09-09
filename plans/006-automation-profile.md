# Plan 006: Automation profile (forms, reports, controls, VBA, macros)

> **Executor instructions**: Follow step by step; verify each command result.
> STOP conditions are hard — COM misbehavior is a STOP, not an improvisation
> opportunity. Update `plans/README.md` when done.
>
> **Drift check (run first)**: oracle HEAD `c8beaab`; new repo has plans
> 002–004 DONE (backends + profile registry working).

## Status

- **Priority**: P2
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: plans/004-mcp-core-profile.md
- **Category**: feature
- **Planned at**: oracle commit `c8beaab`, 2026-09-08

## Execution mode

`@medium` + strict TDD with fakes; real-COM integration tests are marked
`com_integration` and the orchestrator runs them in-shell (subagent COM claims
are not trusted — established lesson). Kill stray MSACCESS before/after.

## Why this matters

Access automation — forms, reports, controls, macros, VBA modules and
compilation — is the capability that justified COM in the first place and the
reason Python won over ReScript. It must run with a hidden Access instance,
clean up deterministically, and expose dangerous operations only through the
opt-in `automation` profile with per-call confirmation.

## Current state (oracle facts)

- `adapters/ui_operations.py` (1558 lines): forms/reports/controls/sections via `DoCmd`/`Forms`/`Reports` collections; operates over the dispatcher.
- `adapters/vba_operations.py` (552): module list/read/write, procedure-level replace, `compile_vba` (compile-error surfacing), save; imports `..config.ServerConfig` and `trusted_locations` at module level.
- `adapters/trusted_locations.py` (107): registry snapshot/restore around VBA writes; `ACCESS_MCP_PRESERVE_TRUSTED_LOCATIONS` env honored.
- Oracle MCP tool surface to consolidate: `mcp/com.py` (24 tools), `mcp/reports.py` (17), `mcp/macros.py` (7), `mcp/vba.py` (14). Forms and reports are structurally duplicated families — the new design uses ONE internal object-kind-parameterized use case with SEPARATE typed MCP wrappers only where schemas differ materially (decision: don't destroy typed schemas just to cut count).
- Misplaced/dangerous items NOT to port as-is: `mcp/vba.py` `save_query` (duplicate of `set_query_sql`); `wincom.py:1346` `execute_vba` via `Application.Run` (exists but intentionally unexposed); `store_credential(password=...)` pattern (plaintext secret as tool argument — forbidden; credentials arrive via env/CLI only if ever needed).
- Known COM invariants (must hold): all calls through the STA dispatcher; `Visible=False`; teardown = `CloseCurrentDatabase` → `Quit` (watchdog) → PID-scoped kill (plan-002 dispatcher).
- Test sources: oracle `tests/unit/test_vba_compilation.py`, integration `test_wincom_form_report.py`, `test_wincom_vba_write.py`, `test_wincom_dev_copy.py`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Unit | `uv run pytest tests/unit -q` | all pass |
| COM integration | `uv run pytest tests/integration -q -m com_integration` | all pass, 0 unexpected skips |
| Orphan check | `Get-Process MSACCESS -ErrorAction SilentlyContinue` | empty after suites |

## Scope

**In scope**: `src/ms_access_mcp/backends/access.py` (full UI/VBA surface), `src/ms_access_mcp/application/automation.py`, `src/ms_access_mcp/mcp_tools/automation.py`, `src/ms_access_mcp/safety.py` (dangerous-op gates), CLI `automation` command group, `tests/**`.

**Out of scope**: versioning/SaveAsText (plan 007), dev-copy/deploy (plan 007), `Application.Run` exposure (backlog — exists in backend, not exposed), macros beyond the listed CRUD+run.

## Git workflow

Branch `plan/006-automation`. Conventional commits per family (`feat(automation): form/report object use cases`, …). No push.

## Steps

### Step 1: Extend the hidden-Access backend

Grow `backends/access.py` from the plan-002 handshake into the full surface: open/close form/report (`DoCmd`), `Forms`/`Reports`/`Controls`/`Sections` enumeration and property get/set, create/rename/delete objects, macro CRUD + `run_macro`, module list/read/write, `add_vba_procedure`, procedure list/get/replace, `compile_vba`, `save_database`. Port behavior from `ui_operations.py`/`vba_operations.py` (dispatcher-call pattern preserved verbatim). Trusted-locations snapshot/restore wraps every VBA write.

Strict TDD: dispatcher fakes; property whitelists validated for set-paths.

**Verify**: `uv run pytest tests/unit/test_backend_access.py -q` → all pass.

### Step 2: Automation use cases (object-kind consolidation)

`application/automation.py`: ONE internal implementation parameterized by `object_kind: Literal["form","report"]` for exists/get/open/close/create/rename/delete/properties/sections/controls; separate typed functions where forms/reports genuinely differ. VBA and macro families as their own functions. Rules: every op requires an ACTIVE Access session (capability check → `BackendUnavailableError` otherwise); dangerous set (`set_vba_code`, `run_macro`, delete/rename of objects) requires `confirm=True` AND the non-readonly policy; compile errors returned as structured `compileErrors` (oracle shape), never swallowed.

**Verify**: `uv run pytest tests/unit/test_application_automation.py -q` → all pass (fakes).

### Step 3: MCP automation profile

`mcp_tools/automation.py` registered under the `automation` profile (zero contribution when disabled — plan-004 budget script extended). Typed tools, verbatim oracle names where they exist (`get_forms`, `open_form`, `create_form`, `add_control`, `get_modules`, `get_vba_code`, `set_vba_code`, `compile_vba`, `run_macro`, …), `object_kind` discriminator for the merged form/report family. isError mapping reused.

**Verify**: budget script — core still ≤ 30; `automation` adds its set; disabled profile → zero tools.

### Step 4: CLI group

`ms-access-mcp automation forms|reports|vba|macros ...` mapping onto the same use cases (`--json`, `--yes`). `run_macro` and `set_vba_code` require `--yes` even for CLI.

**Verify**: `uv run ms-access-mcp automation --help` → exit 0.

### Step 5: Real-COM qualification (orchestrator in-shell)

On DISPOSABLE fixture copies (`temp_db_copy` + SHA-256 of canonical after):
1. Hidden-session invariant: during a form-open + VBA-compile workload, poll `Get-Process MSACCESS | Where MainWindowHandle -ne 0` → always empty (no visible window).
2. VBA lifecycle: inject deliberate compile error → structured errors returned; fix → clean compile; export round-trip matches oracle `sample-sql/test-vba-lifecycle.py` flow.
3. 10 open/close Access-session rounds → zero orphans.
4. Canonical fixture hash unchanged.

**Verify**: all four green in the orchestrator's shell log.

## Test plan

- Unit: backend fakes, use-case confirm gates, profile registration, property whitelists.
- Integration (`com_integration`): form/report CRUD, control set/get, macro run (guarded), VBA write + compile-error surfacing, hidden-window invariant, teardown rounds.
- Hash discipline on every mutating test.

## Done criteria

- [ ] All unit + com_integration tests pass; 0 unexpected skips
- [ ] Zero visible Access windows during automated runs; zero orphans after 10 session rounds
- [ ] Compile-error tool returns structured errors on a bad module (test artifact)
- [ ] `set_vba_code`/`run_macro` blocked without confirm + write policy (automated)
- [ ] No plaintext-password parameters anywhere (`grep -rn "password=" src/ms_access_mcp/mcp_tools/` → no tool argument)
- [ ] `plans/README.md` row 006 updated

## STOP conditions

- Any COM call outside the STA dispatcher.
- Visible window appears, or orphan MSACCESS survives > 10 s after disconnect, after one fix attempt.
- Trusted-locations restore fails on any path (registry safety).

## Maintenance notes

- `Application.Run` (`execute_vba`) stays deliberately unexposed — revisit only with an explicit dangerous-profile decision.
- Reviewer focus: property-set whitelists and confirm gates (arbitrary property/name writes are the widest attack surface).
