"""Passive metadata extraction into versioned documentation evidence."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from ..backends.schema_inspector import QueryParameterInfo, SchemaInspector
from ..errors import AccessMcpError
from ..models import FieldInfo, IndexInfo, QueryInfo, RelationshipInfo, TableInfo
from .documentation import (
    Coverage,
    EvidenceDoc,
    FactProvenance,
    FactStatus,
    FieldEvidence,
    IndexEvidence,
    QueryEvidence,
    RelationshipEvidence,
    RunMetadata,
    SourceEvidence,
    TableEvidence,
)

_QUERY_TYPES = {"select": "select", "action": "action", "ddl": "ddl"}

# Capability keys name an extraction-level property a port may not be able to observe.
# A key present in `InspectionCapabilities.unavailable` maps every dependent fact to
# `unavailable` with that reason instead of a fabricated value.
CAP_FIELD_REQUIRED = "field_required"
CAP_FIELD_ALLOW_ZERO_LENGTH = "field_allow_zero_length"
CAP_PRIMARY_KEY = "primary_key"
CAP_RELATIONSHIPS = "relationships"
CAP_QUERY_SQL = "query_sql"
CAP_QUERY_TYPE = "query_type"


@dataclass(frozen=True)
class InspectionCapabilities:
    """Which extraction properties a port can observe, and what it cannot.

    ``backend_identity`` is recorded as ``source.backend_identity`` in evidence. Every
    key present in ``unavailable`` names a capability the port cannot observe; its value
    is the non-empty reason recorded for each dependent fact, so the seam never
    fabricates a value the engine cannot supply. The default (empty mapping) describes
    the DAO port, which observes every property it reads.
    """

    backend_identity: str = "DAO"
    unavailable: Mapping[str, str] = field(default_factory=dict)

    def reason(self, capability: str) -> str | None:
        """Return the honest unavailable reason for a capability, or None when expressible."""
        return self.unavailable.get(capability, "").strip() or None


class InspectionCapabilityUnavailableError(RuntimeError):
    """Raised by an inspection port method for a capability it cannot expose at all."""


class InspectionPort(Protocol):
    """Read-only inspection surface over an already-open database session."""

    def capabilities(self) -> InspectionCapabilities: ...

    def list_tables(self) -> list[TableInfo]: ...

    def get_table_link_metadata(self, table_name: str) -> dict[str, Any] | None: ...

    def get_indexes(self, table_name: str) -> list[IndexInfo]: ...

    def get_relationships(self) -> list[RelationshipInfo]: ...

    def get_queries(self) -> list[QueryInfo]: ...

    def get_query_parameters(self, query_name: str) -> list[QueryParameterInfo] | None: ...

    def close(self) -> None: ...


@dataclass
class _OpenDatabaseDispatcher:
    """Minimal dispatcher view allowing SchemaInspector to use an open handle."""

    current_db: Any

    def is_connected(self) -> bool:
        return True

    def call(self, operation: Any) -> Any:
        return operation()


class DaoInspectionPort:
    """Thin adapter over SchemaInspector for a caller-owned open DAO handle."""

    def __init__(self, database: Any) -> None:
        self._database = database
        self._inspector = SchemaInspector(_OpenDatabaseDispatcher(database))

    def capabilities(self) -> InspectionCapabilities:
        return InspectionCapabilities()

    def list_tables(self) -> list[TableInfo]:
        return self._inspector.get_tables()

    def get_table_link_metadata(self, table_name: str) -> dict[str, Any] | None:
        return self._inspector.get_table_link_metadata(table_name)

    def get_indexes(self, table_name: str) -> list[IndexInfo]:
        return self._inspector.get_indexes(table_name)

    def get_relationships(self) -> list[RelationshipInfo]:
        return self._inspector.get_relationships()

    def get_queries(self) -> list[QueryInfo]:
        return self._inspector.get_queries()

    def get_query_parameters(self, query_name: str) -> list[QueryParameterInfo] | None:
        return self._inspector.get_query_parameters(query_name)

    def close(self) -> None:
        self._database.Close()


# Access ODBC exposes user tables and linked-table synonyms through SQLTables; saved
# row-returning queries appear as VIEW rows. SQL_PARAM_INPUT marks a declared parameter
# in a SQLProcedureColumns row.
_ODBC_TABLE_TYPES = "TABLE,SYNONYM"
_ODBC_QUERY_TABLE_TYPE = "VIEW"
_ODBC_SYNONYM_TABLE_TYPE = "SYNONYM"
_ODBC_PARAMETER_COLUMN_TYPE = 1

# A listed Access table always has at least one column, so the Access ODBC driver
# returning zero SQLColumns rows (without raising) for a linked table means the column
# read failed -- typically because the linked source is unreachable. This is never an
# observed-empty schema.
_ODBC_NO_COLUMNS_REASON = (
    "ODBC driver returned no columns for a listed table; "
    "the linked source is unreachable or the column read failed"
)

# Probed on the Access ODBC driver: every property below is invisible through ODBC and
# must reach evidence as an explicit `unavailable` reason, never as a fabricated value.
_ODBC_UNAVAILABLE: dict[str, str] = {
    CAP_FIELD_REQUIRED: (
        "Access ODBC SQLColumns reports NULLABLE for every column and does not expose the DAO Required property"
    ),
    CAP_FIELD_ALLOW_ZERO_LENGTH: "Access ODBC SQLColumns has no AllowZeroLength equivalent",
    CAP_PRIMARY_KEY: (
        "Access ODBC SQLPrimaryKeys is unsupported (IM001) and SQLStatistics does not flag the primary index, "
        "so no primary key can be identified"
    ),
    CAP_RELATIONSHIPS: (
        "Access ODBC SQLForeignKeys is unsupported (IM001) and MSysRelationships cannot be read"
    ),
    CAP_QUERY_SQL: "Access ODBC exposes saved queries as VIEW rows only; their SQL text is not available",
    CAP_QUERY_TYPE: (
        "Access ODBC exposes saved queries as VIEW rows without a select, action, or DDL category"
    ),
}


def _safe_close(cursor: Any) -> None:
    """Close a catalog cursor without masking the operation's own result."""
    with contextlib.suppress(Exception):
        cursor.close()


