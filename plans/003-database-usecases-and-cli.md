# Plan 003: Shared database use cases and the argparse CLI

> **Executor instructions**: Follow step by step; verify each command result.
> STOP conditions are hard. Update `plans/README.md` when done.
>
> **Drift check (run first)**: oracle HEAD `c8beaab`; new repo must have plans
> 001–002 DONE (backends importable). On mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: plans/002-com-lifecycle-and-backends.md
- **Category**: feature
- **Planned at**: oracle commit `c8beaab`, 2026-09-08

## Execution mode

`@medium` + strict TDD. Use-case layer is tested against **fakes** (dispatcher/
odbc doubles); backends are re-tested through marked integration tests only at
the equivalence gate. Orchestrator verifies the CLI/MCP-parity seed and the
confirm-gates in-shell.

## Why this matters

CLI and MCP must be thin presenters over ONE application layer (the
cli-first-architecture decision, oracle SDD `sdd/cli-first-architecture`).
This plan creates that layer for the core database domain and the complete
argparse CLI, giving users full no-LLM operation and giving plan 004 a stable
surface to wrap.

## Current state

- Backends exist from plan 002: `backends/{dispatcher,dao_sql,dao,ado,odbc,access,odbc_schema_reader}.py`.
- Oracle reference for semantics: `mcp/_helpers.py:54-154` (`guard_destructive` confirm/dry-run pattern — replicate in `safety.py` consumption), `services/sql_builder.py` (52 lines, port as-is), `services/backend_selector.py` (capability routing).
- Oracle CLI shared-adapter logic `cli/main.py:84-192` (`_get_adapter` + `--backend odbc|com|dao|auto`) is the routing pattern to keep.
- Oracle tool result shapes (parity source of truth): `{"success": bool, "error"?: str}` envelopes, `{"success","data","rowcount"}` for queries, `{"success","rowcount"}` for writes. Preserve these exact shapes.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Unit tests | `uv run pytest tests/unit -q` | all pass |
| Integration | `uv run pytest tests/integration -q` | all pass, no unexpected skips |
| CLI smoke | `uv run ms-access-mcp --help` | exit 0, subcommands listed |
| Lint/Types | `uv run ruff check .` / `uv run pyright` | exit 0 / 0 errors |

## Scope

**In scope**: `src/ms_access_mcp/application/database.py`, `src/ms_access_mcp/capabilities.py` (new), `src/ms_access_mcp/safety.py` (confirm-gate consumption), `src/ms_access_mcp/cli.py`, `tests/**`.

**Out of scope**: MCP tools (plan 004), automation/maintenance/versioning use cases (plans 006/007), XLSX export (deferred extra), installer.

## Git workflow

Branch `plan/003-usecases-cli`. Conventional commits per logical unit. No push.

## Steps

### Step 1: Capability routing

`capabilities.py`: port `BackendCapabilities` Flag + resolution as FUNCTIONS (not a class of statics): `resolve_backend(requested: str, caps: BackendCapabilities, platform: str) -> Literal["odbc","dao","ado","access"]`, raising `BackendUnavailableError`/`BackendCapabilityMismatchError` with the oracle's error wording. `COM_ONLY_CAPS` = VBA|FORMS|REPORTS|MACROS|COMPACT|IMPORT_EXPORT_TEXT.

Strict TDD: port oracle `tests/unit/test_backend_selector.py` cases first.

**Verify**: `uv run pytest tests/unit/test_capabilities.py -q` → all pass.

### Step 2: SQL builder + WHERE allowlist

Port `services/sql_builder.py` → `application/sql_builder.py`. Port the WHERE raw-clause allowlist (`_WHERE_ALLOWLIST_RE` + dangerous patterns, oracle `wincom.py:64-89` — "must match pattern already deployed in odbc.py"): single source in `safety.py`, consumed by both DAO and ODBC write paths via the use cases.

**Verify**: unit tests for allowlist accept/reject cases (port oracle cases; include `()` in the allowed set — the oracle plan-003 gate failure documented its absence as a defect).

### Step 3: `application/database.py` use cases

One class or module of functions, constructor-injected backends (no globals, no container). Operations (names fixed — MCP tools in plan 004 reuse them):

