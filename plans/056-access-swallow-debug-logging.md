# Plan 056: Log the remaining silent COM swallows in `access.py` at DEBUG level

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 079d756..HEAD -- src/ms_access_mcp/backends/access.py`
> If `access.py` changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW (log lines only; swallow semantics preserved byte-for-byte)
- **Mode**: Strict TDD (log contract, same class as plans 010/017)
- **Depends on**: none
- **Category**: tech-debt (observability)
- **Planned at**: commit `079d756`, 2026-09-20

## Why this matters

`backends/access.py` contains at least 31 `except Exception: pass` sites. Five
were verified silent with zero logging: COM failures become indistinguishable
from "no data" (`return []`), "no VBProject" (`return None`), and successful
saves (`pass`). This makes production debugging archaeology — the failure
signal is destroyed at the catch site. Plans 010/017 established the
typed-error + DEBUG-log policy for *mutation* failures; this plan extends the
logging half of that policy to the remaining best-effort swallows without
changing any swallow semantics (several are deliberately correct COM
best-effort; see Exclusions).

## Current state

- `src/ms_access_mcp/backends/access.py` (~1,854 lines) — the COM automation
  adapter; `class AccessAdapter` starts at line 69.
- Verified silent sites (line numbers at commit `079d756`):
  - `:287-288` — `except Exception:` → `pass` in the `finally` close of the
    design-view helper. The docstring at `:270-271` already admits
    "Close failures are swallowed". **Keep swallowing — just log.**
  - `:301-303` — `_get_vb_project`: `except Exception:` → `pass` then
    `return None`. None is an error signal at callers today.
  - `:1680-1681` — per-module `DoCmd.Save` loop: `except Exception:` → `pass`.
  - `:1758-1759` — per-line `ProcOfLine` loop: `except Exception:` → `pass`.
  - `:1761-1762` — `vba_list_procedures` whole-`_do`: `except Exception:` →
    `return []` (error indistinguishable from "no procedures").
- Existing error policy to match: plan 010 landed classify+log helpers around
  `access.py:503-519` (`_log_object_failure` et al.) and plan 017 added a
  RecordSource DEBUG log. Find the existing module logger with
  `rg -n "getLogger|_log" src/ms_access_mcp/backends/access.py` and reuse it;
  only add `logging.getLogger(__name__)` if none exists.
- Log-contract test exemplar: `rg -n "caplog" tests/unit/` — plan 017's tests
  are the pattern for asserting DEBUG records from swallows.

**Exclusions (settled decisions — do NOT change semantics):**
- The `get_forms`/`get_reports` collection-walk swallows were REJECTED for
  semantic change on 2026-09-09 ("one corrupt object must not poison the whole
  list"). Adding a DEBUG log is allowed; raising/collecting errors is not.
- Plan 035's `finally`-close swallow (`:287-288`) stays a swallow — logging it
  is in scope, fixing the double-close shape is not.
- Do NOT narrow `except Exception` to `pywintypes.com_error` (rejected
  2026-09-09).

## Commands you will need

| Purpose   | Command                  | Expected on success |
|-----------|--------------------------|---------------------|
| Install   | `uv sync --extra dev`    | exit 0              |
| Lint      | `uv run ruff check .`    | exit 0              |
| Typecheck | `uv run pyright src/ms_access_mcp/backends/access.py` | 0 errors |
| Unit tests| `uv run pytest tests/unit/ -q` | all pass      |

## Scope

**In scope** (the only files you should modify):
- `src/ms_access_mcp/backends/access.py`
- `tests/unit/test_backend_access.py` (append; do not rewrite existing pins)

**Out of scope** (do NOT touch):
- Any other backend file — dispatcher.py's 13 swallows, schema_inspector.py's
  8, maintenance.py's 5, session_backup.py's 4 are follow-ups after this
  pattern proves out.
- Swallow *semantics* anywhere (no new raises, no returned-error dicts).
- `tests/unit/test_backend_object_mirror.py` swallow pins from plan 014 —
  they must stay green unmodified.

## Git workflow

- Branch: `plan/056-access-swallow-logging`
- Commit per step: conventional commits in the repo style, e.g.
  `test(access): RED — caplog asserts DEBUG on vb-project swallow` then
  `fix(access): GREEN — log swallowed COM failures at DEBUG`. No AI
  attribution. Do not push.

## Steps

### Step 1: RED — log-contract tests for the five verified sites

Append a `TestSwallowLogging` class to `tests/unit/test_backend_access.py`
modeling the fake-COM patterns already used in that file (and plan 017's
caplog tests). For each verified site, drive the failure with a fake that
raises, then assert with `caplog.at_level(logging.DEBUG, logger=...)`:

- `_get_vb_project` failure → exactly one DEBUG record containing
  `"vb project"`.
- `vba_list_procedures` failure → one DEBUG record containing
  `"list procedures"` (result is still `[]`).
- `DoCmd.Save` per-module failure → one DEBUG record per failing module,
  containing the module name.
- `ProcOfLine` per-line failure → DEBUG record(s) containing `"ProcOfLine"`.
- design-view `finally`-close failure → one DEBUG record containing
  `"close"`.

**Verify**: `uv run pytest tests/unit/test_backend_access.py -q -k SwallowLogging`
→ 5+ failures, each `assert ... no log record` shape. Quote one failure in
your report.

### Step 2: GREEN — minimal DEBUG logging at the five sites

Add `_logger.debug(...)` inside each existing `except Exception` handler
(reuse the plan-010 logger/`_log_object_failure` style; include the exception
message and the object/module name where available). Do not change any
`pass`/`return None`/`return []`.

**Verify**: same command → all pass. Full `uv run pytest tests/unit/ -q` →
all pass (swallow pins unmodified).

### Step 3: Sweep the remaining unlogged swallows in this file

`rg -n "except Exception:\s*$" -A1 src/ms_access_mcp/backends/access.py | rg -B1 "pass|continue"` —
for each hit with no log call in the handler body, add the same DEBUG line.
Skip sites inside `get_forms`/`get_reports` collection walks only if adding a
log would fire per-corrupt-object in a hot loop — in that case one DEBUG per
invocation at the end (`"n objects skipped"`) is preferred.

**Verify**: `uv run pytest tests/unit/ -q` → all pass;
`uv run ruff check .` → exit 0.

### Step 4: Gate re-verification

**Verify**: `uv run pyright src/ms_access_mcp/backends/access.py` → 0 errors;
`uv run pytest tests/unit/ -q` → all pass.

## Test plan

- New `TestSwallowLogging` in `tests/unit/test_backend_access.py`: 5 cases
  (Step 1) — happy-path unaffected (existing suite), regression = any
  swallow that starts raising.
- Structural pattern: plan 017's caplog tests
  (`rg -n "caplog" tests/unit/`).
- Verification: `uv run pytest tests/unit/ -q` → all pass including the new
  class.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `uv run pytest tests/unit/ -q` exits 0 with the new `TestSwallowLogging`
- [ ] `uv run ruff check .` exits 0
- [ ] `uv run pyright src/ms_access_mcp/backends/access.py` → 0 errors
- [ ] `rg -n "except Exception" -A2 src/ms_access_mcp/backends/access.py`
      shows every `pass`/`continue` handler body contains a `_logger.debug`
      (or documented per-invocation substitute)
- [ ] `git diff` shows no changes outside the in-scope files
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The line numbers / handler shapes in "Current state" don't match the live
  code (drift since `079d756`).
- Any swallow pin in `test_backend_object_mirror.py` or
  `test_backend_access.py` fails after adding logs (means a log line changed
  behavior — investigate, don't force).
- You find fewer than 20 `except Exception` sites in the file (the codebase
  has drifted materially).

## Maintenance notes

- When plans touch these sites later (e.g. access.py decomposition, plan
  060), the DEBUG lines must move with their handlers.
- Reviewer: confirm no handler changed from swallow to raise — `git diff`
  should show only added log lines.
- Deferred: the same sweep for dispatcher.py (13), schema_inspector.py (8),
  maintenance.py (5), session_backup.py (4) — follow-up plan after this
  pattern lands.
