# Spec: External-database migration profile

Capability: `external-database-migration` (new). Tools: `extract_schema`, `upload_schema`, `transfer_data`, `get_migration_status`.

## Requirements

### REQ-MIG-001: Schema extraction with target-dialect DDL

`extract_schema(database_path, target_type)` MUST read tables, columns (name/type/nullable/default/autoincrement), PKs, FKs, and indexes from an Access DB via the existing DAO/ODBC seam and emit DDL for `postgres`, `mysql`, `sqlite`, or `sqlserver`.

**Rationale**: Extraction is the entry point of every migration and must reuse Plan 026's seam without new Access backend code.

**Acceptance**:
- DAO path: full metadata; ODBC path: granular getters used, unavailable metadata recorded in `unknown_metadata` flags; the ODBC `get_table_schema_plan()` sentinel (`([], "unknown")`) MUST NOT be interpreted as an empty database.
- Output conforms to `ExtractedSchema` (source, version, extracted_at, tables, relationships, unknown_metadata).
- Access path validated/guarded before opening; invalid or missing file returns a sanitized error.
- Type-mapping invariants pinned per dialect; concrete table owned by design phase.

### REQ-MIG-002: Schema upload to target

`upload_schema(target_type, connection_string, schema_json, confirm)` MUST apply DDL to the target in FK topological order, skipping existing tables into `tables_failed`, via psycopg2 / mysql-connector-python / sqlite3 / pyodbc connectors.

**Rationale**: Applying schema before data preserves referential integrity.

**Acceptance**:
- Returns `{tables_created, tables_failed}`; per-table failures isolated.
- postgres/mysql drivers lazily imported; missing driver fails with "install ms-access-mcp[migrate]".
- Requires `confirm=True`; without it, no DDL is executed.
- Errors sanitized — never echo `connection_string`.

### REQ-MIG-003: Batched data transfer with job tracking

`transfer_data(target_type, connection_string, database_path, schema_json=None, transfer_mode="auto", verification_mode="full", table_overrides=None)` MUST copy rows per table in batches and register the completed job in the module-level tracker.

**Rationale**: Row copy is the core migration value; the tracker enables status retrieval without persistence.

**Acceptance**:
- Extracts schema when `schema_json` is omitted; supports per-table overrides (`columns`, `where`, `order_by`).
- `transfer_mode`: `batch` executes; `auto` resolves to batch; `linked`/`passthrough` REJECTED explicitly (documented v1 limitation, never silently ignored).
- `verification_mode`: `full` (row sampling/content) or `count-only`.
- Per-table failure isolation: failed table rolled back, job marked failed with per-table errors in `TableResult`.
- Requires `confirm=True`; returns `{success, job_id}`.

### REQ-MIG-004: Job status retrieval

`get_migration_status(job_id)` MUST return the tracked job dict; job IDs MUST be short (`uuid4().hex[:8]`).

**Rationale**: Synchronous jobs still need post-hoc inspection within the process lifetime.

**Acceptance**:
- Job model: id, status (pending|running|completed|failed), phase (extract|upload|transfer), current_table, progress, started_at, completed_at, results, errors.
- Unknown `job_id` returns a sanitized not-found error, not a stack trace.
- Tracker is in-process only; state loss on restart is a documented limitation.

### REQ-MIG-005: Connection-string-only credentials

The system MUST NOT provide any credential vault, `store_credential`, or `server_id`; `connection_string` is the only auth path.

**Rationale**: The oracle's vault took plaintext passwords as tool arguments — a must-not-port defect.

**Acceptance**:
- No secret values in artifacts, logs, or error messages.
- Docs/help warn about shell-history and log exposure of `connection_string`.
- `grep` for `server_id`/`credential` in migration code yields no hits.

### REQ-MIG-006: Synchronous execution model

`transfer_data` MUST block until complete; `get_migration_status` MUST be a non-blocking read. No threads, background workers, or cross-restart persistence.

**Rationale**: Synchronous in-process scope is pre-decided and keeps the job model trivial.

**Acceptance**:
- `transfer_data` returns only after all tables are processed.
- Tracker is a plain module-level structure for single-threaded use.

### REQ-MIG-007: Layered architecture

MCP tools MUST be thin wrappers → `application/migration.py` use cases → `backends/migration/connectors/` adapters (one module per dialect + registry). Pydantic models in `src/ms_access_mcp/models/migration.py`.

**Rationale**: Plan 014's architecture guard — no MCP→backends imports.

