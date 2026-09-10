# Plan 015: Narrow the blanket ruff per-file-ignores on backends/**

> **Executor instructions**: Follow this plan step by step. This is an
> incremental lint-hygiene plan: re-enable ONE rule group at a time and fix
> only what that group surfaces. Never fix more than the current step's rule
> group. If a rule group surfaces more than ~30 violations, stop and report
> instead of mass-editing. When done, update the status row in
> `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2178509..HEAD -- pyproject.toml src/ms_access_mcp/backends/`
> Plan 009 and 010 should be DONE first (they shrink and stabilize
> `access.py`, the largest offender). If they are not DONE, you may still
> proceed but expect more violations in `access.py`.

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/009-form-report-dedup.md, plans/010-com-error-observability.md (recommended, not blocking)
- **Category**: tech-debt
- **Planned at**: commit `2178509`, 2026-09-09

## Execution mode

**Direct edit (incremental lint remediation).** Config change plus targeted
fixes; existing tests guard behavior. Not TDD (no behavior change by
intent), not SDD.

## Why this matters

`pyproject.toml:57` disables a broad rule set for `backends/**`
(ANN, D, PLR, BLE, EM, TRY, FBT, SLF001) — the lint mask is widest exactly
where the code is most complex (`access.py` 2,374 lines, 100+ broad
catches). Rule groups like BLE (blind except) and TRY (exception
antipatterns) being off means NEW error-handling debt accumulates without
any signal, even after plan 010 fixes the worst existing cases.

## Current state

- `pyproject.toml` `[tool.ruff.lint.per-file-ignores]` — the `backends/**`
  entry at ~L57 disables: `ANN` (annotations), `D` (docstrings), `PLR`
  (pylint refactor: too-many-*), `BLE` (blind except), `EM` (exception
  message), `TRY` (tryceratops), `FBT` (boolean traps), `SLF001` (private
  access).
- Repo currently ruff-clean under this configuration.
- Known intentional private accesses exist (e.g. `self._dispatcher._started`
  checks in `access.py`) — those justify keeping `SLF001` off or targeted
  `# noqa: SLF001` markers; do not blindly re-enable everything.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Lint (backend only) | `uv run ruff check src/ms_access_mcp/backends/` | exit 0 |
| Full unit | `uv run pytest tests/unit/ -q` | all pass |
| Types | `uv run pyright` | 0 errors |

## Scope

**In scope**:
- `pyproject.toml` (per-file-ignores only)
- `src/ms_access_mcp/backends/**` (lint fixes for the re-enabled rule ONLY)

**Out of scope**:
- Any behavior change (message text, control flow semantics).
- Re-enabling these rules outside `backends/**`.
- Rule groups not listed in the steps below.

## Git workflow

- Branch: `plan/015-backend-lint-hygiene`
- One commit per rule group; e.g.
  `style(backends): re-enable BLE with targeted exception narrowing`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Re-enable ANN and D

Remove `ANN` and `D` from the `backends/**` ignore list. Fix missing
annotations/docstrings (the repo already documents public methods — most
violations will be private helpers; match the existing docstring style).

**Verify**: `uv run ruff check .` → exit 0; `uv run pytest tests/unit/ -q` → all pass

### Step 2: Re-enable EM and TRY

Remove `EM` and `TRY`. Fix violations with ruff's suggested patterns (assign
message to a variable before raise; `raise ... from exc` chaining). Do NOT
change WHAT is raised or caught — only the mechanics.

**Verify**: `uv run ruff check .` → exit 0; `uv run pytest tests/unit/ -q` → all pass

### Step 3: Re-enable BLE (blind except)

Remove `BLE`. This is the risky step. For each flagged catch:
- If plan 010 is DONE: most mutator catches are already classified; the
  remaining intentional broad catches (COM best-effort in listers) get
  `except Exception:  # noqa: BLE001 - pywin32 raises bare Exception for COM errors`
  with that exact justification comment.
- If 010 is NOT done: add the same noqa-with-justification markers WITHOUT
  changing behavior.
Never narrow a catch to a specific exception type in this plan.

**Verify**: `uv run ruff check .` → exit 0; `uv run pytest tests/unit/ -q` → all pass;
`grep -c "noqa: BLE001" src/ms_access_mcp/backends/access.py` → record the
count in the commit message (this is the residual-debt metric)

### Step 4: Evaluate PLR, FBT, SLF001 — keep or document

Try removing each of `PLR`, `FBT`, `SLF001` one at a time:
- If violations ≤ 10 and fixes are mechanical (e.g. targeted noqa for
  intentional private access like `_dispatcher._started`): fix and keep the
  rule enabled.
- If violations are structural (PLR0913 too-many-args on genuine COM
  surfaces): KEEP the rule ignored and add a comment in `pyproject.toml`
  above the entry explaining why (`# PLR: COM adapter surfaces legitimately
  exceed arg limits; revisit after dedup`).
Do not force-fit fixes to satisfy a rule that doesn't apply.

**Verify**: `uv run ruff check .` → exit 0; final `uv run pytest tests/unit/ -q` → all pass;
`uv run pyright` → 0 errors

## Test plan

- No new tests: existing suite + ruff are the gates.
- Verification per step: ruff clean + full unit suite green.

## Done criteria

- [ ] `pyproject.toml` `backends/**` ignore list contains only rule groups
      with an inline justification comment
- [ ] `uv run ruff check .` exits 0
- [ ] `uv run pytest tests/unit/ -q` exits 0 with unchanged test count
- [ ] `uv run pyright` → 0 errors
- [ ] `git diff` shows no behavior changes (exception types, messages,
      control flow all preserved; only annotations, docstrings, raise
      mechanics, noqa markers)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- A rule group surfaces > ~30 violations (the group needs its own plan).
- A "lint fix" would require changing behavior (e.g. TRY300 else-block
  restructuring that alters semantics you're not sure of) — noqa with
  justification or skip the site, and note it.
- Re-enabling a rule conflicts with the deliberate COM best-effort pattern
  documented in plan 010 (lister swallows).

## Maintenance notes

- The residual `noqa: BLE001` count in `access.py` is the standing
  error-handling debt metric — it should only ever go DOWN after this plan.
- Reviewers should scrutinize: every added noqa must carry a justification
  comment; bare `noqa` without a rule code and reason is a reject.
- If plan 010 lands after this plan, it must re-verify BLE compliance as
  part of its own gate.
