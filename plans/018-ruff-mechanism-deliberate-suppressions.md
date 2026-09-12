# Plan 018: Correct the ruff rule-selection mechanism and document deliberate suppressions

> **Executor instructions**: Follow this plan step by step. This is a
> config-hygiene plan: the only file you should modify is `pyproject.toml`,
> plus at most ~10 trivial mechanical code sites if an evaluate step calls
> for it. Run every verification command and confirm the expected result
> before moving to the next step. If anything in the "STOP conditions"
> section occurs, stop and report — do not improvise. When done, update the
> status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat ab9a373..HEAD -- pyproject.toml src/ms_access_mcp/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live config before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (plans 016 and 017 are DONE)
- **Category**: tech-debt
- **Planned at**: commit `ab9a373`, 2026-09-11

**Status update (2026-09-11):** The user explicitly authorized revising the
approximately 10-site S101 touch budget. The revised budget permits all 23
measured S101 sites: the original 15 under `src/` plus 8 script sites exposed
by the final repository-wide Ruff gate. Each is a mechanical,
behavior-preserving programmer-invariant suppression with a local reason. Plan 018 is DONE;
plans 019 and 020 remain separate and untouched.

## Execution mode

**Direct edit (lint configuration hygiene).** Config change plus at most a
handful of mechanical one-line fixes; the existing test suite + ruff +
pyright are the gates. Not TDD (no behavior change by intent), not SDD.

## Why this matters

Plan 015 (`plans/015-backend-lint-hygiene.md`) is BLOCKED and its core
premise was wrong. It assumed the `backends/**` lint mask could be lifted by
removing families from `[tool.ruff.lint.per-file-ignores]`. But
`[tool.ruff.lint].select` is only `E,F,W,I,N,C4,UP` — none of the masked
families (S101, ANN, D, ARG002, PLR, BLE, EM, TRY003, FBT, SLF001) are in
`select`, so **every per-file-ignores entry in `pyproject.toml` is dead
config**: removing a family from them changes nothing, and ruff has no
per-file `select`. Enabling a rule family for one directory means: (1) add
it to the global `select`, and (2) keep or add `per-file-ignores` entries
(with justification) for every path that should stay exempt.

A verified census (`uv run ruff check ... --isolated --statistics`, full
command in step 1) shows 533 violations in `backends/**` and 813 in the
rest of `src/` under the union of the masked families. The debt splits into
deliberate patterns (best-effort COM catches, COM-conformance signatures,
pywin32 `Any` returns), structural debt that needs its own plans
(docstrings, raise mechanics), and small tractable groups. This plan fixes
the mechanism, documents the deliberate suppressions so they stop looking
like neglect, and resolves the small groups. Plans 019 and 020 own the two
structural groups.

## Current state

`pyproject.toml:38-58`, exactly as it exists today:

```toml
[tool.ruff.lint]
select = [
    "E",     # pycodestyle errors
    "F",     # pyflakes
    "W",     # warnings
    "I",     # isort
    "N",     # pep8-naming
    "C4",    # comprehensions
    "UP",    # pyupgrade
]
ignore = [
    "E501",  # line too long (handled separately)
    "N802",   # function name in class
    "N803",   # argument name
    "N806",   # variable in function
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "ANN", "D", "ARG002", "PLR", "BLE", "EM", "TRY003", "FBT"]
"src/ms_access_mcp/backends/**" = ["S101", "ANN", "D", "ARG002", "PLR", "BLE", "EM", "TRY003", "FBT", "SLF001"]
"src/ms_access_mcp/doctor.py" = ["S101", "ANN", "D", "ARG002", "PLR", "BLE", "EM", "TRY003", "FBT"]
```

The repo is currently ruff-clean under this configuration — because the
families are off globally, not because the per-file-ignores do anything.

Verified census at `ab9a373` (re-measured in step 1; treat these as the
reference snapshot):

**`src/ms_access_mcp/backends/**` — 533 violations:**

