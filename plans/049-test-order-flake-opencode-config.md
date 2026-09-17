# Plan 049: Isolate the test-order pollution behind the opencode_config S2 flake

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 655f712..HEAD -- tests/unit/test_opencode_config.py src/ms_access_mcp/opencode_config.py tests/unit/test_session_backup.py tests/unit/test_maintenance_recovery.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1 (a suite that lies 1-in-N runs destroys trust in every gate)
- **Effort**: M (diagnosis dominates; the fix is likely S)
- **Risk**: LOW (test-infrastructure only; production code untouched unless
  the root cause is a real ordering bug in `opencode_config`)
- **Depends on**: none. Evidence gathered on the plan-047 tree (`655f712`);
  the flake may pre-exist on `main` — Step 1 establishes that.
- **Category**: tests
- **Planned at**: commit `655f712` (plan-047 branch tip), 2026-09-16

## Why this matters

`tests/unit/test_opencode_config.py::TestS2PreserveUnrelated::test_existing_mcp_engram_and_permission_preserved`
fails in roughly 1-in-3 full-suite runs but always passes in isolation and
in subsets. A nondeterministic suite means any plan's "all tests pass" gate
is a coin flip — and this repo's whole execution model (improve plans with
command-verified steps) depends on that gate being trustworthy. The polluter
is invisible to normal inspection (no `environ`/`chdir`/`monkeypatch` in the
suspect files), so this plan is reproduce-first: capture the failure with
detail, bisect to the polluting test/module, fix the shared state, then pin
stability with repeated full-suite runs.

## Current state

Evidence matrix (2026-09-16, plan-047 verification session):

| Run configuration                              | Result        |
|------------------------------------------------|---------------|
| Full suite, run 1                              | **FAILED** (1/1304) |
| Full suite, runs 2 and 3                       | passed (1304) |
| `test_opencode_config.py` alone                | passed        |
| opencode_config + plan-047 test files only     | passed (60)   |
| Full suite with plan-047 files deselected      | passed (1277) |

- The failing test (`tests/unit/test_opencode_config.py:73-100`) writes
  `tmp_path/opencode.json` with an unrelated `mcp.engram` entry +
  `permission` block, calls `register(target, entry)`, and asserts
  `isinstance(result, Ok)` plus merge survival. All filesystem interaction
  is `tmp_path`-scoped; no env or cwd reads.
- `src/ms_access_mcp/opencode_config.py` module-level state is minimal:
  `Result = Ok | Err` (:40) and `_OPENCODE_SCHEMA` (:246). No `@cache`, no
  globals mutated by `register`/`merge_entry`/`write_atomic`.
- `register` performs load → merge → `write_atomic` (with backup + rollback,
  per plan 005's SDD) — a `Result`-returning pipeline where an `Err` can
  originate from load failures, merge conflicts, or write/backup failures.
- Plan-047's new files (`tests/unit/test_session_backup.py`,
  `tests/unit/test_maintenance_recovery.py`) are NOT the polluter by
  themselves (subset run passes) — but they may shift ordering/timing enough
  to expose a pre-existing polluter elsewhere in the suite.
- Grep confirmed: neither the opencode tests nor the plan-047 tests use
  `environ`, `chdir`, `monkeypatch`, or `gettempdir`.
- Repo conventions: tests must be order-independent (plan 028 fixed a
  collection-phase pollution the same way — subprocess-isolated guard);
  reproduce-first precedent from plans 002/030.

## Commands you will need

| Purpose          | Command                                                       | Expected on success |
|------------------|---------------------------------------------------------------|---------------------|
| Full suite       | `uv run pytest tests/unit/ -q -p no:cacheprovider`            | all pass            |
| Single test      | `uv run pytest tests/unit/test_opencode_config.py -q`         | all pass            |
| Verbose capture  | `uv run pytest tests/unit/ -q -p no:cacheprovider -v 2>&1 | tee <file>` | see Step 1 |
| Lint / typecheck | `uv run ruff check .` / `uv run pyright src/ms_access_mcp/`   | 0 errors            |

## Scope

**In scope**:

- The polluting test file(s) identified by Step 2's bisection (isolation
  fix only — fixture/`tmp_path`/env discipline in THAT file)
- `src/ms_access_mcp/opencode_config.py` — ONLY if Step 2 proves the root
  cause is ordering-dependent behavior in the module itself (e.g.,
  key-order-dependent merge or write); then fix with a determinism pin
- `tests/unit/test_opencode_config.py` — only to add a determinism pin if
  the root cause lives in the module

**Out of scope** (do NOT touch):

- Wholesale test-suite reordering, `pytest-randomly` adoption, or xdist —
  those are CI-policy decisions, not this fix.
- Plan-047 test files unless bisection names them.
- `conftest.py` global fixtures unless bisection names them.

## Git workflow

