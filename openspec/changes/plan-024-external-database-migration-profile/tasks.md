# Tasks: External-database migration profile (Plan 024)

## Task Overview

Plan 024 ships Access-to-external-database migration: a `migration` profile with four MCP tools (`extract_schema`, `upload_schema`, `transfer_data`, `get_migration_status`), an application use-case layer with an in-process job tracker, and four target-dialect connectors under `backends/migration/`. Ordering rationale in one line: models are restructured first because Python forbids `models.py` and `models/migration.py` coexisting — that unblocks every pydantic import; the registry precedes dialect connectors (they subclass and register); sqlite lands first among dialects (stdlib, driver-free) so the end-to-end path is provable without optional drivers; packaging (`migrate` extra + `uv lock`) is deliberately last-but-one so the lockfile changes exactly once, in isolation.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,800–2,200 (impl ~1,100 · tests ~700 · wiring/docs ~50) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 models → PR 2 connectors+tests → PR 3 application+tests → PR 4 tools+wiring+packaging+e2e |
| Delivery strategy | ask-on-risk (defaulted — dispatch did not specify) |
| Chain strategy | pending — plan-branch worktree; stacked commits per work unit recommended |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Tasks | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-------|-----------|----------------------|-----------------|-------------------|
| 1 | Models layer | T-001–T-002 | PR 1 | `uv run pytest tests/unit/test_models_schema.py tests/unit/test_models_versioning.py tests/unit/test_models_migration.py -q` | N/A — pure model layer, no runtime surface | Revert commit; reverse `git mv` restores flat `models.py` + ruff glob |
| 2 | Connector layer | T-003–T-007, T-012 | PR 2 | `uv run pytest tests/unit/test_migration_connectors.py -q` | N/A — fakes only; real cycle requires unit 3/4 | Delete `backends/migration/`; no callers exist yet |
| 3 | Application layer | T-008, T-013 | PR 3 | `uv run pytest tests/unit/test_migration_application.py -q` | N/A — fake connectors/sources by design (REQ-MIG-010) | Delete `application/migration.py`; units 1–2 unaffected |
| 4 | Oracle surface + wiring | T-009–T-011, T-014, T-015 | PR 4 | `uv run pytest tests/unit/test_mcp_migration.py tests/unit/test_profiles.py tests/unit/test_architecture_boundaries.py -q` | `uv run pytest tests/integration/test_migration_sqlite_e2e -q`; server boots with profile `migration` without `migrate` extra | Revert wiring commits; pyproject + `uv.lock` reverted together |

## Tasks

### T-001 — Convert `models.py` to `models/` package
- **Scope:** `git mv src/ms_access_mcp/models.py src/ms_access_mcp/models/__init__.py` (content byte-identical); `pyproject.toml` ruff per-file-ignores `"src/ms_access_mcp/models.py" = ["D"]` → `"src/ms_access_mcp/models/**" = ["D"]`
- **REQ:** REQ-MIG-007
- **Acceptance:**
  - All existing `from ms_access_mcp.models import X` imports work unchanged; plan 026 dataclasses stay in `__init__.py`; `models/__init__.py` does NOT import `models.migration`
  - `tests/unit/test_models_schema.py` + `test_models_versioning.py` pass unmodified; `ruff check .` and `pyright` green
- **Depends on:** none · **Effort:** S · **Risk:** MED (import-surface restructure)

### T-002 — Pydantic migration models
- **Scope:** Create `src/ms_access_mcp/models/migration.py` (`ExtractedSchema`, `MigrationJob`, `TableResult`, `TableTransferConfig`; reuse dataclass `TableSchema` family as nested types) + `tests/unit/test_models_migration.py`
- **REQ:** REQ-MIG-001, REQ-MIG-004, REQ-MIG-007
- **Acceptance:**
  - `ExtractedSchema` fields: source, version, extracted_at, tables, relationships, unknown_metadata; `MigrationJob` fields per REQ-MIG-004 (id, status, phase, current_table, progress, started_at, completed_at, results, errors)
  - Pydantic v2 serializes nested dataclass `TableSchema` natively (no duplicate table shape); model contract tests pass
- **Depends on:** T-001 · **Effort:** M · **Risk:** LOW

