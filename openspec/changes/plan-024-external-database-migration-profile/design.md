# Design: External-database migration profile

Change: `plan-024-external-database-migration-profile` · Baseline `a9e5dd4` · Spec: `specs/migration-profile.md` (REQ-MIG-001…010) · Inputs: exploration.md, proposal.md, `plans/024-migration-profile.md`.

## 1. Architecture Overview

```
MCP tools  (mcp_tools/migration.py)                 thin wrappers; confirm params in signatures
    │   (mcp_tools → application ONLY — plan 014 guard)
    ▼
Application use cases  (application/migration.py)   extract_schema | upload_schema | transfer_data | get_migration_status
    │                                                + module-level Job Tracker (in-process dict)
    ├─► Access SOURCE (existing seam, per call):  BackendSelector.get_adapter(db_path)  [auto→dao→odbc]
    │       └─ DAO:  DaoAdapter.get_table_schema_plan()          (plan 026 inspector — full metadata)
    │       └─ ODBC: get_tables/get_columns/get_indexes/
    │                 get_relationships()                        (granular getters — reduced metadata)
    └─► TARGET:  backends/migration/connectors/{postgres,mysql,sqlite,sqlserver}
            └─ lazy driver import → external DB (psycopg2 | mysql-connector | stdlib sqlite3 | pyodbc)
```

The MCP layer validates arguments and renders results; the application layer owns orchestration, confirm gates, error sanitization, and job tracking; connector modules own dialect DDL rendering and execution. Plan 026's inspector feeds the extract path unchanged — **no new Access backend code** (plan decision 7).

## 2. Module Layout

| File | Action | Purpose |
|------|--------|---------|
| `src/ms_access_mcp/mcp_tools/migration.py` | Create | Four thin tool wrappers + `register()` + `_TOOLS` (pattern: `mcp_tools/versioning.py`) |
| `src/ms_access_mcp/application/migration.py` | Create | `MigrationUseCases`: the four use cases + job tracker + sanitizer |
| `src/ms_access_mcp/backends/migration/__init__.py` | Create | Public API: `get_connector(target_type)`, `render_schema_ddl(target_type, tables)`; lazy dialect-module resolution; "install ms-access-mcp[migrate]" message |
| `src/ms_access_mcp/backends/migration/connectors/__init__.py` | Create | `BaseConnector` ABC + `_CONNECTOR_FACTORIES` registry (target_type → lazy factory) |
| `src/ms_access_mcp/backends/migration/connectors/postgres.py` | Create | psycopg2 connector (driver imported inside `connect()` only) |
| `src/ms_access_mcp/backends/migration/connectors/mysql.py` | Create | mysql-connector connector (lazy import, same rule) |
| `src/ms_access_mcp/backends/migration/connectors/sqlite.py` | Create | stdlib `sqlite3` connector |
| `src/ms_access_mcp/backends/migration/connectors/sqlserver.py` | Create | pyodbc connector (pyodbc is a core dep — top-level import allowed) |
| `src/ms_access_mcp/models/migration.py` | Create | Pydantic models (see §9.1) |
| `src/ms_access_mcp/models.py` → `src/ms_access_mcp/models/__init__.py` | Restructure | Package conversion; content unchanged (§9.1) |
| `src/ms_access_mcp/profiles.py` | Modify | Profile wiring (§7) |
| `src/ms_access_mcp/server.py` | Modify | Help text (§7) |
| `pyproject.toml` | Modify | `migrate` extra + ruff per-file-ignores glob update (§9.1) |
| `tests/unit/test_mcp_migration.py` | Create | Registry + 4 verbatim-name parity (modeled on `test_mcp_core.py`) |
| `tests/unit/test_migration_connectors.py` | Create | Fakes: registry, type map, quoting, render, FK sort |
| `tests/unit/test_migration_application.py` | Create | Confirm gates, sanitization, tracker lifecycle, failure isolation, lazy-import message |
| `tests/unit/test_models_migration.py` | Create | Pydantic model contracts |
| `tests/unit/test_architecture_boundaries.py`, `tests/unit/test_profiles.py` | Modify | Cover new modules/profile |
| `tests/integration/test_migration_sqlite_e2e.py` | Create | Fixture copy → extract → sqlite upload → transfer → verify (temp dir) |

## 3. Data Flow per Tool