- Branch: `plan/049-test-order-flake-opencode-config`.
- Commits: `test(<area>): isolate <polluter> state leakage` (conventional).
- Do NOT push unless instructed.

## Steps

### Step 1: Reproduce with captured detail (READ-ONLY diagnosis)

Loop the full suite up to 10 times, capturing verbose output each iteration:

```powershell
foreach ($i in 1..10) {
  uv run pytest tests/unit/ -q -p no:cacheprovider 2>&1 |
    Select-String -Pattern "test_existing_mcp_engram" -Context 2,25 |
    Out-File "flake_run_$i.txt"
  if ($LASTEXITCODE -ne 0) { break }
}
```

If it fires, the captured context contains the actual assertion/error —
record which assert failed (`isinstance(result, Ok)` vs a merge-survival
assert vs something else). That message discriminates the hypotheses in
Step 2.

**Verify**: either a captured failure (proceed to Step 2 with the message),
or 10 consecutive green runs (proceed to Step 3 — the flake is rarer than
1-in-10; stability hardening still applies, but say so in the report).

### Step 2: Bisect the polluter

Ordered hypotheses, cheapest discriminator first:

1. **Hash-seed-dependent ordering**: run Step 1's loop with
   `$env:PYTHONHASHSEED=0` (fixed). If failures stop entirely across ≥10
   runs while random-seed runs still fail, the root is dict-iteration-order
   dependence — almost certainly in `merge_entry`/`write_atomic` key
   handling or in the polluter constructing files whose key order varies.
2. **Test bisection (if not hash-seed)**: binary-search the suite with
   `--deselect` halves around a failing seed/run until one file (then one
   test) reliably flips S2. Combine: polluter-file + S2 in one command must
   reproduce.
3. **Shared-filesystem state**: if the failing assert is an `Err` from
   register, inspect whether `timestamped_backup`/`write_atomic` collide
   with another test writing the same path (check for
   `ms_access_backups`/`ms_access_dev` tempdir sharing between maintenance
   or session-backup tests and opencode_config).

**Verify**: a one-line repro command of the form
`uv run pytest tests/unit/test_<polluter>.py tests/unit/test_opencode_config.py -q`
(or the PYTHONHASHSEED discriminator) that fails reliably, plus a named
root cause.

### Step 3: Fix + pin

- If the polluter is a test: scope its state (tmp_path instead of shared
  paths, monkeypatch instead of raw env, explicit cleanup). Do not weaken
  the test's assertions.
- If the root is in `opencode_config` (hash-order): write the failing pin
  first (RED) — e.g., construct two inputs whose dict insertion orders
  differ but content matches; assert `merge_entry`/`register` outputs are
  identical — then fix (canonical ordering, e.g., preserve original file
  key order explicitly or `sort_keys` where schema-safe). GREEN follows.
- Regression gate: 5 consecutive full-suite runs green, including at least
  2 with `PYTHONHASHSEED` left random.

**Verify**:
- The Step 2 repro command now passes.
- 5× `uv run pytest tests/unit/ -q -p no:cacheprovider` → all pass each time.
- `uv run ruff check .` and `uv run pyright src/ms_access_mcp/` → clean.

## Test plan

- The determinism pin (Step 3, module-root case) becomes the permanent
  regression test; model it on the existing S-series tests in
  `test_opencode_config.py`.
- Polluter-isolation fixes are verified by the repro command flipping red
  → green.
- No new production behavior is asserted anywhere.

## Done criteria

- [ ] Root cause named in the plan row (one line: file + mechanism)
- [ ] The minimal repro command recorded and passing post-fix
- [ ] 5 consecutive full-suite runs green (2+ with random PYTHONHASHSEED)
- [ ] `uv run ruff check .` exit 0; `uv run pyright src/ms_access_mcp/` 0 errors
- [ ] No production file modified unless the proven root cause is in
      `opencode_config.py` (if so, the RED test pinning it exists)
- [ ] `plans/README.md` row 049 updated

## STOP conditions

Stop and report back (do not improvise) if:

- 10 + 10 (fixed-seed) runs in Step 1 never reproduce and Step 3's
  stability gate is green — report "unreproduced, hardened or not";
  do NOT mark DONE on a hunch.
- Bisection names a polluter whose fix would change its assertions'
  meaning (weakening coverage) — that is an operator decision.
- The root cause turns out to be pytest/uv-level (collection order, plugin
  interference) — report; do not add plugins or reorder the suite.
- A verification fails twice after a reasonable fix attempt.

## Maintenance notes

- If PYTHONHASHSEED discipline matters here, other merge/write modules
  (`credentials.py` JSON round-trips) deserve the same determinism pin —
  record as a follow-up finding, do not fix in this plan.
- The 1-in-N rate means DONE here is a statistical claim; if it ever
  recurs, the repro command from Step 2 is the starting point, not a new
  investigation.
