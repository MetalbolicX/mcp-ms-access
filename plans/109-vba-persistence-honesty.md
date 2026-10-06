# Plan 109: VBA write persistence honesty — created modules vanish on close

> **Executor instructions**: Investigate first (STOP gate at step 1);
> the fix mechanism must be proven against live Access before
> implementation. When done, update the status row in
> `plans/README.md` (section "Plans 105–108", or its successor).

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plan 092 lineage; discovered during plan 106's live gate
- **Category**: automation / envelope honesty / NEW DEFECT
- **Planned at**: 2026-10-10 (discovered executing plan 106)

## Why this matters

DISCOVERED LIVE 2026-10-10 (isolated subprocess probes, reproducible):
`create_module` reports `success: true`, but the VBE component exists
only in the live Access instance's memory — a FRESH session lists zero
modules, and `delete_module` then honestly returns False for the ghost
(`operation failed: delete_module returned False`). The 092-cycle live
gates proved REFUSAL paths only; positive-persistence of VBE writes
was never verified until plan 106's live test forced the question.

The same missing-save suspicion applies to ALL VBE-content writes:
`create_module`, `set_vba_code`, `add_vba_procedure`,
`vba_replace_procedure` (and `rename_module`/`delete_module` outcomes
against persisted state). Every one of them reports success while the
change may silently vanish on disconnect — the exact "transparent
lie" envelope class the 080/081 honesty cycle hunted.

Evidence pins: `tests/integration/test_vba_write_optin_live.py::test_created_module_persists_across_sessions`
(currently `xfail` non-strict with this plan's reference; flips to
XPASS when fixed).

## Steps

1. **Investigate the persistence mechanism (STOP gate)** — read how
   each VBE write path closes its session (`object_kinds._design_view_work`
   save_flag usage vs `app.Quit()` paths in the adapter/dispatcher);
   determine why the VBA project is not saved. Establish the correct
   save mechanism for VBA changes against live Access (candidates:
   saving the container object with `DoCmd.Close(, , acSaveYes)` while
   the module is open in design view; triggering a database-level
   save; `VBE.ActiveVBProject.SaveAs` semantics). PROVE the mechanism
   with a throwaway %TEMP% probe script BEFORE writing product code.
   If no mechanism persists VBA changes programmatically, STOP and
   report — the honest fix would then be envelope truth (report
   "created (session-local); persists only after database save") plus
   docs, not a fake save.
2. **RED (live-gated + fake)** — flip the pinned xfail expectation to
   a real assertion (module listed in a fresh session after create;
   delete-module then succeeds and a third session confirms absence).
   Add the same persistence pin for `set_vba_code` (set code in
   session A, read code in session B).
3. **GREEN** — apply the proven save mechanism in the shared VBA
   write path (all five content write tools), keeping apartment
   discipline (single dispatcher closure) and the 092 safety
   behaviors intact.
4. **Envelope honesty sweep** — while there: `delete_module` of a
   nonexistent module should Err with "module not found", not
   "returned False" (match the 080/081 message discipline).
5. Live gates for create/persist/delete and set-code/persist/read;
   orphan/lock hygiene; full qualify.

## STOP conditions

- No programmatic save mechanism exists (see step 1) — switch to the
  documented session-local envelope contract instead, with operator
  sign-off.
- The save mechanism persists UNRELATED user pending changes
  (over-saving hazard) — report the blast radius before adopting it.

## Out of scope

- run_macro, form/report writes (separate persistence semantics,
  already design-view-gated).
- The opt-in gate itself (plan 106, landed).
