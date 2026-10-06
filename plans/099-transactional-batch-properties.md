# Plan 099: Transactional batch property setters — snapshot-restore on partial failure

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 099–104").
>
> **Drift check (run first)**:
> `git diff --stat 2fbe1de..HEAD -- src/ms_access_mcp/backends/access/forms.py src/ms_access_mcp/backends/access/object_kinds.py`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans 077–098 (landed; E6 acReport fix is in)
- **Category**: automation / design-view integrity
- **Planned at**: commit `2fbe1de`, 2026-10-10 (evidence revision)

## Why this matters

LIVE-CONFIRMED 2026-10-10 on a %TEMP% scratch db: a batch property set
that fails partway **commits the half-applied set**. Evidence:

```
set_form_properties("GateF", {"Caption": "partial-applied", "ZzBogusProp": "x"})
  -> {'Caption': True, 'ZzBogusProp': False}
disconnect + reconnect
get_form_properties("GateF")["Caption"] == 'partial-applied'   # persisted!
```

Root cause: every mutation path closes the design view with
`save_flag=1` (acSaveYes) in the `finally` of the shared design-view
helper — including the three BATCH setters when part of the batch
failed. The per-property envelope is transparent, but the object state
is a silent split-brain across sessions.

**Design decision (recorded, operator may flip before execution):**
batches become **transactional** via snapshot-restore — the same pattern
plan 092 proved for VBA writes (`vba.set_vba_code` snapshot + restore).
Alternative (keep transparent-partial, document it) rejected: the
2026-09-16 audit framed partial commit as a defect, and split-brain
state survives reboots.

## Current state (HEAD `2fbe1de`)

- `src/ms_access_mcp/backends/access/forms.py` batch setters and their
  `save_flag=1` sites: `_set_object_properties` (`:177`),
  `_set_object_control_properties` (`:360`),
  `_set_object_section_properties` (`:755`).
- Single setters (`:141`, `:316`, `:716`), add/remove control
  (`:411`, `:466`), event procedure (`:616`) — single-mutation paths,
  atomic already; UNCHANGED by this plan.
- Shared lifecycle: `object_kinds._design_view_work` — open design view,
  `work(live_obj)`, `finally: DoCmd.Close(close_ac, name, save_flag)`.
- Getters use `save_flag=2` (acSaveNo) — correct, unchanged.
- Batch envelope today: `dict[str, bool]` per property (mirror tests in
  `tests/unit/test_backend_object_mirror.py` KIND_TOKENS family).

## Steps

1. **RED (unit, fakes)** — extend the mirror-test family:
   - For each of the three batch helpers: fake object where the FIRST
     property sets fine and the SECOND raises → assert (a) the helper
     read a snapshot of the first property's prior value BEFORE
     mutating, (b) the restore call ran with that snapshot value, (c)
     the reported envelope still shows per-prop `{'p1': True... }` PLUS
     a new additive key `rolled_back: True`, (d) close received
     `save_flag=1` (restore persisted) or `save_flag=2` when restore
     itself fails (inject restore failure → acSaveNo + no raise).
   - All-success batch: `rolled_back: False`, no restore calls.
2. **GREEN** — in each batch helper, inside the SAME single
   `dispatcher.call` closure (apartment safety, 092 precedent):
   snapshot the batch's target property values before the first set
   (read via the same accessor the setter uses; absent property →
   sentinel meaning "do not restore, set would fail anyway"); apply
   each property collecting results; if any failed, best-effort restore
   each successfully-set property to its snapshot value.
   Close semantics: restore succeeded → `save_flag=1` (persist the
   restoration); restore failed → log warning + `save_flag=2` (never
   commit garbage).
3. **Live gate (marker-gated)** — new
   `tests/integration/test_batch_property_atomicity.py` (mirror
   `test_form_lifecycle.py` hygiene helpers): create form; record
   baseline `Caption`; batch `{Caption: "mutated", ZzBogus: "x"}` →
   envelope has a False + `rolled_back: True`; disconnect; reopen;
   assert `Caption` equals the BASELINE (not "mutated"). Orphan + lock
   checks per house pattern.
4. Docs: ledger row.

## Verify

```
PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest tests/unit/test_backend_object_mirror.py tests/unit/test_backend_access.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest tests/integration/test_batch_property_atomicity.py -m com_integration -q -p no:cacheprovider -s
```

Full gates (unit suite, ruff, pyright, budget) + no MSACCESS orphans.

## STOP conditions

- Live Access rejects the restore write for a property the batch just
  set (Jet quirk) — report; do not force.
- Any existing pin asserts partial-commit semantics deliberately
  (characterize first; escalate rather than silently refresh).

## Out of scope

- Single-property setters, add/remove control, event procedures
  (atomic today).
- Any MCP/CLI envelope change beyond the additive `rolled_back` key.
