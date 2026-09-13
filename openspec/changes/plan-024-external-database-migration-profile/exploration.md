## Context

Plan 024 adds an external-database migration feature family: a `migration`
profile, four oracle-compatible MCP tools, target-dialect connectors, and a
synchronous in-process job/status model. The fixed inputs and boundaries are
defined in `plans/024-migration-profile.md:45-73,151-180`.

## Findings

- The prior exploration reported a stubbed Access schema seam and an older
  Plan 023 baseline (`D:\code\python\mcp-ms-access\openspec\changes\plan-024-external-database-migration-profile\exploration.md:7-19,54-58`).
  The current worktree is at Plan 026's committed baseline; DAO extraction is
  now real, including columns, PKs, FKs, indexes, and unknown-metadata flags
  (`src/ms_access_mcp/backends/schema_inspector.py:275-374`).
- ODBC now exposes granular table, column, index, and relationship reads
  (`src/ms_access_mcp/backends/odbc.py:176-214,216-289`), but its legacy
  `get_table_schema_plan()` still returns `([], "unknown")`
  (`src/ms_access_mcp/backends/odbc.py:447-449`). This is not a reason to add
  Access-backend code, but migration extraction must not blindly use that
  sentinel.
- The oracle exposes exactly four wrappers: `extract_schema` (path validation
  and schema result), `upload_schema`, `transfer_data`, and
  `get_migration_status` (`D:\code\python\ms-access-mcp-server\src\ms_access_mcp\mcp\migration.py:38-68,71-106,109-205,208-216`).
  The latter three shapes include the oracle's `server_id` path, which Plan
  024 explicitly removes (`plans/024-migration-profile.md:49-66`).
- The oracle has no target-dialect files under `adapters/`; implementations
  are under `connectors/`. Its registry covers `postgres`, `mysql`,
  `mariadb`, `sqlite`, and `sqlserver` (`D:\code\python\ms-access-mcp-server\src\ms_access_mcp\connectors\registry.py:35-51`).
- The oracle connector protocol combines lifecycle, DDL, batch insert,
  rollback, existence checks, and verification/strategy capabilities
  (`D:\code\python\ms-access-mcp-server\src\ms_access_mcp\connectors\base.py:23-78`).
  The four dialect connectors follow that shape with dialect-specific quoting
  and placeholders (`D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/postgres.py:36-87`,
  `D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/mysql.py:33-87`,
  `D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/sqlite.py:34-88`,
  `D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/sqlserver.py:33-87`).
- Oracle PostgreSQL/MySQL modules import their drivers at module load
  (`D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/postgres.py:1-4`,
  `D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/mysql.py:1-3`),
  while SQLite uses stdlib and SQL Server imports `pyodbc`
  (`D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/sqlite.py:1-3`,
  `D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/sqlserver.py:1-3`). The
  product must change only the first two to lazy imports and preserve the
  exact `migrate` extra from the plan (`pyproject.toml:6-15`).
- Existing backend capabilities describe Access operations only
  (`src/ms_access_mcp/capabilities.py:11-35`; duplicated in
  `src/ms_access_mcp/backends/__init__.py:14-38`). Target-database connector
  capabilities should remain in the new migration backend seam, not be added
  to these Access flags.
- There is no migration implementation or `backends/migration/` package; the
  current backend factory only constructs `odbc`, `dao`, or `com` Access
  adapters (`src/ms_access_mcp/backends/__init__.py:125-211`).
- `PROFILES` currently contains `core`, `automation`, `maintenance`,
  `versioning`, `ddl`, and `full`; `full` excludes migration
  (`src/ms_access_mcp/profiles.py:14-47`). Server help also omits it
  (`src/ms_access_mcp/server.py:121-126`). Plan 025's parallel `analysis`
  change is not present here and must not be touched.
- The repository already has dataclass schema-plan models in `models.py`
  (`src/ms_access_mcp/models.py:190-255`), but no `models/migration.py`.
  The plan's separate Pydantic migration models are therefore intentional and
  must not be silently merged into the existing module.
- The oracle schema extractor prefers `get_table_schema_plan()` and otherwise
  builds only a reduced table/column shape (`D:\code\python\ms-access-mcp-server\src\ms_access_mcp\services\schema_extractor.py:11-59`).
  Because ODBC's method is a sentinel, the application design must select the
  existing granular ODBC getters or explicitly document reduced ODBC metadata;
  this is the only material feasibility issue found.

## Open questions

- **Connector layout:** Should the new package mirror the oracle or the plan?
  **Proposed resolution:** use `src/ms_access_mcp/backends/migration/` with a
  small protocol/base, registry, and one module each for postgres, mysql,
  sqlite, and sqlserver; keep application orchestration above it.
- **Transfer modes:** The oracle protocol advertises linked/passthrough hooks,
  but every inspected connector reports no linked support and raises for linked
  transfer (`D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/sqlite.py:90-132`; equivalent methods exist in the
  other connectors). **Proposed resolution:** specify batch-only v1 and reject
  `linked`/`passthrough` explicitly; retain `auto` only if it resolves to batch.
- **CLI parity:** The current server is MCP-profile based and the plan allows
  CLI parity as a remaining scope choice (`plans/024-migration-profile.md:151-165`).
  **Proposed resolution:** include `migration extract-schema`, `upload-schema`,
  `transfer-data`, and `status`, following existing argparse/JSON/`--yes`
  conventions.

## Risks

- **ODBC schema completeness:** the existing ODBC schema-plan method is an
  empty sentinel (`src/ms_access_mcp/backends/odbc.py:447-449`), and hidden
  `MSysRelationships` degrades to no relationships
  (`src/ms_access_mcp/backends/odbc_schema_reader.py:27-34,49-59`).
- **Optional-driver eagerness:** oracle imports PostgreSQL/MySQL drivers at
  module import (`D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/postgres.py:1-4`,
  `D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/mysql.py:1-3`),
  which would break the base install unless redesigned lazily.
- **Dialect SQL safety:** connector DDL and identifier SQL is assembled with
  dialect-specific f-strings (`D:/code/python/ms-access-mcp-server/src/ms_access_mcp/connectors/postgres.py:49-60` and the
  corresponding target modules); identifier validation/quoting needs focused
  fake-based tests.
- **External write controls:** oracle wrappers perform target writes without a
  product confirmation parameter (`D:/code/python/ms-access-mcp-server/src/ms_access_mcp/mcp/migration.py:71-106,109-205`);
  Plan 024's `confirm=True` gate and sanitized errors must be added at the new
  application/MCP boundaries.

## Not in scope

- Credential vaults, `store_credential`, `server_id`, secret persistence, or
  secret values in artifacts/errors.
- Changes to existing Access backend files; migration reads use the existing
  DAO/ODBC seam. No fix to Plan 026's ODBC sentinel is proposed here.
- Async/background workers, REST/HTTP, persistence across restarts, or
  migration of the MCP server itself.
- Real PostgreSQL, MySQL/MariaDB, or SQL Server qualification; use connector
  fakes and SQLite end-to-end tests only.
- Plan 025's `analysis` profile, unrelated lockfile changes, and any production
  implementation, proposal, or spec work during this explore phase.
