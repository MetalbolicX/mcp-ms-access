# Plan 092: VBA and design-view write safety (remove-control save flag, rollback, compile contract, event stubs)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. This plan touches COM behavior whose ultimate proof needs live
> Access; unit work uses fakes, and the plan names the operator gates
> explicitly. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row for this plan in
> `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/backends/access/forms.py src/ms_access_mcp/backends/access/reports.py src/ms_access_mcp/backends/access/vba.py src/ms_access_mcp/backends/access/__init__.py src/ms_access_mcp/application/automation.py scripts/step5_qualify.py`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none (units are independent; land after 091 for clean access-package sequencing)
- **Category**: bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

Four audit-confirmed safety gaps in the Access COM surface:

1. **`remove_control` discards its own deletion**: forms/reports delete the
   control (`RunCommand(365)`) then close design view with `save_flag=2`
   (`acSaveNo`) — while every OTHER mutating path closes with `save_flag=1`.
   The deletion is thrown away and success reported. A characterization
   test pins the wrong flag as the contract.
2. **VBA writes have no rollback**: `write_module` does
   `DeleteLines`-all → `AddFromString`; `vba_replace_procedure` does
   `DeleteLines` → `InsertLines`. A failure after the delete leaves the
   module/procedure EMPTY — user source loss on a partial COM failure.
3. **Compile success ≠ compiled**: `compile_vba` returns success when the
   compile COMMAND is accepted; the use-case docstring promises structured
   compile errors that the backend can never emit. `step5_qualify.py`
   asserts `success is True` with deliberately broken code injected — the
   gate certifies the wrong thing.
4. **First-time event procedures fail but still mutate**: setting
   `On<Event> = "[Event Procedure]"` succeeds, then
   `vba_replace_procedure` returns `False` when the procedure doesn't
   exist (no create-on-miss) — the form saves a dangling reference.

## Current state

- `src/ms_access_mcp/backends/access/forms.py` — `forms.py:446`
  `RunCommand(365)` (delete control) then close `save_flag=2` at
  ~line 459 (constant meaning documented at `forms.py:83` /
  `object_kinds.py:143` comments). Mutating exemplars use `save_flag=1`
  (`forms.py:411`). `src/ms_access_mcp/backends/access/reports.py` — same
  pair (~lines 408, 423).
- `tests/unit/test_backend_object_mirror.py` — ~lines 387–397 PINS
  `("Close", ac, "Obj1", 2)` for removal (plan-035 note says "acSaveNo").
- `src/ms_access_mcp/backends/access/vba.py` — `write_module`
  (~lines 295–301), `vba_replace_procedure` (~lines 501–502);
  `compile_vba` (~lines 139–171; success on first non-raising RunCommand,
  ~195–197); `ProcStartLine` miss → `False` (~lines 493–499). Compile
  saves all std modules first via `DoCmd.Save(5, ...)` (~lines 182–188) —
  which PERSISTS any loss from (2).
- `src/ms_access_mcp/backends/access/__init__.py` — live
  `set_control_event_procedure` copy (~lines 539–571): sets the property
  then calls `vba_replace_procedure`; property persists via `save_flag=1`
  close (~line 568). NOTE: `forms.py`'s copies of these functions
  (~lines 475, 566) are dead duplicates of the class methods — drift risk
  flagged by the audit; do not fix them here (plan 093 candidate).
- `src/ms_access_mcp/application/automation.py` — docstring promises
  structured compile errors (~line 823); backend can only emit
  `compileErrors` if it populates it — real adapter path always yields
  `[]` (~line 838 `.get("compileErrors", [])`).
- `scripts/step5_qualify.py` — ~lines 114–127 assert `success is True`
  after injecting a broken module; docstring (~lines 20–24) promises
  "compile error → structured errors returned".
- Conventions: all COM work on the dispatcher STA thread
  (`_with_design_view`, `object_kinds.py:72-98`); characterization tests
  in `test_backend_access.py` / `test_backend_object_mirror.py` use fake
  dispatchers/apps (exemplars for the new tests).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused tests | `uv run pytest tests/unit/test_backend_object_mirror.py tests/unit/test_backend_access.py tests/unit/test_application_automation.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/access/forms.py`, `reports.py` (remove-control save flag)
- `src/ms_access_mcp/backends/access/vba.py` (rollback snapshots; create-on-miss stub; compile result shape)
- `src/ms_access_mcp/backends/access/__init__.py` (`set_control_event_procedure` ordering only)
- `src/ms_access_mcp/application/automation.py` (compile docstring/result keys)
- `scripts/step5_qualify.py` (gate assertion + docstring)
- `tests/unit/test_backend_object_mirror.py`, `tests/unit/test_backend_access.py`, `tests/unit/test_application_automation.py`

**Out of scope**:
- The dead duplicate functions in `forms.py` (plan 093 decides their removal).
- Dialog killer behavior (plan 091).
- Any new MCP tool or schema; relationship/DML backends.

## Git workflow