```text
open_database(path, backend="auto", password=None) -> ConnectionInfo
close_database(name) -> {"success": true}
list_connections() / set_active(name) / get_active() / is_connected()
get_tables() / get_system_tables() / get_table_schema(table)
get_indexes(table) / get_relationships() / get_object_metadata(kind, name)
get_queries() / create_query(name, sql) / set_query_sql(name, sql) / delete_query(name)
query_data(table|sql, params, limit) -> {"success","data","rowcount"}
insert_data(table, data) / update_data(table, data, where) / delete_data(table, where)
create_table(spec) / alter_table(table, ops) / delete_table(table, confirm)
create_index(...) / drop_index(...) / create_relationship(...) / delete_relationship(...)
export_data(table|query, format csv|json, path) 
get_linked_tables()
get_database_properties(names?) / set_database_property(name, value, confirm)
get_database_statistics()
```

Rules: every file path through `safety.is_path_allowed`; every destructive op through the confirm gate (`confirm: bool = False` → error "confirmation required" unless policy allows); readonly policy blocks writes at this layer; single ACTIVE connection by default (pool is out of scope — add only if a measured need appears).

Strict TDD: fakes for all use cases first (RED), then wiring to real backends.

**Verify**: `uv run pytest tests/unit/test_application_database.py -q` → all pass.

### Step 4: CLI

`cli.py` argparse subcommands mapping 1:1 onto use cases (stateless: each command opens/uses/closes):

```text
ms-access-mcp db open|close|list|active
ms-access-mcp tables [--system]
ms-access-mcp schema TABLE
ms-access-mcp indexes TABLE | relationships | stats | props get|set
ms-access-mcp queries [list|create|set|delete]
ms-access-mcp query SQL [--param P...] [--limit N] | insert|update|delete TABLE ...
ms-access-mcp ddl create-table|alter|drop-table|create-index|drop-index|relationship
ms-access-mcp export TABLE|--query Q --format csv|json --out PATH
ms-access-mcp links list
ms-access-mcp doctor [--json] [--deep]
```

Global flags: `--db PATH`, `--backend auto|dao|odbc`, `--json` (machine output), `--yes` (skip interactive confirm), `--allow-write` (overrides readonly policy for this invocation, explicit). Exit codes: 0 success, 1 error envelope, 2 usage. Destructive commands print a preview and require `--yes` or interactive `y`.

**Verify**: `uv run ms-access-mcp --help`; smoke on a temp fixture copy: `tables`, `schema <t>`, `query "SELECT ..."`; destructive `drop-table` without `--yes` → exit 1 with "confirmation required".

### Step 5: Equivalence seed + hygiene gate

- Test: same use case called via CLI `--json` and directly returns identical JSON (this harness grows in plan 004 to cover MCP).
- Run full integration suite; assert canonical fixture SHA-256 unchanged; assert zero `test_db.accdb.tmp_*` files created under `tests/fixtures/`.

**Verify**: `uv run pytest -q` → all pass; hash check passes; `Get-ChildItem tests/fixtures -Filter *.tmp_*` → empty.

## Test plan

- `tests/unit/test_capabilities.py` — routing truth table (ported).
- `tests/unit/test_application_database.py` — fakes: envelopes, confirm gates, readonly, path guard rejections.
- `tests/unit/test_cli_*.py` — argparse contract: flags, exit codes, `--json` shapes.
- `tests/integration/` — real DAO/ODBC/ADO paths on temp copies; hash assertions.

## Done criteria

- [ ] All tests pass; 0 unexpected skips; ruff + pyright clean
- [ ] CLI covers every use case listed in step 3 (checklist diff `--help` vs list)
- [ ] Destructive ops blocked without `--yes` (automated test)
- [ ] CLI `--json` output equals direct use-case JSON (seed harness green)
- [ ] Fixture hash unchanged; zero `tmp_*` leakage
- [ ] `plans/README.md` row 003 updated

## STOP conditions

- A use case needs backend behavior that plan 002 did not port (scope growth → report, don't improvise).
- Confirm-gate or readonly bypass is required to make tests pass — safety regression.
- Result envelopes deviate from the oracle shapes quoted above.

## Maintenance notes

- Plan 004 wraps exactly these use cases — signature changes after this plan require a new plan.
- Reviewer focus: WHERE allowlist completeness and the confirm-gate wiring (both are injection-adjacent).
