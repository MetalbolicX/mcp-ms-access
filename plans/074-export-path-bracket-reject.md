# Plan 074: Reject bracket characters in xlsx export paths (close the Jet DATABASE= escape)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/application/db_export.py`
> On any change, compare "Current state" excerpts against live code.

## Status

- **Priority**: P2 (defense in depth; primary vectors already blocked)
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `a7f2edb`, 2026-09-24

## Why this matters

The IISAM export SQL embeds the caller-controlled output path inside a
bracketed Jet clause: `INSERT INTO [Excel 12.0;HDR=YES;DATABASE={abs_path}].[{sheet_name}$] ...`.
Plan 057 already rejects `;` and `"` in the path and `]` in the sheet name —
but a `]` in the PATH still closes the bracket early and alters the
statement the Access engine executes. Narrow, real gap; one-line fix plus
tests proving legit paths (spaces, apostrophes, unicode) still work.

## Current state

- `src/ms_access_mcp/application/db_export.py:59-68` —
  `_build_iisam_insert_sql`:
  ```python
  def _build_iisam_insert_sql(table_or_query: str, output_path: str, sheet_name: str) -> Result:
      """...
      Raises ValueError if output_path contains ';' or '"' — both are ODBC connection
      string injection vectors in the DATABASE= segment.
      """
      if ";" in output_path or '"' in output_path:
          return _err(
              f"Output path must not contain ';' or '\"' characters (ODBC connection-string "
              f"injection vector). Got: {output_path!r}"
          )
      abs_path = str(Path(output_path).absolute())
      select_clause = build_select(table_or_query, None, None, None)
      return _ok(f"INSERT INTO [Excel 12.0;HDR=YES;DATABASE={abs_path}].[{sheet_name}$] {select_clause}")
  ```
- Sheet name is already sanitized (`_sanitize_sheet_name` :29-50 rejects `]`,
  enforces ≤31 chars) — do not touch.
- Test exemplar: the plan-057 tests for this builder — locate with
  `rg -ln "_build_iisam_insert_sql" tests/unit/` and extend that file.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | ≤ baseline |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS |

## Scope

**In scope**:
- `src/ms_access_mcp/application/db_export.py` (path validation only)
- The plan-057 test file for this builder (extend)

**Out of scope**:
- `_sanitize_sheet_name` (settled).
- The DoCmd export path (DoCmd.TransferSpreadsheet takes a real path
  parameter, no SQL interpolation — unaffected).
- Any CSV/JSON export changes.

## Git workflow

- Branch: `plan/074-export-path-bracket-reject`
- RED then GREEN commit. Do NOT push.

## Steps

### Step 1: RED — `]` in path is rejected

Extend the plan-057 test file:

```python
def test_build_iisam_insert_sql_rejects_closing_bracket_in_path() -> None:
    result = _build_iisam_insert_sql("t", r"C:\tmp\we]ird\out.xlsx", "Sheet1")
    assert not result.success  # Err envelope


def test_build_iisam_insert_sql_accepts_spaced_apostrophe_paths() -> None:
    ok = _build_iisam_insert_sql("t", r"C:\my dir\it's out.xlsx", "Sheet1")
    assert ok.success
    assert "DATABASE=C:\\my dir\\it's out.xlsx" in ok.data
```

**Verify**: first test FAILS (currently returns Ok with the broken SQL);
second passes (guard against over-rejection).

### Step 2: GREEN — extend the rejection set

```python
if any(ch in output_path for ch in (';', '"', "]")):
    return _err(
        f"Output path must not contain ';', '\"' or ']' characters "
        f"(connection-string / bracket-escape vectors in the DATABASE= segment). Got: {output_path!r}"
    )
```

Update the docstring's validation note to match.

**Verify**: both Step-1 tests pass; the full plan-057 suite passes.

### Step 3: Full gates

**Verify**: ruff / pyright / unit / tool_budget green.

## Test plan

- 2 new tests (reject `]`, accept spaced/apostrophe paths).
- Existing 057 quoting suite unmodified and green.

## Done criteria

- [ ] `]` rejected in output_path; `;`/`"` behavior unchanged
- [ ] Legit paths (spaces, apostrophes) still accepted
- [ ] All gates green; `plans/README.md` row updated

## STOP conditions

- Drift in `_build_iisam_insert_sql` (plan 057 recently touched it).
- A legit-windows-path test surfaces other characters Jet cannot tolerate —
  extend the set ONLY with a test proving the failure; report anything
  ambiguous.

## Maintenance notes

- This closes SEC-03's narrowed remainder. The full origin finding (path AND
  sheet interpolation) was vetted: sheet side was already safe.
- If Jet ever needs `[` rejection too (opening bracket inside DATABASE=
  proved harmful with a real-Access test), same pattern, one char.
