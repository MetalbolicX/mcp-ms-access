# Plan 101: Linked-tables envelope truth — rowcount and double nesting

> **Executor instructions**: as plan 094. Update the status row in
> `plans/README.md` (section "Plans 099–104") when done.
>
> **Drift check (run first)**:
> `git diff --stat 2fbe1de..HEAD -- src/ms_access_mcp/backends/dao.py src/ms_access_mcp/application/database.py`
> On mismatch with "Current state", STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW-MED
- **Depends on**: plans 080/081 (facade-adapter honesty history)
- **Category**: envelope honesty / bug
- **Planned at**: commit `2fbe1de`, 2026-10-10

## Why this matters

LIVE-CONFIRMED 2026-10-10:

```
$ ms-access-mcp links list --db <scratch> --json
{"success": true, "data": {"success": true, "linked_tables": [ ...many... ]},
 "rowcount": 2}
```

Two defects in one envelope:
1. `rowcount` is **always 2** — `dao.get_linked_tables()` returns a
   legacy dict wrapper (`dao.py:824 -> dict`), and
   `database.py:467` computes `rowcount=len(linked)` = the number of
   dict KEYS, not tables.
2. **Double nesting** — the adapter's inner `{"success": ...,
   "linked_tables": [...]}` envelope is wrapped again by the facade's
   `_ok(...)`. This is exactly the legacy pattern plans 080/081
   retired everywhere else; `get_linked_tables` was missed.

## Current state (HEAD `2fbe1de`)

- `dao.py:824` `get_linked_tables() -> dict` builds
  `linked_tables: list[dict]` then wraps it (items keep keys
  `name`, `source_table`, `connect_string`, `type`, `attributes` —
  system tables included, e.g. MSysACEs; that inclusion is EXISTING
  behavior, not changed here).
- `database.py:451-468`: `_ok(data=linked, rowcount=len(linked))`.
- Consumers: MCP tool `get_linked_tables` (`mcp_tools/core.py:234`),
  CLI `links list`. Both pass the envelope through.
- The ODBC backend raises a clean capability error (plan 100 notes) —
  untouched.

## Steps

1. **Characterization (RED baseline)** — pin the CURRENT shape in a
   characterization test (double envelope + rowcount==2 on a fake
   adapter shaped like today's DAO), so the refresh is deliberate
   (plan-035/080 precedent).
2. **RED (new contract)** — `dao.get_linked_tables() -> list[dict]`:
   returns the list directly; raises (or returns per existing
   adapter-error convention) when not connected. Facade:
   `_ok(data=linked, rowcount=len(linked))` now counts TABLES;
   single nesting: `data` IS the list.
   Not-connected handling moves to the facade guard (existing
   `_get_adapter` Err path) or a raised `AccessBackendError` caught by
   the facade — pick the convention already used by sibling DAO
   methods at execution time and record the choice in the row.
3. **GREEN** + refresh the characterization pins to the new contract
   (separate commit, house precedent).
4. **Live gate** — `links list --db <scratch> --json` on a %TEMP%
   copy: `rowcount` equals the actual number of listed tables
   (evidence run had many tables with rowcount 2), `data` is a list.

## Verify

```
PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest tests/unit/test_database.py tests/unit/test_mcp_core.py -q -p no:cacheprovider
```
(whichever files pin the envelope after characterization; plus full
standard gates and the live CLI check.)

## STOP conditions

- A consumer depends on the inner `{"success", "linked_tables"}` shape
  beyond passthrough (grep first: `linked_tables` key consumers) —
  surface the dependency, do not silently break.
- `test_tool_schemas.py` or an integration golden pins the double
  envelope — refresh deliberately with the refresh commit, noting the
  prior assertion in its message.

## Out of scope

- Filtering system tables (MSys*) from the list — existing behavior,
  separate product decision if ever wanted.
- ODBC/ADO linked-table support (capability error stays).
