# Plan 107: g13 20-round outcome matrix — execute and record

> **Executor instructions**: Follow step by step. When done, update the
> status row in `plans/README.md` (section "Plans 105–108").
>
> **Drift check (run first)**: `git status --porcelain` clean;
> `scripts/g13_kill_mid_write.py` exists (plan-087-repaired); 
> `docs/specs/crash-recovery.md` exists with the N=20 protocol.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW-MED
- **Depends on**: plan 087 (repaired harness, landed)
- **Category**: evidence / docs
- **Planned at**: commit `5593a4b`, 2026-10-10

## Why this matters

Row 047's original deferral: "20-round outcome matrix in
docs/specs/crash-recovery.md deferred to operator Windows+ACE run."
The repaired harness (plan 087) already proved 20/20
LOCK_STALE-retryable on 2026-10-05, but the PER-ROUND outcome matrix
was never recorded in the spec doc. This machine has Windows + Access
+ ACE — the operator run can happen now. The matrix is the durable
evidence that recovery behaves deterministically under kill timing
variance, not just once.

## Current state (HEAD `5593a4b`)

- `scripts/g13_kill_mid_write.py`: repaired harness; per-round temp
  copies, RNG seed printed, exit-0-only-when-all-rounds-pass, LOCK_STALE
  retry semantics, orphan/lock hygiene.
- `docs/specs/crash-recovery.md`: protocol doc; `:63` documents N=20
  rounds + seed reproducibility; no recorded outcome matrix section.

## Steps

1. Read the harness CLI (`--help` / argparse block) and the protocol
   section of the doc; determine the 20-round invocation (rounds
   count, seed policy — fixed seed for reproducibility) and expected
   output format (per-round outcome lines).
2. Run the matrix on %TEMP% scratch (house hygiene: never the fixture;
   PID-scoped cleanup only; never kill unrelated MSACCESS). Capture
   full per-round output to a temp log; extract: round number, kill
   timing/phase, observed outcome (RECOVERED / LOCK_STALE-retryable /
   FAIL), backup state, and the final exit code + seed.
3. Record in `docs/specs/crash-recovery.md` a new section
   "Recorded 20-round outcome matrix (2026-10-10)": environment line
   (OS build, Access version from the harness log), seed, a compact
   per-round table, the outcome distribution summary, and the exit
   code. Mark it as evidence from ONE machine; the doc's reproducibility
   note (seed) is what makes it re-runnable elsewhere.
4. If ANY round fails non-retryably: STOP, do not edit the doc, report
   the failing round's log excerpt — that is a live defect finding,
   not evidence to file.

## Verify

- Harness exit code 0; orphan/lock checks clean after the run.
- The doc renders a 20-row table; grep confirms no placeholder text.
- No source changes: `git diff --stat` shows only
  `docs/specs/crash-recovery.md`.

## STOP conditions

- Any FAIL (non-LOCK_STALE) round.
- Harness refuses to run 20 rounds (arg limit) — run the documented
  maximum and report; do not hand-edit the script.

## Out of scope

- Harness changes, recovery-code changes, other G-gates.
