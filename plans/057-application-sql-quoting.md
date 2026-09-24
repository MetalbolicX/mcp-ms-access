# Plan 057: Quote identifiers and harden the connection string on the application-layer SQL paths

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 079d756..HEAD -- src/ms_access_mcp/application/migration.py src/ms_access_mcp/application/db_export.py src/ms_access_mcp/backends/dao_sql.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1 (security)
- **Effort**: S/M
- **Risk**: LOW (bracket-quoting is semantics-preserving for legal identifiers)
- **Mode**: Strict TDD (hostile-identifier contract)
- **Depends on**: none (plan 044's `quote_ident` is DONE and is a
  prerequisite artifact, not a plan dependency)
- **Category**: security
- **Planned at**: commit `079d756`, 2026-09-20

## Why this matters

This is an MCP server: identifiers arrive from LLM tool calls, i.e.
untrusted input. Plan 044 hardened the *backend* SQL boundary
(`]`-escaping via `quote_ident` in `backends/dao_sql.py`, 7 migrated sites),
but the *application* layer still builds raw SQL by f-string interpolation.
Verified: `application/migration.py:615` builds
`f"SELECT COUNT(*) FROM {table}"` with no quoting, and
`application/db_export.py:60` interpolates a caller-supplied path into an
ODBC connection string, where `;` and `"` can inject additional connection
properties. Both are injection vectors reachable from registered tools.

## Current state

- `src/ms_access_mcp/application/migration.py` — migration use cases;
  the count query around line 615:
  ```python
  f"SELECT COUNT(*) FROM {table}"
  ```
  `table` flows from extracted-schema/migration-job data, which originates
  from tool parameters.
- `src/ms_access_mcp/application/db_export.py` — export use case; around
  line 60 a connection string is built by interpolating the target file path
  (`IisamProviderProbe`/export SQL path — read lines 40-80; the target is the
  f-string that embeds the output path inside a `…;DATABASE=…`-style
  connection string).
- Canonical quoting helper (reuse, do not reinvent): plan 044 landed
  `quote_ident` — `rg -n "def quote_ident" src/` locates it
  (`backends/dao_sql.py`; a sibling exists in `backends/sql_generator.py`
  from commit `b5a05fa`). Import from the canonical location the grep
  reveals; if both exist, prefer `dao_sql.py` (plan-044 row).
