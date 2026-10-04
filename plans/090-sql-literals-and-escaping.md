# Plan 090: SQL text handling — literal-aware script parsing and identifier escaping

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/backends/script.py src/ms_access_mcp/backends/dao_sql.py src/ms_access_mcp/application/sql_builder.py src/ms_access_mcp/backends/crud_sql.py`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

Two SQL-text correctness gaps:

1. `execute_sql_script`'s parser strips comments with
   `line.split("--")[0]` — everything after `--` dies, INCLUDING content
   inside string literals. A value like `'a--b'` truncates the statement,
   and a `;` after the `--` loses an entire trailing statement silently.
   The docstring documents only the semicolon-in-literal limitation.
2. The application-layer SELECT/CRUD builders interpolate identifiers into
   `[...]` without doubling `]` — `dao_sql.quote_ident` (plan 044) already
   implements the correct Jet escaping and is simply not used there. Any
   identifier containing `]` produces unbalanced SQL on live paths
   (`db_export` passes caller-supplied table names; ODBC CRUD is the
   default fallback backend).

Both are correctness defects on trusted-client input, not new capability
classes (raw SQL is an intentional, confirm-gated capability).

## Current state

- `src/ms_access_mcp/backends/script.py` — the shared script parser
  (~lines 24–36); the load-bearing line (~line 32):
  `line.split("--")[0]`. Callers: `backends/dao.py` `execute_script`
  (~line 521) and `backends/ado.py` (~line 81). Documented limitation in
  the docstring covers semicolons in literals only. Existing parser tests
  live in `tests/unit/test_ado_adapter.py` (`parse_script_lines` cases —
  plan 028 note).
- `src/ms_access_mcp/backends/dao_sql.py` — `quote_ident`
  (~lines 156–165): doubles `]` correctly (plan 044's escaper).
- `src/ms_access_mcp/application/sql_builder.py` — `build_select`
  (~line 24): `FROM [{table_name}]` raw; `columns` joined unquoted.
- `src/ms_access_mcp/backends/crud_sql.py` — raw `[{table}]`/`[{k}]`
  interpolations (~lines 27, 50, 66 area; audit cites 30/44/48/50/60/69/72).
  Consumers: `backends/odbc.py` (active default fallback, ~lines 16, 113)
  and `backends/ado.py` (dead adapter — out of scope).
- `src/ms_access_mcp/application/db_export.py` — passes caller-supplied
  `table_or_query` into `build_select` (~lines 68, 159) — the live
  reachability for the escaping fix.
- Tests: `tests/unit/test_sql_builder.py` (121 LOC),
  `tests/unit/test_crud_sql.py` (316), `tests/unit/test_crud_sql_builder.py`
  (82), `tests/unit/test_dao_sql.py` (485 — quote_ident coverage exemplar).
- Plan 074 context: bracket-reject at export path boundary exists
  (`plans/074-export-path-bracket-reject.md` landed) — check its guard
  scope before assuming which `]` inputs are already rejected where; the
  builder-level escaping is defense-in-depth regardless.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused tests | `uv run pytest tests/unit/test_ado_adapter.py tests/unit/test_sql_builder.py tests/unit/test_crud_sql.py tests/unit/test_crud_sql_builder.py tests/unit/test_dao_sql.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/script.py`
- `src/ms_access_mcp/application/sql_builder.py`
- `src/ms_access_mcp/backends/crud_sql.py`
- `tests/unit/test_ado_adapter.py` (parser cases),
  `tests/unit/test_sql_builder.py`, `tests/unit/test_crud_sql.py`,
  `tests/unit/test_crud_sql_builder.py`

**Out of scope**:
- `backends/ado.py` (dead adapter; plan 093 decides its fate).
- Schema/DDL generators (`backends/sql_generator.py`, migration dialects —
  separate quoting families with their own tests).
- Any behavioral change to `execute_sql_script`'s public contract beyond
  correctness (multi-statement execution stays as-is).

## Git workflow

- Branch: `plan/090-sql-text-handling` from `main`.
- Conventional commits: `fix(sql): literal-aware comment stripping and bracket escaping in builders`.

## Steps

### Step 1: RED — parser literal tests

Extend the `parse_script_lines` tests (in
`tests/unit/test_ado_adapter.py` where they live) with golden cases:
1. `INSERT INTO t (a) VALUES ('a--b')` → statement preserved verbatim
   (the `--` is inside a literal);
2. `UPDATE t SET a = 1 -- trailing comment` + next line `UPDATE t SET b = 2;`
   → comment stripped, second statement survives;
3. a statement ending exactly at `--` with a `;` AFTER the `--` on the
   same line → the `;`-terminated tail is NOT resurrected (document the
   chosen semantics in the docstring);
4. existing semicolon-in-literal cases stay green.

**Verify**: new cases RED (today case 1 truncates).

### Step 2: GREEN — literal-aware scan

Rewrite the parser core as a character scan: track single-quote state
(Jet SQL doubles `''` for an embedded quote — handle it), strip `--`
comments ONLY outside literals, split statements on `;` ONLY outside
literals. Keep the function pure (no I/O) so the goldens are the contract.
Update the docstring's limitation note to describe the new behavior
accurately.

**Verify**: Step-1 goldens GREEN; full focused set green.

### Step 3: RED→GREEN — builder escaping

- RED: `tests/unit/test_sql_builder.py`: `build_select(table="a]b", ...)`
  produces `FROM [a]]b]` (balanced); `tests/unit/test_crud_sql.py` /
  `test_crud_sql_builder.py`: identifiers containing `]` produce doubled
  brackets in every interpolation site (list the sites from Current state).
- GREEN: route every identifier in `sql_builder.build_select` and all
  `crud_sql` builders through `dao_sql.quote_ident` (import from the
  sibling module — backends→backends and application→backends.dao_sql are
  both established import directions; match existing imports).

**Verify**: new cases GREEN; `uv run pytest tests/unit/test_sql_builder.py tests/unit/test_crud_sql.py tests/unit/test_crud_sql_builder.py -q` → pass.

### Step 4: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0; `uv run ruff check .` → 0.

## Test plan

- Parser goldens (4 cases above) — pure-function tests, no COM.
- Escaping goldens per builder site (quote_ident parity with
  `test_dao_sql.py` as the exemplar).
- No integration requirement: both defects are text-level and fully
  unit-testable.

## Done criteria

- [ ] Literal containing `--` survives parsing (golden test)
- [ ] Trailing statement after a comment survives (golden test)
- [ ] `grep -n "\[{table" src/ms_access_mcp/application/sql_builder.py src/ms_access_mcp/backends/crud_sql.py` returns no unescaped interpolations
- [ ] Unit suite + qualify green; `plans/README.md` row updated

## STOP conditions

- If any caller DEPENDS on `--` truncation as a feature (grep callers;
  none known), stop and report.
- If `quote_ident`'s bracket style is wrong for the ODBC CRUD dialect in
  some edge (it is Jet-style and ODBC-to-Access speaks Jet SQL — verify no
  non-Access ODBC target exists), stop and report the target.
- If a verification fails twice after a reasonable fix attempt.

## Maintenance notes

- The parser is shared by DAO and (currently dead) ADO adapters; plan 093's
  ADO decision does not affect this plan.
- When the deferred FK-topology/schema work touches `sql_generator.py`,
  reuse `quote_ident` there too rather than adding a third escaper.
