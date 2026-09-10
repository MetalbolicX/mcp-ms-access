# Plan 012: Move Result envelope and allowed-dirs resolution to shared modules

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> If anything in the "STOP conditions" section occurs, stop and report —
> do not improvise. When done, update the status row for this plan in
> `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2178509..HEAD -- src/ms_access_mcp/application/database.py src/ms_access_mcp/errors.py src/ms_access_mcp/server.py src/ms_access_mcp/mcp_tools/core.py`
> On any mismatch with the "Current state" excerpts, treat as STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none (do NOT run concurrently with plan 010 — both touch
  `errors.py`; sequence after 010 if both are in flight)
- **Category**: tech-debt
- **Planned at**: commit `2178509`, 2026-09-09

## Execution mode

**Direct edit (mechanical relocation).** Pure moves with re-export shims;
existing tests pin all behavior. TDD adds nothing to a relocation whose
contract is already tested; SDD is not warranted. The two changes share a
theme (cross-layer vocabulary living in the wrong module) but are
independent steps.

## Why this matters

Two cross-layer encapsulation leaks confirmed by audit:

1. `Ok`/`Err`/`Result` (plus private `_ok`/`_err`) are defined in
   `application/database.py` — one concrete use-case module — yet imported
   by the CLI, the server, all four `mcp_tools/*` modules, sibling
   application modules, and 8 test files. The codebase's core result
   vocabulary is owned by an unrelated module, and private helpers cross
   module boundaries.
2. `mcp_tools/core.py:20` imports the PRIVATE `_allowed_dirs_from_env` from
   `server.py:43` — the presentation layer reaches into the composition
   root's internals and re-derives wiring (`core.py:27` constructs
   `DatabaseUseCases` a second time). Env-parsing changes in `server` then
   silently alter tool behavior.

## Current state

- `src/ms_access_mcp/application/database.py:23-46` defines `Ok`, `Err`,
  `Result`, `_ok`, `_err`.
- Known importers of the envelope: `cli.py:16`, `server.py:14`,
  `mcp_tools/core.py:19`, `mcp_tools/automation.py:11`,
  `mcp_tools/maintenance.py:14`, `mcp_tools/versioning.py:10`,
  `application/automation.py:13` (imports private `_err`, `_ok`),
  `application/maintenance.py:23`, `application/versioning.py:13`,
  `application/protocols.py:11`, plus ~8 test files. Discover the full list
  with: `grep -rn "from .application.database import\|from ..application.database import\|application.database import" src/ tests/`
- `src/ms_access_mcp/errors.py` — small neutral module (currently the
  project error base; plan 010 adds typed backend errors there).
- `src/ms_access_mcp/server.py:43` — `def _allowed_dirs_from_env(...)`,
  used at `:66`; `mcp_tools/core.py:20` imports it, used at `:27`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Full unit | `uv run pytest tests/unit/ -q` | all pass |
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | 0 errors |
| Import check | `uv run python -c "import ms_access_mcp.cli, ms_access_mcp.server"` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/errors.py`
- `src/ms_access_mcp/application/database.py`
- `src/ms_access_mcp/safety.py` (allowed-dirs helper destination)
- `src/ms_access_mcp/server.py`
- `src/ms_access_mcp/mcp_tools/core.py`
- Any file whose import lines must be updated (imports only — no logic)

**Out of scope**:
- Changing the `Ok`/`Err` API, fields, or semantics in any way.
- Renaming `_ok`/`_err` at call sites beyond what the move requires.
- Refactoring how `mcp_tools/core.py:27` constructs `DatabaseUseCases`
  (the duplicate-wiring concern is noted for a future plan; here we only
  kill the private import).
- The plan-010 typed errors — if 010 is DONE, leave its classes untouched;
  if not DONE, do not pre-create them.

## Git workflow

- Branch: `plan/012-shared-kernel-relocation`
- Two commits: one for the Result move, one for the allowed-dirs helper;
  e.g. `refactor(application): move Result envelope to errors module`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Move the Result envelope to errors.py

Move `Ok`, `Err`, `Result` (exactly as defined at `database.py:23-46`) into
`errors.py`. Make the helpers public there: `ok()`, `err()`. In
`application/database.py`, replace the definitions with:

```python
from ..errors import Err, Ok, Result, err, ok

