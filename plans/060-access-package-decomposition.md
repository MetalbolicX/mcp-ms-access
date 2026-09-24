# Plan 060: Decompose the `AccessAdapter` god class into a package behind the unchanged facade

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
  > maintain the index.
>
> **Drift check (run first)**: `git diff --stat 079d756..HEAD -- src/ms_access_mcp/backends/access.py src/ms_access_mcp/backends/dispatcher.py`
> If either file changed since this plan was written, compare the
> "Current state" map against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: L (multi-PR strangler; land one extraction per commit)
- **Risk**: MED (pure moves; the 1,100-line test suite + mirror pins are the
  safety net)
- **Mode**: Characterization-first; strict TDD for each new module seam
  (import-surface tests RED before the module exists, GREEN on extraction)
- **Depends on**: 056 (swallow logging lands first so moved handlers carry
  their DEBUG lines)
- **Category**: tech-debt (structure)
- **Planned at**: commit `079d756`, 2026-09-20

## Why this matters

`backends/access.py` is ~1,854 lines with ONE class (`AccessAdapter`,
line 69) mixing at least six concerns (verified 2026-09-20): connection
lifecycle, control/section enum tables, design-view plumbing, a
dialog-killer daemon thread, trusted-locations registry wrapping, forms
CRUD, reports CRUD, and VBA compile/procedures. Plans 009/016/035 already
shrank it (-170, ~-300-450, -186 lines respectively) — this plan finishes
the structural split so future work (typed access.py boundary models,
deferred COM-lifecycle fixes) lands in small files. Public contract is
PRESERVED: `AccessAdapter` keeps every method and import surface
(`backends.access.AccessAdapter`) stays valid via a package `__init__`
re-export — zero changes for consumers.

## Current state

- `src/ms_access_mcp/backends/access.py` (~1,854 lines). Verified method
  cluster map (line ranges at `079d756`; approximate ±5 lines):

  | Cluster | Lines | Contents |
  |---|---|---|
  | module helpers | 53-68 | `_props_to_dict` |
  | connection/lifecycle | 77-141 | `__init__`, `connect`, `disconnect`, `is_connected`, `get_current_db_name` |
  | control/section enums | 143-231 | `_control_type_name/_id`, `_section_name/_id` (pure lookup tables) |
  | design-view plumbing | 233-361 | `_open_object_in_design`, `_live_object`, `_with_design_view` (plan 035's helper), `_get_vb_project`, `_save/_load_object_from_text` |
  | dialog-killer thread | 363-400 | `_dialog_killer` (`threading.Event` daemon) |
  | trusted-locations wrap | 402-453 | `_trusted_locations_wrap`, `_list_objects` (delegates to existing `backends/trusted_locations.py`) |
  | failure classification | 557-573 | `_classify_object_failure`, `_log_object_failure` (plan 010) |
  | forms CRUD | 455-743+ | `get_forms`, `open_form`, `create_form`, `set_form_property(ies)`, `get_form_controls`, … |
  | reports CRUD | interleaved after 750 | mirrors forms (plan 016 phase 4 — mirror families are DELIBERATE, do not re-collapse) |
  | VBA compile/procs | ~1680-1762 | compile-command loop, `vba_list_procedures` (swallows — plan 056 adds DEBUG) |

- Consumers of the module path (keep working unchanged):
  `rg -n "from .*access import|backends.access" src/ tests/` — includes
  `backends/__init__.py`, `application/db_export.py` (isinstance check),
  `dispatcher.py`, and the big test modules
  (`test_backend_access.py` 1,132 lines, `test_backend_object_mirror.py`).
- Constraints from prior plans (binding):
  - Plan 016 phase 4 keeps form/report mirror families UNCOLLAPSED.
  - Plan 035's `_with_design_view` signature/semantics are pinned by
    `test_with_design_view` tests.
  - Lister swallows (`get_forms`/`get_reports` collection walks) semantics
    are settled (rejected 2026-09-09).
  - Architecture guard: no leading-underscore FILE names; new modules are
    `control_types.py`, `dialog_killer.py`, etc. (module names without
    leading underscore even though the functions keep theirs).
  - Deferred findings that touch this file (nested STA dispatch
    `:1002→:1840`, design-view partial-save, typed dict returns) are NOT
    fixed here — this plan only MOVES code.

## Commands you will need

| Purpose   | Command                  | Expected on success |
|-----------|--------------------------|---------------------|
| Install   | `uv sync --extra dev`    | exit 0              |
| Lint      | `uv run ruff check .`    | exit 0              |
| Typecheck | `uv run pyright src/ms_access_mcp/backends/` | 0 errors |
| Unit tests| `uv run pytest tests/unit/ -q` | all pass      |
| Arch guard| `uv run pytest tests/unit/test_architecture_boundaries.py -q` | all pass |

## Scope

**In scope** (the only files you should modify/create):
- `src/ms_access_mcp/backends/access/` (new package; see Steps)
- Delete `src/ms_access_mcp/backends/access.py` only in the final step, when
  the package `__init__.py` re-exports `AccessAdapter` with the identical
  import surface
- `tests/unit/test_access_package.py` (new; seam/import-surface tests only)
- One-line import updates ONLY where tests import private helpers directly
  (verify with `rg -n "from ms_access_mcp.backends.access import _|from .access import _" tests/ src/`)

**Out of scope** (do NOT touch):
- Any behavior: no signature, return-shape, error-string, or logging change.
- `dispatcher.py`, `backends/trusted_locations.py` (the wrapper delegates
  to it; the callee is fine as-is).
- Re-collapsing form/report mirrors (016) or reshaping `_with_design_view`
  (035).
- Fixing the deferred COM-lifecycle findings (nested STA dispatch,
  partial-save semantics) — they move WITH their code, unfixed.

## Git workflow

- Branch: `plan/060-access-package-decomposition`
- One conventional commit per extraction, e.g.
  `refactor(access): extract control_types lookup tables`. No AI
  attribution. Do not push. Land 056 first (dependency).

## Steps

Run the full suite between EVERY step. Each extraction follows the same
micro-cycle: (a) RED seam test for the new module, (b) create module by
MOVING code verbatim, (c) facade delegates, (d) suite green, (e) commit.

### Step 1: RED — package seam test

Create `tests/unit/test_access_package.py` asserting (via importlib) that
`ms_access_mcp.backends.access.control_types` exposes
`control_type_name`, `control_type_id`, `section_name`, `section_id`, and
that `ms_access_mcp.backends.access.AccessAdapter` still exists with the
full public method set (enumerate the names from the live class
dynamically in the test setup — do not hardcode 103 names; assert the
package path + 10 spot-check methods).

**Verify**: `uv run pytest tests/unit/test_access_package.py -q` → fails
(no such submodules yet).

### Step 2: Convert the module to a package, extract `control_types.py`

- `git mv src/ms_access_mcp/backends/access.py src/ms_access_mcp/backends/access/__init__.py`
  (import surface `backends.access.AccessAdapter` unchanged from here on).
- Move the `:143-231` lookup tables to
  `src/ms_access_mcp/backends/access/control_types.py` as module-level
  functions; `AccessAdapter` methods delegate one-line (methods stay for
  API compat).

**Verify**: seam test passes; full suite green; commit.

### Step 3: Extract `dialog_killer.py` (`:363-400`)

Move the thread body to a class/function `start_dialog_killer(...)`/
`DialogKiller`; the adapter keeps its thin method. STOP if the thread is
coupled to more than the adapter's `threading.Event` + `DoCmd` accessors.

**Verify**: suite green; commit.

### Step 4: Extract `trusted_locations.py` wrapper (`:402-453`)

Move `_trusted_locations_wrap` + `_list_objects` into
`access/trusted_locations.py` (name is safe — it's a submodule of the
package, distinct from `backends/trusted_locations.py`; import the latter
there, not in the facade). **Verify** + commit.

### Step 5: Extract `vba.py` (~`:1680-1762` + `_get_vb_project`,
`_save/_load_object_from_text` at `:292-360`)

