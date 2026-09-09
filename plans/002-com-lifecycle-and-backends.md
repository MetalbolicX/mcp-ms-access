# Plan 002: Port the COM lifecycle and DAO/ADO/ODBC backends, add doctor

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in "STOP conditions" occurs, stop and report.
> When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git -C D:\code\python\ms-access-mcp-server rev-parse --short HEAD`
> → `c8beaab`. The oracle files below were verified at that commit; on drift,
> compare excerpts before proceeding (mismatch = STOP).

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: plans/001-foundation-and-ts-scaffold-removal.md
- **Category**: migration
- **Planned at**: oracle commit `c8beaab`, 2026-09-08

## Execution mode

`@medium` subagents per step, **strict TDD** where a unit-testable contract
exists, and one hard rule learned from prior sessions: **the orchestrator
re-verifies every COM gate in its own shell** — subagents have previously
reported false COM results. Inline the full oracle file content into each
dispatch (path-only references fail). Kill stray Access before/after gate runs.

## Why this matters

The STA `ComDispatcher` is the single most valuable asset of the old server —
dedicated STA thread, queue+Future marshalling, `CoInitialize`/
`CoUninitialize` symmetry, dialog dismissal, ordered ADO→DAO→Access teardown,
`Quit()` watchdog, PID-scoped `taskkill` fallback. The ReScript migration died
on exactly this surface (`winax` exit-134, plans 043–045). Porting it verbatim
(with one documented fix) is the foundation for every later capability.

## Current state (oracle facts, verified)

- `adapters/com_dispatcher.py` (485 lines): the dispatcher. One known defect to fix while porting — `shutdown()` (`:300-303`) nulls `_access_app`/`_current_db`/`_ado_conn` **on the caller's thread** after `join(timeout=15)` even if the join timed out; ordered release must stay owned by the STA thread (`_run`'s `finally`).
- `adapters/dao.py` (1286): DAO session + CRUD/DDL/QueryDefs/relations/linked tables. Duplicate helpers shared with `wincom.py` (deduplicate into ONE module): `_format_dao_value` (dao:495 vs wincom:321), `_sanitize_where_string` (dao:520 vs wincom:93), `_access_sql_type` (dao:536 vs wincom:1839), `_alter_table_add_column` (dao:1079 vs wincom:1774), `_strip_password` (dao:1172 vs wincom:1243).
- `adapters/odbc.py` (1051) + `odbc_schema_reader.py` (109): ODBC adapter (parameterized CRUD via `?` binding; schema via SQLTables/SQLColumns). Oracle bug to avoid: it inherits `ComOnlyAdapterMixin` — do NOT port the mixin.
- `wincom.py:1119-1123`: `execute_sql_script` runs each statement via `ado.Execute(...)` where `ado = self._dispatcher.ado_conn` captured from `CurrentProject.Connection` at connect (`wincom.py:214`). ADO preserves ANSI-92 semantics (inline `REFERENCES`, `DEFAULT`, `NOW()`) that DAO (ANSI-97) rejects.
- `services/backend_selector.py` (251): `BackendCapabilities` Flag + resolution. `_COM_ONLY_CAPS` = VBA|FORMS|REPORTS|MACROS|COMPACT|IMPORT_EXPORT_TEXT.
- Tests to port/adapt: `tests/unit/test_com_dispatcher.py`, `test_dao_adapter.py`, `test_odbc_adapter*.py`, `test_backend_selector.py`; integration markers `com_integration`/`odbc_integration`; skip gates in `tests/integration/helpers.py:53-77`; `temp_db_copy` fixture pattern in `tests/integration/conftest.py:22-57`.
- Fixtures: `tests/integration/fixtures/test_db.accdb` (11 MB) and `db/northwind.accdb` in the oracle.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Tests (unit) | `uv run pytest tests/unit -q` | all pass |
| Tests (integration) | `uv run pytest tests/integration -q -m "com_integration or odbc_integration"` | all pass, 0 unexpected skips on this machine |
| Lint/Types | `uv run ruff check .` / `uv run pyright` | exit 0 / 0 errors |
| Orphan check | `Get-Process MSACCESS -ErrorAction SilentlyContinue` | empty after gates |

## Scope

**In scope**: `src/ms_access_mcp/backends/**`, `src/ms_access_mcp/doctor.py`, `src/ms_access_mcp/errors.py` (extensions), `tests/**`, `tests/fixtures/**`.

**Out of scope**: `application/` use cases (plan 003), MCP tools (plan 004), CLI beyond `doctor`, `plans/`, the oracle repo.

## Git workflow

Branch `plan/002-com-backends`. Conventional commits per step (`feat(backends): port ComDispatcher`, `fix(dispatcher): keep handle ownership on STA thread after join timeout`, …). No push.

## Steps

### Step 1: Import fixtures

Copy `test_db.accdb` from oracle `tests/integration/fixtures/test_db.accdb` into `tests/fixtures/`. Record its SHA-256 in `tests/fixtures/README.md`. Commit the binary (11 MB is acceptable).

**Verify**: `Get-FileHash tests/fixtures/test_db.accdb` prints the recorded hash.

### Step 2: Port the dispatcher (verbatim + one fix)

Copy oracle `adapters/com_dispatcher.py` → `src/ms_access_mcp/backends/dispatcher.py`. Adaptations ONLY:
1. Package imports (`ms_access_mcp.*`).
2. **The shutdown fix**: when `join(timeout)` times out, do NOT null `_access_app`/`_current_db`/`_ado_conn` from the caller thread; leave the references to the STA thread's `finally` (`_release_com_safe`) and let the watchdog/PID-kill path handle a wedged thread.
3. No other behavior change. Port oracle `tests/unit/test_com_dispatcher.py` adapted to the new import path.

**Verify**: `uv run pytest tests/unit/test_dispatcher.py -q` → all pass (strict TDD: port tests first, watch them fail on missing module, then land the port).

### Step 3: Shared DAO SQL helpers (single source)

Create `backends/dao_sql.py` containing the five helpers listed in Current state, taken from the ORACLE `dao.py` copies (they are the maintained ones). Unit tests for: value formatting round-trips, WHERE sanitization (allowlist behavior), type mapping, password stripping.

**Verify**: `uv run pytest tests/unit/test_dao_sql.py -q` → all pass.

### Step 4: Port DAO adapter

Port `dao.py` → `backends/dao.py` importing `dao_sql` helpers (no duplicates). Keep: `DaoSession` lifecycle (long-lived `OpenDatabase` handle, `ReadOnly=False`), CRUD/DDL/QueryDefs/relations/linked-tables, `DBEngine` compact/repair. Drop: any HTTP-era imports. Port + adapt `tests/unit/test_dao_adapter.py` (fakes for the dispatcher).

**Verify**: `uv run pytest tests/unit -q` → all pass.

### Step 5: Port ODBC adapter

Port `odbc.py` + `odbc_schema_reader.py` → `backends/odbc.py` (+ `backends/odbc_schema_reader.py`). Remove the `ComOnlyAdapterMixin` base entirely; unsupported operations raise `BackendCapabilityMismatchError` directly. Keep `?`-parameter binding for SELECT/CRUD. Port `test_odbc_adapter*` unit tests.

**Verify**: `uv run pytest tests/unit -q` → all pass.

### Step 6: ADO backend

Create `backends/ado.py`:
1. Port `execute_sql_script` semantics: parse statements (oracle `wincom.py:_parse_script_lines` logic), execute each via the ADO connection, statement-level error context.
2. Implement a **direct ACE OLE DB connection** (`Provider=Microsoft.ACE.OLEDB.12.0;Data Source=...`) so scripts run WITHOUT `MSACCESS.EXE`.
3. Integration test on a disposable fixture copy executing an ANSI-92 script (inline `DEFAULT`, `REFERENCES`) — must produce the same schema result as the oracle's `CurrentProject.Connection` path.
4. **Decision gate**: if direct-provider ANSI-92 semantics diverge from `CurrentProject.Connection` (compare against oracle behavior on the same fixture), keep the `CurrentProject` path (requires plan-006 `backends/access.py`) and document the divergence in `tests/integration/ado_semantics.md`.

**Verify**: `uv run pytest tests/integration -q -m "com_integration" -k ado` → pass; `Get-Process MSACCESS` → empty (if direct connection held).

### Step 7: Hidden Access.Application adapter (minimal)

Port ONLY the connect/teardown handshake from oracle `wincom.py:170-262` into `backends/access.py`: `Dispatch("Access.Application")`, `Visible=False`, `OpenCurrentDatabase`, and reliance on dispatcher teardown (`CloseCurrentDatabase` + `Quit` + watchdog + PID kill). Full UI operations come in plan 006. Integration test: connect → read `CurrentDb().Name` → disconnect.

**Verify**: test passes; `Get-Process MSACCESS` empty within 10 s after disconnect.

### Step 8: Doctor

Implement `src/ms_access_mcp/doctor.py` with `ms-access-mcp doctor [--json] [--deep]`:
- Windows x64 + 64-bit Python (`sys.maxsize > 2**32`).
- Python/package versions.
- Access COM activation (`Dispatch("Access.Application")` probe, release immediately).
- ACE OLE DB provider availability (`ADODB.Connection` open on temp empty DB or provider enum).
- DAO `DBEngine.120` constructability.
- ODBC driver scan (`pyodbc.drivers()` contains "Microsoft Access Driver (*.mdb, *.accdb)").
- `--deep`: create + release a hidden Access instance; assert zero MSACCESS.EXE remains; temp-dir writability.
- Exit 1 with actionable remediation text (bitness mismatch guidance) when prerequisites fail; `--json` emits a machine-readable report.

**Verify**: `uv run ms-access-mcp doctor --json` → exit 0 on this machine (x64 Office/ACE present).

### Step 9: Lifecycle gate (orchestrator runs this directly)

```powershell
taskkill /F /IM MSACCESS.EXE 2>$null
1..20 | ForEach-Object {
  uv run python -c "from ms_access_mcp.backends.dispatcher import ComDispatcher; from ms_access_mcp.backends.access import connect_hidden; s=connect_hidden(r'<abs path to temp copy of test_db.accdb>'); s.disconnect()"
  if (Get-Process MSACCESS -ErrorAction SilentlyContinue) { throw "orphan on round $_" }
}
```

**Verify**: completes 20/20 with zero orphans; also run the DAO slice asserting `Get-Process MSACCESS` stays empty throughout (DAO must never launch Access).

## Test plan

- Unit: dispatcher, dao_sql, dao (fakes), odbc (fakes), doctor pure checks.
- Integration (marked): DAO CRUD/DDL on temp copies; ODBC driver tests; ADO ANSI-92 script; hidden-Access handshake; doctor `--deep`.
- Every mutating test: `temp_db_copy` pattern + SHA-256 check of the canonical fixture after the suite.

## Done criteria

- [ ] All unit + integration tests pass; 0 unexpected skips on this x64 machine
- [ ] 20/20 lifecycle rounds with zero `MSACCESS.EXE` orphans
- [ ] DAO operations proven to never launch `MSACCESS.EXE`
- [ ] `doctor --json` exits 0; `doctor` detects a simulated failure (e.g. hidden ODBC driver) with actionable text
- [ ] Canonical fixture hash unchanged after full suite
- [ ] No `ComOnlyAdapterMixin`, no duplicate DAO helpers (`grep -rn "_sanitize_where_string src/"` → one hit)
- [ ] `plans/README.md` row 002 updated

## STOP conditions

- Any COM call escapes the STA dispatcher (thread-affinity violation found in review/tests).
- Orphan `MSACCESS.EXE` appears in more than 0 of the 20 lifecycle rounds after one fix attempt.
- Direct ADO ANSI-92 semantics diverge AND the `CurrentProject` fallback also fails on the fixture.
- Oracle files no longer match `c8beaab` excerpts.

## Maintenance notes

- The shutdown fix changes a delicate teardown path — reviewer must read `plans/045-winax-teardown-crash-elimination.md` context in the oracle to understand why handle ownership matters.
- Plan 003 builds use cases directly on these adapters; keep their public signatures stable from here on.
