# Plan 009: Deduplicate form/report clone families in the COM adapter

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2178509..HEAD -- src/ms_access_mcp/backends/access.py tests/unit/test_backend_access.py`
> If either file changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `2178509`, 2026-09-09

## Execution mode

**Direct edit (behavior-preserving refactor).** Strict TDD does not apply:
no behavior changes, and the existing suite already pins the contract
(`tests/unit/test_backend_access.py`, 600 lines, including
`test_get_forms_returns_empty_on_exception` and
`test_get_reports_returns_empty_on_exception`). SDD is not warranted — no
new user-facing surface. The refactor is validated by keeping the existing
suite green plus one equivalence check per extracted pair.

## Why this matters

`src/ms_access_mcp/backends/access.py` (2,374 lines) contains **nine
form/report method pairs that are structural clones**, differing only in
the COM collection name (`AllForms`/`AllReports`), the Access object-type
constant (`acForm=2` / `acReport=4`), and the model class
(`FormInfo`/`ReportInfo`). Every bug fix — especially the error-observability
work in plan 010 — must currently be applied and tested twice. Collapsing
the clones removes ~350 lines and halves the surface of plan 010.

## Current state

- `src/ms_access_mcp/backends/access.py` — COM adapter; the clone pairs:

| Form side | Report side | Only deltas |
|---|---|---|
| `get_forms` L332 | `get_reports` L1139 | `AllForms`/`AllReports`, `FormInfo`/`ReportInfo` |
| `form_exists` L362 | `report_exists` L1169 | collection name |
| `create_form` L416 | `create_report` L1186 | `DoCmd.CreateForm`/`CreateReport`, constant 2/4 |
| `rename_form` L442 | `rename_report` L1212 | `DoCmd.Rename(..., 2/4, ...)` |
| `delete_form` L456 | `delete_report` L1226 | `DoCmd.DeleteObject(2/4, ...)` |
| `get_form_properties` L470 | `get_report_properties` L1240 | `OpenForm`/`OpenReport`, `Forms`/`Reports` |
| `get_form_controls` L575 | `get_report_controls` L1345 | same enumeration pattern |
| `get_form_sections` L962 | `get_report_sections` L1602 | same |
| `get_form_section_properties` L1006 | `get_report_section_properties` L1646 | same |

Representative excerpt (`get_forms`, L337–360; `get_reports` is identical
modulo the deltas):

```python
def _do():
    forms = []
    try:
        all_forms = self._dispatcher.access_app.CurrentProject.AllForms
        for i in range(all_forms.Count):
            form_obj = all_forms(i)
            try:
                record_source = ""
                try:
                    record_source = str(form_obj.Properties("RecordSource")) if form_obj.Properties.Exists("RecordSource") else ""
                except Exception:
                    pass
                from ms_access_mcp.models import FormInfo
                forms.append(FormInfo(name=form_obj.Name, record_source=record_source))
            except Exception:
                pass
    except Exception:
        pass
    return forms
```

- `_section_map` already exists at `access.py:196-201` — an existing
  precedent for a kind-keyed lookup table. Match that style.
- Repo conventions: ruff-clean, pyright 0 errors, `from __future__ import
  annotations`, lazy `from ms_access_mcp.models import ...` inside methods
  (used to keep import cost off the module path — preserve this pattern
  inside extracted helpers exactly as it appears today).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Unit tests | `uv run pytest tests/unit/ -q` | all pass, same count as before |
| Backend tests | `uv run pytest tests/unit/test_backend_access.py -q` | all pass |
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | 0 errors |

## Scope

**In scope** (the only files you should modify):
- `src/ms_access_mcp/backends/access.py`
- `tests/unit/test_backend_access.py` (only if an existing test mocks a
  symbol path that moves; prefer not to touch tests at all)

**Out of scope** (do NOT touch, even though they look related):
- Any change to exception-handling semantics (the `except Exception: pass`
  / `return False` behavior) — that is plan 010's job. This plan must be
  behavior-identical.
- `backends/dao.py`, `backends/versioning.py`, or any other backend.
- The module-level lazy-import pattern — do not hoist imports to module top.
- Macros/VBA methods in `access.py` — they have no clone pair.

## Git workflow

- Branch: `plan/009-form-report-dedup`
- One commit per extracted pair group (listers / mutators / properties /
  controls+sections); conventional commits, e.g.
  `refactor(backends): extract _list_objects for form/report listers`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Add a kind registry

At module level near `_section_map` (L196), add a private constant mapping
kind → (collection attribute name, object-type constant). Model classes stay
lazily imported inside functions per the existing pattern, so the registry
holds only strings/ints:

```python
_OBJECT_KINDS = {
    "form": {"collection": "AllForms", "ac_type": 2},
    "report": {"collection": "AllReports", "ac_type": 4},
}
```

**Verify**: `uv run ruff check src/ms_access_mcp/backends/access.py` → exit 0

### Step 2: Extract `_list_objects(kind)` and rewire the listers

Extract the shared body of `get_forms`/`get_reports` into
`_list_objects(self, kind: str)`. Resolve the model class inside the helper
via the existing lazy import (`from ms_access_mcp.models import FormInfo,
ReportInfo` then pick by kind). Keep every `try/except Exception: pass`
exactly where it is — including the outer collection-walk swallow. Rewrite:

```python
def get_forms(self):
    """Get all forms..."""   # keep existing docstring
    return self._list_objects("form")

