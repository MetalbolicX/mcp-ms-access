# Plan 010: Make COM mutation failures observable in the access backend

> **Executor instructions**: Follow this plan step by step. Strict TDD: write
> the failing test FIRST, quote its failure output in your step report, then
> implement the minimum to pass. If anything in the "STOP conditions" section
> occurs, stop and report — do not improvise. When done, update the status
> row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2178509..HEAD -- src/ms_access_mcp/backends/access.py src/ms_access_mcp/errors.py tests/unit/test_backend_access.py`
> Plan 009 must be DONE before starting (helpers `_create_object` etc. must
> exist). If 009 is not DONE, STOP.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/009-form-report-dedup.md
- **Category**: bug
- **Planned at**: commit `2178509`, 2026-09-09

## Execution mode

**Strict TDD.** This plan changes the error contract of the backend mutators
(new exception types propagate instead of silent `False`); behavior change +
public-observable semantics = RED → GREEN → REFACTOR for every step. Not
SDD: no new user-facing tools or specs, purely internal error semantics.

## Why this matters

`backends/access.py` mutators (`create_form`, `rename_form`, `delete_form`,
`open_form`, `close_form` and their report siblings — now the shared helpers
from plan 009) collapse every failure into `return False`: a missing form,
a permissions error, a corrupt database, and a hung Access process are
indistinguishable to callers. Worse, `create_form`/`create_report` silently
drop per-property write failures (`except Exception: pass` around
`Properties(prop).Value = value`), so a form is saved half-configured while
reporting success — a data-integrity hazard. The MCP layer cannot surface
meaningful errors, and users cannot build retry logic.

## Current state

- `src/ms_access_mcp/errors.py` — 14 lines; currently defines the project
  error base (see `tests/unit/test_models_errors.py:73,96` referencing
  `AccessMcpError`). Only `backends/odbc.py` imports it.
- Post-009 `backends/access.py` contains shared helpers `_create_object`,
  `_rename_object`, `_delete_object`, `_object_exists` and public wrappers
  `create_form`/`create_report` etc. returning `bool`.
- Pattern B excerpt (pre-009 line numbers, shape unchanged by 009):

```python
def open_form(self, form_name: str) -> bool:
    def _do() -> bool:
        try:
            self._dispatcher.access_app.DoCmd.OpenForm(form_name)
            return True
        except Exception:
            return False
    try:
        return self._dispatcher.call(_do)
    except Exception:
        return False
```

- Pattern C excerpt (`create_form` L425-434, same in `create_report`):

```python
try:
    form_obj.RecordSource = record_source
except Exception:
    pass
if properties:
    for prop_name, value in properties.items():
        try:
            form_obj.Properties(prop_name).Value = value
        except Exception:
            pass
