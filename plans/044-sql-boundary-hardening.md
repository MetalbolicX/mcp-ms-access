# Plan 044: Backend SQL-boundary hardening (identifier escaping + credential scrubbing in error paths)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat dde0998..HEAD -- src/ms_access_mcp/backends/sql_generator.py src/ms_access_mcp/backends/dao_sql.py src/ms_access_mcp/backends/ado.py src/ms_access_mcp/backends/dao.py tests/unit/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `dde0998`, 2026-09-16

## Why this matters

Two defensive gaps at the SQL boundary. (1) Jet bracket identifiers are
interpolated raw: a table/column/constraint name containing `]` terminates the
bracket and injects arbitrary DDL into generated scripts — and those names can
originate from an untrusted `.accdb` file during migration (extract → generate
→ execute against a trusted target). (2) Provider error strings are returned to
the MCP client unscrubbed on ~15 sites across `ado.py`/`dao.py`; DAO/ADO/pyodbc
connection failures routinely echo connect-string fragments, which can re-expose
profile credentials the user correctly kept out of tool arguments. The existing
`_strip_password` regex also truncates at `;`, leaking the tail of passwords
that contain semicolons.

## Current state

Identifier interpolation without escaping (verified):

```python
# backends/sql_generator.py:73
col_parts = [f"[{field.name}]", self._map_type(field)]
# :96
pk_cols = ", ".join(f"[{col}]" for col in table.primary_key)
# :114-119
local_cols_str = ", ".join(f"[{col}]" for col in local_cols)
ref_cols_str = ", ".join(f"[{col}]" for col in fk.foreign_columns)
constraint = (
    f"CONSTRAINT [{fk.name}] FOREIGN KEY ({local_cols_str}) "
    f"REFERENCES [{ref_table}]({ref_cols_str})"
)

# backends/dao_sql.py:120,126
col_def = f"[{name}] {type_sql}"
sql = f"ALTER TABLE [{table_name}] ADD COLUMN {col_def}"
```

The Jet escape convention: inside a bracketed identifier, `]` is escaped by
doubling — `]]`. (Documented Jet/Access behavior; verify with one golden test
rather than assuming — see Step 1.)

Unscrubbed error returns (verified pattern — `str(e)` straight into results):

```python
# backends/ado.py:84-85   errors.append(str(e)); results.append({"statement": stmt[:60], "success": False, "error": str(e)})
# backends/ado.py:115,134,153,171 — {"success": False, ..., "error": str(e)}
# backends/dao.py:246,261,275,401,441,477,516-517,567,586,615,633,702,837 — dict/WriteResult with error=str(e)
```

The one existing scrub site: `dao.py:744` applies `_strip_password`. The scrub
regex itself (`backends/dao_sql.py:156-160`):

```python
patterns = [
    re.compile(r"(?i)(?:^|;)PWD=[^;]*", re.IGNORECASE),
    re.compile(r"(?i)(?:^|;)Password=[^;]*", re.IGNORECASE),
    re.compile(r"(?i)(?:^|;)Jet OLEDB:Database Password=[^;]*", re.IGNORECASE),
]
```

`[^;]*` stops at the first `;` — a password like `a;b` survives as `;b` in the
output. Quoted connect-string values (`PWD="a;b";`) are not handled.

## Commands you will need

| Purpose    | Command                                       | Expected on success |
|------------|-----------------------------------------------|---------------------|
| Lint       | `uv run ruff check .`                         | exit 0              |
| Typecheck  | `uv run pyright src/ms_access_mcp/backends/`  | 0 errors            |
| Unit tests | `uv run pytest tests/unit/ -q`                | all pass            |
| Targeted   | `uv run pytest tests/unit/test_sql_generator.py tests/unit/test_backend_dao.py -q` | all pass |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/sql_generator.py` — escaping helper + call sites
- `src/ms_access_mcp/backends/dao_sql.py` — escaping at its sites + regex fix
- `src/ms_access_mcp/backends/ado.py`, `backends/dao.py` — route error strings
  through the scrub at result-construction time
- Tests: `tests/unit/test_sql_generator.py` (extend), dao/ado backend tests
  (extend), DDL golden tests (`tests/unit/test_connector_ddl_golden.py`) —
  regenerate goldens ONLY if escaping changes legit output (it should not for
  names without `]`)

**Out of scope**:
- Connector quoting unification across migration connectors (explicitly
  deferred in plans/README.md 2026-09-14 audit — needs operator sign-off)
- Any change to WHERE-clause/value handling beyond the identifier and
  credential cases above
- `credentials.py` file-permission hardening (separate small finding)

## Git workflow

- Branch: `plan/044-sql-boundary-hardening`
- Conventional commits; one per step. Do NOT push unless instructed.

## Steps

### Step 1: Centralize identifier quoting with `]` escaping (RED→GREEN)

Add one helper (put it in `backends/dao_sql.py` or a shared backends module —
follow existing import direction; both `sql_generator.py` and `dao_sql.py`
must use it):

```python
def quote_ident(name: str) -> str:
    """Bracket-quote a Jet identifier, escaping embedded close-brackets."""
    return "[" + name.replace("]", "]]") + "]"
