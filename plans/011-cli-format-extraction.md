# Plan 011: Extract CLI result formatting into cli_format.py

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> If anything in the "STOP conditions" section occurs, stop and report —
> do not improvise. When done, update the status row for this plan in
> `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2178509..HEAD -- src/ms_access_mcp/cli.py tests/unit/test_cli.py tests/unit/test_cli_basic.py`
> On any mismatch with the "Current state" excerpts, treat as STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `2178509`, 2026-09-09

## Execution mode

**Direct edit (pure move).** No behavior change; the JSON output shape is a
public contract pinned by existing CLI tests. TDD adds nothing for a move of
pure functions; SDD is not warranted. The only real risk is the JSON shape
drifting — the existing tests guard it.

## Why this matters

`src/ms_access_mcp/cli.py` is 1,787 lines; `main()` alone spans
L1017–L1787 (~771 lines) and mixes five concerns (parser construction,
dispatch, formatting, use-case factories, OpenCode registration
subcommands). A full split is high-risk, but the result-formatting seam is
a stable public contract (`{"success", "data", "rowcount"}`) that deserves
its own named, independently testable module — and it is the lowest-risk
first cut that creates a template for later extractions.

## Current state

- `src/ms_access_mcp/cli.py`:
  - `_format_result` and `_result_to_dict` at L30–L60, plus
    `_cli_allowed_dirs` nearby (L60–L69 region). `_format_result` duplicates
    the body of `_result_to_dict` instead of calling it (dead redundancy).
  - `main()` at L1017 is the last def in the file (ends at EOF L1787).
  - Imports at L16: `from .application.database import Err, Ok`.
- Existing tests: `tests/unit/test_cli.py`, `test_cli_basic.py`,
  `test_cli_automation.py`, `test_cli_configure.py`,
  `test_cli_maintenance.py`, `test_cli_versioning.py` capture stdout/stderr
  and pin the JSON shape.

Excerpt of the seam to move (L30–L69 region — confirm before cutting):

```python
def _format_result(...): ...   # prints JSON {"success", "data", "rowcount"}
def _result_to_dict(...): ...  # same shape as dict
def _cli_allowed_dirs(...): ...
```

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| CLI tests | `uv run pytest tests/unit/test_cli.py tests/unit/test_cli_basic.py -q` | all pass |
| Full unit | `uv run pytest tests/unit/ -q` | all pass |
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | 0 errors |

## Scope

**In scope**:
- `src/ms_access_mcp/cli.py`
- `src/ms_access_mcp/cli_format.py` (create)

**Out of scope**:
- Parser-tree restructure / declarative subparser registry — explicitly
  deferred (highest-risk part of the file; needs per-subparser test
  characterization first).
- Splitting per-domain handler modules (`cli_db.py`, `cli_automation.py`) —
  deferred; the 87 handlers are similar-shape, not duplicated.
- The `_cmd_*` handlers, use-case factories, configure subcommands.
- Any change to the JSON output shape or printed text.

## Git workflow

- Branch: `plan/011-cli-format-extraction`
- One commit; conventional style, e.g.
  `refactor(cli): extract result formatting into cli_format module`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Create cli_format.py

Create `src/ms_access_mcp/cli_format.py` with `from __future__ import
annotations` and move `_format_result`, `_result_to_dict`, and
`_cli_allowed_dirs` into it verbatim, renamed without the leading
underscore (`format_result`, `result_to_dict`, `cli_allowed_dirs`) since
they now form the module's public surface. While moving, make
`format_result` CALL `result_to_dict` instead of duplicating its body —
this is the single behavior-neutral redundancy fix allowed in this plan.

**Verify**: `uv run ruff check src/ms_access_mcp/cli_format.py` → exit 0

### Step 2: Rewire cli.py

In `cli.py`, delete the moved functions and add
`from .cli_format import cli_allowed_dirs, format_result, result_to_dict`.
Update internal call sites (`_format_result(...)` → `format_result(...)`,
etc.). Keep `from .application.database import Err, Ok` where it is — plan
012 handles that.

**Verify**: `uv run pytest tests/unit/test_cli.py tests/unit/test_cli_basic.py -q` → all pass

### Step 3: Full gate

**Verify**: `uv run pytest tests/unit/ -q` → all pass (same count as
before); `uv run ruff check .` → exit 0; `uv run pyright` → 0 errors.
Also run the CLI smoke manually:
`uv run ms-access-mcp --help` → exit 0, help text renders.

## Test plan

- No new tests: existing CLI tests pin the JSON shape and output.
- If any existing test imports `_format_result` from `cli` (check with
  `grep -rn "_format_result\|_result_to_dict\|_cli_allowed_dirs" tests/`),
  update the import to `cli_format` — that is the only allowed test edit.
- Verification: `uv run pytest tests/unit/ -q` → all pass.

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0 with unchanged test count
- [ ] `uv run ruff check .` exits 0; `uv run pyright` 0 errors
- [ ] `uv run ms-access-mcp --help` exits 0
- [ ] `format_result` contains no duplicated serialization logic (calls
      `result_to_dict`)
- [ ] `cli.py` no longer defines `_format_result` / `_result_to_dict` /
      `_cli_allowed_dirs`
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The three functions are not at the expected location or have gained
  dependencies on parser/dispatch internals (the seam is no longer pure).
- Existing tests reach into the functions in a way that requires changing
  assertions (not just imports).
- The JSON shape produced after the move differs in any key, ordering, or
  whitespace from before.

## Maintenance notes

- This module is now the template for future extractions: parser
  construction → `cli_parsers.py`, configure subcommands →
  `cli_configure.py`. Do NOT do those in this plan.
- The JSON shape in `cli_format.py` is a public contract — changes to it
  are breaking changes for downstream tooling; treat any future edit as
  such.
