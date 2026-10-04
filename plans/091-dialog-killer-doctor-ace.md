# Plan 091: Dialog-killer PID scoping and doctor ACE probe classification

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/backends/access/dialog_killer.py src/ms_access_mcp/backends/access/vba.py src/ms_access_mcp/doctor.py tests/unit/test_doctor.py`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW (dialog killer) / LOW (doctor)
- **Depends on**: none
- **Category**: bug / dx
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

1. `DialogKiller` enumerates EVERY top-level window on the desktop and
   clicks/Enters any `#32770` dialog whose title contains "save" — no
   process filter. During a VBA compile window (~seconds, 0.1 s poll), an
   unrelated application's save dialog gets dismissed by this server.
   `AccessAdapter` already captures the Access PID (plan 030), so a
   PID-scoped filter is cheap and precise.
2. `doctor`'s ACE check treats a successful generic `Dispatch(
   "ADODB.Connection")` as "provider available" and swallows the `Open`
   failure that is the actual distinguisher — machines WITHOUT ACE report
   it as available, and the accdb/jet4 format matrix inherits the lie.
   The classification must happen at the raise site.

## Current state

- `src/ms_access_mcp/backends/access/dialog_killer.py` — ~lines 29–45:
  `EnumWindows` over all top-level windows; filter is
  `cls == "#32770" and "save" in title`; fallback Enter keypress
  (~line 43) even without an OK/Save button. NO `GetWindowThreadProcessId`
  anywhere in the file (audit grep). Only caller: `compile_vba`
  (`backends/access/__init__.py` ~line 874); `vba.py:186-205` shows the
  compile window's polling loop.
- Access PID availability: plan 030 recorded `_access_pid` capture at
  connect in the COM adapter (`backends/access/__init__.py` connect path).
- `src/ms_access_mcp/doctor.py` — ACE check (~lines 95–120):
  `Dispatch("ADODB.Connection")` (~line 112) — WDAC-universal, succeeds
  without ACE; `conn.Open(...)` failure swallowed (~lines 113–115);
  unconditional `passed=True` + "provider available" (~lines 117–120).
  The accdb verdict also consults ODBC (~lines 336–342); the dao_engine
  `any()` at ~244/307 is computed and discarded (dead expression).
  The swallow is PINNED by characterization tests
  (`tests/unit/test_doctor.py` ~302–324, plan 014 "swallow pins") — those
  pins get refreshed here with rationale.
- Conventions: PID-scoped process handling is established product policy
  (plans 030/031 — never touch processes beyond the server's own Access
  instance); COM errors classify via `pywintypes.com_error` hresult.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused tests | `uv run pytest tests/unit/test_doctor.py tests/unit/test_backend_access.py tests/unit/test_backend_object_mirror.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/access/dialog_killer.py`
- `src/ms_access_mcp/backends/access/__init__.py` (pass the Access PID to the killer — wiring only)
- `src/ms_access_mcp/doctor.py` (ACE check classification + dead `any()` expressions)
- `tests/unit/test_doctor.py` (refresh swallow pins), a new/extended test
  module for the dialog killer filter

**Out of scope**:
- Any change to compile semantics (plan 092 owns the compile contract).
- Doctor's ODBC checks, registry checks, or CLI surface.
- Window-title matching beyond the existing "save" heuristic (PID scoping
  is the fix; not a title-engine rewrite).

## Git workflow

- Branch: `plan/091-dialog-killer-doctor-ace` from `main`.
- Conventional commits: `fix(access): scope dialog killer to the Access PID; fix(doctor): classify ACE probe failures`.

## Steps

### Step 1: RED — PID-scope dialog killer

Write unit tests with a fake `EnumWindows`/handle→PID mapping (monkeypatch
the win32 calls the module makes; win32 imports must be injectable — if
the module imports at top level, patch the module attributes the same way
`test_backend_access.py` fakes COM):
1. a `#32770` "Save" dialog owned by the Access PID → dismissed;
2. the same dialog owned by ANOTHER pid → never touched;
3. non-`#32770` windows → never touched (existing behavior).

**Verify**: RED — no PID filter exists.

### Step 2: GREEN — filter by owning PID

In `dialog_killer.py`: accept the Access PID (constructor argument or
`kill(pid)` parameter); for each candidate handle resolve
`GetWindowThreadProcessId` and skip handles whose PID differs. Update the
single caller (`compile_vba` path in `backends/access/__init__.py`) to
pass the adapter's captured `_access_pid`. If the PID is unavailable
(edge: killer invoked outside adapter context), default to NOT acting
(fail closed) and log — never fall back to global clicking.

**Verify**: Step-1 tests GREEN.

### Step 3: RED→GREEN — doctor ACE classification

- RED: tests in `tests/unit/test_doctor.py` with a fake
  `ADODB.Connection` whose `Open` raises `pywintypes.com_error` with the
  provider-not-found hresult (`-2147467259` / `0x800A0E7A` style — use the
  documented provider-missing code from the audit, `0x800A0E7A`):
  `check_ace` returns `passed=False` with a provider-missing reason.
  A second fake where `Open` fails with a DIFFERENT error (invalid Data
  Source on a machine WITH the provider) still yields `passed=True` with
  an open-failure note.
- GREEN in `doctor.py`: move classification into the `except` —
  provider-missing hresult → `passed=False`; other failures →
  `passed=True` (provider responded) with the note. Delete the two dead
  `any()` expressions (~lines 244, 307) or wire them into the report —
  deleting is the honest default; pick one and say which in the commit.
- Refresh the plan-014 swallow pins that asserted unconditional `passed`
  — list every refreshed pin in the commit message (plan-035 precedent).

**Verify**: new tests GREEN; `uv run pytest tests/unit/test_doctor.py -q` → all pass.

### Step 4: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0; `uv run ruff check .` → 0.

## Test plan

- Dialog killer: 3 PID-filter cases (fake win32 surface, no real windows).
- Doctor: provider-missing → fail; other-open-failure → pass-with-note;
  refreshed swallow pins enumerated in the commit.
- Real-desktop behavior (does Access actually own its compile dialogs under
  that PID?) is an operator verification — record it in the plan row; the
  unit layer proves the FILTER, not Access's window ownership.

## Done criteria

- [ ] Dialog killer never acts on windows outside the Access PID (unit-pinned; fail-closed default)
- [ ] Doctor ACE check fails on provider-missing hresult and passes with a note on other open failures (tests)
- [ ] Dead `any()` expressions removed (or wired — commit states which)
- [ ] Unit suite + qualify green; `plans/README.md` row updated (including the operator desktop-verification status)

## STOP conditions

- If `GetWindowThreadProcessId` is unavailable through the installed
  pywin32 surface in the executor environment, stop and report — do not
  ship an import-time crash.
- If doctor characterization pins from plan 014 were protecting a
  deliberate contract (read the plan-014 note next to them first), stop
  and present the conflict instead of refreshing blindly.
- If live Access ownership of compile dialogs turns out to be a DIFFERENT
  process (e.g. a host Office process), the operator finding reopens the
  PID choice — report, do not widen the filter on speculation.

## Maintenance notes

- The fail-closed default (no PID → no clicking) may surface previously
  masked compile-dialog hangs on real desktops; if operators report new
  compile stalls, the capture point for `_access_pid` is the thing to fix.
- Doctor's format matrix inherits ACE truthfulness — after this plan,
  re-run `doctor` on a no-ACE machine as the operator check.
