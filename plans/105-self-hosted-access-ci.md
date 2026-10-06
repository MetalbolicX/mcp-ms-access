# Plan 105: TEST-02 — self-hosted Access CI job (dormant until a runner exists)

> **Executor instructions**: Follow step by step. When done, update the
> status row in `plans/README.md` (section "Plans 105–108").
>
> **Drift check (run first)**: `git status --porcelain` clean;
> `.github/workflows/ci.yml` matches the unit-only workflow (G2/G12/unit
> on windows-latest).

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (dormant until remote push + runner registration)
- **Category**: CI / test coverage
- **Planned at**: commit `5593a4b`, 2026-10-10

## Why this matters

TEST-02 (operator-blocked since the 2026-09-16 audit): COM/ODBC
integration coverage never runs in CI — `ci.yml` documents the COM
gates as local-only because GitHub-hosted runners have no Access. The
gap is real but the fix is not hosted runners (impossible); it is an
**opt-in self-hosted job** that activates only when the operator
registers a Windows machine with Access as a labeled runner. Until
then the job is dormant; after `git push` with a registered runner it
gates regressions the unit suite cannot see.

## Current state (HEAD `5593a4b`)

- `.github/workflows/ci.yml`: single job on `windows-latest` — ruff,
  pyright, `pytest tests/unit/ -q`, tool budget. Uses `astral-sh/setup-uv`.
- Markers: `com_integration: requires Windows + Access COM`
  (`pyproject.toml:32`); default runs exclude it.
- House runner rule for local gates: venv executables directly; the CI
  file's `uv` usage is CI-scoped and stays.

## Steps

1. Add a second job `com-integration` to `ci.yml`:
   - `runs-on: [self-hosted, windows, access]` — never hosted runners.
   - Steps: checkout; create an isolated venv (`.venv-ci`) with
     `python -m venv` + `pip install -e .[dev]` (do NOT reuse `uv` here
     — self-hosted machines follow the house rule of direct venv
     executables); `pytest tests/unit -q` (fast sanity on the runner);
     `pytest tests/integration -m com_integration -q` (the live COM
     suite); `python scripts/g10_repeated_shutdown.py` (shutdown
     resilience smoke). Access version printed via a probe step for
     the run log.
   - Keep the existing header comment accurate: replace "COM/ODBC
     gates are LOCAL-ONLY" wording with a note that the
     `com-integration` job runs ONLY on a self-hosted runner labeled
     `windows, access`, and stays skipped otherwise.
2. No changes to the hosted job. No new secrets. Workflow syntax check:
   `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml',encoding='utf-8'))"`.

## Verify

- YAML parses; hosted job text unchanged (`git diff` shows only the
  added job + comment tweak).
- Local sanity (this machine acts as the would-be runner): run the
  same com_integration invocation once manually and confirm green +
  no orphans.

## STOP conditions

- The self-hosted job would require secrets or broad permissions —
  stop and report.

## Out of scope

- Registering the runner, creating the GitHub repo, pushing (operator).
- Full G-matrix in CI (g13 chaos stays a local, operator-triggered run).
