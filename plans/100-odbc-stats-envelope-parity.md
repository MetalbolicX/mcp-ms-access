# Plan 100: ODBC read-path envelope parity — stats asdict failure

> **Executor instructions**: as plan 094. Update the status row in
> `plans/README.md` (section "Plans 099–104") when done.
>
> **Drift check (run first)**:
> `git diff --stat 2fbe1de..HEAD -- src/ms_access_mcp/application/db_analysis.py src/ms_access_mcp/application/db_admin.py src/ms_access_mcp/backends/odbc.py`
> On mismatch with "Current state", STOP.

## Status

- **Priority**: P2
- **Effort**: S-M
- **Risk**: MED
- **Depends on**: plans 071/080/081 (envelope history)
- **Category**: backend parity / bug
- **Planned at**: commit `2fbe1de`, 2026-10-10

## Why this matters

LIVE-CONFIRMED 2026-10-10:

```
$ ms-access-mcp stats --db <scratch> --backend odbc --json
{"success": false, "error": "asdict() should be called on dataclass instances"}
```

Plan 071 unified write-path envelopes on typed models; the
`get_database_statistics` READ path still calls `asdict()` on an
adapter result that the ODBC backend returns as a plain dict →
`TypeError` → facade converts to Err. DAO path works (typed model).
The deferred finding (ledger, 077–093 section) listed several sibling
read methods; only stats reproduced live — characterization decides the
real blast radius.

## Current state (HEAD `2fbe1de`)

- CLI `stats` → `DatabaseUseCases.get_database_statistics` → adapter.
- `--backend odbc` connects fine (A1/A2 evidence: query works through
  ODBC); only the stats read path dies at the seam.
- `links list --backend odbc` returns a clean capability error
  ("ODBC backend does not support linked tables") — BY DESIGN, not a
  defect; stays.

## Steps

1. **Characterization matrix (RED baseline)** — run every core read
   CLI command against a %TEMP% scratch copy with `--backend odbc
   --json`: `tables`, `schema`, `stats`, `relationships`, `query`,
   `indexes`, `export`. Pin current outcomes in a table (pass/fail +
   error text). Expected from evidence: only `stats` fails.
2. **Locate the seam(s)** — grep `asdict(` in
   `application/db_analysis.py`, `db_admin.py`, `database.py` for
   sites consuming adapter statistics/analysis results; identify which
   receive a dict on ODBC (071 missed read paths).
3. **RED unit** — fake ODBC-flavored adapter returning plain dict
   where DAO returns the typed model; consumer must produce `Ok` with
   the same shape as DAO (assert keys, not class).
4. **GREEN** — normalize at the seam per the 071 pattern (prefer: the
   ODBC backend returns the typed model so consumers stay uniform;
   fallback if disproportionate: consumer-level model-or-dict
   normalization helper).
5. **Live gate** — re-run the matrix; `stats --backend odbc --json`
   returns `success: true` with the same key set as DAO (values may
   legitimately differ where ODBC lacks data — assert keys, tolerate
   value gaps, document any).

## Verify

Unit: focused suite for the touched application module(s).
Live: matrix above, all `success: true` except `links` (capability
error by design). Standard full gates.

## STOP conditions

- ODBC driver absent on the execution machine → land unit-level fix,
  mark the live matrix `pending operator`, report.
- Characterization finds MORE failing read paths than stats: fix stats
  here; additional paths get their own rows/plan extension decision —
  do not balloon this plan.

## Out of scope

- `links` on ODBC (deliberate capability error).
- Any behavior change on the DAO path (characterization pins must stay
  green).
