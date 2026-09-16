# Plan 041: One-command verification baseline (fast qualify gate + pyright CI/local alignment)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat dde0998..HEAD -- pyproject.toml .github/workflows/ci.yml scripts/tool_budget.py scripts/step5_qualify.py README.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests / dx
- **Planned at**: commit `dde0998`, 2026-09-16

## Why this matters

There is no one-command way to know the codebase works. CI runs exactly four
things (ruff, full-project pyright, unit tests, tool budget), while the 13-gate
qualification matrix lives scattered across ~6 manual scripts that an operator
must remember to run. Two consequences: (1) every risky change lacks a fast,
reproducible local signal, and (2) the pyright gate diverges between CI and
developer practice — CI runs whole-project `uv run pyright` with no config
file, while plan 037 documented that full pyright "times out at 10 min (known
slow); plan only required subtree", so developers validate with subtree-scoped
runs that are NOT the signal CI enforces. This plan is the prerequisite for the
contract-fix plans (042+) that follow it: they need a trustworthy fast gate.

## Current state

- `.github/workflows/ci.yml` (54 lines) — runs on `windows-latest`,
  `timeout-minutes: 15`, steps: `uv sync --extra dev`, `uv run ruff check .`,
  `uv run pyright`, `uv run pytest tests/unit/ -q`,
  `uv run python scripts/tool_budget.py`. Header comment declares COM/ODBC
  gates G1, G3-G7, G9-G11, G13 LOCAL-ONLY.
- `pyproject.toml` — has NO `[tool.pyright]` section and no `pyrightconfig.json`
  exists. pytest markers declared: `com_integration`, `odbc_integration`, `slow`.
  Dev extra exists: `dev = [pytest>=8.0.0, ruff>=0.5.0, pyright>=1.1.300]`.
- `scripts/step5_qualify.py` — the 13-gate local qualification runner (exists;
  do not rewrite it — this plan adds a FAST gate alongside it).
- `scripts/tool_budget.py` — G12; builds each profile via
  `ms_access_mcp.server.build_server(profile)` and asserts `core <= 30` tools
  (literal `30` at line 46) plus profile-composition invariants.
- Documented evidence (plans/README.md, plan 037 row): "full `pyright` on the
  entire project times out at 10 min (known slow on this codebase; plan only
  required subtree)". CI job timeout is 15 min — pyright is the dominant cost.
- There is no Makefile/justfile/noxfile/task runner of any kind (verified absent).

## Commands you will need

| Purpose    | Command                                  | Expected on success |
|------------|------------------------------------------|---------------------|
| Install    | `uv sync --extra dev`                    | exit 0              |
| Lint       | `uv run ruff check .`                    | exit 0, no errors   |
| Typecheck  | `uv run pyright`                         | exit 0              |
| Unit tests | `uv run pytest tests/unit/ -q`           | all pass (1247 passed / 5 skipped baseline) |
| Budget     | `uv run python scripts/tool_budget.py`   | PASS                |
| New gate   | `uv run python scripts/qualify.py`       | exit 0, all gates green |

## Scope

**In scope** (the only files you should modify):
- `scripts/qualify.py` (create)
- `pyproject.toml` (add `[tool.pyright]` section only)
- `README.md` (Development section addition)
- `tests/unit/test_qualify_script.py` (create — smoke tests for the aggregator)

**Out of scope** (do NOT touch):
- `scripts/step5_qualify.py` and the G*-scripts — the 13-gate matrix stays as-is.
- `.github/workflows/ci.yml` — CI keeps running its current commands; the goal is
  making local == CI, not changing CI in this plan.
- `tests/integration/**` — integration gating stays local-only.
- Any `src/` file.

## Git workflow

- Branch: `plan/041-qualify-baseline`
- Conventional commits, English, no AI attribution (repo style: `feat(scripts): ...`, `docs: ...`).
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Add an explicit pyright configuration

Add a `[tool.pyright]` section to `pyproject.toml` that makes the typecheck
scope explicit and identical for CI and local runs:

```toml
[tool.pyright]
include = ["src", "tests", "scripts"]
typeCheckingMode = "standard"
pythonVersion = "3.11"
```

