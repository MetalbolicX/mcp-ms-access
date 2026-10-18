"""Typed, serializable evidence contracts for documentation workflows."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from .documentation_limits import ensure_evidence_within_limit

EvidenceState = Literal["observed", "unavailable", "failed", "excluded"]
QueryType = Literal["select", "action", "ddl", "unknown"]
EvidenceStatus = Literal["complete", "partial"]
_MISSING = object()
T = TypeVar("T")

# Connection-string keys whose values are credentials. The whole ``key=value`` pair is
# removed before a connection string reaches evidence, a report, or an error message.
_CREDENTIAL_KEYS = frozenset(
    {
        "pwd",
        "password",
        "passwd",
        "uid",
        "user",
        "userid",
        "user id",
        "username",
        "user name",
        "jet oledb:database password",
    }
)
_REDACTED_CONNECTION = "[redacted connection string]"
# Credential key assignments inside free text, e.g. an engine error message.
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"(pwd|passwd|password|uid|user\s*id|userid|username|user\s*name|user|"
    r"jet\s+oledb:database\s+password)\s*="
)


def _connection_segments(value: str) -> list[str] | None:
    """Split a connection string on unbraced semicolons.

    Returns ``None`` when brace quoting is unbalanced or a stray closing brace is
    present. Such a string cannot be parsed reliably, so callers fail safe instead of
    guessing where a credential value ends. Inside braces, ``}}`` is the ODBC escape for
    a literal ``}``.
    """
    segments: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(value):
        character = value[index]
        if character == "{":
            depth += 1
            current.append(character)
        elif character == "}":
            if depth == 0:
                return None
            if depth == 1 and index + 1 < len(value) and value[index + 1] == "}":
                current.append("}}")
                index += 2
                continue
            depth -= 1
            current.append(character)
        elif character == ";" and depth == 0:
            segments.append("".join(current))
            current = []
        else:
            current.append(character)
        index += 1
    if depth:
        return None
    segments.append("".join(current))
    return segments


def _is_credential_pair(segment: str) -> bool:
    key, separator, _value = segment.partition("=")
    return bool(separator) and key.strip().casefold() in _CREDENTIAL_KEYS


def sanitize_connection_string(value: str | None) -> str | None:
    """Remove credential pairs from a connection string before it reaches output.

    The whole ``key=value`` pair is dropped for credential keys, so no partial secret
    survives. A string whose brace quoting cannot be parsed fails safe to a redaction
    marker rather than risk leaking a braced or semicolon-containing value. A string
    with no credential pair is returned unchanged, preserving local catalogue metadata
    such as ``;DATABASE=...``.
    """
    if value is None or value == "":
        return value
    segments = _connection_segments(value)
    if segments is None:
        return _REDACTED_CONNECTION
    return ";".join(segment for segment in segments if not _is_credential_pair(segment))


def _credential_value_end(text: str, start: int) -> int:
    """Return the index just past a credential value beginning at ``start``.

    A braced value is consumed through its matching closing brace (``}}`` escapes a
    literal ``}``); an unterminated braced value is redacted to the end of the text. An
    unbraced value runs to the next semicolon or the end, whichever comes first.
    """
    index = start
    while index < len(text) and text[index] in " \t":
        index += 1
    if index < len(text) and text[index] == "{":
        depth = 0
        while index < len(text):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                if depth == 1 and index + 1 < len(text) and text[index + 1] == "}":
                    # ``}}`` escapes a literal ``}`` inside a braced value.
                    index += 2
                    continue
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        return len(text)
    while index < len(text) and text[index] != ";":
        index += 1
    return index


def redact_connection_secrets(text: str) -> str:
    """Redact credential values embedded anywhere in free text, e.g. an error message.

    Uses the same brace-aware value scan as a connection string so braced or
    semicolon-containing values are removed whole instead of leaving a partial secret.
    """
    if not text:
        return text
    result: list[str] = []
    position = 0
    for match in _CREDENTIAL_ASSIGNMENT.finditer(text):
        if match.start() < position:
            continue
        result.append(text[position : match.end()])
        position = _credential_value_end(text, match.end())
        result.append("***")
    result.append(text[position:])
    return "".join(result)


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
class ContainerInventoryEvidence:
    """One engine-level container inventory fact (Forms, Reports, Scripts, Modules).

    ``objects`` carries the per-fact state: ``observed`` with the container's object
    list (an empty list is a known-empty container, never an unknown one),
    ``unavailable`` when the engine cannot expose DAO Containers at all, or ``failed``
    with safe error context when this container's read raised. Each object carries its
    ``name`` plus ``last_modified``/``date_created`` only when the engine exposes those
    document timestamps cheaply. ``method`` records the engine-level read behind the
    fact, so a consumer can distinguish it from an instantiation-level reader's facts.
    """

    category: str
    container: str
    method: str
    objects: FactStatus[list[dict[str, Any]]]

    def validate(self) -> None:
        if not self.category or not self.container or not self.method:
            raise ValueError("Container inventory category, container, and method must be non-empty")
        self.objects.validate()
        provenance = self.objects.provenance
        if (
            provenance.object_kind != self.category
            or provenance.object_name != self.container
            or provenance.property != "objects"
        ):
            raise ValueError("Container inventory provenance must identify this container")
        if self.objects.state == "observed":
            for item in self.objects.value:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
                    raise ValueError("Observed container objects must carry non-empty names")
                if set(item) - {"name", "last_modified", "date_created"}:
                    raise ValueError("Observed container objects carry only name, last_modified, and date_created")
                for key in ("last_modified", "date_created"):
                    value = item.get(key)
                    if value is not None and (not isinstance(value, str) or not value.strip()):
                        raise ValueError("Container object timestamps must be non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "category": self.category,
            "container": self.container,
            "method": self.method,
            "objects": self.objects.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContainerInventoryEvidence:
        return cls(
            category=data["category"],
            container=data["container"],
            method=data["method"],
            objects=FactStatus.from_dict(data["objects"]),
        )


@dataclass
class StartupPropertyEvidence:
    """One engine-level startup database-property fact (name -> string value).

    The catalogue is enumerated generically from the engine and filtered to a defined
    startup allowlist, so an allowlisted property the database does not carry produces
    no fact. ``value`` is ``observed`` with the property value as a string,
    ``unavailable`` when the engine cannot expose database properties at all, or
    ``failed`` when the property enumeration raised. ``method`` records the engine-level
    read behind the fact.
    """

    name: str
    method: str
    value: FactStatus[str]

    def validate(self) -> None:
        if not self.name or not self.method:
            raise ValueError("Startup property name and method must be non-empty")
        self.value.validate()
        provenance = self.value.provenance
        if (
            provenance.object_kind != "startup_property"
            or provenance.object_name != self.name
            or provenance.property != "value"
        ):
            raise ValueError("Startup property provenance must identify this property")
        if self.value.state == "observed" and not isinstance(self.value.value, str):
            raise ValueError("Observed startup properties must be recorded as strings")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"name": self.name, "method": self.method, "value": self.value.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StartupPropertyEvidence:
        return cls(name=data["name"], method=data["method"], value=FactStatus.from_dict(data["value"]))


@dataclass
class AppLogicEvidence:
    """Engine-level app-logic inventory behind the default-off ``include_app_logic`` opt-in.

    v2 adds these facts **additively**: an :class:`EvidenceDoc` carries this object only
    when the caller explicitly opts in, so the default evidence payload keeps the v1
    shape byte-for-byte (agreement 10). Every fact here is engine-level (DAO) and records
    its ``method``; instantiation-level facts introduced later must carry their own
    method so consumers can tell the two tiers apart.
    """

    containers: list[ContainerInventoryEvidence] = field(default_factory=list)
    startup_properties: list[StartupPropertyEvidence] = field(default_factory=list)

    def validate(self) -> None:
        for item in self.containers:
            item.validate()
        for item in self.startup_properties:
            item.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "containers": [item.to_dict() for item in self.containers],
            "startup_properties": [item.to_dict() for item in self.startup_properties],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppLogicEvidence:
        return cls(
            containers=[ContainerInventoryEvidence.from_dict(item) for item in data.get("containers", [])],
            startup_properties=[StartupPropertyEvidence.from_dict(item) for item in data.get("startup_properties", [])],
        )


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
    # Additive v2 category, present only when the caller opted into app-logic inventory;
    # ``None`` keeps the v1 payload byte-identical (agreement 10).
    app_logic: AppLogicEvidence | None = None
    schema: Literal["access.documentation.evidence/1"] = "access.documentation.evidence/1"

    @property
    def status(self) -> EvidenceStatus:
        facts: list[FactStatus[Any]] = []
        facts.extend(item.type for table in self.tables for item in table.fields)
        facts.extend(fact for table in self.tables for item in table.fields for fact in (item.size, item.required, item.allow_zero_length, item.default_value))
        facts.extend(table.primary_key for table in self.tables)
        facts.extend(query_fact for query in self.queries for query_fact in (query.sql, query.type, query.parameters))
        facts.extend(relationship.attributes for relationship in self.relationships)
        if self.app_logic is not None:
            facts.extend(item.objects for item in self.app_logic.containers)
            facts.extend(item.value for item in self.app_logic.startup_properties)
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
        if self.app_logic is not None:
            self.app_logic.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data: dict[str, Any] = {"status": self.status, "tables": [item.to_dict() for item in self.tables], "relationships": [item.to_dict() for item in self.relationships], "queries": [item.to_dict() for item in self.queries]}
        if self.app_logic is not None:
            data["app_logic"] = self.app_logic.to_dict()
        result = {"schema": self.schema, "run": self.run.to_dict(), "source": self.source.to_dict(), "coverage": self.coverage.to_dict(), "warnings": list(self.warnings), "data": data}
        ensure_evidence_within_limit(result)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceDoc:
        ensure_evidence_within_limit(data)
        if data.get("schema") != "access.documentation.evidence/1":
            raise ValueError(f"Unsupported evidence schema: {data.get('schema')}")
        payload = data["data"]
        app_logic_payload = payload.get("app_logic")
        document = cls(schema=data["schema"], run=RunMetadata.from_dict(data["run"]), source=SourceEvidence.from_dict(data["source"]), coverage=Coverage.from_dict(data["coverage"]), warnings=list(data["warnings"]), tables=[TableEvidence.from_dict(item) for item in payload["tables"]], relationships=[RelationshipEvidence.from_dict(item) for item in payload["relationships"]], queries=[QueryEvidence.from_dict(item) for item in payload["queries"]], app_logic=AppLogicEvidence.from_dict(app_logic_payload) if app_logic_payload is not None else None)
        document.validate()
        if payload.get("status") != document.status:
            raise ValueError("Serialized evidence status does not match its facts and coverage")
        return document