**`extract_schema(database_path, target_type)`**
1. MCP wrapper forwards to `MigrationUseCases.extract_schema`.
2. Application resolves+validates the path (`os.path.isfile`, resolved absolute; sanitized error, no file opened — REQ-MIG-001).
3. Opens source adapter via `BackendSelector.get_adapter(db_path)`; DAO path calls `get_table_schema_plan()` (full metadata); ODBC path composes `TableSchema` from granular getters (§6).
4. Dialect module renders DDL **without connecting**: `render_schema_ddl(target_type, tables)` → `list[str]` (CREATE TABLE in FK-topological order, then ALTER TABLE FKs, then indexes).
5. Returns `{success, schema: ExtractedSchema, ddl: list[str]}`.

**`upload_schema(target_type, connection_string, schema_json, confirm)`**
1. Application gates on `confirm=True` (no DDL executed otherwise — REQ-MIG-002).
2. Parses `schema_json` → `ExtractedSchema`; sorts tables FK-topologically.
3. `get_connector(target_type)` → `connect()` (lazy driver import; missing driver → sanitized "install ms-access-mcp[migrate]" error, no connection attempt).
4. Per table: `table_exists()` → skip into `tables_failed`, else `create_table(render_table_ddl(t))`; per-table failures isolated (try/except, continue).
5. `disconnect()`; return `{tables_created, tables_failed}`.

**`transfer_data(target_type, connection_string, database_path, schema_json=None, transfer_mode="auto", verification_mode="full", table_overrides=None, confirm)`**
1. Application gates `confirm`; rejects `transfer_mode ∈ {linked, passthrough}` explicitly (v1 batch-only; `auto`→`batch`); applies `TableTransferConfig` overrides (columns/where/order_by).
2. Extracts schema if `schema_json` omitted (same path as `extract_schema` steps 2-3).
3. Registers job (`status=running, phase=transfer`); for each table in FK order: SELECT via source `execute_query` (honoring overrides) → `insert_batch()` in 1000-row batches → `TableResult`; failure → `rollback_table()`, record error, mark job failed, continue isolation.
4. Verification (`full`: sampled content compare; `count-only`: row counts); `tracker.register(final_job)`.
5. Returns `{success, job_id}` only after completion (synchronous — REQ-MIG-006).

**`get_migration_status(job_id)`** — application → `tracker.get(job_id)` → job dict; unknown id → sanitized not-found error (no stack trace).

## 4. Connector Interface

Spec REQ-MIG-007 pins the method set; the dispatch prompt's pseudocode names map onto it:

| Prompt pseudocode | Spec-pinned contract |
|---|---|
| `test_connection()` | `connect()` (raises on failure; `disconnect()` in `finally`) |
| `execute_ddl(statements)` | `create_table(ddl)` per statement (per-table isolation lives above) |
| `copy_table(source_cursor, …)` | application loop: source `execute_query` → `insert_batch()` |
| `close()` | `disconnect()` |

```python
class BaseConnector(ABC):
    def __init__(self, connection_string: str): ...
    def connect(self) -> None: ...                                    # lazy driver import happens here
    def table_exists(self, table: str) -> bool: ...
    def create_table(self, ddl: str) -> None: ...
    def insert_batch(self, table: str, columns: list[str], rows: list[tuple]) -> int: ...
    def rollback_table(self, table: str) -> None: ...
    def disconnect(self) -> None: ...
```

Connection-free, module-level in each dialect module (used by `extract_schema` without a target):

```python
TYPE_MAP: dict[str, str]                    # Access source_type → dialect type
def quote_identifier(name: str) -> str      # rejects ; " ' -- and whitespace; escapes per dialect
def render_table_ddl(table: TableSchema) -> str
```

Placeholders: postgres/mysql `%s`; sqlite/sqlserver `?`. Identifier quoting: pg `"x"`, mysql `` `x` ``, sqlite `"x"`, sqlserver `[x]`.

### Type map (design-owned per REQ-MIG-001 — source vocabulary: `schema_inspector.py:26-44`)