- Branch: `plan/092-vba-design-view-safety` from `main`.
- Conventional commits per unit: `fix(access): persist control removals (acSaveYes close)`.

## Steps

### Step 1: remove-control save flag (characterization refresh + fix)

- RED: change the pinned expectation in
  `test_backend_object_mirror.py` (~lines 387–397) to
  `("Close", ac, "Obj1", 1)` — documenting in the commit message that the
  old pin recorded the defect (deletion discarded), plan-035 precedent.
- GREEN: `forms.py:459` and `reports.py:423`: close with `save_flag=1`
  for the REMOVE path (keep `save_flag=2` for read-only getters).
- Operator gate: live round-trip (remove a control on a scratch database,
  reopen, assert gone) — record in the plan row; unit fakes prove the flag
  change, not Access's persistence.

**Verify**: `uv run pytest tests/unit/test_backend_object_mirror.py -q` → pass.

### Step 2: VBA write rollback (strict TDD on fakes)

- RED: fake-module tests — a fake VBE module whose `AddFromString` (or
  `InsertLines`) raises AFTER `DeleteLines` ran: `write_module` and
  `vba_replace_procedure` must RESTORE the previous module text (assert
  the fake received restoration calls with the original content) and
  return failure.
- GREEN in `vba.py`: before deleting, read the existing text (the module
  object exposes lines — read via the existing accessor the code already
  uses elsewhere in the file); on exception, best-effort restore via
  `AddFromString`/`InsertLines` with the snapshot, then return False /
  raise as today's contract dictates. Keep snapshots OFF the STA queue
  problem in mind: read and restore both happen on the same dispatcher
  call path as the mutation (single `dispatcher.call` wrapping
  delete+write+restore keeps apartment correctness — wrap the whole
  mutation, don't add cross-thread hops).

**Verify**: focused fake tests GREEN.

### Step 3: Create-on-miss event stubs

- RED: fake-app test — `set_control_event_procedure` on a form whose
  module LACKS the procedure: must insert a stub
  (`Private Sub <control>_<event>()\nEnd Sub`) and return success
  (property set + procedure existing), instead of `False` with the
  property left dangling.
- GREEN in `__init__.py`'s live copy: when `vba_replace_procedure` misses
  (`ProcStartLine` absent), create the stub then insert the body; only set
  the `On<Event>` property after the procedure exists (reorder property-
  set AFTER successful write), keeping `save_flag=1` persistence.
- Operator gate: live create of a first-time event procedure.

**Verify**: fake tests GREEN.

### Step 4: Compile contract honesty

- `application/automation.py`: fix the docstring — compile success means
  "compile command accepted"; structured `compileErrors` remain an
  aspirational field only if a backend populates them. If the tool
  description repeats the promise, align it.
- `scripts/step5_qualify.py`: the broken-module compile check must assert
  the ACTUAL contract (command accepted) and its docstring must stop
  promising structured errors; add a comment marking real compile-error
  detection as an open operator-verified question (whether RunCommand
  raises on compile errors with the dialog killer scoped per plan 091 is
  live-only knowledge).
- Do NOT attempt to detect compile errors speculatively in this plan.

**Verify**: `uv run pytest tests/unit/test_application_automation.py -q` → pass; step5 docstring asserts what the code does.

### Step 5: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0; `uv run ruff check .` → 0.

## Test plan

- Refreshed removal pin (flag 1) + getter pins unchanged.
- Rollback fakes (2 paths: module write, procedure replace).
- Event-stub create-on-miss fake + dangling-property ordering assertion.
- Docstring/gate alignment (no behavior assertion changes beyond the above).
- Operator gates (live Access): removal round-trip, VBA restore on
  injected failure, first-time event procedure, compile behavior with
  broken code — list each with result in the plan row.

## Done criteria

- [ ] Remove path closes with `save_flag=1`; refreshed pin passes
- [ ] Rollback restores prior module text on injected failure (fakes; operator-confirmed if available)
- [ ] Event procedures create-on-miss; property set ordered after success (fakes)
- [ ] Compile docstrings/gate assert the real contract
- [ ] Unit suite + qualify green; `plans/README.md` row updated with operator-gate statuses

## STOP conditions

- If the plan-035 note beside the mirror pin documents a deliberate reason
  for `save_flag=2` on removal (read it first), stop and present the
  contradiction — do not refresh the pin against documented intent.
- If wrapping delete+write+restore in one dispatcher call breaks the STA
  queue contract (plan 030 rules), stop and report — restructure with the
  dispatcher's owner pattern, never cross threads.
- If live operator gates contradict any unit-pinned behavior, stop and
  report the divergence before landing further steps.

## Maintenance notes

- The dead duplicate functions in `forms.py` (`forms.py:475,566` family)
  are the standing drift hazard behind finding (4); plan 093 should delete
  or deduplicate them — until then, any event-procedure fix must be made
  in the LIVE class copy in `__init__.py` only.
- Real compile-error detection (dialog classification or `VBE` state) is a
  future plan gated on the operator finding recorded here.