### T-003 — Connector registry + BaseConnector ABC
- **Scope:** Create `src/ms_access_mcp/backends/migration/__init__.py` (`get_connector(target_type, connection_string)`, `render_schema_ddl(target_type, tables)` with FK-topological sort, "install ms-access-mcp[migrate]" message) + `backends/migration/connectors/__init__.py` (`BaseConnector` ABC: `connect/table_exists/create_table/insert_batch/rollback_table/disconnect`; `_CONNECTOR_FACTORIES` lazy registry)
- **REQ:** REQ-MIG-002, REQ-MIG-007
- **Acceptance:**
  - Registry resolves target_type → factory lazily (no dialect module imported at package import); unknown dialect → sanitized error
  - `render_schema_ddl` orders CREATE TABLE FK-topologically, then ALTER TABLE FKs, then indexes; works without connecting
- **Depends on:** T-001 (needs `TableSchema`) · **Effort:** M · **Risk:** LOW

### T-004 — Sqlite connector (stdlib)
- **Scope:** Create `src/ms_access_mcp/backends/migration/connectors/sqlite.py`: `TYPE_MAP`, `quote_identifier` (`"x"`, rejects `; " ' --` and whitespace), `render_table_ddl` (`INTEGER PRIMARY KEY AUTOINCREMENT` for autoincrement PK), connector with `?` placeholders
- **REQ:** REQ-MIG-001, REQ-MIG-002, REQ-MIG-003
- **Acceptance:**
  - Zero third-party imports (stdlib `sqlite3` only); DDL/insert/rollback executable against a temp sqlite file
  - Type mapping per design §4 table (Date/Time → TEXT ISO-8601; unmapped → TEXT fallback with source-type comment)
- **Depends on:** T-003 · **Effort:** M · **Risk:** LOW

### T-005 — SQL Server connector (pyodbc)
- **Scope:** Create `src/ms_access_mcp/backends/migration/connectors/sqlserver.py`: pyodbc (core dep — top-level import allowed), `[x]` quoting, `?` placeholders, `IDENTITY(1,1)`, `NVARCHAR(MAX)` fallbacks per design §4
- **REQ:** REQ-MIG-001, REQ-MIG-002, REQ-MIG-003
- **Acceptance:**
  - Module-level `TYPE_MAP`/`quote_identifier`/`render_table_ddl` contract identical to T-004 shape; driver never required at import time of `backends.migration`
  - Import of the package without pyodbc-installed-targets does not break base install (pyodbc is core; unit tests use fakes anyway)
- **Depends on:** T-003 · **Effort:** M · **Risk:** LOW

### T-006 — Postgres connector (lazy psycopg2)
- **Scope:** Create `src/ms_access_mcp/backends/migration/connectors/postgres.py`: driver imported inside `connect()` ONLY; `%s` placeholders, `"x"` quoting, `GENERATED ALWAYS AS IDENTITY`, per design §4
- **REQ:** REQ-MIG-001, REQ-MIG-002, REQ-MIG-003
- **Acceptance:**
  - Importing module without psycopg2 installed succeeds; `connect()` raises sanitized "install ms-access-mcp[migrate]" error with no connection attempt
  - Type map rows per design §4 (Currency → NUMERIC(19,4), GUID → UUID, Binary → BYTEA, etc.)
- **Depends on:** T-003 · **Effort:** M · **Risk:** MED (lazy-import correctness)

### T-007 — MySQL connector (lazy mysql-connector)
- **Scope:** Create `src/ms_access_mcp/backends/migration/connectors/mysql.py`: lazy import inside `connect()`; `%s` placeholders, `` `x` `` quoting, `AUTO_INCREMENT` with PK emitted inline (mysql key requirement), per design §4
- **REQ:** REQ-MIG-001, REQ-MIG-002, REQ-MIG-003
- **Acceptance:**
  - Same lazy-import guarantee as T-006; autoincrement renderer emits PK inline (design Open Question note)
  - UNSIGNED variants mapped per design §4 type table
- **Depends on:** T-003 · **Effort:** M · **Risk:** MED

