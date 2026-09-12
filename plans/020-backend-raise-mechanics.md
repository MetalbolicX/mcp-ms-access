# Plan 020: Fix raise mechanics in backends/** (re-enable EM and TRY003)

> **Executor instructions**: Follow this plan step by step. This is a
> behavior-preserving raise-mechanics plan: you change HOW exceptions are
> constructed and raised, never WHAT is raised, what callers catch, or the
> message text. Run every verification command and confirm the expected
> result before moving to the next step. If anything in the "STOP
> conditions" section occurs, stop and report — do not improvise. When
> done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat ab9a373..HEAD -- pyproject.toml src/ms_access_mcp/backends/ src/ms_access_mcp/errors.py`
> Plan 018 must be DONE first (it lands the select/per-file-ignores
> mechanism this plan builds on); sequence after plan 019 (both touch
> `pyproject.toml` and `backends/**` files, including `access.py`). On any
> mismatch with "Current state", treat as STOP.

## Status

- **Status**: DONE
- **Priority**: P3
- **Effort**: M
- **Risk**: LOW
- **Depends on**: plans/018-ruff-mechanism-deliberate-suppressions.md; sequence after plans/019-backend-docstrings.md
- **Category**: tech-debt
- **Planned at**: commit `ab9a373`, 2026-09-11

## Execution mode

**Direct edit (behavior-preserving mechanics).** Existing tests pin the
error contract established by plan 010 (typed errors +
`AccessObjectNotFoundError`/`AccessBackendError` classification, bool API
preserved). Ruff + the full unit suite + pyright are the gates. Not TDD
(no behavior change by intent), not SDD.

## Why this matters

`backends/**` raises exceptions with string literals and f-strings inlined
in the `raise` call (72 sites: EM101 35, EM102 7, TRY003 30). Inline
messages make the raise site do two jobs at once, resist reuse in logging,
and — because the families are unselected — let new raises keep
accumulating the pattern silently. Extracting the message to a named
variable and chaining `raise ... from exc` are mechanical, zero-behavior
improvements that also align the backend layer with the typed-error regime
plans 010/016/017 established. After this plan, EM and TRY003 are enforced
on `backends/**`, so the pattern stops growing.

## Current state

**Mechanism (established by plan 018 — read it first)**: ruff rule families
are enabled globally via `[tool.ruff.lint].select`; `per-file-ignores`
entries only silence families already in `select` — ruff has no per-file
`select`.

**Verified census at `ab9a373`** (re-measure in step 1): EM101 35, EM102 7,
TRY003 30 in `backends/**` = 72 sites. TRY003 ("long messages outside the
exception class") and EM101/EM102 almost always co-fire on the same raise —
extracting the message to a variable fixes both at once.

Representative sites (verify against live code before editing):
`src/ms_access_mcp/backends/__init__.py:78-79` (TRY003 + EM102),
`:119` (TRY003 + EM102), `:161-162` (TRY003 + EM101),
`:185-186` (TRY003 + EM101); `src/ms_access_mcp/backends/dao.py:138-139`,
`:164-165` (TRY003 + EM102), `:463` (TRY003 + EM101).

**The established chaining precedent** —
`src/ms_access_mcp/backends/access.py:527-528`:

```python
            except Exception as exc:
                raise self._classify_object_failure(kind, "rename", old_name, exc) from exc
```

(`:552-553` is the same shape in the delete path.) When you touch a raise
inside an `except` block, chain it `from exc` like this.

**Typed-error inventory** (`src/ms_access_mcp/errors.py`, plan 010/012
ownership): `AccessMcpError` (:71), `BackendUnavailableError` (:75),
`BackendCapabilityMismatchError` (:79), `DaoOperationError` (:83),
`AccessObjectNotFoundError` (:93), `AccessBackendError` (:103). Note:
`capabilities.py` ALSO defines `BackendUnavailableError` /
`BackendCapabilityMismatchError` (:39/:48) — pre-existing duplication; do
not unify it in this plan.

**Violations outside `backends/`** (verified at `ab9a373`): EM101 4,
EM102 6, TRY003 10 = 20 sites across `application/**`, `capabilities.py`,
`errors.py`, `sql_builder.py`, `opencode_config.py`, `profiles.py`,
`server.py`, `cli.py`, `mcp_tools/core.py`. These stay exempt via
per-file-ignores in this plan. `tests/**` and `doctor.py` already carry
EM/TRY003 exemptions.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Census (backends) | `uv run ruff check src/ms_access_mcp/backends/ --isolated --statistics --select EM,TRY003` | statistics table (exit 1 is fine) |
| Census (rest) | `uv run ruff check src/ms_access_mcp/ --isolated --statistics --select EM,TRY003 --exclude src/ms_access_mcp/backends` | statistics table (exit 1 is fine) |
| Backend tests | `uv run pytest tests/unit/test_backend_access.py tests/unit/test_backend_dao.py -q` | all pass |
| Full unit | `uv run pytest tests/unit/ -q` | all pass |
| Types | `uv run pyright` | 0 errors |

## Scope

**In scope** (the only files you should modify):
- `pyproject.toml` (`select` + per-file-ignores entries with justification
  comments)
- `src/ms_access_mcp/backends/**` — raise mechanics only

**Out of scope** (do NOT touch, even though they look related):
- The 20 EM/TRY003 violations outside `backends/` — justified exemptions in
  step 1, deferred to a future plan.
- Docstrings (D family) — plan 019 owns them.
- BLE001 catches — deliberate, documented in plan 018; never narrow or
  restructure them.
- Changing exception TYPES (except step 3's tightly-gated typed-error
  alignment), message TEXT, or control flow.
- The `capabilities.py`/`errors.py` error-class duplication.

## Git workflow

- Branch: `plan/020-backend-raise-mechanics`
- One commit per file; conventional commits, e.g.
  `style(backends): extract raise messages to variables in dao.py`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Enable EM + TRY003 with justified exemptions, then unmask backends

1. Re-measure both censuses (commands table) and record the numbers. If
   the backends EM/TRY003 total drifted from 72 by more than ±10, treat as
   STOP.
2. Add `"EM"` and `"TRY003"` to `[tool.ruff.lint].select` in
   `pyproject.toml`.
3. Run `uv run ruff check .`. For EVERY path outside `backends/` that now
   reports violations, add a per-file-ignores entry
   `"<path>" = ["EM", "TRY003"]` with a justification comment, e.g.:
   `# EM/TRY003: backends/** fixed in plan 020; this path deferred to a future plan.`
   Use the ruff output to find the paths — the snapshot expects ~20 sites
   across the paths listed in "Current state".
4. Remove `"EM"` and `"TRY003"` from the `src/ms_access_mcp/backends/**`
   per-file-ignores entry.

**Verify**: `uv run ruff check .` → exit 1, reporting ~72 violations ALL
under `src/ms_access_mcp/backends/`. Any violation outside `backends/`
means a missing exemption entry — fix the config, not the code.

### Step 2: Fix EM101/EM102/TRY003, one file per commit

For each file with violations (start with `__init__.py` and `dao.py`, the
verified offenders; get the full list from
`uv run ruff check src/ms_access_mcp/backends/ --select EM,TRY003`):

The transformation, and ONLY this transformation:

```python
# before
raise SomeError(f"operation '{name}' failed: {detail}")
# after
msg = f"operation '{name}' failed: {detail}"
raise SomeError(msg)
```

Rules that make this behavior-preserving:

- The message expression is copied BYTE-FOR-BYTE into the variable
  assignment. Never reword, shorten, or reorder — TRY003 is fixed by the
  extraction, not by shortening the message.
- Name the variable `msg` unless the enclosing scope already binds `msg`
  (then `error_msg`). Match the file's existing convention if one exists.
- If the raise sits inside an `except ... as exc:` block and is not already
  chained, add `from exc` (precedent: `access.py:528`). Never ADD a raise
  inside an except that wasn't there, and never REMOVE existing chaining.
- If the raised object is produced by a helper (e.g.
  `raise self._classify_object_failure(...) from exc`), EM/TRY003 do not
  apply to it — leave it alone.

**Verify** (after each file):
`uv run ruff check <file> --select EM,TRY003` → exit 0;
`uv run pytest tests/unit/ -q` → all pass. Commit per file.

### Step 3: Typed-error alignment (tightly gated)

While editing step 2, you will see vanilla raises (`raise ValueError(...)`,
`raise RuntimeError(...)`, `raise Exception(...)`). Convert one to a typed
error from `src/ms_access_mcp/errors.py` ONLY when ALL of these hold:

1. The semantics obviously match (e.g. a "database not open" RuntimeError
   in `dao.py` → `DaoOperationError`).
2. The message text stays byte-for-byte identical (the typed error must
   accept the message; check its `__init__` signature first — e.g.
   `AccessObjectNotFoundError(kind, name)` builds its own message, so sites
   whose message can't be reproduced exactly by the typed constructor are
   NOT convertible).
3. `rg "except (ValueError|RuntimeError|Exception)" src/ tests/` shows no
   caller or test depending on the vanilla type at that site.
4. The full unit suite stays green WITHOUT editing any test.

If ANY condition fails or you are unsure: keep the vanilla raise (with its
message extracted per step 2) and list the site in your final report under
"typed-error candidates deferred". Expect most sites to be deferred — that
is the normal outcome, not a failure.

**Verify**: `uv run pytest tests/unit/ -q` → all pass (unchanged test
count, unchanged test files); `uv run pyright` → 0 errors.

### Step 4: Final gate

**Verify**: `uv run ruff check .` → exit 0;
`uv run pytest tests/unit/ -q` → all pass;
`uv run pyright` → 0 errors;
`git diff --stat` → only `pyproject.toml` and `src/ms_access_mcp/backends/**`;
`git diff -U0 | rg '^\+' | rg -iv 'msg|raise|noqa|from exc|#'` review →
nothing but message-extraction assignments, raise lines, and chaining.

## Test plan

- No new tests: the existing suite pins the plan-010 error contract; ruff
  + pyright are the gates.
- Watch specifically: `tests/unit/test_backend_access.py` (12 caplog tests,
  lines ~855-1275, assert warning message substrings — message text must
  not change) and `tests/unit/test_backend_dao.py` (characterization tests
  including error paths).

## Done criteria

- [x] `"EM"` and `"TRY003"` are in `[tool.ruff.lint].select`; `backends/**`
      no longer appears in any EM/TRY003 per-file-ignores entry
- [x] Every path exempt from EM/TRY003 carries a per-file-ignores entry
      with an inline justification comment
- [x] `uv run ruff check .` exits 0
- [x] `uv run pytest tests/unit/ -q` exits 0 with unchanged test count and
      zero test-file modifications
- [x] `uv run pyright` → 0 errors
- [x] `git diff` shows: message extraction to variables, `from exc`
      chaining additions inside existing except blocks, at most the gated
      typed-error conversions from step 3 — and NOTHING else (no message
      rewording, no catch changes, no control-flow changes)
- [x] Deferred typed-error candidates listed in the final report
- [x] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- Plan 018 is not DONE, or its mechanism doesn't match "Current state".
- The re-measured backends EM/TRY003 census differs from 72 by more than
  ±10.
- A fix would require changing message text, exception type (outside step
  3's gate), or what any caller catches.
- A step-3 conversion requires editing an existing test to stay green —
  that means the test pins the vanilla type; revert the conversion and
  defer the site.
- You find a raise whose message is constructed with side effects (e.g. a
  function call in the f-string that must stay lazy) — extraction would
  change timing; leave it, `# noqa` with justification, and flag it.

## Maintenance notes

- The exemption entries added in step 1 are the deferred raise-mechanics
  backlog (~20 sites outside backends); a future plan should shrink the
  list directory by directory.
- The deferred typed-error candidates from step 3 are input for a future
  error-contract plan — converting them properly requires deciding the
  constructor-message mapping per error class.
- Reviewers should scrutinize: `git diff` word-by-word on every moved
  message (byte-identical), every added `from exc` (inside an existing
  except only), and any typed-error conversion against step 3's four
  conditions.
