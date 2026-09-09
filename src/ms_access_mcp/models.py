"""Models ported from oracle src/ms_access_mcp/models/database.py using stdlib dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field


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
