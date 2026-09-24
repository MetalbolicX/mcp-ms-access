# Plan 061: Split `cli.py build_parser` (~586 lines) into per-family builder functions

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 079d756..HEAD -- src/ms_access_mcp/cli.py tests/unit/test_cli_dispatch.py`
> If either file changed since this plan was written, compare the
> "Current state" facts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: LOW (behavior-preserving; plan 036's 28 dispatch tests + the new
  surface pin are the net)
- **Mode**: Characterization-first (pin the full subcommand surface), then
  direct refactor in verifiable chunks — strict TDD applies to the surface
  pin (RED: it must FAIL first by asserting the pin captures the complete
  live set, proven by intentionally adding a dummy assertion run)
- **Depends on**: none (sequence after 041 recommended — fast trustworthy
  gate; not blocking)
- **Category**: tech-debt (maintainability)
- **Planned at**: commit `079d756`, 2026-09-20

## Why this matters

`cli.py build_parser()` starts at line 1197 and spans ~586 lines to EOF —
one function adding ~90 subparsers inline. Plan 036 already collapsed
`main()` dispatch into a `set_defaults` registry with 28 characterization
tests; the parser CONSTRUCTION is the remaining monolith. This plan is the
deliberate intermediate cut reconciling two settled deferrals: the full
"declarative command-spec table" rewrite (deferred 2026-09-16) and the
"per-domain cli modules" split (deferred 2026-09-09) stay deferred — this
plan only extracts builder FUNCTIONS inside `cli.py`, changing no public
CLI surface, no file layout, no dispatch semantics.

## Current state

- `src/ms_access_mcp/cli.py` (1,479 lines, 21 imports) — console-script
  entry (`ms-access-mcp = ms_access_mcp.cli:main`).
  - `build_parser()` at `:1197` → EOF (~586 lines): constructs
    `ArgumentParser` then adds ~90 subparsers inline (db/tables/schema/
    ddl/automation/vba/versioning/credentials/doctor/configure/mcp
    families), each already ending in `set_defaults(func=...)` from plan
    036.
  - `_run_validator` at `:865` (74 lines) — out of scope.
- `tests/unit/test_cli_dispatch.py` — plan 036's 28 characterization tests
  (routing + exit codes). Plus `test_cli*.py` family (8 files).
- 2026-09-16 deferral record (plans/README.md): "cli.py declarative
  command-spec table (1842 LOC / 108 subparsers — god file by volume, not
  logic) — M; the `_TOOLS` pattern is the exemplar." NOTE: that census
  counts the pre-036/pre-011 file; current is 1,479 lines / ~90
  subparsers. This plan does NOT implement the spec table — it makes the
  future spec-table plan mechanical by giving each family a named builder.

## Commands you will need

| Purpose   | Command                  | Expected on success |
|-----------|--------------------------|---------------------|
| Install   | `uv sync --extra dev`    | exit 0              |
| Lint      | `uv run ruff check .`    | exit 0              |
| Typecheck | `uv run pyright src/ms_access_mcp/cli.py` | 0 errors |
| Unit tests| `uv run pytest tests/unit/ -q` | all pass      |
| CLI tests | `uv run pytest tests/unit/test_cli_dispatch.py tests/unit/test_cli_basic.py -q` | all pass |

## Scope

**In scope** (the only files you should modify):
- `src/ms_access_mcp/cli.py` (function extraction ONLY within the file)
- `tests/unit/test_cli_surface.py` (create)

**Out of scope** (do NOT touch):
- New cli modules/files (per-domain split stays deferred, 2026-09-09).
- Any subcommand name, flag, default, help string, or `set_defaults` wiring.
- `main()`, `_run_validator`, the `_cmd_*` handlers, `cli_format.py`.
- The declarative spec-table rewrite (deferred 2026-09-16) — do not
  introduce a data-driven registry here.

## Git workflow

- Branch: `plan/061-cli-parser-family-builders`
- Conventional commits: `test(cli): pin full subcommand surface` →
  `refactor(cli): extract _build_db_parser` (one commit per family batch).
  No AI attribution. Do not push.

## Steps

### Step 1: Pin the complete subcommand surface

Create `tests/unit/test_cli_surface.py`:

1. Programmatic capture: call `build_parser()` and collect every
   subcommand name from the action registry (`parser._actions` →
   `_SubParsersAction.choices.keys()`) — do NOT hardcode a hand-written
   list as the source of truth. Assert the count is > 80 (guards against
   silent truncation) and snapshot the sorted names into a frozen list in
   the test file.
2. Prove the pin is live: temporarily append a fake assertion for a
   nonexistent subcommand, watch it fail, remove it — quote this in the
   report (this is the RED discipline for a characterization pin).
3. Also pin per-family membership (db vs automation vs credentials…)
   using the family map you'll extract in Step 2 — family lists become
   the contract the builders must reproduce.

**Verify**: `uv run pytest tests/unit/test_cli_surface.py -q` → passes
against current code; RED proof quoted.

### Step 2: Extract family builders — mechanical pass

Inside `cli.py`, replace the inline blocks in `build_parser()` with calls
to one builder function per family, e.g.:

```python
def _build_db_parser(sub: _SubParsersAction) -> None: ...
def _build_table_parser(sub: _SubParsersAction) -> None: ...
def _build_schema_parser(sub: _SubParsersAction) -> None: ...
def _build_ddl_parser(sub: _SubParsersAction) -> None: ...
def _build_automation_parser(sub: _SubParsersAction) -> None: ...
def _build_vba_parser(sub: _SubParsersAction) -> None: ...
def _build_versioning_parser(sub: _SubParsersAction) -> None: ...
def _build_credentials_parser(sub: _SubParsersAction) -> None: ...
def _build_doctor_parser(sub: _SubParsersAction) -> None: ...
def _build_configure_parser(sub: _SubParsersAction) -> None: ...
def _build_mcp_parser(sub: _SubParsersAction) -> None: ...
```

Rules: each builder receives the subparsers action and contains ONLY its
family's `add_parser(...)` blocks, moved verbatim (same order, same kwargs,
same `set_defaults`). `build_parser()` becomes a ~30-line orchestrator.
Extract 2-3 families per commit; run the full CLI test set between
commits.

**Verify** after each batch:
`uv run pytest tests/unit/test_cli_surface.py tests/unit/test_cli_dispatch.py -q`
→ all pass unmodified.

### Step 3: Final gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass;
`uv run ruff check .` → exit 0;
`uv run pyright src/ms_access_mcp/cli.py` → 0 errors;
`uv run python -m ms_access_mcp --help` → lists the same top-level
commands as before (spot-check 3).

## Test plan

- `tests/unit/test_cli_surface.py`: exact subcommand set pin (programmatic
  capture + frozen snapshot), per-family membership pin, count floor.
- Existing net: 28 dispatch tests + `test_cli*` family, unmodified.
- Verification: commands table, all green.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `build_parser()` body is ≤ ~50 lines (orchestrator only)
- [ ] `rg -n "def _build_.*_parser" src/ms_access_mcp/cli.py` shows ≥ 10
      family builders
- [ ] `uv run pytest tests/unit/ -q` exits 0 with the surface pin present
- [ ] No subcommand/flag/default changed: surface pin + dispatch tests
      green UNMODIFIED
- [ ] `uv run ruff check .` + `uv run pyright src/ms_access_mcp/cli.py`
      clean
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- `build_parser` is no longer at/near `:1197` or already < 200 lines
  (drift — someone restructured it).
- Any family's subparsers are conditionally registered (platform/profile
  gating discovered mid-extraction) — report; conditional families need a
  decision, not improvisation.
- A moved `add_parser` block can't keep its exact kwargs (e.g. shared
  `parents=` parser objects defined mid-body) — report the coupling.
- The dispatch tests fail at any commit — revert that commit and report.

## Maintenance notes

- The family builders are the stepping stone for the deferred declarative
  spec-table plan: converting one `_build_X_parser` into a data table
  becomes an isolated change. Do NOT attempt that conversion here.
- New CLI commands should be added inside the owning family builder —
  note this in the family map comment at the top of `build_parser()`.
- Reviewer: diffs must be move-verbatim; any kwarg edit is a red flag.
