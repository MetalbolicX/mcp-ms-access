# Plan 086: Analysis envelope correctness (Ok/Err handling, timing, sample_size)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/application/analysis.py src/ms_access_mcp/errors.py tests/unit/test_mcp_analysis.py`
> Plan 082 will already have changed `mcp_tools/analysis.py` wiring —
> expected drift; this plan owns the use-case internals.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/082-*.md (analysis tools connected; tests can inject adapters)
- **Category**: bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

`analyze_query` was written against a result API that does not exist: it
calls `is_ok()`/`unwrap()`/`schema_result["data"]` on the real `Ok`/`Err`
dataclasses, which expose only `data`/`rowcount`/`message` (plus
`map`/`and_then`/`match`). Verified in the audit with real envelopes: the
schema branch throws `TypeError` into a bare `except` and silently returns
`{}`; the timing branch matches neither case so `timing` is never
reported; and the accepted `sample_size` parameter is never used — the
full query executes unbounded. The tool does real work and throws the
results away.

## Current state

- `src/ms_access_mcp/errors.py` — `Ok`/`Err` dataclasses (~lines 34–79):
  attributes `data`, `rowcount`, `message`; combinators `map`/`and_then`/
  `match`. NO `is_ok`, NO `unwrap`, NO `__getitem__`.
- `src/ms_access_mcp/application/analysis.py` — the analyzer service:
  - Schema branch (~lines 295–298): `schema_result["data"]` /
    `is_ok()`-style checks against `query_data` results — falls into
    `except` → schema `{}`.
  - Timing branch (~lines 329–341): branches on `is_ok()` and `isinstance(
    ..., dict)` — neither fires for `Ok`/`Err`, so `timing` key never set.
  - `sample_size` (~line 256): accepted in the signature, never referenced
    again.
  - `connection_name` is dropped from the result envelope (~line 75 area,
    wrapper side).
- `src/ms_access_mcp/mcp_tools/analysis.py` — after plan 082 the tools are
  connected; `analyze_query` reaches the use case. Unit tests currently
  inject dict-shaped fakes that mask the envelope mismatch
  (`tests/unit/test_mcp_analysis.py` uses empty-`sql` early-return and
  dict fakes only).
- Conventions: consume `Ok`/`Err` with `isinstance(result, Ok)` / `.data`,
  or the `match` combinator — see `application/database.py` and
  `application/migration.py:479-483` (`isinstance(schema_result, Err)`) for
  the house style.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused tests | `uv run pytest tests/unit/test_mcp_analysis.py tests/unit/test_cli.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/application/analysis.py`
- `tests/unit/test_mcp_analysis.py` (fake refresh: dict fakes → `Ok`/`Err` fakes)

**Out of scope**:
- `src/ms_access_mcp/errors.py` (do NOT add `is_ok`/`unwrap` to satisfy
  one consumer — the envelope is the shared kernel).
- `mcp_tools/analysis.py` (plan 082 owns wiring).
- Index-awareness for `analyze_query` recommendations (direction backlog —
  explicitly not here).
- SQL rewriting beyond honoring `sample_size` on fetched rows.

## Git workflow

- Branch: `plan/086-analysis-envelope` from `main` (after 082).
- Conventional commits: `fix(analysis): consume Ok/Err envelopes and report timing`.

## Steps

### Step 1: RED — envelope-contract tests

Replace/augment the dict fakes in `tests/unit/test_mcp_analysis.py` (or add
`tests/unit/test_analysis_envelope.py`) with a fake use case/adapter
returning REAL `Ok`/`Err` envelopes, asserting:
1. `analyze_query` on a successful query returns a non-empty `schema`
   mapping built from `.data` (columns present);
2. the result carries a `timing` dict with an `executed` flag and a
   duration;
3. `Err` from the query surfaces as a failure/empty-schema with an error
   message (not silent `{}` + `success: true`);
4. `sample_size=5` with 20 fake rows limits analyzed rows to 5;
5. `connection_name` round-trips into the result envelope.

**Verify**: RED on 1, 2, 4, 5 (3 may accidentally pass via `except`).

### Step 2: GREEN — fix the branches

In `application/analysis.py`:
- Replace `is_ok()`/`unwrap()`/`["data"]` access with
  `isinstance(result, Ok)` → `result.data`; `isinstance(result, Err)` →
  error handling that sets an explicit failure/degraded flag in the
  envelope (never a bare `except: pass`).
- Set the `timing` dict on the success path (execution happened; include
  duration from the existing timing capture).
- Apply `sample_size` to the fetched rows (`rows[:sample_size]`) BEFORE
  analysis; document in the docstring that sampling is post-execution row
  limiting, not a SQL `TOP` rewrite (that is plan 088 territory).
- Thread `connection_name` through to the envelope.

**Verify**: Step-1 tests GREEN.

### Step 3: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0; `uv run ruff check .` → 0; budget unchanged.

## Test plan

- Five cases above; model envelope usage on
  `tests/unit/test_application_migration.py`'s real-envelope fakes.
- Keep the existing early-return test (`empty sql`) intact.

## Done criteria

- [x] `grep -n "is_ok\|unwrap\|\[\"data\"\]" src/ms_access_mcp/application/analysis.py` returns no hits
- [x] Non-empty schema + timing + sample_size + connection_name all tested and passing
- [x] Unit suite + qualify green; `plans/README.md` row updated

## STOP conditions

- If fixing the branches reveals the analyzer needs richer adapter data
  than the fakes imply (e.g. column metadata absent from `QueryResult`),
  stop and report the data-shape gap — do not fabricate fields.
- If any existing test pins `schema: {}` as the contract, refresh it with
  rationale in the commit message (plan-035 precedent) — but if it pins it
  deliberately for another reason, stop and ask.

## Maintenance notes

- `get_er_diagram`/`generate_sql` (same module family) route through
  `DatabaseUseCases` schema methods — after plan 082 they are connected;
  verify their envelopes use `isinstance` too while in the file (cheap
  sweep, same commit).
- Sampling via SQL `TOP` (query-level) remains plan 088's concern; the
  post-fetch slice here is the honest interim contract.