| Code | Count | Classification |
|------|-------|----------------|
| BLE001 | 176 | Deliberate: best-effort COM pattern established by plans 010/016/017 (one corrupt object must not poison a list; property writes are best-effort) |
| D102 | 82 | Structural — plan 019 |
| ARG002 | 57 | Deliberate: COM interface conformance, fixed signatures |
| SLF001 | 53 | Deliberate: intentional private access, e.g. `self._dispatcher._started` |
| EM101 | 35 | Structural — plan 020 |
| ANN401 | 30 | Deliberate: pywin32 COM interop returns `Any` |
| TRY003 | 30 | Structural — plan 020 |
| D107 | 10 | Structural — plan 019 |
| D413 | 9 | Structural — plan 019 (auto-fixable with `--fix`) |
| ANN202 | 8 | Tractable — this plan, step 3 |
| EM102 | 7 | Structural — plan 020 |
| S101 | 6 | Tractable — this plan, step 3 (assert in production code) |
| ANN201 | 6 | Tractable — this plan, step 3 |
| ANN003 | 5 | Tractable — this plan, step 3 |
| FBT001 | 4 | Tractable in count — this plan, step 3 |
| FBT002 | 4 | Tractable in count — this plan, step 3 |
| FBT003 | 4 | Tractable in count — this plan, step 3 |
| D105 | 2 | Structural — plan 019 |
| ANN205 | 1 | Tractable — this plan, step 3 |
| ANN002 | 1 | Tractable — this plan, step 3 |
| ANN001 | 1 | Tractable — this plan, step 3 |
| D103 | 1 | Structural — plan 019 |
| D401 | 1 | Structural — plan 019 |

**Rest of `src/` (everything except `backends/`) — 813 violations.** The
numbers that drive this plan's decisions:

