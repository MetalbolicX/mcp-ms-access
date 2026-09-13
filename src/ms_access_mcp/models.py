"""Models ported from oracle src/ms_access_mcp/models/database.py using stdlib dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class FieldInfo:
    name: str
    type: str
    size: int = 0
    required: bool = False
    allow_zero_length: bool = True
    default_value: str | int | float | bool | None = None
    is_autoincrement: bool = False


@dataclass
class TableInfo:
    name: str
    fields: list[FieldInfo] = field(default_factory=list)
    record_count: int = 0
    primary_key: list[str] = field(default_factory=list)


@dataclass
class QueryInfo:
    name: str
    sql: str
    type: str


@dataclass
class RelationshipInfo:
    name: str
    table: str
    foreign_table: str
    attributes: str = ""
    columns: list[str] = field(default_factory=list)
    foreign_columns: list[str] = field(default_factory=list)


@dataclass
class ForeignKeyInfo:
    name: str
    columns: list[str]  # child columns
    foreign_table: str  # parent table
    foreign_columns: list[str]  # parent columns


@dataclass
class ControlInfo:
    name: str
    type: str
    properties: dict = field(default_factory=dict)


@dataclass
class FormInfo:
    name: str
    record_source: str = ""
    controls: list[ControlInfo] = field(default_factory=list)


@dataclass
class ReportInfo:
    name: str
    record_source: str = ""
    controls: list[ControlInfo] = field(default_factory=list)


@dataclass
class MacroInfo:
    name: str
    type: str = "Macro"
    date_created: str = ""
    date_modified: str = ""


@dataclass
class ModuleInfo:
    name: str
    type: str = "Module"
    code: str = ""


@dataclass
class QueryResult:
    """Result of a query execution."""
    success: bool
    rows: list[dict]
    count: int
    columns: list[str]
    error: str | None = None


@dataclass
class LinkedTableInfo:
    name: str
    source_table: str
    connect_string: str
    type: str = "ODBC"
    attributes: int = 0  # DAO dbHiddenObject flag (0x00000001)


@dataclass
class IndexInfo:
    """Describes an index on a table — used by get_indexes."""
    name: str
    columns: list[str] = field(default_factory=list)
    is_unique: bool = False
    is_primary: bool = False
    ignore_nulls: bool = False


@dataclass
class DatabasePropertyInfo:
    """Describes a single database property (DAO `CurrentDb.Properties`).

    `type` is the high-level DAO type name as a string — one of:
    "Text" (10), "Long" (4), "Boolean" (1), "Double" (7), "Date" (8), "Byte" (2).
    `built_in` is True for Access-managed properties (e.g. AppTitle), False for
    user-defined custom properties.
    """
    name: str
    value: str
    type: str  # "Text" | "Long" | "Boolean" | "Double" | "Date" | "Byte"
    built_in: bool = False


@dataclass
class ObjectTextRef:
    kind: Literal["form", "report", "module", "macro", "query"]
    name: str


@dataclass
class ObjectKind:
    kind: Literal["form", "report", "module", "macro", "query"]
    save_as_text_code: int | None

    @classmethod
    def KIND_MAP(cls) -> dict[str, ObjectKind]:
        return {
            "form": cls(kind="form", save_as_text_code=2),
            "report": cls(kind="report", save_as_text_code=4),
            "module": cls(kind="module", save_as_text_code=None),
            "macro": cls(kind="macro", save_as_text_code=8),
            "query": cls(kind="query", save_as_text_code=5),
        }


@dataclass
class ExportAllResult:
    success: bool
    exported: dict[str, list[str]]
    output_dir: str
    file_count: int
    error: str | None = None


@dataclass
class ImportAllResult:
    success: bool
    imported: dict[str, list[str]]
    errors: list[str] | None = None
    error: str | None = None


@dataclass
class CompareResult:
    new: list[ObjectTextRef]
    missing: list[ObjectTextRef]
    changed: list[ObjectTextRef]
    unchanged: list[ObjectTextRef]


@dataclass
class SchemaDdlResult:
    success: bool
    ddl_tables: str | None = None
    ddl_relationships: str | None = None
    tables_exported: int = 0
    relationships_exported: int = 0
    error: str | None = None


# =============================================================================
# Schema-plan models (ported from oracle models/migration.py)
# =============================================================================


@dataclass
class ColumnSchema:
    """Schema for a single column, used in get_table_schema_plan."""

    name: str
    source_type: str
    max_length: int | None = None
    allow_null: bool = True
    is_autoincrement: bool = False
    default_value: str | None = None

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self.source_type = self.source_type.strip()
        if self.default_value is not None:
            stripped = self.default_value.strip()
            self.default_value = None if stripped == "" else stripped


@dataclass
class ForeignKeySchema:
    """Schema for a foreign key, used in get_table_schema_plan."""

    name: str
    columns: list[str]
    referenced_table: str
    referenced_columns: list[str]


@dataclass
class IndexSchema:
    """Schema for an index (non-primary), used in get_table_schema_plan."""

    name: str
    columns: list[str]
    is_unique: bool = False


@dataclass
class UnknownMetadata:
    """Flags for schema aspects that could not be determined."""

    primary_keys: bool = False
    foreign_keys: bool = False
    defaults: bool = False
    indexes: bool = False
    autoincrement: bool = False


@dataclass
class TableSchema:
    """Full schema for a table, returned by get_table_schema_plan."""

    name: str
    columns: list[ColumnSchema]
    primary_key: list[str]
    foreign_keys: list[ForeignKeySchema]
    indexes: list[IndexSchema]

    def __post_init__(self) -> None:
        self.name = self.name.strip()
