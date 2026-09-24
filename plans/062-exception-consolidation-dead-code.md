# Plan 062: Consolidate the triplicated exception hierarchies and delete the verified-dead symbols

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 079d756..HEAD -- src/ms_access_mcp/errors.py src/ms_access_mcp/capabilities.py src/ms_access_mcp/backends/__init__.py src/ms_access_mcp/application/database.py src/ms_access_mcp/backends/access.py`
> If any in-scope file changed since this plan was written (e.g. plan 060
> restructured `access.py` into a package), compare the "Current state"
> facts against the live code before proceeding; on a mismatch, treat it as
> a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW (identity re-export keeps every import site working)
- **Mode**: Strict TDD (identity/equivalence contract RED→GREEN; deletion
  gated by reference re-verification)
- **Depends on**: 060 (access.py decomposition lands first —
  `connect_hidden` lives there and the file will have moved)
- **Category**: tech-debt (hygiene)
- **Planned at**: commit `079d756`, 2026-09-20

## Why this matters

The same exception class names are defined in three places —
`errors.py:75-107`, `capabilities.py:39,48`, `backends/__init__.py:50,55` —
so `except SomeError` at a call site may catch a DIFFERENT class than the
one raised, depending on import path. Three verified-dead symbols
(`backend_available`, `set_active`, `connect_hidden`) accumulate
maintenance noise. Neither issue is urgent; both are cheap to fix once and
permanently confusing until fixed.

## Current state

- `src/ms_access_mcp/errors.py` — canonical error module (74 lines);
  owns the `Result` envelope since plan 012. Exception classes around
  lines 75-107 — wait: the file is 74 lines at the census used for
  planning; the `:75-107` range came from the audit's structural scan and
  may include the tail of the file plus adjacent defs. **Locate the exact
  class definitions with `rg -n "^class .*Error" src/ms_access_mcp/errors.py src/ms_access_mcp/capabilities.py src/ms_access_mcp/backends/__init__.py`
  and use the live output as the merge map.** The audit verified the
  NAME COLLISION across the three modules, not exact stable line numbers.
- Verified-dead symbols (zero references in `src/` AND `tests/` at
  `079d756`, from the 2026-09-20 audit):
  - `backends/__init__.py:119` — `backend_available`
  - `application/database.py:239` — `set_active`
  - `access.py:1847` — `connect_hidden` (src-dead; NOTE: audit flagged it
    test-referenced — re-verify; if tests reference it, it is NOT dead,
    see STOP)
- Prior art for the consolidation pattern: plan 012 moved the Result
  envelope into `errors.py` with re-export shims — follow that pattern
  (`from ..errors import X as X` at the old sites).
- Exclusions owned by other plans: stray root files (`nul`,
  `pnpm-workspace.yaml`) — plan 050 (TODO) owns them; do not touch. The
  near-duplicate test filenames (`test_backend_access.py` vs
  `test_backends_access.py`) — assessed below, intentionally left alone.

## Commands you will need

| Purpose   | Command                  | Expected on success |
|-----------|--------------------------|---------------------|
| Install   | `uv sync --extra dev`    | exit 0              |
| Lint      | `uv run ruff check .`    | exit 0              |
| Typecheck | `uv run pyright src/ms_access_mcp/errors.py src/ms_access_mcp/capabilities.py src/ms_access_mcp/backends/__init__.py` | 0 errors |
| Unit tests| `uv run pytest tests/unit/ -q` | all pass      |

## Scope

**In scope** (the only files you should modify):
- `src/ms_access_mcp/errors.py` (canonical definitions)
- `src/ms_access_mcp/capabilities.py` (replace local defs with re-exports)
- `src/ms_access_mcp/backends/__init__.py` (replace local defs with
  re-exports; delete `backend_available` if re-verified dead)
- `src/ms_access_mcp/application/database.py` (delete `set_active` if
  re-verified dead)
- `src/ms_access_mcp/backends/access.py` — or `backends/access/` package
  after plan 060 (delete `connect_hidden` if re-verified dead)
- `tests/unit/test_exception_unification.py` (create)

**Out of scope** (do NOT touch):
- Stray root files — plan 050.
- `application/sql_builder.py` (unused-by-backends legacy — separate
  decision, recorded in plan 058's maintenance notes).
- Renaming/merging `test_backends_access.py` into
  `test_backend_access.py`: REJECTED here — the 67-line file covers a
  distinct concern; merging test files during a hygiene plan mixes
  evidence. Leave as-is.
- Any behavioral change to raised/caught exception types (identity
  unification only where classes are semantically identical).

## Git workflow

- Branch: `plan/062-exception-consolidation-dead-code`
- Conventional commits: `test(errors): RED — cross-module exception
  identity` → `refactor(errors): single definition + re-export shims` →
  `chore(backends): delete dead symbols`. No AI attribution. Do not push.

## Steps

### Step 1: Map the collision precisely

Run: `rg -n "^class \w+Error|^class \w+Warning" src/ms_access_mcp/errors.py src/ms_access_mcp/capabilities.py src/ms_access_mcp/backends/__init__.py`
and for each duplicated NAME, diff the two/three class bodies (docstring,
bases, attributes, `__init__`).

- Identical bodies → merge candidates (this plan).
- Any semantic difference (different bases/attrs) → STOP for that class
  and report; it's a same-name-different-purpose case.

**Verify**: map recorded in the step report (name → verdict per site).

### Step 2: RED — identity tests

Create `tests/unit/test_exception_unification.py` asserting, for each
merge-candidate name, e.g.:

```python
def test_backend_error_is_canonical():
    from ms_access_mcp.backends import SomeError as BackendSomeError
    from ms_access_mcp.errors import SomeError
    assert BackendSomeError is SomeError
