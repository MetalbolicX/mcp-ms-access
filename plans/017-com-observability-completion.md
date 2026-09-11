# Plan 017: Complete plan-010 COM error observability

> **Executor instructions**: Follow this plan step by step. Strict TDD: write
> the failing test FIRST, quote its failure output in your step report, then
> implement the minimum to pass. If anything in the "STOP conditions" section
> occurs, stop and report — do not improvise. When done, update the status
> row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 9f0a8c7..HEAD -- src/ms_access_mcp/backends/access.py tests/unit/test_backend_access.py`
> On any mismatch with "Current state", treat as STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/010-com-error-observability.md (DONE)
- **Category**: bug (observability)
- **Planned at**: commit `9f0a8c7`, 2026-09-10

## Execution mode

**Strict TDD.** This plan adds new observable log output (a DEBUG record and
typed-error names in WARNING records) — log content is part of the
error-observability contract plan 010 established, so RED → GREEN → REFACTOR
per step. No production behavior beyond log records changes; the public
`bool` API is untouched. Not SDD: two surgical edits + their tests.

## Why this matters

Plan 010 is marked DONE, but two of its done criteria are unmet in current
`main`:

1. **RecordSource failures are silently swallowed**
   (`access.py:483-484`, bare `except Exception: pass`). Plan 010 Step 3
   (`plans/010-com-error-observability.md:193-194`) requires keeping the
   best-effort swallow but logging the failure at DEBUG. A form whose
   `RecordSource` assignment fails is saved half-configured with zero
   trace — the exact data-integrity hazard the plan set out to close.
2. **Classification warnings do not name the typed error**
   (`access.py:508`, and the direct logs at `:526`/`:545`). Plan 010 done
   criterion (`plans/010-com-error-observability.md:226-227`) requires
   WARNING logs *naming the typed error*. `AccessObjectNotFoundError` and
   `AccessBackendError` define no `__str__` (`errors.py:93-112`), so `%s`
   renders message-only: `delete form 'X' failed: form 'X' not found`.
   Operators cannot distinguish "not found" from "backend failure" without
   reading the message text.

## Current state

Evidence collected read-only against `main @ 9f0a8c7`:

- **Gap 1 target** — `access.py:480-484`:
  ```python
  if record_source:
      try:
          obj.RecordSource = record_source
      except Exception:
          pass
  ```
- **Gap 2 targets** — three log formats, all message-only:
  - `access.py:505-508` `_log_object_failure` (4 call sites: open `:444`,
    close `:462`, create `:494`, set_property `:628`):
    `logger.warning("%s %s '%s' failed: %s", operation, kind, object_name, error)`
  - `access.py:526` (`_rename_object` catch):
    `logger.warning("rename %s '%s' failed: %s", kind, old_name, exc)`
  - `access.py:545` (`_delete_object` catch):
    `logger.warning("delete %s '%s' failed: %s", kind, object_name, exc)`
- **Error classes** (`errors.py:93-112`): neither defines `__str__`.
  `AccessObjectNotFoundError(kind, name)` renders `"{kind} '{name}' not found"`;
  `AccessBackendError(op, name, detail)` renders
  `"{op} '{name}' failed: {detail}"`. Naming the class requires
  `type(error).__name__`.
- **Logger setup**: `access.py:31` `logger = logging.getLogger(__name__)`;
  all existing log calls use %-style lazy args; access.py has zero
  `logger.debug` today. Follow `logger` (the plan-010 text says `_logger`;
  the code drifted — code wins).
- **Existing tests are safe**: all 12 caplog tests in
  `tests/unit/test_backend_access.py` (lines 855-1275) assert substrings of
  `r.message` (object name, prop name, operation word) with
  `caplog.set_level(logging.WARNING)` — none pins the exact format, none
  asserts an upper bound on records. Adding class names and a DEBUG record
  breaks none of them.
- **RecordSource branch is untested**: no test passes a truthy
  `record_source` to `create_form`/`create_report`; the `:77`/`:100`
  RecordSource fakes belong to the listing path.
- **Broad-catch budget**: 51 `except Exception: pass` in access.py;
  converting `:483` to `except Exception as exc:` LOWERS the count,
  satisfying plan 010's done criterion (`plans/010-...:224-225`).
- **Architecture guard**: `tests/unit/test_architecture_boundaries.py`
  checks imports only — editing log statements cannot trip it.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Backend tests | `uv run pytest tests/unit/test_backend_access.py -q` | all pass |
| Full unit | `uv run pytest tests/unit/ -q` | all pass |
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | 0 errors |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/access.py` (3 log-format edits + 1 swallow → DEBUG)
- `tests/unit/test_backend_access.py` (new additive tests only)

**Out of scope**:
- The 17 other `except Exception: pass` swallows in plan-016's mirror
  helpers (`access.py:584-1274` — property writes, Close-in-finally).
  Assessed and deferred; a separate plan owns them.
- Lister semantics (`_list_objects` best-effort walks) — CORRECT, do not touch.
- Narrowing catches to `pywintypes.com_error` — rejected in the audit.
- CLI/MCP error surfacing — plan 010 deferred it; still deferred.

