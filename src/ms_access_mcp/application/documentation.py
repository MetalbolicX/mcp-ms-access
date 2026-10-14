"""Typed, serializable evidence contracts for documentation workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

EvidenceState = Literal["observed", "unavailable", "failed", "excluded"]
QueryType = Literal["select", "action", "ddl", "unknown"]
EvidenceStatus = Literal["complete", "partial"]
_MISSING = object()
T = TypeVar("T")


@dataclass
class FactProvenance:
    object_kind: str
    object_name: str
    property: str

    def validate(self) -> None:
        if not self.object_kind or not self.object_name or not self.property:
            raise ValueError("Fact provenance fields must be non-empty")

    def to_dict(self) -> dict[str, str]:
        return {"object_kind": self.object_kind, "object_name": self.object_name, "property": self.property}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactProvenance:
        return cls(**data)


@dataclass(init=False)
class FactStatus(Generic[T]):
    state: EvidenceState
    provenance: FactProvenance
    _value: Any = field(repr=False)
    reason: str | None
    _value_present: bool = field(repr=False)

    def __init__(self, state: EvidenceState, provenance: FactProvenance, value: Any = _MISSING, reason: str | None = None) -> None:
        self.state = state
        self.provenance = provenance
        self._value = None if value is _MISSING else value
        self.reason = reason
        self._value_present = value is not _MISSING

    @property
    def value(self) -> Any:
        return self._value

    @classmethod
    def observed(cls, value: T, provenance: FactProvenance) -> FactStatus[T]:
        return cls(state="observed", value=value, provenance=provenance)

    @classmethod
    def unavailable(cls, reason: str, provenance: FactProvenance) -> FactStatus[Any]:
        return cls(state="unavailable", reason=reason, provenance=provenance)

    @classmethod
    def failed(cls, reason: str, provenance: FactProvenance) -> FactStatus[Any]:
        return cls(state="failed", reason=reason, provenance=provenance)

    @classmethod
    def excluded(cls, reason: str, provenance: FactProvenance) -> FactStatus[Any]:
        return cls(state="excluded", reason=reason, provenance=provenance)

    def validate(self) -> None:
        if self.state not in ("observed", "unavailable", "failed", "excluded"):
            raise ValueError(f"Unknown fact state: {self.state}")
        self.provenance.validate()
        if self.state == "observed":
            if not self._value_present:
                raise ValueError("Observed facts must include a value")
            if self.reason is not None:
                raise ValueError("Observed facts cannot have a non-observed reason")
        else:
            if self._value_present:
                raise ValueError("Only observed facts may include a value")
            if not self.reason or not self.reason.strip():
                raise ValueError("Non-observed facts require a reason")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        result: dict[str, Any] = {"state": self.state}
        if self.state == "observed":
            result["value"] = self.value
        else:
            result["reason"] = self.reason
        result["provenance"] = self.provenance.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactStatus[Any]:
        state = data["state"]
        kwargs: dict[str, Any] = {
            "state": state,
            "provenance": FactProvenance.from_dict(data["provenance"]),
        }
        if state == "observed":
            if "value" not in data:
                raise ValueError("Observed facts must include a value")
            kwargs["value"] = data["value"]
        else:
            kwargs["reason"] = data.get("reason")
        fact = cls(**kwargs)
        fact.validate()
        return fact


@dataclass
class FieldEvidence:
    name: str
    type: FactStatus[str]
    size: FactStatus[int]
    required: FactStatus[bool]
    allow_zero_length: FactStatus[bool]
    default_value: FactStatus[Any]

    def validate(self) -> None:
        if not self.name:
            raise ValueError("Field name must be non-empty")
        for fact in (self.type, self.size, self.required, self.allow_zero_length, self.default_value):
            fact.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"name": self.name, "type": self.type.to_dict(), "size": self.size.to_dict(), "required": self.required.to_dict(), "allow_zero_length": self.allow_zero_length.to_dict(), "default_value": self.default_value.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FieldEvidence:
        return cls(name=data["name"], type=FactStatus.from_dict(data["type"]), size=FactStatus.from_dict(data["size"]), required=FactStatus.from_dict(data["required"]), allow_zero_length=FactStatus.from_dict(data["allow_zero_length"]), default_value=FactStatus.from_dict(data["default_value"]))


@dataclass
class IndexEvidence:
    name: str
    columns: list[str] = field(default_factory=list)
    is_unique: bool = False
    is_primary: bool = False
    ignore_nulls: bool = False

    def validate(self) -> None:
        if not self.name or any(not column for column in self.columns):
            raise ValueError("Index and column names must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"name": self.name, "columns": list(self.columns), "is_unique": self.is_unique, "is_primary": self.is_primary, "ignore_nulls": self.ignore_nulls}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IndexEvidence:
        return cls(**data)


@dataclass
class TableEvidence:
    name: str
    fields: list[FieldEvidence]
    primary_key: FactStatus[list[str]]
    indexes: list[IndexEvidence] = field(default_factory=list)
    kind: str = "local"
    connect: str | None = None
    source_table_name: str | None = None

    def validate(self) -> None:
        if not self.name:
            raise ValueError("Table name must be non-empty")
        if self.kind not in ("local", "linked"):
            raise ValueError(f"Unknown table kind: {self.kind}")
        if self.kind == "local" and (self.connect is not None or self.source_table_name is not None):
            raise ValueError("Local tables must not carry link metadata")
        self.primary_key.validate()
        for item in self.fields:
            item.validate()
        for item in self.indexes:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"name": self.name, "fields": [item.to_dict() for item in self.fields], "primary_key": self.primary_key.to_dict(), "indexes": [item.to_dict() for item in self.indexes], "kind": self.kind, "connect": self.connect, "source_table_name": self.source_table_name}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TableEvidence:
        return cls(name=data["name"], fields=[FieldEvidence.from_dict(item) for item in data["fields"]], primary_key=FactStatus.from_dict(data["primary_key"]), indexes=[IndexEvidence.from_dict(item) for item in data["indexes"]], kind=data.get("kind", "local"), connect=data.get("connect"), source_table_name=data.get("source_table_name"))


@dataclass
class RelationshipEvidence:
    name: str
    parent_table: str
    child_table: str
    parent_columns: list[str]
    child_columns: list[str]
    attributes: FactStatus[Any] = field(default_factory=lambda: FactStatus.unavailable("Relationship attributes have not been decoded", FactProvenance("relationship", "unknown", "attributes")))

    def __post_init__(self) -> None:
        if self.attributes.provenance.object_name == "unknown":
            self.attributes.provenance.object_name = self.name

    def validate(self) -> None:
        if not self.name or not self.parent_table or not self.child_table:
            raise ValueError("Relationship names and tables must be non-empty")
        if len(self.parent_columns) != len(self.child_columns):
            raise ValueError("Relationship parent and child columns must be aligned pairs")
        if any(not column for column in self.parent_columns + self.child_columns):
            raise ValueError("Relationship column names must be non-empty")
        self.attributes.validate()
        if self.attributes.provenance.object_kind != "relationship" or self.attributes.provenance.object_name != self.name or self.attributes.provenance.property != "attributes":
            raise ValueError("Relationship attributes provenance must identify this relationship")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"name": self.name, "parent_table": self.parent_table, "child_table": self.child_table, "parent_columns": list(self.parent_columns), "child_columns": list(self.child_columns), "attributes": self.attributes.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelationshipEvidence:
        return cls(name=data["name"], parent_table=data["parent_table"], child_table=data["child_table"], parent_columns=list(data["parent_columns"]), child_columns=list(data["child_columns"]), attributes=FactStatus.from_dict(data["attributes"]))


@dataclass
class QueryEvidence:
    name: str
    sql: FactStatus[str]
    type: FactStatus[QueryType]
    parameters: FactStatus[list[dict[str, Any]]] = field(default_factory=lambda: FactStatus.unavailable("Query parameters are not decoded", FactProvenance("query", "unknown", "parameters")))

    def __post_init__(self) -> None:
        if self.parameters.provenance.object_name == "unknown":
            self.parameters.provenance.object_name = self.name

    def validate(self) -> None:
        if not self.name:
            raise ValueError("Query name must be non-empty")
        self.sql.validate()
        self.type.validate()
        self.parameters.validate()
        if self.parameters.state == "observed":
            for parameter in self.parameters.value:
                if not isinstance(parameter, dict) or not {"name", "type", "type_name"} <= set(parameter):
                    raise ValueError("Observed query parameters must carry name, type, and type_name")
                if not isinstance(parameter["name"], str) or not parameter["name"]:
                    raise ValueError("Query parameter names must be non-empty strings")
                if not isinstance(parameter["type"], int) or isinstance(parameter["type"], bool):
                    raise ValueError("Query parameter type must be an integer DAO field-type code")
                if not isinstance(parameter["type_name"], str) or not parameter["type_name"]:
                    raise ValueError("Query parameter type_name must be a non-empty string")
        if self.sql.provenance.object_kind != "query" or self.sql.provenance.object_name != self.name or self.sql.provenance.property != "sql":
            raise ValueError("Query SQL provenance must identify this query")
        if self.type.provenance.object_kind != "query" or self.type.provenance.object_name != self.name or self.type.provenance.property != "type":
            raise ValueError("Query type provenance must identify this query")
        if self.sql.state == "observed" and (not isinstance(self.sql.value, str) or not self.sql.value.strip()):
            raise ValueError("Observed query SQL cannot be blank; record failed or unavailable with a reason")
        if self.type.state == "observed" and self.type.value not in ("select", "action", "ddl", "unknown"):
            raise ValueError("Observed query type must be select, action, ddl, or unknown")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"name": self.name, "sql": self.sql.to_dict(), "type": self.type.to_dict(), "parameters": self.parameters.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueryEvidence:
        return cls(name=data["name"], sql=FactStatus.from_dict(data["sql"]), type=FactStatus.from_dict(data["type"]), parameters=FactStatus.from_dict(data["parameters"]))


@dataclass
class RunMetadata:
    run_id: str
    extractor_version: str
    scope_tables: list[str]
    scope_queries: list[str]

    def validate(self) -> None:
        if not self.run_id or not self.extractor_version:
            raise ValueError("Run id and extractor version must be non-empty")
        if any(not name for name in self.scope_tables + self.scope_queries):
            raise ValueError("Scope object names must be non-empty")
        if len(set(self.scope_tables)) != len(self.scope_tables) or len(set(self.scope_queries)) != len(self.scope_queries):
            raise ValueError("Scope names must be unique under exact-name semantics")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"run_id": self.run_id, "extractor_version": self.extractor_version, "scope": {"tables": list(self.scope_tables), "queries": list(self.scope_queries)}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunMetadata:
        scope = data["scope"]
        return cls(run_id=data["run_id"], extractor_version=data["extractor_version"], scope_tables=list(scope["tables"]), scope_queries=list(scope["queries"]))


@dataclass
class SourceEvidence:
    path: str
    format: str
    backend_identity: str
    acquisition_mode: str
    consistency_marker: str

    def validate(self) -> None:
        if not all((self.path, self.format, self.backend_identity, self.acquisition_mode, self.consistency_marker)):
            raise ValueError("Source metadata fields must be non-empty")

    def to_dict(self) -> dict[str, str]:
        self.validate()
        return {"path": self.path, "format": self.format, "backend_identity": self.backend_identity, "acquisition_mode": self.acquisition_mode, "consistency_marker": self.consistency_marker}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceEvidence:
        return cls(**data)


@dataclass
class CoverageStatus:
    inspected: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)
    unsupported: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if any(not name for name in self.inspected):
            raise ValueError("Inspected names must be non-empty")
        for states in (self.excluded, self.unsupported, self.failed):
            if any(not name or not reason or not reason.strip() for name, reason in states.items()):
                raise ValueError("Coverage gaps require object names and reasons")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"inspected": list(self.inspected), "excluded": dict(self.excluded), "unsupported": dict(self.unsupported), "failed": dict(self.failed)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverageStatus:
        return cls(inspected=list(data["inspected"]), excluded=dict(data["excluded"]), unsupported=dict(data["unsupported"]), failed=dict(data["failed"]))


@dataclass
class Coverage:
    tables: CoverageStatus = field(default_factory=CoverageStatus)
    queries: CoverageStatus = field(default_factory=CoverageStatus)
    relationships: CoverageStatus = field(default_factory=CoverageStatus)

    def validate(self) -> None:
        self.tables.validate()
        self.queries.validate()
        self.relationships.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"tables": self.tables.to_dict(), "queries": self.queries.to_dict(), "relationships": self.relationships.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Coverage:
        return cls(tables=CoverageStatus.from_dict(data["tables"]), queries=CoverageStatus.from_dict(data["queries"]), relationships=CoverageStatus.from_dict(data["relationships"]))


@dataclass
class EvidenceDoc:
    run: RunMetadata
    source: SourceEvidence
    coverage: Coverage
    tables: list[TableEvidence] = field(default_factory=list)
    relationships: list[RelationshipEvidence] = field(default_factory=list)
    queries: list[QueryEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema: Literal["access.documentation.evidence/1"] = "access.documentation.evidence/1"

    @property
    def status(self) -> EvidenceStatus:
        facts: list[FactStatus[Any]] = []
        facts.extend(item.type for table in self.tables for item in table.fields)
        facts.extend(fact for table in self.tables for item in table.fields for fact in (item.size, item.required, item.allow_zero_length, item.default_value))
        facts.extend(table.primary_key for table in self.tables)
        facts.extend(query_fact for query in self.queries for query_fact in (query.sql, query.type, query.parameters))
        facts.extend(relationship.attributes for relationship in self.relationships)
        has_fact_gap = any(fact.state not in ("observed", "excluded") for fact in facts)
        coverage_gaps = any(status.failed or status.unsupported for status in (self.coverage.tables, self.coverage.queries, self.coverage.relationships))
        return "partial" if has_fact_gap or coverage_gaps else "complete"

    def validate(self) -> None:
        if self.schema != "access.documentation.evidence/1":
            raise ValueError(f"Unsupported evidence schema: {self.schema}")
        self.run.validate()
        self.source.validate()
        self.coverage.validate()
        if any(not warning.strip() for warning in self.warnings):
            raise ValueError("Warnings must be non-empty strings")
        for table in self.tables:
            table.validate()
        for relationship in self.relationships:
            relationship.validate()
        for query in self.queries:
            query.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"schema": self.schema, "run": self.run.to_dict(), "source": self.source.to_dict(), "coverage": self.coverage.to_dict(), "warnings": list(self.warnings), "data": {"status": self.status, "tables": [item.to_dict() for item in self.tables], "relationships": [item.to_dict() for item in self.relationships], "queries": [item.to_dict() for item in self.queries]}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceDoc:
        if data.get("schema") != "access.documentation.evidence/1":
            raise ValueError(f"Unsupported evidence schema: {data.get('schema')}")
        payload = data["data"]
        document = cls(schema=data["schema"], run=RunMetadata.from_dict(data["run"]), source=SourceEvidence.from_dict(data["source"]), coverage=Coverage.from_dict(data["coverage"]), warnings=list(data["warnings"]), tables=[TableEvidence.from_dict(item) for item in payload["tables"]], relationships=[RelationshipEvidence.from_dict(item) for item in payload["relationships"]], queries=[QueryEvidence.from_dict(item) for item in payload["queries"]])
        document.validate()
        if payload.get("status") != document.status:
            raise ValueError("Serialized evidence status does not match its facts and coverage")
        return document
