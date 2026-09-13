# Proposal: External-database migration profile

## Intent

Ship Access-to-external-database migration through four MCP tools, preserving oracle names without its credential-vault defect.

## Context

Confirmed handoff: Plan 024 decisions :45-73, baseline `a9e5dd4`. Drift since `c764e12` includes DDL, XLSX, and Plan 026 schema work; extraction uses that updated seam (`exploration.md:10-20`).

## Scope

### In scope
- New `migration` profile, four tools, Pydantic models, postgres/mysql/sqlite/sqlserver connectors, and tests.
- Optional `pyproject.toml` extra with coordinated `uv.lock` update; server help, `plans/README.md`, and OpenSpec artifacts.
- CLI parity remains a spec-phase decision, not an approved deliverable here.

### Out of scope
- Credential vault, `server_id`, async/background workers, restart persistence, per-dialect DDL tuning, real-server qualification, and new Access backend code.
- Plan 025's `analysis` profile and unrelated work.

## Capabilities

### New Capabilities
- `external-database-migration`: schema extraction/upload, data transfer, and job-status retrieval.

### Modified Capabilities
- None; existing specs cover installer/configuration, not migration.

## Approach

- New `migration` profile in `profiles.py`; module `ms_access_mcp.mcp_tools.migration`; included in `"full"`. Tool names: `extract_schema`, `upload_schema`, `transfer_data`, `get_migration_status`.
- MCP tools thin → `application/migration.py` use cases → connectors under `backends/migration/` (one module per target dialect + registry). Pydantic models in `src/ms_access_mcp/models/migration.py`.
- `migrate = ["psycopg2-binary>=2.9.0", "mysql-connector-python>=8.0.0"]`; sqlite via stdlib `sqlite3`; sqlserver via existing `pyodbc`; lazy imports for postgres/mysql.
- `transfer_data` runs to completion; `get_migration_status` reads the module-level tracker. No threads or restart persistence. Batch-only remains the design recommendation.
- DAO/ODBC reads use existing `execute_query`/schema getters. ODBC uses granular getters, recording unavailable metadata in `unknown_metadata`, never interpreting its schema-plan sentinel as an empty database.
- NO credential vault / NO `server_id`. Passwords live only inside caller-provided `connection_string`; sanitize errors, guard source paths, require `confirm=True` for external writes. Test with fakes and SQLite.

## Risks

- Driver/server availability: CI lacks postgres/mysql per handoff; Plan :260-269 requires fakes/SQLite, not live qualification. Dev-host availability remains unverified.
- Lock drift: optional dependencies invalidate `uv.lock` (Plan :171-176); apply runs `uv lock` once, reviews driver/transitive changes, isolates pre-existing edits.
- Parallel profile merge: Plan 025 shares registration surfaces (`exploration.md:53-57`); coordinate `profiles.py`/`server.py` edits.
- Metadata loss: ODBC sentinel and hidden relationships (`exploration.md:85-90`); test granular extraction and explicit unknown flags.

## Alternatives considered

- Blind oracle port: rejected for eager imports, vault behavior, and ODBC sentinel handling.
- Background/persistent jobs: rejected by synchronous scope.

## Rollback Plan

Revert migration code/registration and paired dependency/lock changes together. Target writes require backup restoration or reviewed cleanup; code rollback cannot undo them.

## Success Criteria

- [ ] Four tools load through `migration`/`full` without optional drivers.
- [ ] SQLite round-trip, metadata-loss, confirmation, and sanitization tests pass.
- [ ] Pytest, Ruff, and Pyright gates pass; archive and plan index updated.
