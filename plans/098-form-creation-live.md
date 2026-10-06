# Plan 098: Form/report creation repaired against live Access (E5 follow-up)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> Honor the STOP conditions — report instead of improvising. When done,
> update the status row in `plans/README.md` (section "Plan 098").
>
> **Drift check (run first)**:
> `git diff --stat de546e9..HEAD -- src/ms_access_mcp/backends/access/object_kinds.py src/ms_access_mcp/backends/access/trusted_locations.py src/ms_access_mcp/backends/access/forms.py src/ms_access_mcp/backends/access/reports.py`
> Expected: empty (commits since `de546e9` touched versioning/g13/ledger
> only). Any drift → STOP.

## Status

- **Priority**: P1 (unblocks gates 092(a)/(c))
- **Effort**: M
- **Risk**: MED (live COM design-view flows)
- **Depends on**: 092 code landed (78027f6); E5 diagnosis (ledger, corrected 4e4cb01)
- **Category**: bug fix / live-contract repair
- **Planned at**: 2026-10-05, after `4e4cb01`

## Why this matters

The `create_form`/`create_report` MCP tools have never worked against
real Access: `object_kinds._create_object` calls
`getattr(access_app.DoCmd, "CreateForm")()` — a method that does not
exist on DoCmd (live: `AttributeError: <unknown>.CreateForm`). The
correct COM API is `Application.CreateForm()/CreateReport()`. The
requested `object_name` is also never applied (the flow would create
the default `Form1` regardless), and `template_name` is accepted but
ignored. Unit fakes had a `CreateForm` attribute on their fake DoCmd,
which kept tests green while production failed (ledger E5(a)).

Adjacent concern (E5(c), downgraded): `trusted_locations.list_objects`
swallows collection-access exceptions into a silent empty/partial
listing (debug log only) — the same masking pattern that hid E5(a).

Live facts established during the gate session (2026-10-05):

- `Application.CreateForm()` with no args WORKS in hidden mode and
  creates the default `Form1` in design view.
- `Application.CreateForm("GateForm")` failed once with a misleading
  `"Microsoft Access can't find the database 'GateForm.'"` — mechanism
  unconfirmed (possibly poisoned session state from an earlier failed
  `DoCmd.CreateForm` probe in the same dispatcher call). Must be
  retested in a fresh session (Step 1 candidate A).
- `DoCmd.Close(0, name, 1)` with the wrong type constant (0=acTable)
  silently no-ops for forms; the correct constants are `acForm=2`,
  `acReport=4` (already in `OBJECT_KINDS` as `close_ac`/`ac_type`).
- An unnamed/dirty form left open at teardown makes the hidden Access
  surface a **visible save prompt on the operator's desktop** — the
  failure mode the user actually saw. Every code path must guarantee
  no dirty form survives (acSaveNo close on error paths).
- `_rename_object` is correct by inspection
  (`DoCmd.Rename(NewName, acType, OldName)`); unverified live.

## Current state (HEAD)

- `backends/access/object_kinds.py:117-156` `_create_object` — broken
  `DoCmd.CreateForm/CreateReport` call; `object_name`/`template_name`
  unused; closes `obj.Name` (default `Form1`) with acSaveYes and no
  failure guard.
- `backends/access/object_kinds.py:182+` `_rename_object` — correct
  order, keep.
- `backends/access/trusted_locations.py:29+` `list_objects` —
  `getattr(CurrentProject, collection)` inside try/except that logs at
  debug and returns partial/empty; per-object `Properties` skips are
  legitimate and stay.
- Adapter wrappers: `backends/access/forms.py:68+ create_form`,
  `reports.py` `create_report` (pass-through, `_create_object`).
- Likely unit pins: `tests/unit/test_backend_object_mirror.py`
  (form/report design-view pins, plan-092 territory),
  `tests/unit/test_backends_access.py` (adapter-level), possibly
  `tests/unit/test_application_automation.py` — locate every pin by
  content (`grep -rn "create_form\|_create_object\|DoCmd" tests/unit/`)
  and refresh with the plan-035 rationale in the commit.
