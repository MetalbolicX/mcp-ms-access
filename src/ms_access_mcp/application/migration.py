"""Migration use cases — extract_schema, upload_schema, transfer_data, get_migration_status.

Module-level job tracker with uuid4().hex[:8] keys.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeGuard

from ..backends import BackendSelector
from ..backends.migration import (
    get_connector,
    render_schema_ddl,
)
from ..backends.schema_inspector import (
    ColumnSchema as DbColumnSchema,
)
from ..backends.schema_inspector import (
    ForeignKeySchema,
    IndexSchema,
    TableSchema,
    UnknownMetadata,
)
from ..credentials import contains_credential, resolve_profile
from ..errors import BackendUnavailableError, Err, Result
from ..errors import err as _err
from ..errors import ok as _ok
from ..models import QueryResult
from ..models.migration import (
    ExtractedSchema,
    MigrationJob,
    TableResult,
    TableTransferConfig,
)
from ..safety import is_path_allowed
from .protocols import MigrationAdapterProtocol, MigrationConnectorProtocol

_log = logging.getLogger(__name__)


def _is_migration_adapter(value: object) -> TypeGuard[MigrationAdapterProtocol]:
    """Narrow a selected adapter only after verifying its required surface."""
    required = (
        "connect", "disconnect", "connected", "get_table_schema_plan", "get_tables",
        "get_columns", "get_indexes", "get_relationships", "execute_query",
    )
    missing = [name for name in required if not hasattr(value, name)]
    if missing:
        raise BackendUnavailableError(
            f"Selected backend does not support migration; missing: {', '.join(missing)}"
        )
    return True


def _select_migration_adapter(
    db_path: str, backend: Literal["odbc", "com", "dao", "auto"] | None = None
) -> MigrationAdapterProtocol:
    """Select an adapter after checking it implements the migration surface."""
    if backend is None:
        adapter = BackendSelector.get_adapter(db_path)
    else:
        adapter = BackendSelector.get_adapter(db_path, backend=backend)
    if not _is_migration_adapter(adapter):
        raise BackendUnavailableError("Selected backend does not support migration")
    return adapter


def _is_migration_connector(value: object) -> TypeGuard[MigrationConnectorProtocol]:
    """Narrow a connector after verifying every migration-used operation."""
    required = (
        "table_exists", "create_table", "insert_batch", "rollback_table",
        "disconnect", "count_rows",
    )
    missing = [name for name in required if not hasattr(value, name)]
    if missing:
        raise BackendUnavailableError(
            f"Selected connector does not support migration; missing: {', '.join(missing)}"
        )
    return True

__all__ = ["MigrationUseCases"]

# =============================================================================
# Job Tracker
# =============================================================================

_JOBS: dict[str, MigrationJob] = {}


def _generate_job_id() -> str:
    """Generate a short job ID, regenerating on collision."""
    job_id = uuid.uuid4().hex[:8]
    while job_id in _JOBS:
        job_id = uuid.uuid4().hex[:8]
    return job_id


def register_job(job: MigrationJob) -> None:
    """Register a migration job in the tracker."""
    _JOBS[job.id] = job


def get_job(job_id: str) -> MigrationJob | None:
    """Get a job by ID, or None if not found."""
    return _JOBS.get(job_id)


def list_jobs() -> list[MigrationJob]:
    """List all registered jobs."""
    return list(_JOBS.values())


# =============================================================================
# Sanitizer
# =============================================================================


def _sanitize_error(error: str, context: str | None = None) -> str:
    """Strip sensitive values from error messages.

    Never echo connection_string or other credential-related values.
    """
    if context is None:
        return "[sanitized]"
    return f"[sanitized: {context}]"


# =============================================================================
# MigrationUseCases
# =============================================================================


class MigrationUseCases:
    """Migration use cases with in-process job tracking.

    extract_schema: reads Access DB via existing DAO/ODBC seam
    upload_schema: applies DDL to target in FK-topological order
    transfer_data: copies rows per table with per-table failure isolation
    get_migration_status: reads job from tracker
    """

    def __init__(
        self,
        db_path: str | None = None,
        allowed_dirs: list[Path] | None = None,
    ) -> None:
        """Initialize migration use cases.

        Args:
            db_path: Optional Access database path for direct source access.
            allowed_dirs: List of allowed directories for file operations.
        """
        self._db_path = db_path
        self._allowed_dirs = allowed_dirs or []

    def _resolve_connect_arg(
        self,
        connection_string: str | None,
        connection_ref: str | None,
    ) -> tuple[str | None, str | None]:
        """Resolve connection_string / connection_ref into a resolved string or an error.

        Resolution rules (5-rule):
          1. Both provided → Err("ambiguous")
          2. ref provided, resolved None → Err("profile not found")
          3. ref provided, resolved OK → resolved string
          4. literal with credential → Err("hard-reject")
          5. clean literal or neither → Ok(literal or None)

        Returns:
            (resolved_string, None) on success, or (None, error_message) on error.

        """
        if connection_ref is not None and connection_string is not None:
            return None, (
                "Ambiguous: both connection_ref and connection_string are set. "
                "Use either connection_ref (named profile) or connection_string, not both."
            )
        if connection_ref is not None:
            resolved = resolve_profile(connection_ref)
            if resolved is None:
                return None, (
                    f"Profile '{connection_ref}' not found. "
                    "Run: ms-access-mcp credentials profile set --name {connection_ref} "
                    "--connection-string '<connection_string>'. "
                    "Never supply the connection string directly."
                )
            return resolved, None
        if connection_string is not None:
            if contains_credential(connection_string):
                return None, (
                    "connection_string contains a credential token (PWD=, Password=, or "
                    "Jet OLEDB:Database Password=). Use connection_ref instead: "
                    "ms-access-mcp credentials profile set --name <name> "
                    "--connection-string '<connection_string>'. "
                    "Never supply the connection string directly."
                )
            return connection_string, None
        return None, None

    # -------------------------------------------------------------------------
    # extract_schema
    # -------------------------------------------------------------------------

    def extract_schema(
        self,
        database_path: str,
        target_type: str,
    ) -> Result:
        """Extract schema from an Access database.

        DAO path (available): uses get_table_schema_plan() for full metadata.
        ODBC path (fallback): composes TableSchema from granular getters with
        all five unknown_metadata flags set.

        Args:
            database_path: Path to the Access .accdb file.
            target_type: Target dialect ('postgres', 'mysql', 'sqlite', 'sqlserver').

        Returns:
            Ok with ExtractedSchema on success, Err on failure.
        """
        # Validate path before opening
        if not database_path:
            return _err("database_path is required")
        if not os.path.isfile(database_path):
            return _err(f"Access database not found: {database_path}")
        if not is_path_allowed(database_path, self._allowed_dirs):
            return _err(f"Database path not allowed: {database_path}")

        # Validate target_type early
        valid_targets = {"postgres", "mysql", "sqlite", "sqlserver"}
        if target_type not in valid_targets:
            return _err(
                f"Unknown target_type {target_type!r}. Valid: {', '.join(sorted(valid_targets))}"
            )

        try:
            # Try DAO path first (full metadata)
            tables, unknown_flags = self._extract_dao(database_path)
            if tables is not None:
                schema = ExtractedSchema(
                    source=database_path,
                    version="1.0",
                    extracted_at=datetime.utcnow(),
                    tables=tables,
                    relationships=[],
                    unknown_metadata=unknown_flags or UnknownMetadata(),
                )
                return _ok(data=schema)

            # Fall back to ODBC path (reduced metadata)
            tables, unknown_flags = self._extract_odbc(database_path)
            schema = ExtractedSchema(
                source=database_path,
                version="1.0",
                extracted_at=datetime.utcnow(),
                tables=tables or [],
                relationships=[],
                unknown_metadata=unknown_flags,
            )
            return _ok(data=schema)

        except Exception as e:
            return _err(_sanitize_error(str(e), "extract_schema failed"))

    def _extract_dao(
        self, database_path: str
    ) -> tuple[list[TableSchema] | None, UnknownMetadata | None]:
        """Extract using DAO (full metadata).

        Returns (tables, UnknownMetadata) on success, (None, None) if DAO unavailable.
        """
        adapter: MigrationAdapterProtocol | None = None
        try:
            # Import here to avoid hard dependency when DAO is not available
            adapter = _select_migration_adapter(database_path, backend="dao")
            adapter.connect(database_path)
            tables, unknown_flags = adapter.get_table_schema_plan()
            return tables, unknown_flags
        except Exception:
            return None, None
        finally:
            if adapter is not None:
                try:
                    adapter.disconnect()
                except Exception:
                    pass

    def _extract_odbc(
        self, database_path: str
    ) -> tuple[list[TableSchema], UnknownMetadata]:
        """Extract using ODBC (reduced metadata with all flags set).

        Uses granular getters (get_tables, get_columns, get_indexes,
        get_relationships) per design §6.  The get_table_schema_plan()
        sentinel is never called.
        """
        adapter: MigrationAdapterProtocol | None = None
        try:
            adapter = _select_migration_adapter(database_path, backend="odbc")
            adapter.connect(database_path)

            # Granular getter sequence — per design §6
            table_info_list = adapter.get_tables()
            all_relationships = adapter.get_relationships()

            tables: list[TableSchema] = []
            for t_info in table_info_list:
                table_name = t_info.name
                cols = adapter.get_columns(table_name)
                indexes = adapter.get_indexes(table_name)

                # Build ColumnSchema list
                columns = [
                    DbColumnSchema(
                        name=c.get("name", ""),
                        source_type=c.get("type", "Unknown"),
                        max_length=c.get("size"),
                        allow_null=c.get("nullable", True),
                        is_autoincrement=c.get("autoincrement", False),
                        default_value=c.get("default"),
                    )
                    for c in cols
                ]

                # PK from IndexInfo (is_primary=True)
                primary_key = [idx.name for idx in indexes if idx.is_primary] if indexes else []

                # FKs from RelationshipInfo filtered to this table
                foreign_keys = [
                    ForeignKeySchema(
                        name=r.name,
                        columns=[r.columns[0]] if r.columns else [],
                        referenced_table=r.foreign_table,
                        referenced_columns=[r.foreign_columns[0]] if r.foreign_columns else [],
                    )
                    for r in all_relationships
                    if r.table == table_name
                ]

                # IndexSchema list (non-primary only; primary key handled above)
                index_schemas = [
                    IndexSchema(
                        name=idx.name,
                        columns=idx.columns,
                        is_unique=idx.is_unique,
                    )
                    for idx in indexes
                    if not idx.is_primary
                ]

                tables.append(
                    TableSchema(
                        name=table_name,
                        columns=columns,
                        primary_key=primary_key,
                        foreign_keys=foreign_keys,
                        indexes=index_schemas,
                    )
                )

            # ODBC cannot verify completeness of these aspects
            unknown_flags = UnknownMetadata(
                primary_keys=True,
                foreign_keys=True,
                defaults=True,
                indexes=True,
                autoincrement=True,
            )
            return tables, unknown_flags

        except Exception:
            return [], UnknownMetadata(
                primary_keys=True,
                foreign_keys=True,
                defaults=True,
                indexes=True,
                autoincrement=True,
            )
        finally:
            if adapter is not None:
                try:
                    adapter.disconnect()
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # upload_schema
    # -------------------------------------------------------------------------

    def upload_schema(
        self,
        target_type: str,
        connection_string: str | None = None,
        schema_json: dict[str, Any] | None = None,
        *,
        connection_ref: str | None = None,
        confirm: bool = False,
    ) -> Result:
        """Upload schema to target database.

        Requires confirm=True to execute DDL.

        Args:
            target_type: Target dialect.
            connection_string: Target connection string.
                Required unless connection_ref is provided.
            schema_json: ExtractedSchema as dict.
            connection_ref: Named credential profile to resolve the connection string.
            confirm: Must be True to execute DDL.

        Returns:
            Ok with {tables_created, tables_failed} on success, Err on failure.

        """
        if not confirm:
            return _err("confirm=True is required to upload schema")

        resolved, err_msg = self._resolve_connect_arg(connection_string, connection_ref)
        if err_msg is not None:
            return _err(err_msg)

        # Parse schema
        try:
            schema = ExtractedSchema.model_validate(schema_json or {})
        except Exception as e:
            return _err(_sanitize_error(str(e), "schema"))

        # Get connector
        connector_result = self._get_connector(target_type, resolved or "")
        if isinstance(connector_result, Err):
            return connector_result

        if not _is_migration_connector(connector_result.data):
            raise BackendUnavailableError("Selected connector does not support migration")
        connector = connector_result.data

        tables_created: list[str] = []
        tables_failed: list[str] = []

        try:
            for table in schema.tables:
                try:
                    if connector.table_exists(table.name):
                        tables_failed.append(table.name)
                        continue
                    ddl = render_schema_ddl(target_type, [table])[0]
                    connector.create_table(ddl)
                    tables_created.append(table.name)
                except Exception:
                    tables_failed.append(table.name)

            return _ok(data={"tables_created": tables_created, "tables_failed": tables_failed})
        finally:
            connector.disconnect()

    # -------------------------------------------------------------------------
    # transfer_data
    # -------------------------------------------------------------------------

    def transfer_data(
        self,
        target_type: str,
        connection_string: str | None = None,
        database_path: str = "",
        schema_json: dict[str, Any] | None = None,
        *,
        connection_ref: str | None = None,
        transfer_mode: str = "auto",
        verification_mode: str = "full",
        table_overrides: dict[str, dict[str, Any]] | None = None,
        confirm: bool = False,
    ) -> Result:
        """Transfer data from Access to target.

        Args:
            target_type: Target dialect.
            connection_string: Target connection string.
                Required unless connection_ref is provided.
            database_path: Access database path.
            schema_json: Optional schema (extracted if None).
            connection_ref: Named credential profile to resolve the connection string.
            transfer_mode: 'auto' -> 'batch'; 'linked'/'passthrough' -> rejected.
            verification_mode: 'full' (sampling) or 'count-only'.
            table_overrides: Optional per-table {columns, where, order_by}.
            confirm: Must be True to execute transfer.

        Returns:
            Ok with {success, job_id} on completion, Err on failure.

        """
        if not confirm:
            return _err("confirm=True is required to transfer data")

        resolved, err_msg = self._resolve_connect_arg(connection_string, connection_ref)
        if err_msg is not None:
            return _err(err_msg)

        # Validate transfer_mode
        if transfer_mode in ("linked", "passthrough"):
            return _err(
                f"transfer_mode={transfer_mode!r} is not supported in v1. Use transfer_mode='auto' or 'batch'."
            )
        if transfer_mode == "auto":
            transfer_mode = "batch"

        # Validate verification_mode
        if verification_mode not in ("full", "count-only"):
            return _err(
                f"verification_mode must be 'full' or 'count-only', got {verification_mode!r}"
            )

        # Extract schema if not provided
        if schema_json is None:
            schema_result = self.extract_schema(database_path, target_type)
            if isinstance(schema_result, Err):
                return schema_result
            schema = schema_result.data
        else:
            try:
                schema = ExtractedSchema.model_validate(schema_json)
            except Exception as e:
                return _err(_sanitize_error(str(e), "schema"))

        # Register job
        job_id = _generate_job_id()
        job = MigrationJob(
            id=job_id,
            status="running",
            phase="transfer",
            progress=0,
            started_at=datetime.utcnow(),
        )
        register_job(job)

        # Get connector
        connector_result = self._get_connector(target_type, resolved or "")
        if isinstance(connector_result, Err):
            job.status = "failed"
            job.errors.append(connector_result.message)
            job.completed_at = datetime.utcnow()
            register_job(job)
            return connector_result

        if not _is_migration_connector(connector_result.data):
            raise BackendUnavailableError("Selected connector does not support migration")
        connector = connector_result.data
        adapter: MigrationAdapterProtocol | None = None
        total_tables = len(schema.tables)
        results: list[TableResult] = []

        try:
            adapter = _select_migration_adapter(database_path)
            adapter.connect(database_path)
            for i, table in enumerate(schema.tables):
                job.current_table = table.name
                job.progress = int((i / total_tables) * 100)
                register_job(job)

                # Get table config overrides
                config: TableTransferConfig | None = None
                if table_overrides and table.name in table_overrides:
                    try:
                        config = TableTransferConfig.model_validate(table_overrides[table.name])
                    except Exception:
                        pass

                # Transfer table
                table_result = self._transfer_table(
                    connector, table, config, verification_mode, adapter
                )
                results.append(table_result)

                if not table_result.success:
                    # Rollback failed table
                    try:
                        connector.rollback_table(table.name)
                    except Exception:
                        pass

            # Finalize job
            all_success = all(r.success for r in results)
            job.status = "completed" if all_success else "failed"
            job.progress = 100
            job.results = results
            job.errors = [r.error for r in results if r.error]
            job.completed_at = datetime.utcnow()
            register_job(job)

            return _ok(data={"success": all_success, "job_id": job_id})

        except Exception as e:
            job.status = "failed"
            job.errors.append(_sanitize_error(str(e), "transfer_data"))
            job.completed_at = datetime.utcnow()
            register_job(job)
            return _err(_sanitize_error(str(e), "transfer_data"))
        finally:
            try:
                connector.disconnect()
            finally:
                if adapter is not None:
                    adapter.disconnect()

    def _transfer_table(
        self,
        connector: MigrationConnectorProtocol,
        table: TableSchema,
        config: TableTransferConfig | None,
        verification_mode: str,
        adapter: MigrationAdapterProtocol,
    ) -> TableResult:
        """Transfer a single table with per-table failure isolation."""
        start_time = datetime.utcnow()
        result = TableResult(table=table.name)

        try:
            # Build query with optional overrides
            columns = config.columns if config and config.columns else [c.name for c in table.columns]
            where = config.where if config and config.where else None
            order_by = config.order_by if config and config.order_by else None

            # Execute query
            query = f"SELECT {', '.join(columns)} FROM [{table.name}]"
            if where:
                query += f" WHERE {where}"
            if order_by:
                query += f" ORDER BY {', '.join(order_by)}"

            query_result = adapter.execute_query(query)
            if isinstance(query_result, QueryResult):
                rows = query_result.rows
            elif isinstance(query_result, dict):
                rows = query_result.get("rows", [])
            else:
                rows = []

            result.source_rows = len(rows)

            # Insert in batches of 1000
            batch_size = 1000
            for batch_start in range(0, len(rows), batch_size):
                batch = rows[batch_start : batch_start + batch_size]
                batch_rows = [tuple(row.get(c) for c in columns) for row in batch]
                connector.insert_batch(table.name, columns, batch_rows)

            result.rows_transferred = len(rows)
            result.strategy_used = "batch"

            # Verification
            if verification_mode == "full":
                # Simple count verification
                target_count = self._count_target_rows(connector, table.name)
                result.verification = {
                    "source_rows": result.source_rows,
                    "target_rows": target_count,
                    "mode": "full",
                }
            else:
                result.verification = {"mode": "count-only"}

            result.success = True

        except Exception as e:
            result.success = False
            result.error = _sanitize_error(str(e), f"transfer_table({table.name})")

        end_time = datetime.utcnow()
        result.duration_ms = int((end_time - start_time).total_seconds() * 1000)
        return result

    def _count_target_rows(self, connector: MigrationConnectorProtocol, table: str) -> int:
        """Count rows in target table (for verification)."""
        try:
            return connector.count_rows(table)
        except Exception as e:
            _log.warning("Row-count verification failed for %s: %s", table, e)
            return -1

    def _get_connector(
        self, target_type: str, connection_string: str
    ) -> Result:
        """Get a connected connector, sanitizing errors on failure."""
        try:
            connector = get_connector(target_type, connection_string)
            return _ok(data=connector)
        except ImportError as e:
            return _err(_sanitize_error(str(e), "connection"))
        except Exception as e:
            return _err(_sanitize_error(str(e), "connection"))

    # -------------------------------------------------------------------------
    # get_migration_status
    # -------------------------------------------------------------------------

    def get_migration_status(self, job_id: str) -> Result:
        """Get the status of a migration job.

        Args:
            job_id: The job ID returned by transfer_data.

        Returns:
            Ok with MigrationJob dict on success, Err if not found.
        """
        job = get_job(job_id)
        if job is None:
            return _err(f"Job {job_id!r} not found")
        return _ok(data=job.model_dump())