**Acceptance**:
- Import direction verified: `mcp_tools → application → backends/migration`.
- Connector interface: `connect/create_table/table_exists/insert_batch/rollback_table/disconnect`.
- Migration Pydantic models NOT merged into the existing dataclass `models.py`.

### REQ-MIG-008: Profile wiring

`migration` MUST be added to `PROFILES`, dispatch, `"full"` concatenation, and `server.py` help text, registering the four verbatim tool names.

**Rationale**: Profile parity is how the tools become reachable.

**Acceptance**:
- `load_profile("migration")` and `"full"` expose exactly the four tools.
- Tools load successfully without the `migrate` extra installed.
- No edits to Plan 025's parallel `analysis` surfaces.

### REQ-MIG-009: Optional `migrate` extra

`pyproject.toml` MUST add `migrate = ["psycopg2-binary>=2.9.0", "mysql-connector-python>=8.0.0"]`; sqlite uses stdlib `sqlite3`, sqlserver the existing `pyodbc`.

**Rationale**: Base install stays lean; drivers are opt-in.

**Acceptance**:
- `uv lock` diff adds only the two drivers plus transitive deps; committed together with the pyproject change.
- Unit suite green with the extra absent.

### REQ-MIG-010: Fake-first test coverage

Unit tests MUST use fake connectors (no real drivers); integration tests MUST be marked and skip gracefully when drivers are absent.

**Rationale**: CI has no postgres/mysql servers; contracts are pinned by fakes plus a sqlite end-to-end cycle.

**Acceptance**:
- SQLite end-to-end: fixture copy → extract → upload → transfer → verify on a temp dir.
- Coverage: registry, type mapping, FK sort, job lifecycle, failure isolation, confirm gates, lazy-import message, error sanitization.

## Scenarios

### extract_schema

**Scenario: Happy path (DAO)**
- GIVEN a valid Access file and `target_type="postgres"`
- WHEN `extract_schema` is called
- THEN tables, columns, PKs, FKs, indexes are returned with postgres DDL and empty `unknown_metadata`.

**Scenario: ODBC reduced metadata**
- GIVEN only the ODBC backend is available
- WHEN extraction uses granular getters and relationships are unreadable
- THEN the schema succeeds and `unknown_metadata` flags the missing relationship data (sentinel never treated as empty DB).

**Scenario: Invalid path**
- GIVEN a nonexistent or disallowed `database_path`
- WHEN `extract_schema` is called
- THEN a sanitized error is returned and no file is opened.

**Scenario: Empty Access DB**
- GIVEN a valid DB with no user tables
- WHEN `extract_schema` is called
- THEN an empty `tables` list is returned with `success=True`.

### upload_schema

**Scenario: Happy path**
- GIVEN valid schema_json, sqlite target, `confirm=True`
- WHEN `upload_schema` runs
- THEN tables are created in FK order and listed in `tables_created`.

**Scenario: Missing driver**
- GIVEN `target_type="postgres"` without psycopg2 installed
- WHEN `upload_schema` runs
- THEN it fails with "install ms-access-mcp[migrate]" and no connection attempt.

**Scenario: Confirm gate**
- GIVEN `confirm` is not True
- WHEN `upload_schema` runs
- THEN no DDL executes and a confirmation-required error is returned.

**Scenario: Existing tables**
- GIVEN some tables already exist on the target
- WHEN `upload_schema` runs
- THEN existing tables land in `tables_failed`, the rest in `tables_created`.

### transfer_data

**Scenario: Happy path (sqlite end-to-end)**
- GIVEN a fixture Access copy, empty sqlite target, `confirm=True`
- WHEN `transfer_data` runs with defaults
- THEN rows are copied in batches, the job completes, and `{success, job_id}` is returned.

**Scenario: Rejected transfer mode**
- GIVEN `transfer_mode="linked"` or `"passthrough"`
- WHEN `transfer_data` runs
- THEN it fails with an explicit unsupported-mode error (never silently batched).

**Scenario: Per-table failure isolation**
- GIVEN one table fails mid-transfer
- WHEN `transfer_data` runs
- THEN that table is rolled back, others complete, and the job is marked failed with per-table errors.

**Scenario: Invalid connection string**
- GIVEN a malformed `connection_string`
- WHEN `transfer_data` runs
- THEN a sanitized error is returned containing no credentials.

### get_migration_status

**Scenario: Happy path**
- GIVEN a completed `transfer_data` job
- WHEN `get_migration_status(job_id)` is called
- THEN the job dict with status `completed`, results, and progress is returned without blocking.

**Scenario: Unknown job**
- GIVEN a job ID never registered
- WHEN `get_migration_status` is called
- THEN a sanitized not-found error is returned.