| Access `source_type` | postgres | mysql | sqlite | sqlserver |
|---|---|---|---|---|
| Boolean | BOOLEAN | TINYINT(1) | INTEGER | BIT |
| Byte / Unsigned Byte | SMALLINT | TINYINT UNSIGNED | INTEGER | TINYINT |
| Integer | SMALLINT | SMALLINT | INTEGER | SMALLINT |
| Unsigned Integer | INTEGER | SMALLINT UNSIGNED | INTEGER | INT |
| Long Integer | INTEGER | INT | INTEGER | INT |
| Unsigned Long Integer | BIGINT | INT UNSIGNED | INTEGER | BIGINT |
| Big Integer | BIGINT | BIGINT | INTEGER | BIGINT |
| Single | REAL | FLOAT | REAL | REAL |
| Double | DOUBLE PRECISION | DOUBLE | REAL | FLOAT |
| Currency | NUMERIC(19,4) | DECIMAL(19,4) | NUMERIC | NUMERIC(19,4) |
| Decimal | NUMERIC(28,14) | DECIMAL(28,14) | NUMERIC | NUMERIC(28,14) |
| Date/Time | TIMESTAMP | DATETIME | TEXT (ISO-8601) | DATETIME2 |
| Text (`max_length=n`) | VARCHAR(n), TEXT if n is None | VARCHAR(n), TEXT | TEXT | NVARCHAR(n), NVARCHAR(MAX) |
| Memo | TEXT | LONGTEXT | TEXT | NVARCHAR(MAX) |
| Binary | BYTEA | VARBINARY(n), BLOB | BLOB | VARBINARY(n), VARBINARY(MAX) |
| GUID | UUID | CHAR(36) | TEXT | UNIQUEIDENTIFIER |
| `Unknown(N)` / unmapped | TEXT | TEXT | TEXT | NVARCHAR(MAX) |

Autoincrement single-column PK: pg `GENERATED ALWAYS AS IDENTITY`; mysql `AUTO_INCREMENT` (key-required); sqlite `INTEGER PRIMARY KEY AUTOINCREMENT`; sqlserver `IDENTITY(1,1)`. Lookups are case-insensitive; misses fall to the fallback row and the DDL comment records the source type.

## 5. Job Tracker

Module-level in `application/migration.py`:

```python
_JOBS: dict[str, MigrationJob] = {}
def register(job: MigrationJob) -> None: ...
def get(job_id: str) -> MigrationJob | None: ...
def list_jobs() -> list[MigrationJob]: ...
```

IDs: `uuid4().hex[:8]`, regenerated on collision (paranoia-cheap). **Limitation (documented in tool docstrings + §Open Questions → none blocking):** in-process, single-threaded, no lock (REQ-MIG-006), no persistence — state is lost on restart; callers re-run `transfer_data` rather than resume.

## 6. ODBC Reduced-Metadata Strategy (REQ-MIG-001)

When the ODBC adapter is the source, `extract_schema` builds the **same `list[TableSchema]` shape** from the granular getters (`odbc.py:176-289`) and returns `UnknownMetadata(primary_keys=True, foreign_keys=True, indexes=True, defaults=True, autoincrement=True)` — all five flags.

