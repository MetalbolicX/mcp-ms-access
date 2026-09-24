# Plan 064: Pin the toolchain — explicit pyright config and mysql-connector floor

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- pyproject.toml uv.lock`
> On any change, compare "Current state" against the live files before proceeding.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW (MED only if pinning surfaces new pyright diagnostics — see STOP)
- **Depends on**: none
- **Category**: dx | deps
- **Planned at**: commit `a7f2edb`, 2026-09-24

## Why this matters

CI's G2 type gate runs bare `uv run pyright` with ZERO project configuration
(no `[tool.pyright]` in pyproject.toml, no pyrightconfig.json — both verified
absent). Check scope and strictness are whatever the resolving pyright
version's defaults are, so results can drift between machines and CI with no
repo-visible change — unlike ruff, whose config is meticulously documented in
pyproject.toml:35–101. Separately, the `migrate` extra declares
`mysql-connector-python>=8.0.0` while uv.lock resolves 26.7.0 — a two-major
manifest/lock drift where a lock-free resolve could pick an 8.x nobody tests.

This plan is config-only (no TDD — the gates ARE the verification).

## Current state

- `pyproject.toml` (102 lines): deps at lines 5–16 — `mcp>=1.27,<2.0`,
  `pydantic>=2.0.0`, `pyodbc>=5.0.0`, `pywin32>=306; sys_platform=='win32'`;
  dev extra includes `pyright>=1.1.300` (locked 1.1.411); migrate extra:
  ```toml
  migrate = ["psycopg2-binary>=2.9.0", "mysql-connector-python>=8.0.0"]
  ```
  No `[tool.pyright]` section anywhere.
- `.github/workflows/ci.yml:47-48` runs bare `uv run pyright` (G2).
- `uv.lock`: `mysql-connector-python` version `26.7.0`.
- Locked-current (verified, leave alone): mcp 1.30.0, pydantic 2.13.5,
  pyodbc 5.3.0, pywin32 312, openpyxl 3.1.5, psycopg2-binary 2.9.13
  (psycopg2-vs-psycopg3 is a settled tradeoff — do not change).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Baseline types | `uv run pyright` | record error count BEFORE config change |
| Lint | `uv run ruff check .` | exit 0 |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Lock consistency | `uv lock --check` | exit 0 ("lockfile is up to date") |
| Sync check | `uv sync --extra dev` | exit 0, no version changes |

## Scope

**In scope**:
- `pyproject.toml` (add `[tool.pyright]`; raise mysql floor)

**Out of scope**:
- `uv.lock` (must remain unchanged — the floor raise matches the locked 26.7.0)
- Any source file touched to satisfy newly-surfaced pyright diagnostics beyond
  the trivial threshold in STOP conditions (that cleanup, if needed, is a
  follow-up plan; plan 070 also retires the known `migration.py:574`
  pre-existing error if it appears in your baseline).

## Git workflow

- Branch: `plan/064-toolchain-pins`
- Commit: `chore: pin pyright config and mysql-connector floor` (config-only).
- Do NOT push.

## Steps

### Step 1: Record the pyright baseline

**Verify**: `uv run pyright` → record the exact error count and locations
(expected: 0 or the single known `migration.py:574` item owned by plans
050/070). This number is the acceptance bound for Step 2.

### Step 2: Add explicit pyright configuration

Append to `pyproject.toml`:

```toml
[tool.pyright]
include = ["src"]
typeCheckingMode = "basic"
```

(Rationale to keep in a trailing comment: freeze the G2 gate's meaning;
`src`-only scope — tests and scripts are not the gate's subject.)

**Verify**: `uv run pyright` → error count ≤ baseline; ideally the `src`-only
scope makes it 0. If new diagnostics appear, fix ONLY if trivially mechanical
(typos, obvious annotations) AND ≤5 sites; otherwise STOP and report the list.

### Step 3: Raise the mysql-connector-python floor

Change the migrate extra to:

```toml
migrate = ["psycopg2-binary>=2.9.0", "mysql-connector-python>=26.7.0"]
```

**Verify**: `uv lock --check` → exit 0 (lock already at 26.7.0, so no lock
change); `uv sync --extra dev` → exit 0 with no package changes.

### Step 4: Full gates

**Verify**: `uv run ruff check .` → exit 0; `uv run pytest tests/unit/ -q` →
all pass; `uv run python scripts/tool_budget.py` → PASS.

## Test plan

No new tests (config-only). Verification is the gate set above.

## Done criteria

- [ ] `[tool.pyright]` exists in pyproject.toml with include + mode
- [ ] `uv run pyright` error count ≤ Step-1 baseline
- [ ] `uv lock --check` exit 0; `git diff --stat` shows ONLY pyproject.toml
- [ ] ruff / unit tests / tool_budget all green
- [ ] `plans/README.md` status row updated

## STOP conditions

- Step 2 surfaces >5 new pyright diagnostics or any non-trivial typing design
  question — report the list instead of fixing.
- `uv lock --check` reports the lockfile needs changes after the floor raise
  (unexpected — the lock already satisfies >=26.7.0).
- pyproject.toml drifted from the "Current state" description.

## Maintenance notes

- Stricter modes (`standard`, `strict`) are a separate adoption track; do not
  bundle. Track the `migration.py:574` pre-existing error via plans 050/070.
- If CI later adds a Linux job, `include = ["src"]` keeps pyright honest about
  win32-only imports via `sys_platform` markers already in the deps.
