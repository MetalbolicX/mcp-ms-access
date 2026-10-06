# Plan 110: macro create returns False live — diagnose and fix

> **Executor instructions**: Diagnose first (STOP gate at step 1);
> the root cause must be reproduced and understood before any fix.
> When done, update the status row in `plans/README.md`.

## Status

- **Priority**: P2
- **Effort**: S-M
- **Risk**: LOW-MED
- **Depends on**: plan 109 lineage; discovered executing plan 109's macro pin
- **Category**: automation / NEW DEFECT
- **Planned at**: 2026-10-10 (discovered executing plan 109)

## Why this matters

DISCOVERED LIVE 2026-10-10 (plan 109's macro persistence pin): the CLI
call `automation macros create --name X --allow-write --yes --json`
returns `{"success": false, "error": "operation failed: create_macro
returned False"}` against a %TEMP% scratch copy of the fixture. Macro
CREATION is broken on the live path — persistence could not even be
reached. Envelope shape is honest (Err, no fake success), but the
capability is dead.

Suspicion to check first: `macros.py:92`
`create_macro(dispatcher, macro_name, load_object_from_text=None)` —
an injected callable with a `None` default; if the use-case/CLI layer
fails to pass the loader (or the DAO adapter wiring drops it), the
function likely bails to False. Confirm by reading the call chain
(mcp_tools/automation.py -> application/automation.py ->
backends/access/__init__.py:836 -> macros.py) and reproducing with a
focused unit fake before touching anything.

## Steps

1. **Diagnose (STOP gate)** — trace the full call chain; write a unit
   RED proving the exact failure point (which layer returns False and
   why). Reproduce live once on %TEMP% scratch with the same call.
2. **Fix** at the layer the diagnosis indicts (wiring vs
   implementation). Macros persist via SaveAsText/LoadFromText
   (disk-level), so no VBA-style save mechanism is expected here —
   verify persistence with the plan-109 pin, which is already in
   place as `xfail` in `tests/integration/test_vba_persistence_live.py`
   and flips to XPASS when this lands.
3. **Pins** — unit fakes for the fixed layer; the existing live pin
   becomes the persistence proof (remove the xfail in the same change).
4. Full qualify + live gate + orphan hygiene.

## STOP conditions

- The diagnosis reveals a design-level rewrite (macro object model) —
  report before proceeding.
- Fixing create_macro exposes further broken macro operations
  (rename/delete/run) — fix create only; file the rest.

## Out of scope

- run_macro semantics, macro action-content editing, any VBA work.
