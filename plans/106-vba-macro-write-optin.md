# Plan 106: VBA/macro write opt-in — disabled by default behind one env gate

> **Executor instructions**: Follow step by step. When done, update the
> status row in `plans/README.md` (section "Plans 105–108").
>
> **Drift check (run first)**: `git diff --stat 5593a4b..HEAD --
> src/ms_access_mcp/application/automation.py src/ms_access_mcp/mcp_tools/automation.py`
> empty.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: safety default / behavior change
- **Planned at**: commit `5593a4b`, 2026-10-10

## Why this matters

Deferred operator decision (2026-09-16 audit, listed operator-blocked
in the 099–104 cycle): the VBA/macro WRITE tools can silently rewrite
code objects on any connected database. Form/report writes got
design-view discipline (plan 092) and confirmation gating exists
(`_CONFIRM_REQUIRED`), but a mis-configured server still exposes
code-content writes by default. **Decision (operator-authorized this
cycle): VBA/macro writes become OPT-IN — disabled unless the operator
sets one environment variable.**

Scope (10 tools): `set_vba_code`, `add_vba_procedure`,
`vba_replace_procedure`, `create_module`, `rename_module`,
`delete_module`, `create_macro`, `rename_macro`, `delete_macro`,
`set_control_event_procedure`. NOT gated: `run_macro` (execution of
existing code, already confirm-gated), all form/report design writes
(different risk class, plan-092 discipline).

## Current state (HEAD `5593a4b`)

- Tools declared in `mcp_tools/automation.py`; each calls a use-case
  (`uc.<name>` in `application/automation.py`). Gating at the
  USE-CASE layer covers MCP and CLI surfaces with one seam.
- Env-config precedent: `ACCESS_MCP_ALLOWED_DIRS` (`safety.py`).
- Existing unit tests call these use-cases directly and will see the
  gate; they must opt in via fixture/monkeypatch deliberately.

## Steps

1. **RED unit** — in a fresh test module
   `tests/unit/test_vba_write_optin.py`: with the env var unset, every
   gated use-case returns Err whose message contains
   `ACCESS_MCP_ENABLE_VBA_WRITES` and no adapter call occurs (fake
   adapter asserts zero calls); with the env var set to
   `1`/`true`/`yes` (case-insensitive), behavior is exactly the
   pre-change passthrough (existing tests keep proving this under a
   conftest fixture that enables the gate).
2. **GREEN** — implement the gate in `application/automation.py`: one
   shared helper reading the env var at call time (not import time);
   apply to the 10 use-cases. Err shape matches the house `_err(...)`
   convention. CLI parity check: one CLI-level test proving the
   command surfaces the same actionable message.
3. **Deliberate test refresh** — existing tests that exercise the
   gated use-cases get the enabling fixture (autouse in the affected
   modules or explicit monkeypatch); record which files changed and
   why (contract change, not drift).
4. **Docs** — README configuration section (or the env-var table the
   repo already keeps): new variable, default off, exact opt-in
   value, scoped tool list, and the rationale one-liner. Tool
   docstrings for the 10 tools mention the gate.
5. Budget gate must stay green (no registration change — tools stay
   listed, they refuse at call time).

## Verify

```
PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest tests/unit -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B scripts/qualify.py
```
Live check (scratch, marker-gated): with gate off, `set_vba_code`
returns the disabled Err; with `ACCESS_MCP_ENABLE_VBA_WRITES=1`, a
no-op write path behaves as before.

## STOP conditions

- A consumer (docs example, script, smoke) breaks in a way the gate
  message does not cover — report instead of widening the bypass.
- The budget/profile system already gates any of these tools in a way
  that conflicts — report; do not stack two gates silently.

## Out of scope

- `run_macro`, form/report/control writes, confirmation-flag design.
- Any tool registration/profile change.
