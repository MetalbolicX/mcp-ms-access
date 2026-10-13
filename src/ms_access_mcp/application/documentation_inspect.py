"""Passive metadata extraction into versioned documentation evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from ..backends.schema_inspector import SchemaInspector
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


class InspectionPort(Protocol):
    """Read-only inspection surface over an already-open database session."""

    def list_tables(self) -> list[TableInfo]: ...

    def get_table_link_metadata(self, table_name: str) -> dict[str, Any] | None: ...

    def get_indexes(self, table_name: str) -> list[IndexInfo]: ...

    def get_relationships(self) -> list[RelationshipInfo]: ...

    def get_queries(self) -> list[QueryInfo]: ...

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

    def close(self) -> None:
        self._database.Close()


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
        for table in selected_tables:
            coverage.tables.inspected.append(table.name)
            try:
                indexes = port.get_indexes(table.name)
                link_metadata = port.get_table_link_metadata(table.name)
                table_evidence.append(_table_evidence(table, indexes, link_metadata))
            except Exception as error:
                reason = _reason(error)
                coverage.tables.failed[table.name] = reason
                table_evidence.append(_table_evidence(table, None, failure=reason))

        relationship_evidence: list[RelationshipEvidence] = []
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
            evidence = _query_evidence(query)
            if evidence.sql.state == "failed":
                coverage.queries.failed[query.name] = evidence.sql.reason or "Query SQL read failed"
            coverage.queries.unsupported[query.name] = "Query parameters are unavailable because extraction is not implemented"
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
                backend_identity="DAO",
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
) -> TableEvidence:
    fields = [
        FieldEvidence(
            name=field.name,
            type=FactStatus.observed(field.type, _provenance("field", field.name, "type")),
            size=FactStatus.observed(field.size, _provenance("field", field.name, "size")),
            required=FactStatus.observed(field.required, _provenance("field", field.name, "required")),
            allow_zero_length=FactStatus.observed(field.allow_zero_length, _provenance("field", field.name, "allow_zero_length")),
            default_value=_field_default_fact(field),
        )
        for field in table.fields
    ]
    if indexes is None:
        primary_key = FactStatus.failed(failure or "Index metadata read failed", _provenance("table", table.name, "primary_key"))
        index_evidence: list[IndexEvidence] = []
    else:
        primary = next((item for item in indexes if item.is_primary), None)
        primary_key = FactStatus.observed(list(primary.columns) if primary else [], _provenance("table", table.name, "primary_key"))
        index_evidence = [
            IndexEvidence(item.name, list(item.columns), item.is_unique, item.is_primary, item.ignore_nulls)
            for item in indexes
        ]
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


def _field_default_fact(field: FieldInfo) -> FactStatus[Any]:
    """Map a field default without fabricating `observed null` for ports that expose none."""
    provenance = _provenance("field", field.name, "default_value")
    if field.default_value is None:
        return FactStatus.unavailable(
            "Field default value was not exposed by the engine or runtime", provenance
        )
    return FactStatus.observed(field.default_value, provenance)


def _query_evidence(query: QueryInfo) -> QueryEvidence:
    provenance_sql = _provenance("query", query.name, "sql")
    sql = query.sql if isinstance(query.sql, str) else ""
    sql_fact = (
        FactStatus.observed(sql, provenance_sql)
        if sql.strip()
        else FactStatus.failed("QueryDef SQL is blank or could not be read", provenance_sql)
    )
    query_type = str(query.type).strip().casefold()
    mapped_type = _QUERY_TYPES.get(query_type, "unknown")
    return QueryEvidence(
        name=query.name,
        sql=sql_fact,
        type=FactStatus.observed(mapped_type, _provenance("query", query.name, "type")),
        parameters=FactStatus.unavailable("Query parameters are not extracted", _provenance("query", query.name, "parameters")),
    )


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