```

Failing golden tests first: a field named `weird]name` produces
`[weird]]name]` in generated DDL. Then apply at every `[{...}]` site found by
`rg -n '\[\{' src/ms_access_mcp/backends/` (the excerpted ones plus any others
the grep reveals — list them in the step report).

**Verify**: new golden test passes; `uv run pytest tests/unit/ -q` → all pass
(existing DDL goldens unchanged — escaping must be a no-op for normal names);
`uv run ruff check .` → exit 0.

### Step 2: Fix `_strip_password` for quoted/semicolon-bearing values

Extend the patterns so a quoted value (`PWD="a;b";`) is consumed through its
closing quote, and an unquoted value consumes through the next `;` or
end-of-string. Keep the surrounding-delimiter preservation behavior (existing
tests pin it). New failing tests first:
- `...;PWD="a;b";Other=1` → PWD token fully removed, `Other=1` intact
- `PWD=a;b` at end of string → fully removed
- existing matrix from plan 040 stays green

**Verify**: `uv run pytest tests/unit/ -k strip_password -q` → all pass.

### Step 3: Scrub error strings at the backend result boundary

In `ado.py` and `dao.py`, pass every `str(e)` that lands in a returned
dict/WriteResult through `_strip_password` (import from `dao_sql` — both are
`backends/` modules; if the architecture guard objects to the import
direction, move the helper to a shared backends location and re-export).
Do NOT alter exception raising/logging behavior — only the text placed into
result payloads. The `stmt[:60]` echo in `ado.py:85` must ALSO be scrubbed.

Failing test first: a fake connection raising
`Exception('...;PWD=hunter2;...')` produces a result whose `error` contains no
`hunter2`.

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `rg -n "error.*str\(e\)" src/ms_access_mcp/backends/ado.py src/ms_access_mcp/backends/dao.py` — every hit is scrubbed (verify each in the step report).

## Test plan

- Golden: `quote_ident` matrix (plain, `]`, `]]`, empty-ish names) in
  `test_sql_generator.py`; DDL goldens must NOT change for existing fixtures.
- `_strip_password`: quoted/semicolon cases above, plus regression of the
  plan-040 matrix.
- Boundary scrub: fake-exception test per backend (ado, dao) asserting the
  credential substring is absent from the returned payload.
- Structural pattern: `tests/unit/test_connector_ddl_golden.py` for goldens;
  existing dao characterization tests (plan 014) for backend behavior pins.

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0 (≥ baseline; goldens byte-identical except new cases)
- [ ] `uv run ruff check .` exit 0; `uv run pyright src/ms_access_mcp/backends/` 0 errors
- [ ] `rg -n 'f"\[\{' src/ms_access_mcp/backends/` → no matches (all interpolation via `quote_ident`)
- [ ] Scrub test proves `PWD=` values with `;` never appear in result payloads
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- A golden test changes for a name WITHOUT `]` — escaping is not a pure no-op
  for normal names; stop and investigate before regenerating any golden.
- The architecture guard (`tests/unit/test_architecture_boundaries.py`) rejects
  the helper's import direction — stop, place the helper where the guard
  allows, and note the decision in the step report (do NOT weaken the guard;
  recall the plan-034 lesson: never fix import issues by naming files with a
  leading underscore).
- Live verification reveals Jet does NOT treat `]]` as an escaped `]` in some
  context (e.g. inside CONSTRAINT names) — stop and report the evidence.
- The existing unit suite is red before you start — stop.

## Maintenance notes

- Reviewers: every future `[{...}]` interpolation in backends must use
  `quote_ident` — consider a ruff custom rule or a guard-test grep in a
  follow-up if the pattern recurs.
- This plan deliberately does NOT attempt to make crafted identifiers
  executable-proof beyond escaping; the migration path's deeper answer is
  treating source-schema names as data everywhere (consistent with the plan-040
  credential model).
- Deferred: connector quoting unification (operator decision pending);
  credentials-file Windows DACL hardening (separate small finding).