If the live repo already passes `uv run pyright` whole-project within the CI
timeout, this config must NOT narrow away currently-checked files — its purpose
is determinism (same command, same scope, everywhere), not weakening the gate.
Measure first: run `uv run pyright` once and note wall time.

**Verify**: `uv run pyright` → exit 0. Record wall time in the commit message.
**Verify**: `uv run ruff check .` → exit 0 (config-only change).

### Step 2: Create `scripts/qualify.py` — the fast local gate

A stdlib-only script that runs the exact CI commands in order, streaming output,
and prints a gate summary. Shape:

```python
"""Fast local qualification gate — mirrors the CI checks exactly.

Runs: ruff (G2), pyright (G2), unit tests, tool budget (G12).
The full 13-gate COM/ODBC matrix remains in scripts/step5_qualify.py and
requires a local Microsoft Access installation.
"""
```

Requirements:
- Run each step with `subprocess.run(["uv", "run", ...])` (list form, no shell).
- Continue after failures so the summary shows ALL red gates, not just the first.
- Exit code = 0 iff every gate passed.
- Print a final table: `GATE ruff ... OK` / `FAIL (exit N)` per gate.
- Support `--skip-pyright` flag (documented escape hatch for quick iteration;
  the default MUST include pyright).

**Verify**: `uv run python scripts/qualify.py` → exit 0, four gates all OK.
**Verify**: `uv run python scripts/qualify.py --skip-pyright` → exit 0, pyright shown as SKIPPED.

### Step 3: Smoke-test the aggregator

Create `tests/unit/test_qualify_script.py`:
- Import the script's gate-list builder (structure the script so the gate list
  is a module-level data structure importable without executing anything).
- Assert the gates are exactly: ruff, pyright, unit tests, tool budget — in that
  order — and that `--skip-pyright` removes exactly one gate.
- Do NOT invoke subprocesses from the unit test.

**Verify**: `uv run pytest tests/unit/test_qualify_script.py -q` → all pass.

### Step 4: Document in README

Add a short "Development" section to `README.md` (after Quick start):

```markdown
## Development

Fast local gate (mirrors CI): `uv run python scripts/qualify.py`
Full 13-gate qualification (requires local MS Access): `uv run python scripts/step5_qualify.py`
```

**Verify**: `uv run ruff check .` → exit 0; `uv run pytest tests/unit/ -q` → all pass.

## Test plan

- New: `tests/unit/test_qualify_script.py` (gate list shape, skip flag, ordering).
- Existing suite must stay green: `uv run pytest tests/unit/ -q` → 1247+ passed.

## Done criteria

- [ ] `uv run python scripts/qualify.py` exits 0 with all four gates OK
- [ ] `uv run pyright` exits 0 with the new config, and the checked file set is
      not smaller than before (spot-check: a deliberate type error in
      `src/ms_access_mcp/safety.py` is caught, then reverted)
- [ ] `uv run pytest tests/unit/ -q` exits 0 (baseline 1247 passed / 5 skipped preserved)
- [ ] `uv run python scripts/tool_budget.py` prints PASS
- [ ] README documents both qualify commands
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- `uv run pyright` with the new config reports NEW errors that whole-project
  pyright did not report before (config changed inference, not just scope) —
  stop and report the delta; do not fix src files in this plan.
- The baseline unit suite is not green before you start (1247 passed / 5 skipped)
  — stop; the tree is not at the state this plan assumes.
- `scripts/step5_qualify.py` turns out to already provide exactly this fast gate
  (read it first) — stop and report; the plan may be redundant.

## Maintenance notes

- Plans 042+ rely on this gate as their fast feedback loop; keep it fast (<2 min
  excluding pyright). If pyright wall time still dominates, a follow-up may add
  per-subtree pyright invocation to `qualify.py` — but only with a matching CI
  change, so local and CI never diverge again.
- A reviewer should scrutinize: the `--skip-pyright` escape hatch must remain
  opt-in and loud (printed as SKIPPED in the summary), never the default.
- Deferred out of this plan: pre-commit hooks, `.env.example`, `AGENTS.md`
  (separate DX finding), optional self-hosted CI job for COM/ODBC integration
  markers (needs runner with Access — operator decision).
