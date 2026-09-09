# Plan 008: Windows x64 release qualification

> **Executor instructions**: This plan QUALIFIES, it does not build features.
> The orchestrator runs the gates directly; `@fast` subagents only collect
> evidence summaries. Any red gate = BLOCKED status + report, not a fix-it
> session. Update `plans/README.md` when done.
>
> **Drift check (run first)**: plans 001–007 must all be DONE.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans 001–007
- **Category**: tests
- **Planned at**: oracle commit `c8beaab`, 2026-09-08

## Execution mode

Orchestrator-direct gate execution + `@fast` evidence collection (CAP:8,
read-only). Produce a dated release report at `docs/release/<version>-report.md`.

## Why this matters

The ReScript effort taught the hard lesson: single lucky runs prove nothing
for nondeterministic COM behavior; only repeated rounds with zero orphans,
zero crashes, and unchanged fixtures count. This plan is the objective
boundary between "works on my machine" and "release".

## Current state

- All features landed per plans 001–007; gate scripts exist (`scripts/stdio_smoke.py`, `scripts/tool_budget.py`).
- Environment: this machine = Windows x64 + 64-bit Office/ACE (doctor green in plan 002). GitHub-hosted Windows runners do NOT have Access — CI can cover unit + static + ODBC-absent paths only; the full matrix below is LOCAL.
- Known historical failure classes to prove absent: orphan MSACCESS on shutdown (`docs/session-termination-error.md` in oracle), fixture tmp-copy leakage (~2 GB in the old repo), stdout pollution breaking stdio clients, tool-schema flooding.

## Commands you will need

| Purpose | Command |
|---------|---------|
| Full suite | `uv run pytest -q` |
| Static | `uv run ruff check .`; `uv run pyright` |
| Doctor | `uv run ms-access-mcp doctor --json --deep` |
| Wheel | `uv build`; `uvx --from dist/ms_access_mcp-<ver>-py3-none-any.whl ms-access-mcp doctor --json` |

## Scope

**In scope**: `docs/release/**`, CI workflow file (subset), gate scripts (bugfixes only if a gate script itself is broken — product bugs = BLOCKED, not fixed here).

**Out of scope**: new features, dependency changes, installer behavior changes.

## Gate matrix (all must pass; repeated gates run 20×)

| # | Gate | Command/method | Pass criteria |
|---|------|----------------|--------------|
| G1 | Clean install | fresh `UV_TOOL_BIN_DIR` temp; `scripts/install.ps1 -NoRegister` | exit 0; exe answers `tools/list`; zero activation steps |
| G2 | Static | ruff + pyright | exit 0 / 0 errors |
| G3 | Full suite | `uv run pytest -q` | 0 failures; 0 UNEXPECTED skips (all skip-gates false on this machine) |
| G4 | Fixture hygiene | hash before/after; `Get-ChildItem tests/fixtures -Filter *.tmp_*` | canonical hash unchanged; zero tmp files |
| G5 | Backend behavior | `-m "com_integration or odbc_integration"` detailed | DAO/ADO/ODBC suites green incl. ANSI-92 + relationships |
| G6 | Hidden Access | during G5 run: poll visible windows | `MainWindowHandle -ne 0` count = 0 throughout; process count returns to baseline |
| G7 | VBA compile | plan-006 artifacts | structured errors on bad module; clean compile on good |
| G8 | Stdio spawn | `scripts/stdio_smoke.py` (installed exe) | handshake + call < 10 s; stdout is pure JSON-RPC |
| G9 | CLI/MCP equivalence | equivalence harness | identical normalized JSON for representative ops |
| G10 | Repeated shutdown | **20×**: spawn → connect → one tool → close-stdin; separate loop with Ctrl-C (CTRL_C_EVENT) | zero residual python/MSACCESS within 10 s each round; no `InvalidStateError`/exit-134 in stderr |
| G11 | Wheel install | `uv build` + `uvx --from <wheel> doctor --json` + stdio smoke | works outside source tree |
| G12 | Tool budget | `scripts/tool_budget.py` | core ≤ 30; disabled profiles = 0 tools |
| G13 | Registration safety | plan-005 scenario suite + one manual local registration | merge/rollback/remove green; OpenCode lists tools after restart |

## Steps

### Step 1: Freeze candidate

Tag candidate commit; record versions (`ms-access-mcp --version`, `uv --version`, Python, Office/ACE from doctor `--json`) into the report header.

### Step 2: Run G1–G13 in order

Capture each gate's command output (trimmed) into `docs/release/<version>-report.md`. G10 runs LAST (most likely to expose lifecycle issues).

### Step 3: CI subset

Add `.github/workflows/ci.yml` (if repo gets a remote): windows-latest → G2, unit tests, G12 (no Access-dependent gates). Document explicitly in the workflow header that COM/ODBC gates are local-only.

### Step 4: Verdict

- All green → `plans/README.md` row 008 DONE + report linked; version tagged.
- Any red → BLOCKED with the gate number and failing output; fixing returns to the owning plan (002/004/005/006/007).

## Done criteria

- [ ] Release report exists with all 13 gates green (20× where specified)
- [ ] Zero orphan processes/files across the entire run
- [ ] CI subset green (or explicitly deferred if no remote yet)
- [ ] `plans/README.md` row 008 updated

## STOP conditions

- Any gate red after a single legitimate re-run (flaky = red).
- Environment drift mid-run (Windows/Office update) — restart the matrix from G1.

## Maintenance notes

- This matrix is reusable verbatim for every future release; only the version header changes.
- Backlog after release: migration extra, PyInstaller artifact, additional harness `configure` targets.