**Flag semantics:** `True` means "this aspect could not be *verified as complete*; any included values are best-effort." Justification per aspect on ODBC: `defaults`/`autoincrement` are not exposed by SQLColumns at all; PKs, indexes, and relationships arrive from getters that swallow exceptions and return `[]`, so absence is indistinguishable from read failure. DAO flags remain narrower (set only on actual detection gaps, per plan 026's inspector). The ODBC `get_table_schema_plan()` sentinel `([], "unknown")` (`odbc.py:447-449`) is **never called** by migration code; an empty `get_tables()` result is a genuine empty database → `success=True`, empty `tables` (spec scenario).

## 7. Profile Wiring (REQ-MIG-008)

Exact edits to `src/ms_access_mcp/profiles.py`:

```python
PROFILES: frozenset[str] = frozenset({"core", "automation", "maintenance", "versioning", "ddl", "migration", "full"})
...
    migration = ["ms_access_mcp.mcp_tools.migration"]     # after the ddl list
    dispatch: dict[str, list[str]] = { ..., "migration": migration,
        "full": core + automation + maintenance + versioning + ddl + migration }
```

`server.py:117-128` help text: add `migration` to both the opt-in list and `Valid values:`.

**Merge expectation (flagged):** plan 025 adds `analysis` to the same `"full"` concatenation on its own branch. Both branches will conflict at `profiles.py:46` and the `server.py` help string; the orchestrator merges by concatenating both (`core + automation + maintenance + versioning + ddl + analysis + migration`). Neither branch may touch the other's profile.

## 8. Test Strategy (REQ-MIG-010)

| Layer | What | Approach |
|-------|------|----------|
| Unit | Registry resolution, lazy-import error, type map, quoting, DDL render, FK topological sort | Fake connectors; no real drivers; `TestCase` + monkeypatched `sys.modules` to simulate missing psycopg2/mysql-connector |
| Unit | Use cases: confirm gates, per-table failure isolation, rollback calls, tracker lifecycle (pending→running→completed/failed), sanitizer strips `connection_string` | Fake `BaseConnector` + fake source adapter (returns canned `TableSchema` lists) |
| Unit | MCP registry + verbatim names; profile wiring (`migration`, `full`) | `test_mcp_migration.py`, `test_profiles.py` |
| Integration | SQLite end-to-end: fixture `.accdb` copy → extract (DAO or ODBC per availability) → upload to temp sqlite file → transfer → verification | Stdlib sqlite3 target — always available; `com_integration` marker for DAO paths, `odbc_integration` for ODBC paths |
| Integration | postgres/mysql/sqlserver happy paths | Skipped gracefully when driver absent (`pytest.importorskip`) — CI has none (plan :260-269) |

Gates: `uv run pytest tests/unit -q` green **without** the `migrate` extra; full `uv run pytest -q`; `ruff check .`; `pyright` 0/0/0.

## 9. Migration Impact on Existing Code

### 9.1 `models.py` → `models/` package (deliberate restructuring)

Python forbids a module and a package sharing a name, so `src/ms_access_mcp/models/migration.py` (path pinned by plan decision 6 and REQ-MIG-007) **requires** converting the flat module:

- `git mv src/ms_access_mcp/models.py src/ms_access_mcp/models/__init__.py` — content byte-identical.
- Plan 026's five schema-plan dataclasses (`ColumnSchema`, `ForeignKeySchema`, `IndexSchema`, `UnknownMetadata`, `TableSchema`, `models.py:195-255`) **stay in `__init__.py`** — every existing `from ms_access_mcp.models import X` keeps working; `test_models_schema.py`/`test_models_versioning.py` untouched (a free regression check).
- `models/migration.py` holds the **pydantic** models — `ExtractedSchema`, `MigrationJob`, `TableResult`, `TableTransferConfig` — and *reuses* the dataclass `TableSchema` family as nested field types (pydantic v2 serializes stdlib dataclasses natively). One source of truth for the table shape; no duplicate `TableSchema`.
- `models/__init__.py` must **not** import `models.migration` (avoids circular import).
- `pyproject.toml` per-file-ignores: `"src/ms_access_mcp/models.py" = ["D"]` → `"src/ms_access_mcp/models/**" = ["D"]`.

**Why the split departs from plan 026's flat convention:** plan 026 added five DAO-derived stdlib dataclasses and deliberately kept the module flat. Migration adds pydantic (validation, `model_dump`, MCP schema generation) for a different consumer family. The split is: flat dataclasses for DAO-derived shapes (unchanged import surface), package submodule for pydantic migration models. Documented here as the sanctioned deviation; plan 026's tests passing post-`git mv` is the acceptance proof.

### 9.2 DDL-generator duplication (out of scope)

`backends/versioning.py` (`export_schema_ddl`) and `backends/sql_generator.py` (`JET_TYPE_MAP`) already emit Access-side DDL. Migration renders *target-dialect* DDL via `render_table_ddl` and uses the **schema_inspector path**, never versioning's. The three-way duplication is a pre-existing concern — **out of scope for this plan** (exploration "Not in scope"; consolidation would touch existing backends, violating plan decision 7).

## 10. Rollback

1. Revert the plan's commits on `plan/024-migration-profile` (code + registration + lock change land separately; revert in reverse order).
2. Registration removal: drop `"migration"` from `PROFILES`, `dispatch`, the `"full"` concatenation, and the `server.py` help text — all in the reverted commits.
3. `pyproject.toml`: remove the `migrate` extra; regenerate with `uv lock` (keeps only the pre-existing uncommitted lock modification, which was never ours to commit).
4. External writes already performed are not undoable by code rollback — restore targets from backup (proposal Rollback Plan).

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. Target SQL is parameterized (`insert_batch` placeholders) and identifiers pass through `quote_identifier` validation; both are covered by fake-based unit tests.

## Open Questions

- None blocking. Non-blocking note: mysql `AUTO_INCREMENT` requires the column be a key — renderer emits PK inline for autoincrement columns (covered by unit tests).