```

- `access.py` already has `import logging` (L19) — check whether a module
  logger exists; if not, add `_logger = logging.getLogger(__name__)`.
- The dispatcher (`backends/dispatcher.py`) propagates exceptions raised
  inside `_do` through `self._dispatcher.call(_do)`.
- CLI callers expect `bool` from these methods; MCP tools wrap use cases
  that also consume the bool. **Backward-compat contract**: public
  `create_form`-style methods keep returning `bool`; the new exceptions are
  raised by the internal helpers and caught at the public boundary into a
  logged warning + `False`, EXCEPT the new typed accessor
  `create_form_strict`-style path below. Read the steps precisely — the
  contract is: bool API unchanged, failures now logged and classified.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Backend tests | `uv run pytest tests/unit/test_backend_access.py -q` | all pass |
| Error tests | `uv run pytest tests/unit/test_models_errors.py -q` | all pass |
| Full unit | `uv run pytest tests/unit/ -q` | all pass |
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | 0 errors |

## Scope

**In scope**:
- `src/ms_access_mcp/errors.py`
- `src/ms_access_mcp/backends/access.py`
- `tests/unit/test_models_errors.py`
- `tests/unit/test_backend_access.py`

**Out of scope**:
- CLI/MCP layers (`cli.py`, `mcp_tools/**`, `application/**`) — the bool
  contract is preserved; richer surfacing is a separate future plan.
- Lister semantics (`_list_objects`, `get_forms`, `get_reports`) — the
  collection-walk `except Exception: pass` is CORRECT COM best-effort and
  must not change.
- Narrowing catches to `pywintypes.com_error` — rejected during audit
  (couples the file to pywin32 internals beyond the existing dispatcher
  import; not worth it).
- `backends/dao.py`, `backends/versioning.py`, `backends/dispatcher.py`.

## Git workflow

- Branch: `plan/010-com-error-observability`
- Commit per step; conventional commits, e.g.
  `fix(backends): classify COM mutation failures with typed errors`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1 (RED): Typed error classes in errors.py

Add tests to `tests/unit/test_models_errors.py` asserting:
- `AccessObjectNotFoundError` exists, subclasses `AccessMcpError`, carries
  `.object_name` and `.object_kind` attributes, and its message contains both.
- `AccessBackendError` exists, subclasses `AccessMcpError`, carries
  `.operation` and `.object_name`, and formats the original error text into
  its message via `__cause__` chaining support.

Run the test, quote the ImportError/AttributeError failure in your report.

**Verify (RED)**: `uv run pytest tests/unit/test_models_errors.py -q` → fails

Then implement in `errors.py`:

```python
class AccessObjectNotFoundError(AccessMcpError):
    def __init__(self, object_kind: str, object_name: str) -> None: ...

class AccessBackendError(AccessMcpError):
    def __init__(self, operation: str, object_name: str, detail: str) -> None: ...
```

**Verify (GREEN)**: `uv run pytest tests/unit/test_models_errors.py -q` → pass

### Step 2 (RED): Mutators raise typed errors from helpers

Add tests to `tests/unit/test_backend_access.py` using the existing
FakeDispatcher pattern (see the `get_current_db_name` dispatcher-spy tests
added in commit `6e6c585` — model after those):
- `delete_form` on a dispatcher whose `DoCmd.DeleteObject` raises an
  exception whose message contains the object name → public method returns
  `False` AND logs a warning containing `AccessObjectNotFoundError`
  classification (use `caplog`).
- `delete_form` on a dispatcher raising a generic COM failure → returns
  `False` AND logs a warning containing the `AccessBackendError` detail.
- Same two cases for `rename_form` (or the shared `_rename_object` helper
  directly, whichever keeps the public bool contract testable).

**Verify (RED)**: new tests fail (no log records / no classification).

Then implement: inside the shared helpers, catch `Exception as exc`,
classify (heuristic: name-in-message or HRESULT `0x8004...` not-found class
→ `AccessObjectNotFoundError`; else `AccessBackendError`), and at the PUBLIC
wrapper boundary log `_logger.warning("%s failed: %s", operation, err)` and
return `False`. The internal helper may raise the typed error; the public
wrapper converts to bool + log.

**Verify (GREEN)**: `uv run pytest tests/unit/test_backend_access.py -q` →
all pass, including pre-existing tests (bool contract unchanged).

### Step 3 (RED): Property-write failures are logged, not swallowed

Add a `caplog` test: `create_form(..., properties={"BadProp": 1})` with a
fake form object whose `Properties("BadProp").Value` setter raises →
returns `True` (contract preserved) AND a warning log record naming
`BadProp` exists.

**Verify (RED)**: test fails (no log record).

Then implement in `_create_object`: replace the inner
`except Exception: pass` around property writes with
`except Exception as exc: _logger.warning("create_%s: property %s rejected: %s", kind, prop_name, exc)`.
Keep the `RecordSource` best-effort swallow as-is (documented Access
behavior: record source assignment timing varies), but log it at DEBUG.

**Verify (GREEN)**: `uv run pytest tests/unit/test_backend_access.py -q` → pass

### Step 4: Full gate

**Verify**: `uv run pytest tests/unit/ -q` → all pass;
`uv run ruff check .` → exit 0; `uv run pyright` → 0 errors.
Optional COM integration (local Access only):
`uv run pytest tests/integration -m com_integration -q` → same pass/skip
profile as before this plan.

## Test plan

- New tests (all in existing files, model after the FakeDispatcher spy tests
  from commit `6e6c585`):
  1. `test_models_errors.py`: the two new error classes (attributes, message
     content, inheritance).
  2. `test_backend_access.py`: not-found classification (delete + rename),
     generic-failure classification, property-rejection logging,
     bool contract preservation on failure (returns `False`, doesn't raise).
- Structural pattern: `tests/unit/test_backend_access.py` existing
  dispatcher-fake tests.
- Verification: `uv run pytest tests/unit/ -q` → all pass with ≥5 new tests.

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0; ≥5 new tests exist and pass
- [ ] `uv run ruff check .` exits 0; `uv run pyright` 0 errors
- [ ] Public form/report mutators still return `bool` (no signature change)
- [ ] `grep -n "except Exception:" src/ms_access_mcp/backends/access.py`
      count is unchanged or lower — no NEW broad catches added
- [ ] Property-write rejection produces a WARNING log; classification
      produces WARNING logs naming the typed error
- [ ] Lister (`_list_objects`) catch semantics untouched in the diff
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- Plan 009 is not DONE or the helpers it creates are absent/renamed.
- The FakeDispatcher spy pattern from `6e6c585` no longer exists in the test
  file to model after.
- Preserving the public `bool` contract proves impossible without touching
  CLI or application layers (that would mean the contract assumption is
  false — report it).
- A step's RED test cannot be made to fail for the right reason.

## Maintenance notes

- Future plan candidate: let MCP tools catch `AccessObjectNotFoundError` to
  return structured "not found" tool errors instead of generic failure —
  requires threading the typed errors through `application/` use cases,
  deliberately deferred here to keep the bool contract stable.
- Reviewers should scrutinize: the not-found heuristic (name-in-message is
  fragile across Access locales — if localization issues appear, switch to
  HRESULT matching only).
- The lister swallows are intentional; do not "clean them up" later without
  reading this plan and the audit that justified them.
