# Plan 072: Scrub credential-bearing error text at every backend boundary

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/backends/odbc.py src/ms_access_mcp/backends/dao.py src/ms_access_mcp/doctor.py src/ms_access_mcp/application/migration.py src/ms_access_mcp/application/database.py src/ms_access_mcp/errors.py`
> On any change, compare "Current state" excerpts against live code.

## Status

- **Priority**: P1 (security hygiene)
- **Effort**: S-M
- **Risk**: LOW (error-text scrubbing only, no behavior change)
- **Depends on**: plan 071 recommended (odbc.py returns reshape first — avoids
  double-touching the same lines)
- **Category**: security
- **Planned at**: commit `a7f2edb`, 2026-09-24

## Why this matters

Migration profiles legitimately hold full connection strings (which may embed
`PWD=...`), and driver failures (pyodbc/psycopg2/mysql-connector/ADO) are
exactly the errors most likely to echo connection-string fragments. Sibling
backends already scrub (`ado.py`/`dao.py` use `_strip_password` — plans
040/044), but several paths return raw `str(e)`: ODBC's four CRUD except
paths, DAO's `execute_query` except, migration's `_get_connector` ImportError
branch beside sanitized siblings, doctor's six detail probes, and
database.py's linked-table flows. These leak credential material to the MCP
client and into any structured logs of tool results.

## Current state

- `src/ms_access_mcp/backends/odbc.py` — four raw sites (post-071 lines may
  shift; match by symbol):
  ```python
  except Exception as e:
      self._conn.rollback()
      return {"success": False, "error": str(e), "affected": 0}   # ← execute_query/insert/update/delete
  ```
- `src/ms_access_mcp/backends/dao.py:378-385` — `execute_query`'s lone raw
  `error=str(e)` among scrubbed neighbors.
- `src/ms_access_mcp/application/migration.py:628-632`:
  ```python
  except ImportError as e:
      return _err(str(e))                      # ← raw, beside the sanitized sibling
  except Exception as e:
      return _err(_sanitize_error(str(e), "connection"))
  ```
- `src/ms_access_mcp/doctor.py` — six `details={"error": str(e)}` sites
  (around :88, :126, :160, :187, :267, :287 — verify each).
- `src/ms_access_mcp/application/database.py` — three `raw = str(e)` captures
  in linked-table flows (around :1067, :1114, :1194 — post-068 line numbers
  may shift; verify the post-processing actually neutralizes them; if it
  already does, note it and leave).
- Existing helpers: `_strip_password` lives in
  `src/ms_access_mcp/backends/dao_sql.py` (plans 040/044 extended its
  patterns); `_sanitize_error` in migration.py. APPLICATION-layer modules
  importing `backends.dao_sql._strip_password` would violate the
  architecture guard's spirit (private cross-layer import) — hence the
  promotion step below.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | ≤ baseline |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Leak census | `rg -n "str\(e\)" src/ms_access_mcp/backends/odbc.py src/ms_access_mcp/doctor.py src/ms_access_mcp/application/migration.py` | only inside sanitizer wrappers / 0 raw sites |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS |

## Scope

**In scope**:
- `src/ms_access_mcp/errors.py` (promote a public `strip_password`)
- `src/ms_access_mcp/backends/dao_sql.py` (delegate `_strip_password` to the promoted helper)
- `src/ms_access_mcp/backends/odbc.py`, `dao.py` (raw sites → scrubbed)
- `src/ms_access_mcp/application/migration.py` (`_get_connector` ImportError branch)
- `src/ms_access_mcp/doctor.py` (six detail sites)
- `src/ms_access_mcp/application/database.py` (linked-table sites, only if unneutralized)
- `tests/unit/test_error_scrubbing.py` (create or extend — check
  `rg -ln "strip_password" tests/unit/` for the existing harness from plans
  040/044 and extend it)

**Out of scope**:
- Log statements (DEBUG logs inside except blocks — plan-056 convention;
  scrubbing those is a separate sweep if ever needed).
- `access/` package (already covered by plan 010/056 error policy).
- Any behavioral change beyond text scrubbing.

## Git workflow

- Branch: `plan/072-error-scrubbing`
- RED → GREEN per step. Do NOT push.

## Steps

### Step 1: RED — parametrized leak test

Create/extend `tests/unit/test_error_scrubbing.py` with a parametrized case
list: for each target site, an injected exception whose message contains
`PWD=sekret;DATABASE=x` (and a `Password=sekret` variant), asserting the
returned error text does NOT contain `sekret` (nor `PWD=`/`Password=` in
value form). Drive each site via its existing unit-test fake pattern:

- odbc CRUD: fake pyodbc connection whose cursor raises (reuse odbc adapter
  tests' fake);
- dao `execute_query`: fake dispatcher pattern (see plan 067's test file);
- migration `_get_connector`: monkeypatch `get_connector` to raise
  `ImportError("... PWD=sekret ...")` and `Exception(...)`;
- doctor: monkeypatch each probed function to raise;
- database.py linked-table flows: fake adapter raising with the marker.

**Verify**: `uv run pytest tests/unit/test_error_scrubbing.py -q` → N
FAILURES, one per leaky site.

### Step 2: GREEN — promote `strip_password` and route every site

1. In `errors.py`, add a public `strip_password(text: str) -> str` whose
   implementation MOVES the regex logic from `dao_sql._strip_password`
   (verbatim — plans 040/044 pattern set is load-bearing).
2. `dao_sql.py` keeps `_strip_password` as a thin alias/delegate (importers
   unchanged).
3. Route every RED site through `strip_password` (odbc's four, dao's
   `execute_query`, migration's ImportError branch — switch to
   `_sanitize_error(str(e), "connection")` for symmetry with its sibling —
   doctor's six, database.py's unneutralized captures).

**Verify**: Step-1 tests all pass; `uv run pytest tests/unit/ -q` green;
leak census command shows only wrapped sites.

### Step 3: Full gates

**Verify**: ruff / pyright / unit / tool_budget / arch guard green
(`errors.py` is an application-layer-safe location — confirm the guard
agrees; it banned win32com + private imports in application/**, and
`errors.py` is the sanctioned shared home per plan 012).

## Test plan

- Parametrized leak tests (≥8 cases across the 5 files).
- Existing scrubbing tests from plans 040/044 stay green (the promoted
  helper must be behavior-identical — their suite is the regression net).

## Done criteria

- [x] `strip_password` public in `errors.py`; `dao_sql._strip_password` delegates
- [x] Leak census command returns no raw `str(e)` result sites in the five files
- [x] All leak tests pass; suites green
- [x] All gates green; `plans/README.md` row updated

## STOP conditions

- Drift (plan 071 reshaped odbc.py — run after it).
- Any site where scrubbing would REMOVE diagnostic value the tests depend
  on (e.g. doctor details asserted verbatim) — refresh those assertions
  deliberately, per plan-035 lesson, in a separate commit.
- `errors.py` promotion trips the architecture guard (unexpected — plan 012
  established it as the shared home).

## Maintenance notes

- If a NEW `str(e)` appears at a result boundary in review, point at
  `errors.strip_password` — this plan makes it the one-liner answer.
- Rotation note (SEC-02's operational half): any password whose value may
  have reached client-side logs should be rotated — a README/security-note
  line is deferred to plan 073's README touch.