### T-008 — Application use cases + job tracker
- **Scope:** Create `src/ms_access_mcp/application/migration.py`: `MigrationUseCases` (`extract_schema`, `upload_schema`, `transfer_data`, `get_migration_status`) + module-level `_JOBS` tracker (`uuid4().hex[:8]`, collision-regenerated; `register/get/list_jobs`)
- **REQ:** REQ-MIG-001…006
- **Acceptance:**
  - Extract: DAO path uses `get_table_schema_plan()` (full metadata); ODBC path composes granular getters with all five `unknown_metadata` flags; sentinel `([], "unknown")` never called; path validated before open; empty DB → `success=True`, empty `tables`
  - Upload/transfer: `confirm=True` gates (no DDL/rows otherwise); existing tables → `tables_failed`; `linked`/`passthrough` rejected explicitly, `auto`→`batch`; 1000-row batches; per-table failure → `rollback_table`, job failed with per-table `TableResult` errors; verification `full`/`count-only`
  - Errors sanitized (never echo `connection_string`); unknown `job_id` → sanitized not-found; `transfer_data` returns `{success, job_id}` only after completion (synchronous); no `server_id`/credential strings anywhere (REQ-MIG-005)
- **Depends on:** T-002, T-003 · **Effort:** L · **Risk:** HIGH (largest surface: gates, sanitization, isolation, tracker)

### T-009 — MCP tool wrappers
- **Scope:** Create `src/ms_access_mcp/mcp_tools/migration.py` (four thin wrappers + `register()` + `_TOOLS`, pattern `mcp_tools/versioning.py`) + `tests/unit/test_mcp_migration.py`; extend `tests/unit/test_architecture_boundaries.py` for `mcp_tools → application → backends/migration` direction
- **REQ:** REQ-MIG-005, REQ-MIG-007
- **Acceptance:**
  - Tool names verbatim: `extract_schema`, `upload_schema`, `transfer_data`, `get_migration_status`; `confirm` params in signatures; wrappers contain no business logic
  - Architecture-boundary test proves import direction; docstrings warn about connection-string exposure (shell history/logs)
- **Depends on:** T-008 · **Effort:** M · **Risk:** LOW

### T-010 — Profile wiring
- **Scope:** Modify `src/ms_access_mcp/profiles.py` (`PROFILES` + `"migration"`, `migration = [...]` after ddl, dispatch entry, `"full"` concat) + `server.py` help text (opt-in list + `Valid values:`) + `tests/unit/test_profiles.py`
- **REQ:** REQ-MIG-008
- **Acceptance:**
  - `load_profile("migration")` and `"full"` expose exactly the four tools; tools load without `migrate` extra installed
  - Zero edits to plan 025's `analysis` surfaces (merge conflict resolved by concatenation at integration time, per design §7)
- **Depends on:** T-009 · **Effort:** S · **Risk:** LOW

### T-011 — `migrate` extra + lockfile coordination
- **Scope:** Modify `pyproject.toml`: `migrate = ["psycopg2-binary>=2.9.0", "mysql-connector-python>=8.0.0"]`; run `uv lock` ONCE; commit pyproject + `uv.lock` together
- **REQ:** REQ-MIG-009
- **Acceptance:**
  - `uv.lock` diff adds only the two drivers + transitive deps; pre-existing uncommitted lock edits isolated (never committed by this plan)
  - Unit suite green with the extra absent
- **Depends on:** none (ordered after T-010 to isolate lock churn) · **Effort:** S · **Risk:** MED (lock drift)

### T-012 — Connector unit tests (fakes)
- **Scope:** Create `tests/unit/test_migration_connectors.py`: registry resolution, lazy-import error via monkeypatched `sys.modules`, type-map invariants per dialect, `quote_identifier` rejection set (`; " ' --`, whitespace), DDL render, FK topological sort
- **REQ:** REQ-MIG-010
- **Acceptance:**
  - All four dialect modules covered without real drivers; asserts exact SQL emitted (placeholders, quoting, autoincrement clauses)
  - Registry test proves no dialect module import at package import time
- **Depends on:** T-004, T-005, T-006, T-007 · **Effort:** M · **Risk:** LOW

### T-013 — Application unit tests (fakes)
- **Scope:** Create `tests/unit/test_migration_application.py`: fake `BaseConnector` + fake source adapter (canned `TableSchema` lists); confirm gates, per-table failure isolation + rollback calls, tracker lifecycle (running→completed/failed), sanitizer strips `connection_string`, linked/passthrough rejection, ODBC unknown-flags path, sentinel-avoidance, empty-DB scenario
- **REQ:** REQ-MIG-010 (pins REQ-MIG-001…006 acceptance)
- **Acceptance:**
  - Every spec scenario under `upload_schema`/`transfer_data`/`get_migration_status` mapped to a test; no real DB touched