| Family | Outside `backends/` | Decision consequence |
|--------|--------------------|----------------------|
| FBT | 341 (FBT001 175, FBT002 165, FBT003 1) | Cannot go in global `select` — keep out, document |
| D | 334 (cli.py 148, application/** ~158, models.py 21, others ≤3) | Plan 019 scopes it |
| BLE001 | 65 | Too many to fix here — keep out, document |
| ANN401 | 25 | Too many to fix here — keep out, document |
| EM101+EM102+TRY003 | 20 | Plan 020 scopes it |
| S101 | 9 | Within the ≤10 trivial-fix budget — candidate to enable |
| ARG002 | 4 | Within budget — candidate to enable |
| SLF001 | 1 | Within budget — candidate to enable |
| Tractable ANN (ANN001/002/003/201/202/204) | ~14 | With 22 in backends ≈ 36 total — over the ~30 threshold; apply the keep-or-document rule |

The deliberate-pattern precedents the executor must honor (from plans
010/016/017, all DONE): COM listers swallow per-object failures on purpose;
mutator catches are classified into `AccessObjectNotFoundError` /
`AccessBackendError` (see `src/ms_access_mcp/errors.py:93-112`); narrow
catches to `pywintypes.com_error` were explicitly REJECTED (see
`plans/README.md` "Findings considered and rejected").

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Isolated census (backends) | `uv run ruff check src/ms_access_mcp/backends/ --isolated --statistics --select ANN,D,EM,TRY003,BLE,FBT,SLF001,S101,ARG002` | statistics table (exit 1 is fine — violations exist) |
| Isolated census (rest) | `uv run ruff check src/ms_access_mcp/ --isolated --statistics --select ANN,D,EM,TRY003,BLE,FBT,SLF001,S101,ARG002 --exclude src/ms_access_mcp/backends` | statistics table (exit 1 is fine) |
| Full unit | `uv run pytest tests/unit/ -q` | all pass |
| Types | `uv run pyright` | 0 errors |

(`--isolated` ignores `pyproject.toml`, so these commands measure raw debt
independent of config. They print warnings about D203/D211 and D212/D213
incompatibility — cosmetic, ignore them.)

## Scope

**In scope** (the only files you should modify):
- `pyproject.toml` (`[tool.ruff.lint]` `select` and `per-file-ignores` only,
  plus justification comments)
- Trivial mechanical code sites ONLY when a step's evaluate rule allows it
  (≤ ~10 sites originally; revised by explicit authorization to 23 S101
  suppression sites total, plus the existing ARG002/SLF001 touches: renaming
  an unused argument to `_name`, adding a `# noqa: <CODE> - <reason>` marker,
  or converting one
  assert to a raise.) The 8 script sites discovered by the repository-wide
  final gate are included in the revised S101 budget.
  `assert` per step 3's rule)

**Out of scope** (do NOT touch, even though they look related):
- Docstring additions (D family) — plan 019 owns them.
- Raise-mechanics fixes (EM101, EM102, TRY003) — plan 020 owns them.
- Any of the 176 BLE001, 57 ARG002, 53 SLF001, or 30 ANN401 sites in
  `backends/**` — deliberate, documented in step 2, never "fixed".
- The 341 FBT violations outside `backends/` — documented deferral.
- `tests/**` — its per-file-ignores entry stays; asserts and unannotated
  fakes are idiomatic in tests.
- Any behavior change anywhere.

## Git workflow

- Branch: `plan/018-ruff-mechanism`
- One commit per step; conventional commits, e.g.
  `build(lint): correct ruff select/per-file-ignores mechanism and document deliberate suppressions`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1 (evidence, read-only): Measure the cross-directory blast radius

Run BOTH census commands from the commands table. Paste the full statistics
output into your step report. Compare against the "Current state" snapshot:

- If every family's count is within ±20% of the snapshot, proceed using the
  snapshot-driven decisions below.
- If a family's count moved by more than ±20%, re-derive that family's
  decision from its NEW numbers using the same rules (≤ ~10 outside
  `backends/` → candidate to enable; more → keep out and document), and note
  the divergence in your report. This is NOT a STOP by itself.

Document the final numbers in your report — they are the evidence the
deliberate-suppression comments in step 2 cite.

**Verify**: both commands produce statistics tables; numbers recorded.

### Step 2: Fix the mechanism and document the deliberate suppressions

Rewrite the `per-file-ignores` section so that every retained code is LIVE
(present in `select`) and every entry carries an inline justification
comment. Specifically:

1. Add a comment block directly above `[tool.ruff.lint.per-file-ignores]`
   stating the mechanism, one or two lines: rule families are enabled
   globally via `[tool.ruff.lint].select`; entries below are deliberate,
   justified exemptions — a code listed here that is not in `select` is
   dead config and must be removed.
2. **BLE**: keep `BLE` OUT of `select` (176 backends + 65 elsewhere is not
   fixable in this plan and the backends pattern is deliberate). Remove
   `"BLE"` from all three per-file-ignores entries (dead). Above the
   `backends/**` entry add a justification comment citing the census and
   the precedent, e.g.:
   `# BLE001 (176 in backends/**, 65 elsewhere @ <SHA>): deliberate best-effort COM pattern (plans 010/016/017) — one corrupt object must not poison a list. Family stays unselected; do not "fix" these catches.`
3. **ANN401**: keep ANN's `Any`-annotation code out of enforcement (30 in
   backends + 25 elsewhere; structural for pywin32 COM interop). Remove
   `"ANN"` from the per-file-ignores entries (dead; step 3 decides about
   the tractable ANN codes). Add a justification comment citing the census:
   `# ANN401 (30 in backends/**): structural — pywin32 COM interop returns Any. Suppressed by leaving ANN unselected; see plan 018 report.`
4. **ARG002**: add `"ARG002"` to `select`. Keep `"ARG002"` in the
   `backends/**` and `tests/**` per-file-ignores entries (now LIVE) with a
   justification comment: COM adapter methods conform to fixed interface
   signatures; unused args are structural, not negligence. Fix the ~4
   violations elsewhere (snapshot says 4): rename the unused argument with
   a leading underscore or, if the signature is imposed by a Protocol,
   add `# noqa: ARG002 - <reason>`.
5. **SLF001**: add `"SLF001"` to `select`. Keep `"SLF001"` in the
   `backends/**` entry (now LIVE) with a justification comment naming the
   canonical example (`self._dispatcher._started` lifecycle checks in
   `access.py`). Fix the ~1 violation elsewhere (snapshot says 1).
6. **PLR, D, EM, TRY003, FBT, S101**: handled elsewhere — D/EM/TRY003 by
   plans 019/020, FBT/S101 in step 3, PLR below. Remove `"PLR"` from all
   entries (dead) and add one comment line: PLR stays unselected (COM
   adapter surfaces legitimately exceed argument/complexity limits; revisit
   after further dedup).

**Verify**: `uv run ruff check .` → exit 0;
`uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0 errors

### Step 3: Evaluate the small groups — keep or fix, one at a time

Apply each group independently. For EACH group: temporarily add the code(s)
to `select`, run `uv run ruff check .`, and apply the decision rule:

- Total violations ≤ ~30 → fix them all (this is the "keep" branch) and
  keep the code in `select`. Fixes must be mechanical; anything requiring
  judgment gets `# noqa: <CODE> - <reason>` instead.
- Total violations > ~30, or fixes would not be mechanical → remove the
  code from `select` again, and add a justification comment citing the
  measured count (the "document" branch, following plan-015 step-4's
  keep-and-document pattern).

Groups, in order:

1. **Tractable ANN codes**: add the individual codes
   `"ANN001", "ANN002", "ANN003", "ANN201", "ANN202", "ANN204", "ANN205"`
   (NOT the `"ANN"` umbrella — that would also enable ANN401). Snapshot
   predicts ~36 total (22 backends + ~14 elsewhere), which is over the
   threshold → expected outcome is the document branch: comment citing the
   count, defer to a future scoped plan. If the re-measured count is ≤ ~30,
   fix instead (annotations only, `-> None` / arg types; use `Any` where the
   real type is unknowable — never invent a precise type you cannot
   verify).
2. **FBT**: snapshot predicts 353 total (12 backends + 341 elsewhere) →
   document branch. Comment: FBT stays unselected; the boolean-default
   pattern is widespread outside backends and needs its own plan; ruff has
   no per-file `select`, so the 12 backends sites cannot be scoped-enabled.
3. **S101**: snapshot measured 15 total (6 backends + 9 elsewhere;
   `tests/**` keeps its LIVE S101 exemption — asserts are idiomatic in
   tests). With the explicitly authorized revised budget, keep the branch.
   Each site guards a programmer invariant whose removal under `python -O`
   is intentional, so retain the assert with
   `# noqa: S101 - <reason>`. No assert-to-raise conversion is needed; this
   preserves behavior and avoids changing observable exception semantics.

**Verify** (after each group): `uv run ruff check .` → exit 0;
`uv run pytest tests/unit/ -q` → all pass

### Step 4: Final gate

**Verify**: `uv run ruff check .` → exit 0;
`uv run pytest tests/unit/ -q` → all pass (unchanged test count);
`uv run pyright` → 0 errors;
`git diff --stat` → `pyproject.toml` plus at most ~10 one-line code touches.

## Test plan

- No new tests: existing suite + ruff + pyright are the gates.
- Verification per step: ruff clean + full unit suite green.

## Done criteria

- [x] `pyproject.toml` contains no dead per-file-ignores codes (every code
      listed is present in `select`)
- [x] Every retained per-file-ignore code carries an inline justification
      comment citing the reason (and the census count where relevant)
- [x] The deliberate suppressions (BLE001, ARG002, SLF001, ANN401) are
      documented with their measured counts; none of their sites were
      "fixed"
- [x] Each step-3 group has an explicit recorded outcome: kept-and-fixed or
      documented-and-deferred, with the measured count
- [x] `uv run ruff check .` exits 0
- [x] `uv run pytest tests/unit/ -q` exits 0 with unchanged test count
- [x] `uv run pyright` → 0 errors
- [x] `git diff` shows no behavior changes (at most: argument renames to
      `_name`, noqa markers with reasons, assert→raise conversions per the
      step-3 rule, config edits)
- [x] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The config at "Current state" doesn't match the excerpts (drift check
  should catch this first).
- A group's re-measured count exceeds ~30 AND the document branch feels
  wrong (e.g. the violations are all trivial one-liners across 30 files —
  that tension means the threshold needs a human decision).
- An assert→raise conversion would change observable behavior and you're
  unsure which semantics are intended — noqa it and flag instead; if a
  whole group hinges on such a site, STOP.
- A fix appears to require touching a docstring (D) or raise-mechanics
  (EM/TRY003) site — those belong to plans 019/020; leave the code out of
  `select` and note it.
- The revised 23-site S101 suppression budget (explicitly authorized by the
  user) is exhausted and more in-scope S101 sites remain.

## Maintenance notes

- After this plan lands, `plans/README.md` should never again carry a plan
  that assumes per-file-ignores enable rules. The comment block added in
  step 2 is the standing documentation of the mechanism.
- The documented-deferred groups (expected: BLE, ANN401, FBT, PLR, and
  possibly tractable ANN) are the audit trail for future lint plans — any
  future plan touching them should cite and update these comments.
- Plans 019 and 020 build on this plan's mechanism: each adds its family to
  `select`, adds justified per-file-ignores for the directories that stay
  exempt, and fixes `backends/**`.
- Reviewers should scrutinize: every added or retained noqa/ignore must
  carry a reason; bare `noqa` without a rule code and reason is a reject.