Move VBA compile/procedure/save-to-text machinery to `access/vba.py` as
functions taking the adapter/com-app as first arg (they are already
stateless w.r.t. `self` beyond connection access). Keep method delegates.
**Verify** + commit.

### Step 6: Extract `forms.py` and `reports.py`

Move each family wholesale (mirrors stay mirrors). If a family exceeds
~600 lines after move, split read-path vs write-path — NOT form vs report
cross-collapse. **Verify** + commit (one commit per family).

### Step 7: Final gate + README row

- `rg -n "class AccessAdapter" src/ms_access_mcp/backends/access/__init__.py`
  — the facade remains, now composed of delegating methods.
- Full commands table green. `plans/README.md` row updated.

## Test plan

- `tests/unit/test_access_package.py`: import surface, delegation
  spot-checks (calls reach the extracted module — monkeypatch the extracted
  function and assert the adapter method calls it), added incrementally per
  step (RED before each extraction).
- Existing `test_backend_access.py` (1,132 lines),
  `test_backend_object_mirror.py`, `test_backends_access.py` must stay
  green UNMODIFIED throughout — they are the characterization net.
- Verification: full unit suite + arch guard after every extraction.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `backends/access/` package exists; `backends/access.py` file is gone
- [ ] `rg -n "from ms_access_mcp.backends.access import AccessAdapter" src/ tests/` all still resolve; every pre-existing test import unmodified
- [ ] No file in `backends/access/` exceeds ~700 lines
- [ ] `uv run pytest tests/unit/ -q` exits 0 — zero modifications to
      `test_backend_access.py` / `test_backend_object_mirror.py`
- [ ] `uv run ruff check .` + `uv run pyright src/ms_access_mcp/backends/` clean
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- Any extraction turns out to require editing moved logic (not a verbatim
  move) to keep the suite green — report which coupling broke the move.
- Mirror pins (`test_backend_object_mirror.py`) fail at any step.
- The architecture guard trips on a new submodule name (re-check naming,
  then STOP if renaming doesn't clear it).
- `dispatcher.py` or STA behavior becomes involved (COM threading — out of
  scope).
- Total effort balloons: if after Step 5 the suite has gone red twice,
  STOP and report rather than continuing.

## Maintenance notes

- After this lands, the deferred "typed backend-boundary models for
  access.py" follow-up (post-037 backlog) should target the extracted
  modules, not the facade.
- The deferred COM-lifecycle fixes (nested STA dispatch at former `:1002`,
  partial-save semantics) now have precise homes: `vba.py` /
  design-view plumbing — plan them against the new layout.
- Reviewer: every commit diff should read as move+delegate; logic edits
  inside moved blocks are red flags (except the mechanical `self.` →
  passed-context renames, which must be called out in the commit body).