- **Depends on:** T-008 · **Effort:** M · **Risk:** LOW

### T-014 — SQLite end-to-end integration test
- **Scope:** Create `tests/integration/test_migration_sqlite_e2e.py`: fixture `.accdb` copy → `extract_schema` → `upload_schema` to temp sqlite file → `transfer_data` → verification; `com_integration`/`odbc_integration` markers per backend availability
- **REQ:** REQ-MIG-010
- **Acceptance:**
  - Full cycle green on temp dir (sqlite target always available); rows verified via `verification_mode="full"` sampling + counts
  - postgres/mysql/sqlserver integration variants use `pytest.importorskip` and skip gracefully when drivers absent
- **Depends on:** T-004, T-008 · **Effort:** M · **Risk:** MED (fixture/backend availability on host)

### T-015 — Plan index + gates
- **Scope:** Update `plans/README.md` row 024 → DONE with evidence links; final gate run
- **REQ:** none (project hygiene)
- **Acceptance:**
  - `uv run pytest -q`, `ruff check .`, `pyright` (0/0/0) all green; README row cites commits/tests as evidence
- **Depends on:** T-010, T-011, T-012, T-013, T-014 · **Effort:** S · **Risk:** LOW

## Dependency Graph

```
T-001 ──► T-002 ──────────────┐
T-001 ──► T-003 ──► T-004 ──► │(T-004 also feeds T-014)
              ├──► T-005 ──► T-012
              ├──► T-006 ──► T-012
              └──► T-007 ──► T-012
T-002 + T-003 ──► T-008 ──► T-009 ──► T-010 ──► T-015
T-008 ──► T-013                    T-011 ──► T-015
T-004 + T-008 ──► T-014 ──► T-015
T-012 ──► T-015   (T-015 also needs T-010..T-014 all green)
```

Critical path: T-001 → T-003 → T-004 → T-008 → T-009 → T-010 → T-015.

## Sequencing Rationale

1. **T-001 first** — Python forbids `models.py` and `models/migration.py` coexisting; the package conversion unblocks all pydantic imports, and plan 026's untouched model tests double as a free regression gate. Ruff glob moves atomically with the `git mv`.
2. **Registry before dialects** — connectors subclass `BaseConnector` and register into `_CONNECTOR_FACTORIES`; the ABC contract is what unit-test fakes later pin.
3. **sqlite first among dialects** — the only driver-free connector (stdlib), making the e2e path provable before any optional driver exists; sqlserver next (pyodbc is already core); postgres/mysql last because the lazy-import pattern is the correctness risk, and mysql adds the AUTO_INCREMENT-PK-inline quirk.
4. **Application after models + registry** — its unit tests use fake connectors, so dialect order doesn't block it; it depends only on the model shapes and the registry seam.
5. **Packaging deliberately late** — a single `uv lock` run in T-011 keeps lock churn isolated to one commit pair and avoids touching `uv.lock` while code is still moving.
6. **Tests trail their subjects; e2e before README** — T-014 proves the whole stack; T-015 records evidence only after gates pass.

## Out-of-Scope Reminders

- **Ruff glob:** do not touch `pyproject.toml` per-file-ignores until T-001 moves `models.py` → `models/__init__.py` (they move in the same commit).
- **Lockfile:** do not touch `uv.lock` until T-011; one `uv lock` run, reviewed diff (only the two drivers + transitives), pyproject + lock committed together, pre-existing uncommitted lock edits left alone.
- **Plan 025:** never edit `analysis` surfaces in `profiles.py`/`server.py`; expected merge conflict at the `"full"` concat resolves by concatenating both lists.
- **No refactors** of `backends/versioning.py` or `backends/sql_generator.py` (pre-existing DDL duplication is documented out of scope, design §9.2).
- **No new Access backend code**; no fix to ODBC's `get_table_schema_plan()` sentinel — migration code simply never calls it.
- **No** credential vault, `server_id`, secrets in artifacts/logs, async workers, or restart persistence.
