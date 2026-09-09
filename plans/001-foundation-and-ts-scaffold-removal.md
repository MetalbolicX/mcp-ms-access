# Plan 001: Create the foundation and remove the TS scaffold

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: This is a greenfield repo — nothing exists yet
> outside `plans/`. Confirm the oracle exists:
> `git -C D:\code\python\ms-access-mcp-server rev-parse --short HEAD` → `c8beaab`
> (or later on `rescript/045-winax-teardown`). If the oracle files named below
> no longer match their descriptions, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: direction
- **Planned at**: oracle commit `c8beaab`, 2026-09-08

## Execution mode

`Task(subagent_type="medium")` + strict TDD for steps 5–7. Dispatch template:

```
Task(subagent_type="medium", prompt="""
CAP:5
Work in D:\code\python\mcp-ms-access (git repo, plans/ exists — do not touch it).
<step text + inlined code/spec>
Strict TDD: write the failing test first, quote its failure output in your
report, then implement minimally.
Conventional commits, no AI attribution. Do not push.
Report: files changed, commands run, RED/GREEN evidence.
[acceptance]
check: run command="uv run pytest -q" expect=exit 0
check: run command="uv run ruff check ." expect=exit 0
check: run command="uv run pyright" expect=0 errors
criteria: <step criteria>
deliverable: <paths>
[/acceptance]
""")
```

The orchestrator re-runs every verification in its own shell before accepting.

## Why this matters

