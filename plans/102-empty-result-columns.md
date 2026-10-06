# Plan 102: Empty-result column preservation on DAO

> **Executor instructions**: as plan 094. Update the status row in
> `plans/README.md` (section "Plans 099–104") when done.
>
> **Drift check (run first)**:
> `git diff --stat 2fbe1de..HEAD -- src/ms_access_mcp/backends/dao.py`
> On mismatch with "Current state", STOP.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans 037/071 (QueryResult typing), 088 (limit work)
- **Category**: backend correctness
- **Planned at**: commit `2fbe1de`, 2026-10-10

## Why this matters

CODE-CONFIRMED (evidence pass): a zero-row SELECT on DAO loses column
metadata — `QueryResult(columns=[])` — while the ODBC path preserves
columns via `cursor.description` (`odbc.py:106`). Jet exposes
`rs.Fields` even at EOF, so the DAO path CAN recover the schema. Any
consumer that renders empty tables, builds dynamic UIs, or validates
queries gets a different shape depending on backend and row count.

## Current state (HEAD `2fbe1de`)

- `dao.py:383` (in `execute_query`'s `_do` closure):
  `if rs.EOF: return QueryResult(success=True, rows=[], count=0, columns=[])`
  — the early return precedes the `columns = [rs.Fields(i).Name ...]`
  line that the non-empty path uses.
- ODBC path already correct.
- CLI `query` envelope surfaces only `data`/`rowcount` — columns are
  invisible there either way (NOT this plan's concern; see Out of
  scope).

## Steps

1. **RED (unit)** — DAO-fake recordset with `EOF=True` but populated
   `Fields` (names F1/F2) → `execute_query("SELECT ...", ...)` returns
   `QueryResult(columns=["F1","F2"], rows=[], count=0)`. The existing
   fake family in `tests/unit/test_backend_access.py` /
   `test_dao_recordset_safety.py` has the shapes to extend.
2. **GREEN** — in `execute_query`, read `columns` from
   `rs.Fields` BEFORE the EOF early-return (same expression the
   non-empty path uses; keep the finally-close recordset discipline
   from plan 067 — the EOF branch must still flow through the
   finally-close, no early return that skips it).
3. **Integration pin (marker-gated)** — extend the Access adapter
   integration test family: live
   `execute_query("SELECT CategoryID, CategoryName FROM Categories WHERE 1=0")`
   → columns == `["CategoryID", "CategoryName"]`, rows == [].
4. **Parity note** — after this lands, DAO and ODBC agree on
   empty-result columns; record the parity statement in the row.

## Verify

Focused unit suites + the marker-gated integration pin + standard full
gates. No COM-hygiene changes (single connect cycle in the test).

## STOP conditions

- Live Jet returns a Fields collection that THROWS at EOF on some
  shapes (e.g. aggregate queries with no FROM) — catch, fall back to
  `columns=[]` for that shape, and pin BOTH behaviors.

## Out of scope

- Surfacing `columns` through the CLI query envelope or MCP
  `query_data` dict (separate contract change; note as follow-up if
  wanted).
- ODBC path (already correct — pin it in the same unit test file if a
  slot exists, cheap triangulation).