class OdbcInspectionPort:
    """Inspection port over an already-open ODBC connection (T2).

    Reads only ODBC catalog metadata (SQLTables, SQLColumns, SQLStatistics, and
    SQLProcedureColumns). It never executes a saved query, reads a table row, or writes.
    Catalog calls on a linked table DO contact the linked source read-only, so no
    isolation guarantee is provided over ODBC (unlike the DAO path's lock-probe-qualified
    isolation). A linked table whose source is unreachable yields zero SQLColumns/
    SQLStatistics rows without raising, so the service records a listed table that returns
    no columns as ``failed`` rather than as an observed-empty schema. Every property Access
    ODBC cannot expose is declared in ``capabilities()`` with a per-capability reason:

    * ``field_required`` / ``field_allow_zero_length`` -- not represented by SQLColumns.
    * ``primary_key`` -- SQLPrimaryKeys is unsupported and SQLStatistics does not flag the
      primary index, so reported indexes carry ``is_primary=False`` without claiming a key.
    * ``relationships`` -- SQLForeignKeys is unsupported and MSysRelationships is hidden.
    * ``query_sql`` / ``query_type`` -- saved queries are visible as VIEW rows only.

    The adapter owns the connection, so ``close`` is deliberately a no-op.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        # Populated by list_tables() so get_indexes() can reject a table whose column
        # read returned zero rows instead of presenting it as an observed-empty schema.
        self._tables_without_columns: set[str] = set()

    def capabilities(self) -> InspectionCapabilities:
        return InspectionCapabilities(backend_identity="ODBC", unavailable=dict(_ODBC_UNAVAILABLE))

    def list_tables(self) -> list[TableInfo]:
        tables: list[TableInfo] = []
        for name, _table_type in self._catalog_rows(_ODBC_TABLE_TYPES):
            fields = self._columns(name)
            if not fields:
                self._tables_without_columns.add(name)
            tables.append(TableInfo(name=name, fields=fields))
        return tables

    def get_table_link_metadata(self, table_name: str) -> dict[str, Any] | None:
        for name, table_type in self._catalog_rows(None, table=table_name):
            if name == table_name and table_type == _ODBC_SYNONYM_TABLE_TYPE:
                # The link target is local-catalogue metadata ODBC does not expose.
                return {"connect": None, "source_table_name": None}
        return None

    def get_indexes(self, table_name: str) -> list[IndexInfo]:
        if table_name in self._tables_without_columns:
            # The column read for this listed table returned zero rows; surface the same
            # failed-table path the service uses for read failures rather than letting
            # SQLStatistics present an observed-empty index list for a broken read.
            raise AccessMcpError(_ODBC_NO_COLUMNS_REASON)
        grouped: dict[str, list[str]] = {}
        unique: dict[str, bool] = {}
        for row in self._statistics_rows(table_name):
            index_name = getattr(row, "index_name", None)
            column_name = getattr(row, "column_name", None)
            if not index_name or not column_name:
                continue  # SQL_TABLE_STAT carries no index
            grouped.setdefault(index_name, []).append(column_name)
            unique[index_name] = not bool(getattr(row, "non_unique", 0))
        return [
            IndexInfo(
                name=name,
                columns=columns,
                is_unique=unique[name],
                is_primary=False,
                ignore_nulls=False,
            )
            for name, columns in grouped.items()
        ]

    def get_relationships(self) -> list[RelationshipInfo]:
        raise InspectionCapabilityUnavailableError(_ODBC_UNAVAILABLE[CAP_RELATIONSHIPS])

    def get_queries(self) -> list[QueryInfo]:
        return [
            QueryInfo(name=name, sql="", type="")
            for name, _table_type in self._catalog_rows(_ODBC_QUERY_TABLE_TYPE)
        ]

    def get_query_parameters(self, query_name: str) -> list[QueryParameterInfo] | None:
        rows = self._procedure_column_rows(query_name)
        if rows is None:
            return None
        return [
            QueryParameterInfo(
                name=str(row.column_name),
                type=int(row.data_type),
                type_name=str(row.type_name or ""),
            )
            for row in rows
            if getattr(row, "column_type", None) == _ODBC_PARAMETER_COLUMN_TYPE
        ]

    def close(self) -> None:
        """Leave the adapter-owned connection open; the session closes it."""
        return None

    def _catalog_rows(self, table_type: str | None, table: str | None = None) -> list[tuple[str, str]]:
        cursor = self._connection.cursor()
        try:
            cursor.tables(tableType=table_type, table=table)
            return [
                (str(row.table_name), str(getattr(row, "table_type", "")))
                for row in cursor.fetchall()
                if getattr(row, "table_name", None)
            ]
        finally:
            _safe_close(cursor)

    def _columns(self, table_name: str) -> list[FieldInfo]:
        cursor = self._connection.cursor()
        try:
            cursor.columns(table=table_name)
            return [
                FieldInfo(
                    name=str(row.column_name),
                    type=str(row.type_name or ""),
                    size=int(row.column_size or 0),
                    # Neither flag is observable through ODBC; capabilities() maps them
                    # to `unavailable`, so these placeholders never reach evidence.
                    required=False,
                    allow_zero_length=False,
                    default_value=None,
                )
                for row in cursor.fetchall()
                if getattr(row, "column_name", None)
            ]
        finally:
            _safe_close(cursor)

    def _statistics_rows(self, table_name: str) -> list[Any]:
        cursor = self._connection.cursor()
        try:
            cursor.statistics(table=table_name)
            return list(cursor.fetchall())
        finally:
            _safe_close(cursor)

    def _procedure_column_rows(self, query_name: str) -> list[Any] | None:
        cursor = self._connection.cursor()
        try:
            cursor.procedureColumns(procedure=query_name)
            return list(cursor.fetchall())
        except Exception:
            # A driver without SQLProcedureColumns support is unavailable, not empty.
            return None
        finally:
            _safe_close(cursor)


def open_inspection_port(dispatcher: Any) -> DaoInspectionPort:
    """Reopen the dispatcher's database engine-level read-only and wrap it for inspection.

    The documentation-inspection path must never hold a writable handle, so this
    asks the dispatcher to reopen the already-open database with DAO
    ``ReadOnly=True`` before metadata is read. Every other tool keeps its existing
    open semantics; only this path requests the read-only reopen.
    """
    dispatcher.reopen_dao_database_read_only()
    return DaoInspectionPort(dispatcher.current_db)


class DocumentationInspectService:
    """Build evidence while preserving extraction gaps as explicit fact states."""

    def inspect(
        self,
        port: InspectionPort,
        *,
        tables: list[str] | None,
        queries: list[str] | None,
    ) -> EvidenceDoc:
        try:
            return self._inspect(port, tables=tables, queries=queries)
        finally:
            port.close()

    def _inspect(
        self,
        port: InspectionPort,
        *,
        tables: list[str] | None,
        queries: list[str] | None,
    ) -> EvidenceDoc:
        capabilities = port.capabilities()
        raw_tables = port.list_tables()
        eligible_tables = [table for table in raw_tables if not _is_system_name(table.name)]
        raw_queries: list[QueryInfo] | None = None
        query_error: Exception | None = None
        try:
            raw_queries = port.get_queries()
        except Exception as error:
            query_error = error
        eligible_queries = (
            [query for query in raw_queries if not _is_system_name(query.name)]
            if raw_queries is not None
            else []
        )

        selected_tables = _select_objects("table", eligible_tables, tables)
        selected_queries = _select_objects("query", eligible_queries, queries) if raw_queries is not None else []

        coverage = Coverage()
        coverage.tables.excluded.update(
            {table.name: "System or temporary table excluded by schema-inspector rules" for table in raw_tables if _is_system_name(table.name)}
        )
        if raw_queries is not None:
            coverage.queries.excluded.update(
                {query.name: "System or temporary query excluded by schema-inspector rules" for query in raw_queries if _is_system_name(query.name)}
            )
        else:
            coverage.queries.failed["queries"] = _reason(query_error)

        table_evidence: list[TableEvidence] = []
        primary_key_reason = capabilities.reason(CAP_PRIMARY_KEY)
        for table in selected_tables:
            coverage.tables.inspected.append(table.name)
            try:
                indexes = port.get_indexes(table.name)
                link_metadata = port.get_table_link_metadata(table.name)
            except Exception as error:
                reason = _reason(error)
                coverage.tables.failed[table.name] = reason
                table_evidence.append(_table_evidence(table, None, failure=reason, capabilities=capabilities))
                continue
            if primary_key_reason is not None:
                # The port read the table but cannot identify its primary index.
                coverage.tables.unsupported[table.name] = primary_key_reason
            table_evidence.append(_table_evidence(table, indexes, link_metadata, capabilities=capabilities))

        relationship_evidence: list[RelationshipEvidence] = []
        relationship_gap = capabilities.reason(CAP_RELATIONSHIPS)
        if relationship_gap is not None:
            # An invisible capability is `unavailable`; it is never a read failure.
            coverage.relationships.unsupported["relationships"] = relationship_gap
        else:
            try:
                for relationship in port.get_relationships():
                    if _is_system_name(relationship.name):
                        coverage.relationships.excluded[relationship.name] = "System or temporary relationship excluded"
                        continue
                    coverage.relationships.inspected.append(relationship.name)
                    coverage.relationships.unsupported[relationship.name] = (
                        "Relationship attributes are unavailable: DAO attributes are undecoded and ODBC supplies an empty value"
                    )
                    relationship_evidence.append(_relationship_evidence(relationship))
            except Exception as error:
                coverage.relationships.failed["relationships"] = _reason(error)

        query_evidence: list[QueryEvidence] = []
        for query in selected_queries:
            coverage.queries.inspected.append(query.name)
            parameter_data: list[QueryParameterInfo] | None = None
            parameter_failure: str | None = None
            try:
                parameter_data = port.get_query_parameters(query.name)
            except Exception as error:
                parameter_failure = _reason(error)
            evidence = _query_evidence(query, parameter_data, parameter_failure, capabilities)
            if evidence.sql.state == "failed":
                coverage.queries.failed[query.name] = evidence.sql.reason or "Query SQL read failed"
            if parameter_failure is not None:
                coverage.queries.failed[query.name] = parameter_failure
            elif evidence.parameters.state == "unavailable":
                coverage.queries.unsupported[query.name] = evidence.parameters.reason or "Query parameters are unavailable"
            query_evidence.append(evidence)

        document = EvidenceDoc(
            run=RunMetadata(
                run_id=str(uuid4()),
                extractor_version="1.0",
                scope_tables=[item.name for item in selected_tables],
                scope_queries=[item.name for item in selected_queries],
            ),
            source=SourceEvidence(
                path="unspecified",
                format="unknown",
                backend_identity=capabilities.backend_identity,
                acquisition_mode="already_open_session",
                consistency_marker="caller_managed",
            ),
            coverage=coverage,
            tables=table_evidence,
            relationships=relationship_evidence,
            queries=query_evidence,
        )
        document.validate()
        return document


def _is_system_name(name: str) -> bool:
    return name.startswith("MSys") or name.startswith("~")


def _select_objects(kind: str, eligible: list[Any], requested: list[str] | None) -> list[Any]:
    if requested is None:
        return eligible
    by_name = {item.name: item for item in eligible}
    unknown = [name for name in requested if name not in by_name]
    if unknown:
        raise AccessMcpError(f"Unknown requested {kind} name(s): {', '.join(unknown)}")
    return [by_name[name] for name in requested]


def _reason(error: Exception | None) -> str:
    if error is None:
        return "Metadata read failed"
    return str(error).strip() or type(error).__name__


def _provenance(kind: str, name: str, prop: str) -> FactProvenance:
    return FactProvenance(kind, name or "unnamed", prop)


def _table_evidence(
    table: TableInfo,
    indexes: list[IndexInfo] | None,
    link_metadata: dict[str, Any] | None = None,
    failure: str | None = None,
    capabilities: InspectionCapabilities | None = None,
) -> TableEvidence:
    capabilities = capabilities or InspectionCapabilities()
    fields = [_field_evidence(field, capabilities) for field in table.fields]
    if indexes is None:
        primary_key = FactStatus.failed(failure or "Index metadata read failed", _provenance("table", table.name, "primary_key"))
        index_evidence: list[IndexEvidence] = []
    else:
        index_evidence = [
            IndexEvidence(item.name, list(item.columns), item.is_unique, item.is_primary, item.ignore_nulls)
            for item in indexes
        ]
        if primary_reason := capabilities.reason(CAP_PRIMARY_KEY):
            # A read failure stays `failed`; an unobservable primary flag is `unavailable`.
            primary_key = FactStatus.unavailable(primary_reason, _provenance("table", table.name, "primary_key"))
        else:
            primary = next((item for item in indexes if item.is_primary), None)
            primary_key = FactStatus.observed(list(primary.columns) if primary else [], _provenance("table", table.name, "primary_key"))
    kind = "linked" if link_metadata else "local"
    connect = link_metadata.get("connect") if link_metadata else None
    source_table_name = link_metadata.get("source_table_name") if link_metadata else None
    return TableEvidence(
        name=table.name,
        fields=fields,
        primary_key=primary_key,
        indexes=index_evidence,
        kind=kind,
        connect=connect,
        source_table_name=source_table_name,
    )


def _field_evidence(field: FieldInfo, capabilities: InspectionCapabilities) -> FieldEvidence:
    """Map one field, keeping unobservable properties as explicit unavailable facts."""
    if required_reason := capabilities.reason(CAP_FIELD_REQUIRED):
        required = FactStatus.unavailable(required_reason, _provenance("field", field.name, "required"))
    else:
        required = FactStatus.observed(field.required, _provenance("field", field.name, "required"))
    if zero_length_reason := capabilities.reason(CAP_FIELD_ALLOW_ZERO_LENGTH):
        allow_zero_length = FactStatus.unavailable(zero_length_reason, _provenance("field", field.name, "allow_zero_length"))
    else:
        allow_zero_length = FactStatus.observed(field.allow_zero_length, _provenance("field", field.name, "allow_zero_length"))
    return FieldEvidence(
        name=field.name,
        type=FactStatus.observed(field.type, _provenance("field", field.name, "type")),
        size=FactStatus.observed(field.size, _provenance("field", field.name, "size")),
        required=required,
        allow_zero_length=allow_zero_length,
        default_value=_field_default_fact(field),
    )


def _field_default_fact(field: FieldInfo) -> FactStatus[Any]:
    """Map a field default without fabricating `observed null` for ports that expose none."""
    provenance = _provenance("field", field.name, "default_value")
    if field.default_value is None:
        return FactStatus.unavailable(
            "Field default value was not exposed by the engine or runtime", provenance
        )
    return FactStatus.observed(field.default_value, provenance)


def _query_evidence(
    query: QueryInfo,
    parameter_data: list[QueryParameterInfo] | None = None,
    parameter_failure: str | None = None,
    capabilities: InspectionCapabilities | None = None,
) -> QueryEvidence:
    capabilities = capabilities or InspectionCapabilities()
    provenance_sql = _provenance("query", query.name, "sql")
    sql = query.sql if isinstance(query.sql, str) else ""
    sql_fact = (
        FactStatus.observed(sql, provenance_sql)
        if sql.strip()
        else FactStatus.failed("QueryDef SQL is blank or could not be read", provenance_sql)
    )
    if sql_reason := capabilities.reason(CAP_QUERY_SQL):
        sql_fact = FactStatus.unavailable(sql_reason, provenance_sql)
    query_type = str(query.type).strip().casefold()
    mapped_type = _QUERY_TYPES.get(query_type, "unknown")
    type_provenance = _provenance("query", query.name, "type")
    if type_reason := capabilities.reason(CAP_QUERY_TYPE):
        type_fact = FactStatus.unavailable(type_reason, type_provenance)
    else:
        type_fact = FactStatus.observed(mapped_type, type_provenance)
    return QueryEvidence(
        name=query.name,
        sql=sql_fact,
        type=type_fact,
        parameters=_query_parameters_fact(query.name, parameter_data, parameter_failure),
    )


def _query_parameters_fact(
    query_name: str,
    parameter_data: list[QueryParameterInfo] | None,
    parameter_failure: str | None,
) -> FactStatus[Any]:
    """Map producer parameter data at the seam without fabricating emptiness.

    ``None`` means the producer cannot expose parameters, so the fact is
    ``unavailable`` (never an invented empty/null). An empty list is a known empty
    (observed), which is a different thing. A per-query read failure is ``failed``.
    Parameter names and the producer's own type codes/names pass through unchanged
    (DAO field-type codes for the DAO port; ODBC SQL data-type codes for the ODBC port).
    """
    provenance = _provenance("query", query_name, "parameters")
    if parameter_failure is not None:
        return FactStatus.failed(parameter_failure, provenance)
    if parameter_data is None:
        return FactStatus.unavailable(
            "Query parameters were not exposed by the engine or runtime", provenance
        )
    return FactStatus.observed([parameter.to_dict() for parameter in parameter_data], provenance)


def _relationship_evidence(relationship: RelationshipInfo) -> RelationshipEvidence:
    return RelationshipEvidence(
        name=relationship.name,
        parent_table=relationship.foreign_table,
        child_table=relationship.table,
        parent_columns=list(relationship.foreign_columns),
        child_columns=list(relationship.columns),
        attributes=FactStatus.unavailable(
            "Relationship attributes are not decoded (DAO attributes are an undecoded bit string; ODBC supplies an empty value)",
            _provenance("relationship", relationship.name, "attributes"),
        ),
    )
