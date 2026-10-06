# Plan 077: Repair the password-scrub test seam and restore the green unit baseline

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093") — unless a reviewer
> dispatched you and told you they maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- tests/unit/test_database.py src/ms_access_mcp/application/database.py src/ms_access_mcp/credentials.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

The unit suite at `main` has exactly one failing test:
`tests/unit/test_database.py::TestCreateLinkedTableErrorScrub::test_adapter_error_scrubs_password`.
It fails because the test monkeypatches the wrong seam — it patches
`ms_access_mcp.credentials.resolve_profile`, while production code calls the
name already imported into `ms_access_mcp.application.database` at module
import time. The patch never intercepts, so the test short-circuits with
"Profile 'prod_dw' not found" and never reaches the leaking adapter. The
password-scrubbing behavior the test claims to verify is therefore
unexercised, and every downstream plan needs a green baseline.

## Current state

- `src/ms_access_mcp/application/database.py` — use-case facade. Line 18:
  `from ..credentials import contains_credential, resolve_profile`
  (a from-import binding made at import time). The call site (~line 1004)
  invokes the unqualified name `resolve_profile(connect_ref)`.
- `src/ms_access_mcp/credentials.py` — credential store; owns the real
  `resolve_profile`.
- `tests/unit/test_database.py` — the failing test class
  `TestCreateLinkedTableErrorScrub` (~lines 1016–1052):

```python
monkeypatch.setattr(
    "ms_access_mcp.credentials.resolve_profile",
    lambda name: "ODBC;PWD=hunter2;Server=srv" if name == "prod_dw" else None,
)
```

  The class then registers `db._connections["default"] = LeakingAdapter()`
  (an adapter whose `create_linked_table` raises
  `Exception(f"ODBC connect failed: {connect_string}")`) and calls
  `db.create_linked_table(..., connect_ref="prod_dw", confirm=True)`.
  Assertions: result is `Err`, `"hunter2" not in result.message`, and
  `"***" in result.message or "[sanitized]" in result.message`.

- Verified during the audit: with the wrong seam, `resolve_profile` hits the
  real empty store and returns `Err("Profile 'prod_dw' not found…")` before
  the adapter runs — the assertions pass vacuously in isolation contexts but
  the suite run fails on them (environment-dependent store state). With the
  correct seam, the leaking adapter runs and the error message is scrubbed:
  the `PWD=…` token is removed entirely (no `hunter2`), and the message
  retains `ODBC;Server=srv`.
- Repo conventions: monkeypatch the consumer module's binding, as done in
  `tests/unit/test_credentials.py` and `tests/unit/test_cli_credentials.py`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused test (RED first) | `uv run pytest tests/unit/test_database.py::TestCreateLinkedTableErrorScrub -q` | fails before the fix, passes after |
| Full unit suite | `uv run pytest tests/unit/ -q` | all pass (baseline restored) |
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | exit 0, no errors |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope** (the only files you should modify):
- `tests/unit/test_database.py`

**Out of scope** (do NOT touch):
- `src/ms_access_mcp/application/database.py`, `src/ms_access_mcp/credentials.py`
  — the production scrubbing path is correct; only the test seam is wrong.
- Any other test file.

## Git workflow

- Branch: `plan/077-scrub-test-seam` from `main`.
- Commit style: conventional commits, e.g. `test: fix resolve_profile patch seam in linked-table scrub test`.

## Steps

### Step 1: Reproduce the failure (RED)

Run the focused test and capture the failure output:

**Verify**: `uv run pytest tests/unit/test_database.py::TestCreateLinkedTableErrorScrub -q` → 1 failed, with either the profile-not-found `Err` or the assertion diff.

### Step 2: Patch the correct seam

Change the `monkeypatch.setattr` target from
`"ms_access_mcp.credentials.resolve_profile"` to
`"ms_access_mcp.application.database.resolve_profile"` (the binding the
production call site actually uses). Keep the lambda and everything else
unchanged.

**Verify**: `uv run pytest tests/unit/test_database.py::TestCreateLinkedTableErrorScrub -q` → passes, or fails ONLY on the `"***" / "[sanitized]"` assertion (see Step 3).

### Step 3: Pin the real scrub contract if the marker assertion fails

The scrubbing may remove the `PWD=…` token entirely rather than substituting
a marker. If — and only if — the failure is the
`assert "***" in result.message or "[sanitized]" in result.message` line,
replace that assertion with assertions that pin the actual contract:

```python
assert "hunter2" not in result.message
assert "PWD=" not in result.message.upper()
assert "ODBC;Server=srv" in result.message
```

Record this expectation refresh in the commit message (precedent: plan 035
refreshed mirror-test pins that had pinned a defect as the contract). Do not
weaken the primary invariant: the secret value must never appear.

**Verify**: `uv run pytest tests/unit/test_database.py::TestCreateLinkedTableErrorScrub -q` → passes.

### Step 4: Restore the green baseline

**Verify**: `uv run pytest tests/unit/ -q` → all pass (0 failed; the 5
marker-gated skips remain). Then `uv run ruff check .` → exit 0,
`uv run pyright` → exit 0, `uv run python scripts/qualify.py` → exit 0.

## Test plan

- No new test files. The repaired test itself is the deliverable: it must now
  exercise the leaking-adapter path (you can confirm by temporarily breaking
  the seam again — not committed — and seeing the failure mode change).
- Structural pattern: same file, same class; only the seam and (possibly) the
  marker assertion change.

## Done criteria

Machine-checkable. ALL must hold:

- [x] `uv run pytest tests/unit/ -q` exits 0 (baseline green)
- [x] The patched target is `ms_access_mcp.application.database.resolve_profile`
- [x] `"hunter2"` cannot appear in the asserted message (invariant assertion present)
- [x] `git status` shows changes only in `tests/unit/test_database.py`
- [x] `plans/README.md` status row for 077 updated

## STOP conditions

Stop and report back (do not improvise) if:

- After the seam fix, the test fails with an error OTHER than the marker
  assertion (e.g. the leaking adapter is still not reached) — the connect_ref
  dispatch in `create_linked_table` may have drifted.
- The full suite has any OTHER failure besides this test — the baseline
  assumption of this plan is exactly one failing test.

## Maintenance notes

- Plans 078–093 assume a green unit baseline; this plan must land first.
- If a future refactor moves `resolve_profile` again, prefer injecting the
  resolver over from-imports so tests can patch one authoritative seam.