- No live/integration test has ever created a form (fixture has none).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused unit | `PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest tests/unit/test_backend_object_mirror.py tests/unit/test_backends_access.py tests/unit/test_application_automation.py -q -p no:cacheprovider` | all pass |
| LIVE lifecycle | `... -B -m pytest tests/integration/test_form_lifecycle.py -q -p no:cacheprovider` | pass (or documented skip without Access) |
| Unit suite / lint / types / budget | house commands | green |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/access/object_kinds.py` (_create_object
  rewrite; rename untouched)
- `src/ms_access_mcp/backends/access/trusted_locations.py`
  (collection-access failure loudness only)
- `tests/integration/test_form_lifecycle.py` (new, marker-gated)
- Unit test files holding create/list pins (locate by grep; refresh
  only pins actually asserting the old broken call shape)

**Out of scope**:
- `_with_design_view` (plan-092 save_flag semantics — verified there)
- MCP/facade signatures (unchanged)
- Form templates beyond what the Step-1 winner supports (document
  honestly if the live API cannot take a template)

## Git workflow

- Branch `plan/098-form-creation` from `plan/097-fk-orientation`.
- Commit units: (1) live creation fix + unit pins, (2) listing
  loudness, (3) integration lifecycle test + ledger row.

## Steps

### Step 1: LIVE naming matrix (parent-run or executor-run probe)

On a fresh `%TEMP%` copy of the fixture, one candidate per FRESH
session (connect → try → verify → disconnect; PID-scoped orphan check
between sessions per ledger E3):

- **A**: `app.CreateForm("MxA")` (and `CreateReport("MxR")`) — does
  name-at-creation work in a clean session? If it raises the
  "database" error again, record it as the API's real behavior.
- **B**: `app.CreateForm()` → `DoCmd.Rename("MxB", 2, obj.Name)` →
  `DoCmd.Close(2, "MxB", 1)` — does Rename work on an unsaved
  design-view form?
- **C**: `app.CreateForm()` → set `RecordSource` → `DoCmd.Save(2,
  "MxC")` → `DoCmd.Close(2, "MxC", 1)` — rename-on-save path.

Verification per candidate: `db.Containers("Forms").Documents` lists
the saved name (the independent DAO view), `adapter.form_exists(name)`
is True, and teardown leaves NO dirty form (no visible prompt; if any
candidate errors, close with `DoCmd.Close(2, obj.Name, 2)` acSaveNo
before raising). The winner is the first candidate that produces a
saved, correctly-named form with no prompt. Record the matrix verbatim
in the commit message.

**Verify**: matrix recorded; if NO candidate works, STOP and report
(needs deeper research — do not ship a speculative path).

### Step 2: RED→GREEN — `_create_object` against the live API

- RED: unit test with a fake `access_app` exposing ONLY the real COM
  surface (`Application.CreateForm/CreateReport`, `DoCmd.Save/Rename/
  Close`) and NO `DoCmd.CreateForm`: `_create_object` must call the
  Application method, apply `object_name` via the Step-1 winner, set
  `RecordSource`/properties, and `Close(ac_type, object_name, 1)`.
  Include the failure-guard case: mid-flow exception →
  `Close(..., 2)` (acSaveNo) attempted before returning False/raising.
- GREEN: rewrite `_create_object` per the matrix; keep the
  `log_object_failure` classification path; `template_name` passed
  only if the winner supports it (document if not).

**Verify**: focused unit green; old fake-pins refreshed (listed in the
commit).

### Step 3: RED→GREEN — listing loudness

- RED: fake `CurrentProject` whose collection property raises →
  `list_objects` must NOT return a silent [] (raise or return an
  error the adapter surfaces); per-object `Properties` skips remain
  silent.
- GREEN: implement the minimal change in `trusted_locations.py`.

**Verify**: focused unit green.

### Step 4: LIVE acceptance = the blocked 092 gates

Create `tests/integration/test_form_lifecycle.py` (marker-gated like
`test_access_adapter.py`, %TEMP% scratch copy):

1. `adapter.create_form("GateForm")` → `form_exists`, DAO Containers,
   and `get_forms()` all agree the named form exists (first-ever
   non-empty live listing).
2. Add a control, then REMOVE it via the plan-092 remove path
   (`save_flag=1`), disconnect, reopen, assert the control is gone —
   **gate 092(a)**.
3. `set_control_event_procedure` on the form whose module lacks the
   procedure → stub created, `On<Event>` set after success — **gate
   092(c)**.
4. Teardown deletes the scratch; no MSACCESS leaks (baseline-relative
   assertion per the E0 pattern).

**Verify**: the file runs live on this machine (user-authorized
design-view traffic; the failure guards from Step 2 keep it
prompt-free); without Access it self-skips.

### Step 5: Full gates + ledger

Unit suite, ruff, pyright, budget; integration file + existing
integration files re-run. Update: this plan's row (DONE + matrix
summary), ledger rows 092(a)/(c) (BLOCKED → PASSED via this test),
E5 note pointing at the fix commit.

## Done criteria

- [ ] Matrix recorded; winner implemented (no speculative paths)
- [ ] `create_form`/`create_report` create the REQUESTED name, live
- [ ] Every failure path closes dirty objects with acSaveNo (unit-pinned)
- [ ] Listing failures are loud; first non-empty live `get_forms` observed
- [ ] `test_form_lifecycle.py` proves 092(a)/(c) live (or skips cleanly)
- [ ] Full gates green; ledger rows updated; no orphans, no prompts

## STOP conditions

- No Step-1 candidate produces a saved named form → STOP (report the
  matrix; needs research, maybe SaveAsText/LoadFromText crafting).
- A visible save prompt appears during any step → STOP that step,
  report which path leaked the dirty object; never continue with an
  unsaved form open.
- If live behavior contradicts a unit pin in a way the matrix cannot
  explain → STOP and report the divergence.
- Repo-path DBs are read-only on this machine (E4) — everything runs
  on %TEMP% copies; anything else → STOP.

## Maintenance notes

- `Application.CreateForm` opens a real design view inside the hidden
  Access; any future flow that opens design view must carry the same
  acSaveNo failure guard (the visible-prompt tripwire).
- If template support turns out impossible live, keep the parameter
  for API compatibility but document the no-op in the tool docstring —
  never pretend it worked.