- Layering: `application/` importing `backends/` is the established direction
  (plan 026's `sql_generator` is consumed by application use cases). The
  architecture guard allows it; `dao_sql` has no leading underscore so the
  private-import rule is not tripped.
- Connector-side precedent: the migration connectors
  (`backends/migration/connectors/*`) already validate/quote identifiers;
  this plan closes the Access-side gap.

## Commands you will need

| Purpose   | Command                  | Expected on success |
|-----------|--------------------------|---------------------|
| Install   | `uv sync --extra dev`    | exit 0              |
| Lint      | `uv run ruff check .`    | exit 0              |
| Typecheck | `uv run pyright src/ms_access_mcp/application/` | 0 errors (1 pre-existing at migration.py:563 acceptable if still present — plan 050 owns it) |
| Unit tests| `uv run pytest tests/unit/ -q` | all pass      |

## Scope

**In scope** (the only files you should modify):
- `src/ms_access_mcp/application/migration.py`
- `src/ms_access_mcp/application/db_export.py`
- `tests/unit/test_application_migration.py` (append; create if absent —
  check with `Glob tests/unit/test_application_migration.py`)
- `tests/unit/test_db_export.py` or the existing export test module (locate
  via `rg -ln "db_export|export_data" tests/unit/`)

**Out of scope** (do NOT touch):
- `backends/dao_sql.py` / `backends/sql_generator.py` — read-only reuse.
- Connector quoting (postgres `%s` parity etc.) — rejected/deferred
  2026-09-14 as behavior-changing; untouched here.
- Any behavior change for legal identifiers: `[Legal Name]`-style quoting
  must produce equivalent results, not just different SQL bytes.

## Git workflow

- Branch: `plan/057-application-sql-quoting`
- Conventional commits, e.g. `test(migration): RED — hostile identifiers must
  be bracket-escaped` → `fix(migration): quote table names in count SQL`.
  No AI attribution. Do not push.

## Steps

### Step 1: RED — hostile-identifier tests for the migration count path

Append tests to `tests/unit/test_application_migration.py` driving the
count/transfer path with a fake adapter that captures the SQL it receives.
Cases:

- table name `Orders]; DROP TABLE x` → captured SQL contains
  `[Orders]]; DROP TABLE x]` (bracket-doubled) or the call raises a
  validation error — pin whichever contract you implement in Step 2, but
  the raw unquoted form must NOT appear.
- table name `My Table` (legal, spaced) → SQL uses `[My Table]`.
- table name `x]` → escaped to `[x]]`.

**Verify**: `uv run pytest tests/unit/test_application_migration.py -q` →
new tests fail showing unquoted `FROM Orders]; DROP TABLE x` in the capture.

### Step 2: GREEN — apply `quote_ident` in migration.py

Wrap the `:615` table interpolation (and any sibling identifier
interpolations you find in the same read pass — `rg -n "FROM \{|INTO \[?\{" src/ms_access_mcp/application/migration.py`)
with the canonical `quote_ident`.

**Verify**: Step 1 command → all pass; full
`uv run pytest tests/unit/ -q` → all pass.

### Step 3: RED — connection-string hardening tests for db_export

Append tests asserting the exported connection string / SQL built from a
path containing `;` (e.g. `C:\out;HDR=NO\x.mdb`) and `"` characters: either
the path is rejected with a clear validation error before interpolation, or
the interpolation is escaped such that no additional `;`-delimited property
can be smuggled. Pin the chosen contract.

**Verify**: new tests fail (current code interpolates raw).

### Step 4: GREEN — validate/escape the path in db_export.py

Minimal fix honoring the pinned contract: validate
(`if ";" in path or '"' in path: raise ValueError(...)`) or escape
(bracket/quote the DATABASE segment per ACE syntax). Prefer validation —
paths with `;`/`"` are never legitimate on Windows for this product.

**Verify**: `uv run pytest tests/unit/ -q` → all pass.

### Step 5: Gate re-verification

**Verify**: `uv run ruff check .` → exit 0;
`uv run pyright src/ms_access_mcp/application/` → 0 new errors.

## Test plan

- New: hostile-identifier capture tests (Step 1, 3 cases), connection-string
  hardening tests (Step 3, 2+ cases: `;` smuggle, `"` smuggle, legal path
  unchanged).
- Structural pattern: fake-adapter capture style used in
  `tests/unit/test_facade_adapter_contract.py`.
- Verification: `uv run pytest tests/unit/ -q` → all pass including new
  tests.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `rg -n "FROM \{table\}" src/ms_access_mcp/application/` returns no
      matches
- [ ] `uv run pytest tests/unit/ -q` exits 0 with new tests present
- [ ] `uv run ruff check .` exits 0
- [ ] `uv run pyright src/ms_access_mcp/application/` → no NEW errors
- [ ] `git status` shows only in-scope files modified
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The `:615` site or the db_export connection-string interpolation cannot be
  found within ±30 lines of the stated locations (drift).
- Existing tests pin the *unquoted* SQL bytes for these paths (quote-parity
  conflict) — report the pinning test instead of editing it.
- `quote_ident` does not exist at the grepped locations (044 regression).
- Bracket-quoting breaks a live-semantics assumption you can prove from
  other tests (e.g. a dialect that rejects brackets).

## Maintenance notes

- Future identifier interpolations in `application/` should import the same
  helper; a follow-up could add an architecture-guard or ruff custom check
  banning `FROM {`/`INTO {` f-strings outside `dao_sql.py`.
- Reviewer: confirm no legal-identifier behavior change — the SQL bytes
  change, the results must not.
- `safety.py` path-guard (`allowed_dirs`) already constrains export paths;
  the Step-4 validation is defense-in-depth at the SQL/conn-string boundary,
  not a replacement.