_ok = ok  # backward-compat aliases; remove in a later cleanup
_err = err
```

Update the sibling application modules (`automation.py:13`,
`maintenance.py:23`, `versioning.py:13`, `protocols.py:11`) to import
`err`/`ok` (public) from `..errors` instead of private `_err`/`_ok` from
`.database`, and fix their call sites (`_err(...)` → `err(...)`, etc.).
Leave ALL other importers (`cli.py`, `server.py`, `mcp_tools/*`, tests)
importing `Err`/`Ok`/`Result` from `application.database` — the re-export
keeps them working; mass import rewrites are deferred to a later cleanup.

**Verify**: `uv run pytest tests/unit/ -q` → all pass;
`uv run pyright` → 0 errors

### Step 2: Make allowed-dirs resolution a public safety helper

Move the body of `server.py:_allowed_dirs_from_env` into a public function
in `safety.py` (the module that owns path-guard policy), named
`allowed_dirs_from_env`. In `server.py`, replace the private definition
with `from .safety import allowed_dirs_from_env` and update the `:66` call
site. In `mcp_tools/core.py:20`, change the import to
`from ..safety import allowed_dirs_from_env` and update the `:27` usage.

**Verify**: `uv run pytest tests/unit/ -q` → all pass;
`grep -rn "_allowed_dirs_from_env" src/` → no matches;
`grep -rn "from ..server import\|from .server import" src/ms_access_mcp/mcp_tools/` → no matches

### Step 3: Full gate

**Verify**: `uv run pytest tests/unit/ -q` → all pass (unchanged count);
`uv run ruff check .` → exit 0; `uv run pyright` → 0 errors;
`uv run python -c "import ms_access_mcp.cli, ms_access_mcp.server"` → exit 0
(catches circular-import regressions).

## Test plan

- No new tests: behavior is pinned by the existing suite (8 test files
  import the envelope; server/core tests cover allowed-dirs wiring).
- Verification: `uv run pytest tests/unit/ -q` → all pass, unchanged count.

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0 with unchanged test count
- [ ] `uv run ruff check .` exits 0; `uv run pyright` 0 errors
- [ ] `grep -rn "from ..application.database import _err\|from .database import _err\|import _ok" src/` → no matches outside
      `application/database.py` itself
- [ ] `grep -rn "_allowed_dirs_from_env" src/` → no matches
- [ ] `mcp_tools/` contains no import of `..server`
- [ ] `errors.py` exports `Ok`, `Err`, `Result`, `ok`, `err`
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- `Ok`/`Err`/`Result` at `database.py:23-46` differ from a plain dataclass/
  generic-envelope shape (e.g. they've grown database-specific logic — then
  they don't belong in `errors.py` unchanged; report).
- Moving allowed-dirs resolution into `safety.py` creates a circular import
  (safety importing server or vice versa transitively).
- Plan 010 is IN PROGRESS on a conflicting branch — sequence, don't merge
  the two.

## Maintenance notes

- Follow-up (deferred, not this plan): migrate remaining
  `from .application.database import Err, Ok` importers to `..errors` and
  delete the `_ok`/`_err` aliases; also revisit the duplicate
  `DatabaseUseCases` construction in `mcp_tools/core.py:27` (inject via
  tool context instead).
- Reviewers should scrutinize: the re-export shim in `database.py` — it
  must be import-only, no redefinition.
- `errors.py` is becoming the shared kernel: plan 010's typed errors and
  this plan's envelope both live there. Keep it dependency-free (no imports
  from `application/`, `backends/`, `mcp_tools/`, `server`).