```

(one test per name, both non-canonical sites). These FAIL today (distinct
classes).

**Verify**: `uv run pytest tests/unit/test_exception_unification.py -q` →
all fail with identity `AssertionError`. Quote one.

### Step 3: GREEN — single definition + re-export shims

Keep the canonical definition in `errors.py`; in `capabilities.py` and
`backends/__init__.py` replace the class definitions with
`from .errors import SomeError  # canonical since plan 062` style imports
(match plan 012's shim style, minding its `err as _err` walrus-avoidance
lesson if it applies). Every existing `from X import SomeError` site keeps
working.

**Verify**: Step 2 tests pass; `uv run pytest tests/unit/ -q` → all pass
(critical: any `except SomeError` cross-module catch that previously
MISSED now catches — if a test relied on the miss, it fails here; treat as
a discovered latent bug and report it, don't silently edit).

### Step 4: Dead-symbol deletion (re-verify then delete)

For each of `backend_available`, `set_active`, `connect_hidden`: re-run
the reference check NOW (post-060 tree):
`rg -n "backend_available|set_active|connect_hidden" src/ tests/`.
If references exist ONLY in its own definition → delete it. If tests
reference it (audit flagged `connect_hidden` as test-referenced): delete
the test WITH it only if the test exercises nothing else; otherwise STOP
for that symbol.

**Verify**: `uv run pytest tests/unit/ -q` → all pass;
`rg -n "def backend_available|def set_active|def connect_hidden" src/`
→ no matches.

### Step 5: Gate re-verification

**Verify**: `uv run ruff check .` → exit 0; pyright on changed files →
0 errors; `uv run pytest tests/unit/ -q` → all pass.

## Test plan

- `tests/unit/test_exception_unification.py`: identity assertions per
  duplicated name (RED in Step 2, GREEN in Step 3).
- Deletion safety: full unit suite green after Step 4 with no test edits
  (except a same-change deletion of a test that only exercised a deleted
  symbol — must be called out in the commit body).
- Structural pattern: plan 012's shim re-exports in `errors.py` /
  `database.py`.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `rg -n "^class .*Error" src/ms_access_mcp/capabilities.py src/ms_access_mcp/backends/__init__.py`
      shows only re-export comments/imports — no local class definitions
      of merge-candidate names
- [ ] All identity tests pass; full unit suite green
- [ ] Deleted symbols return no `def` matches in `src/`
- [ ] `uv run ruff check .` + pyright on changed files clean
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1 finds same-name classes with different bases/attributes
  (semantically distinct — merging would change behavior).
- Any existing test fails after the identity merge in a way that indicates
  code RELIED on class distinctness (e.g. asserting an exception does NOT
  propagate) — that's a latent-behavior discovery, report it.
- `connect_hidden` turns out to be referenced by integration tests only —
  decide with the operator whether COM-integration coverage counts as a
  live reference.
- Plan 060 has not landed (this plan depends on it for the access.py
  layout).

## Maintenance notes

- New exceptions must be defined in `errors.py` ONLY; the re-export sites
  are compatibility shims — do not add new definitions there. Consider a
  tiny architecture-guard rule (extend
  `test_architecture_boundaries.py`) banning class definitions named
  `*Error` outside `errors.py` — optional, only if trivial.
- Reviewer: the identity tests are the whole proof; behavior-affecting
  fallout (Step 3's latent catches) must be enumerated in the report, not
  papered over.