The product direction is now Python-only (decision recorded in Engram
#1304 and follow-ups): the old repo stays as an oracle, and a clean package
must exist before any backend, CLI, or MCP work can land. This plan also
removes the abandoned TypeScript scaffold `D:\code\typescript\mcp-ms-access`
(an empty hello-world, not a git repo) so no stale artifact survives.

## Current state

- Target repo `D:\code\python\mcp-ms-access` contains ONLY `plans/` (this file set). No git history yet, no source.
- TS scaffold `D:\code\typescript\mcp-ms-access` was inspected on 2026-09-08: only `src/index.mts` (shebang + `console.log("Hello world!!")`), `package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `tsconfig.json`, `eslint.config.mjs`, `.editorconfig`, `.gitignore`, `.npmignore`, and `node_modules/`. `git status` there fails — it is NOT a git repository.
- `uv` is NOT installed on this machine (verified: `where.exe uv` finds nothing).
- Oracle facts used here: `path_guard.py:36-43` and `config.py:134-142` contain a multi-directory bug — `return True` sits inside the `for allowed in ...` loop while `try/except ValueError` wraps the WHOLE loop, so only the first allowed directory is ever honored. The new implementation must fix this (per-directory try/except, `return False` only after the loop).
- Oracle `models/database.py` (82 lines) holds 11 dataclasses (`TableInfo`, `ColumnInfo`, `QueryInfo`, `IndexInfo`, `RelationshipInfo`, …) — port these names/shapes.
- Oracle `tests/unit/test_no_provider_sdk_in_core.py` is the pattern for an architecture guard test; the new guard asserts no web/CLI frameworks load on core import.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| uv bootstrap | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` | `uv --version` prints a version |
| Python | `uv python install 3.11` | exit 0 |
| Sync | `uv sync --extra dev` | exit 0, `.venv` created |
| Tests | `uv run pytest -q` | all pass |
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | 0 errors |

Shell is Windows PowerShell 5.1. New shell sessions need `%USERPROFILE%\.local\bin` on PATH to find `uv`.

## Scope

**In scope** (create/modify only):
- `D:\code\typescript\mcp-ms-access\` (delete)
- `D:\code\python\mcp-ms-access\`: `.python-version`, `.gitignore`, `pyproject.toml`, `README.md` (short), `src/ms_access_mcp/**`, `tests/unit/**`

**Out of scope**: `plans/` (never modify), the oracle repo (read-only), any backend/COM/ADO/ODBC implementation (plans 002+), MCP server, CLI commands beyond `--help`.

## Git workflow

- Run `git init` in step 3; branch `main` for the plans commit, then `plan/001-foundation` for the work.
- Conventional commits, no AI attribution, English. Example: `feat(core): package skeleton, safety paths, models`.

## Steps

### Step 1: Remove the TS scaffold

From a working directory that is NOT the scaffold (e.g. `D:\code\python`):

1. `Get-ChildItem -Force D:\code\typescript\mcp-ms-access` — assert the listing contains ONLY the known scaffold files (`src`, `node_modules`, `package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `tsconfig.json`, `eslint.config.mjs`, `.editorconfig`, `.gitignore`, `.npmignore`).
2. `git -C D:\code\typescript\mcp-ms-access status` — assert it FAILS (not a repo).
3. `Remove-Item -Recurse -Force D:\code\typescript\mcp-ms-access`

**Verify**: `Test-Path D:\code\typescript\mcp-ms-access` → `False`.

### Step 2: Bootstrap uv and Python

Run the uv installer (table above), reopen the shell or refresh PATH, then `uv python install 3.11`.

**Verify**: `uv --version` and `uv python find 3.11` both succeed.

### Step 3: Create the package manifest and skeleton

`pyproject.toml` (exact content):

```toml
[project]
name = "ms-access-mcp"
version = "0.1.0"
description = "Local stdio MCP server and CLI for Microsoft Access (Windows x64)"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.27,<2.0",
    "pydantic>=2.0.0",
    "pyodbc>=5.0.0",
    "pywin32>=306; sys_platform == 'win32'",
]

[project.optional-dependencies]
xlsx = ["openpyxl>=3.1.0"]
dev = ["pytest>=8.0.0", "ruff>=0.5.0", "pyright>=1.1.300"]

[project.scripts]
ms-access-mcp = "ms_access_mcp.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/ms_access_mcp"]

[tool.pytest.ini_options]
markers = [
    "com_integration: requires Windows + Access COM",
    "odbc_integration: requires Access ODBC driver",
    "slow: long-running",
]
```

`.python-version` → `3.11`. `.gitignore` → at least `.venv/`, `__pycache__/`, `dist/`, `*.log`, `.pytest_cache/`.

Create docstring-only modules (empty implementations, no logic):

```text
src/ms_access_mcp/
  __init__.py            # """MS Access local MCP server and CLI."""
  cli.py                 # argparse root; main() with only --version (step 7 extends)
  server.py              # placeholder for plan 004
  doctor.py              # placeholder for plan 002
  opencode_config.py     # placeholder for plan 005
  models.py              # step 6
  safety.py              # step 5
  errors.py              # step 6
  application/__init__.py
  backends/__init__.py
  mcp_tools/__init__.py
```

`git init`, commit `plans/` on `main` if not already committed, branch `plan/001-foundation`, commit skeleton.

**Verify**: `uv sync --extra dev` → exit 0; `uv run python -c "import ms_access_mcp"` → exit 0.

### Step 4: Architecture guard test (RED first)

`tests/unit/test_architecture_boundaries.py`:
- import `ms_access_mcp`, `ms_access_mcp.safety`, `ms_access_mcp.models`, `ms_access_mcp.errors` in a subprocess (`sys.modules` isolation) and assert none of `fastapi`, `starlette`, `jinja2`, `typer`, `uvicorn`, `fastmcp` (the third-party top-level package) appear in `sys.modules`.
- Model after oracle `tests/unit/test_no_provider_sdk_in_core.py` (subprocess import + denylist pattern).

This must pass immediately after skeleton (nothing imports them yet); it exists to FAIL if anyone regresses later.

**Verify**: `uv run pytest tests/unit/test_architecture_boundaries.py -q` → 1 passed.

### Step 5: `safety.py` — corrected path guard (strict TDD)

RED: `tests/unit/test_safety_paths.py` with cases (write these tests FIRST and quote the failure):
1. Single allowed dir: path inside → allowed; path outside → rejected.
2. **Multi-dir regression (the oracle bug)**: `allowed = [a, b]`, path under `b` → allowed. This is the case the oracle gets wrong.
3. Traversal: `allowed=[a]`, `a\..\..\other\file` resolved → rejected.
4. Network path `\\server\share\x.accdb` → rejected by default.
5. Nonexistent path → rejected (no exception).
6. Case-insensitivity on Windows: `D:\DBS` vs `d:\dbs` → allowed.

GREEN: `safety.py` with:

```python
def is_path_allowed(path: str | Path, allowed_dirs: list[Path]) -> bool:
    p = Path(path).resolve(strict=False)
    if not p.exists():
        return False
    for allowed in allowed_dirs:
        try:
            p.relative_to(Path(allowed).resolve(strict=False))
            return True          # per-directory try/except — the oracle bug fix
        except ValueError:
            continue
    return False
```

Plus `WritePolicy` (dataclass): `readonly: bool = True` default, `allow_write()` gate used by later plans, and `require_acknowledgment(operation: str)` helper returning an error when a destructive op lacks `confirm=True`.

**Verify**: `uv run pytest tests/unit/test_safety_paths.py -q` → all pass.

### Step 6: `models.py` + `errors.py`

Port the 11 dataclass names/fields from oracle `src/ms_access_mcp/models/database.py` (read the file; copy field-for-field, stdlib `dataclasses` only — pydantic models come with MCP tools in plan 004).

`errors.py`: `AccessMcpError(Exception)`, `BackendUnavailableError`, `BackendCapabilityMismatchError`, `DaoOperationError(message, cause)`. Tests: each raises with the expected message shape.

**Verify**: `uv run pytest -q` → all pass; `uv run pyright` → 0 errors.

### Step 7: Minimal `cli.py`

`argparse` root with `--version` printing `ms-access-mcp 0.1.0`, subparser placeholder `doctor` that prints "not implemented" and exits 1 (real doctor lands in plan 002). Test via subprocess asserting exit codes.

**Verify**: `uv run ms-access-mcp --version` → `ms-access-mcp 0.1.0`.

## Test plan

- `tests/unit/test_safety_paths.py` — 6 cases above (regression: multi-dir).
- `tests/unit/test_models_errors.py` — dataclass shapes + error taxonomy.
- `tests/unit/test_cli_basic.py` — `--version`, `doctor` placeholder exit 1.
- `tests/unit/test_architecture_boundaries.py` — denylist import guard.

## Done criteria

- [ ] `Test-Path D:\code\typescript\mcp-ms-access` → False
- [ ] `uv sync --extra dev`, `uv run pytest -q`, `uv run ruff check .`, `uv run pyright` all exit 0
- [ ] Architecture guard test exists and passes
- [ ] Multi-dir path-guard regression test exists and passes
- [ ] `git status` shows no files outside the in-scope list
- [ ] `plans/README.md` row 001 updated

## STOP conditions

- The TS scaffold contains unexpected files beyond the known list, or `git status` there succeeds (it has history — user data risk).
- `uv` cannot be installed or cannot manage Python 3.11 on this machine.
- `pywin32>=306` wheel fails to install on x64.
- The oracle HEAD no longer matches `c8beaab` and referenced oracle files drifted.

## Maintenance notes

- Plans 002–008 assume the tree created here; coordinate any rename through a new plan.
- Reviewer focus: the path-guard fix and the architecture denylist — both are regression magnets.
- Deferred: pydantic input models (plan 004), xlsx extra wiring (post-003).