def get_reports(self):
    """Get all reports..."""
    return self._list_objects("report")
```

**Verify**: `uv run pytest tests/unit/test_backend_access.py -q` → all pass
(both `*_returns_empty_on_exception` tests must still pass unchanged)

### Step 3: Extract `_object_exists(kind, name)`

Same treatment for `form_exists`/`report_exists`. Public wrappers keep their
signatures and docstrings.

**Verify**: `uv run pytest tests/unit/test_backend_access.py -q` → all pass

### Step 4: Extract `_create_object`, `_rename_object`, `_delete_object`

Parameterize on `kind` using `_OBJECT_KINDS[kind]["ac_type"]` for the
`DoCmd.Rename`/`DoCmd.DeleteObject` constant and `CreateForm`/`CreateReport`
selection for create. Keep the silent per-property `except Exception: pass`
blocks in `create_*` byte-for-byte in behavior (plan 010 will make them
observable — not here).

**Verify**: `uv run pytest tests/unit/test_backend_access.py -q` → all pass

### Step 5: Extract the properties/controls/sections pairs

One helper per pair: `_get_object_properties(kind, name)`,
`_get_object_controls(kind, name)`, `_get_object_sections(kind, name)`,
`_get_object_section_properties(kind, name, section)`. Preserve the
`finally`-block `DoCmd.Close` behavior exactly, including which name
argument it passes.

**Verify**: `uv run pytest tests/unit/test_backend_access.py -q` → all pass

### Step 6: Full static + suite gate

**Verify**: `uv run ruff check .` → exit 0; `uv run pyright` → 0 errors;
`uv run pytest tests/unit/ -q` → all pass with the same test count as the
pre-refactor run (record both counts in the commit message).

## Test plan

- No new tests are required — this is a pure refactor pinned by the existing
  suite. If any existing test fails after a step, the refactor changed
  behavior: revert that step, do not "fix" the test.
- Equivalence spot-check per step: run
  `uv run pytest tests/unit/test_backend_access.py -k "form or report" -q`
  and confirm the pass count never changes.
- Optional integration confirmation (only if Access is available locally):
  `uv run pytest tests/integration -m com_integration -q` → same results as
  before the refactor. Skip-gated environments: note the skip and move on.

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0 with unchanged test count
- [ ] `uv run ruff check .` exits 0
- [ ] `uv run pyright` reports 0 errors
- [ ] `access.py` line count reduced by roughly 250–400 lines
- [ ] No `except` clause semantics changed (`git diff` shows moved code, not
      edited catch bodies)
- [ ] All nine public form/report methods still exist with identical
      signatures and docstrings
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The clone pairs in "Current state" don't match the live code (line drift
  beyond ±20 lines, or method bodies that differ in more than the three
  documented deltas).
- Any existing test fails and the failure is not obviously a moved-symbol
  mock path.
- A pair turns out NOT to be a true clone (different control flow, extra
  parameters, different return shapes). Leave that pair alone and report it.
- The refactor appears to require touching a file outside the in-scope list.

## Maintenance notes

- Plan 010 (error observability) builds directly on these helpers — it
  modifies error semantics in ONE place per operation instead of two.
- Reviewers should scrutinize: the `finally`/close behavior in
  `_get_object_properties`, and that no `except Exception: pass` was
  accidentally dropped or narrowed.
- Future macro/VBA dedup is explicitly deferred — those methods have no
  clone pair and a registry for two kinds is enough abstraction.
