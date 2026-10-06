# Plan 104: Ledger hygiene sweep — stale records from the evidence revision

> **Executor instructions**: as plan 094. Update the status row in
> `plans/README.md` (section "Plans 099–104") when done.
>
> **Drift check (run first)**:
> `git status --porcelain` must be clean; `git log --oneline -1` must
> be a descendant of `2fbe1de`.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans 099–103 (sequenced last so checklist ticks
  reflect final states; may run earlier if the operator reorders)
- **Category**: docs hygiene
- **Planned at**: commit `2fbe1de`, 2026-10-10

## Why this matters

The 2026-10-10 evidence revision found records that misrepresent
state — a stale "pending" that is actually complete, 29 plan files
with unticked checklists despite DONE rows, and one stray file:

1. **Ledger row 047 is stale**: it still says the g13 3/3 CLEAN
   evidence is "void until the repaired harness (0d45a18) is re-run by
   an operator" — the re-run HAPPENED on 2026-10-05 (plan 087:
   20/20 LOCK_STALE-retryable, exit 0, no orphan leaks; commits
   `3d78aa6` chain). Any reader today believes g13 evidence is still
   missing.
2. **Plan checklists 070–098 unticked**: every plan file in that range
   keeps `- [ ]` boxes whose outcomes are evidenced DONE in the
   ledger rows (example: `plans/098:196` "unit-pinned" — the pin
   exists at `tests/unit/test_backend_access.py:1248`). Stale boxes
   make real pending work indistinguishable from noise.
3. **Root `nul` stray file**: still on disk (gitignored — invisible
   to status). The 2026-09-16 hygiene finding's sibling
   (`pnpm-workspace.yaml`) was removed; `nul` was not.

## Steps

1. **Row 047 annotation** — append to the row's supersession note:
   "RE-RUN COMPLETED 2026-10-05 via plan 087 (20/20 LOCK_STALE
   retryable, exit 0, no orphan leaks; `3d78aa6`)" — one sentence, no
   rewriting of history.
2. **Checkbox sweep** — for each `plans/0[789]*.md` (070–098) and the
   cycle sections' own checklists: tick (`- [x]`) ONLY boxes whose
   outcome is directly evidenced by the ledger row (commit hashes,
   gate results). For any box that CANNOT be evidenced from the row:
   leave unticked and append a one-line `> NOTE (2026-10-10): outcome
   not evidenced in ledger row — left open` so the residue is visible
   instead of silently ticked. Mechanical pass; no wording changes to
   box text.
3. **Delete the stray** — `rm nul` from the repo root (it is
   gitignored and untracked; plain filesystem delete, no `git rm`).
4. Ledger row for this plan + commit.

## Verify

```
grep -c "^- \[ \]" plans/0[789]*.md | awk -F: '$2>0'   # remaining unticked boxes each carry a NOTE line
ls nul 2>&1                                        # No such file
git diff --stat                                     # docs-only
```
Standard gates are unnecessary for docs-only, but run the unit suite
once (cheap, catches accidental file damage from the sweep).

## STOP conditions

- Any checklist box whose evidence is CONTRADICTED by the row (row
  says DONE, box outcome looks false) — stop and report; that is a
  real finding, not hygiene.
- The `nul` file refuses deletion on the operator's shell — report
  the exact error (Windows reserved-name handling), do not improvise
  workarounds.

## Out of scope

- Ticking pre-070 plan files (their cycles predate the DONE-row
  discipline; leave for a future sweep if ever wanted).
- Any code change.