## Git workflow

- Branch: `plan/017-com-observability-completion`
- Commit per step; conventional commits, e.g.
  `fix(backends): log RecordSource rejection at DEBUG in _create_object`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1 (RED): RecordSource rejection logged at DEBUG

Add a test to `tests/unit/test_backend_access.py`: a fake form object whose
`RecordSource` property setter raises, `create_form(..., record_source="qryX")`
→ returns `True` (best-effort contract preserved) AND
`caplog.at_level(logging.DEBUG)` captures a record containing `RecordSource`
and the source value. Model the fake on the existing `_FakeForm` pattern in
that file (property-write tests at `:1022-1032`).

**Verify (RED)**: `uv run pytest tests/unit/test_backend_access.py -q` →
fails (no DEBUG record exists today).

Then implement in `_create_object` (`access.py:483-484`):

```python
except Exception as exc:
    logger.debug("create_%s: RecordSource %r rejected: %s", kind, record_source, exc)
```

Keep the swallow semantics — no re-raise, no WARNING level, `return True`
path unchanged.

**Verify (GREEN)**: `uv run pytest tests/unit/test_backend_access.py -q` → pass

### Step 2 (RED): Classification warnings name the typed error

Add tests asserting the rendered warning message contains the class name:
- `delete_form` not-found → `"AccessObjectNotFoundError" in msg`
  (existing fake at `:855-871` for the setup shape).
- `delete_form` generic COM failure → `"AccessBackendError" in msg`
  (`:873-892`).
- One rename case → typed name in the `:526` format (`:925-951`).
- One `_set_object_property` open-failure case → typed name via
  `_log_object_failure` (`:1235-1247`).

These are NEW tests; do not edit the existing substring tests (they must
keep passing unchanged).

**Verify (RED)**: new tests fail (messages are class-name-free today).

Then implement — update all THREE formats (evidence: rename/delete bypass
`_log_object_failure`, and the done criterion covers all classification
output):

```python
# access.py:508
logger.warning("%s %s '%s' failed: %s: %s", operation, kind, object_name, type(error).__name__, error)
# access.py:526
logger.warning("rename %s '%s' failed: %s: %s", kind, old_name, type(exc).__name__, exc)
# access.py:545
logger.warning("delete %s '%s' failed: %s: %s", kind, object_name, type(exc).__name__, exc)
```

Renders e.g. `delete form 'GhostForm' failed: AccessObjectNotFoundError: form 'GhostForm' not found`.

**Verify (GREEN)**: `uv run pytest tests/unit/test_backend_access.py -q` →
all pass, including the 12 pre-existing caplog tests unchanged.

### Step 3: Full gate

**Verify**: `uv run pytest tests/unit/ -q` → all pass (count increases by
the new tests); `uv run ruff check .` → exit 0; `uv run pyright` → 0 errors.

## Test plan

- New tests (all in `test_backend_access.py`, model on the existing
  FakeDispatcher/`_FakeForm` fakes):
  1. RecordSource rejection → `True` + DEBUG record naming RecordSource.
  2. delete not-found → WARNING names `AccessObjectNotFoundError`.
  3. delete generic failure → WARNING names `AccessBackendError`.
  4. rename not-found → WARNING names the typed error (direct-log path).
  5. set_property open-failure → WARNING names the typed error
     (`_log_object_failure` path).
- Structural pattern: existing caplog tests at `:855-1275`.
- Verification: `uv run pytest tests/unit/ -q` → all pass with ≥5 new tests.

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0; ≥5 new tests exist and pass
- [ ] `uv run ruff check .` exits 0; `uv run pyright` 0 errors
- [ ] Public form/report mutators still return `bool` (no signature change)
- [ ] `grep -n "except Exception:" src/ms_access_mcp/backends/access.py`
      count is unchanged or lower — no NEW broad catches added
- [ ] RecordSource rejection produces a DEBUG log; the swallow behavior is
      otherwise byte-identical (returns True, continues to property writes)
- [ ] All classification WARNING logs (open/close/create/set_property/
      rename/delete paths) name the typed error class
- [ ] All 12 pre-existing caplog tests pass UNCHANGED (no test edits to
      accommodate the new format)
- [ ] Zero changes outside `access.py` and `test_backend_access.py`
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- Any of the 12 pre-existing caplog tests fail after the format change
  (means a test pinned more than the evidence showed — re-audit, don't
  edit the test).
- The RecordSource fake cannot raise in the setter without production
  changes (means the seam assumption is wrong — report).
- `_log_object_failure`/`_classify_object_failure` signatures or call-site
  counts drifted from the evidence (4 call sites; drift check should catch
  this first).

## Maintenance notes

- This plan CLOSES plan 010's observability contract; after it lands, the
  `plans/010-...` done criteria at `:224-228` are all literally satisfied
  (note the closure in the README row).
- The 17 deferred mirror-helper swallows (`access.py:584-1274`) are the
  natural next observability batch — they follow the same "best-effort
  behavior + DEBUG log" pattern established here. Do not extend this plan
  to cover them; write plan 018 against the post-017 state if prioritized.
