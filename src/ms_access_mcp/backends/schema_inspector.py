"""SchemaInspector — DAO schema read surface (plan 026).

Ported behaviors from oracle src/ms_access_mcp/adapters/schema_inspector.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import (
    ColumnSchema,
    DatabaseStatistics,
    FieldInfo,
    ForeignKeyInfo,
    ForeignKeySchema,
    GenerateSqlResult,
    IndexInfo,
    IndexSchema,
    ObjectMetadata,
    QueryInfo,
    RelationshipInfo,
    RelationshipResult,
    TableInfo,
    TableSchema,
    UnknownMetadata,
)
from .sql_generator import JetSqlGenerator

# Access DAO field type codes → human-readable name
_ACCESS_TYPE_MAP: dict[int, str] = {
    1: "Boolean",
    2: "Byte",
    3: "Integer",
    4: "Long Integer",
    5: "Currency",
    6: "Single",
    7: "Double",
    8: "Date/Time",
    10: "Text",
    11: "Binary",
    12: "Memo",
    15: "GUID",
    16: "Big Integer",
    17: "Unsigned Byte",
    18: "Unsigned Integer",
    19: "Unsigned Long Integer",
    20: "Decimal",
}


def _access_type_name(type_code: int) -> str:
    """Map an Access DAO field type code to its human-readable name."""
    return _ACCESS_TYPE_MAP.get(type_code, f"Unknown({type_code})")


# DAO TableDef.Attributes bit set on an attached (linked) table.
_DAO_ATTACHED_TABLE = 0x40000000


def _com_string(value: Any) -> str:
    """Normalize a COM string read, stripping ACE's trailing NUL padding."""
    if value is None:
        return ""
    return str(value).rstrip("\x00")


@dataclass(frozen=True)
class QueryParameterInfo:
    """One declared saved-query parameter read from a producer's parameter surface.

    ``type`` and ``type_name`` use the producing port's own taxonomy: the DAO port
    reports the DAO field-type code (``4`` = ``dbLong``) with the reused DAO type name
    (``Long Integer``), and the ODBC port reports the ODBC SQL data type code with its
    ODBC type name (``INTEGER``); no second taxonomy is invented for DAO.
    """

    name: str
    type: int
    type_name: str

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable evidence value for this parameter."""
        return {"name": self.name, "type": self.type, "type_name": self.type_name}


# DAO QueryDef.Type codes → evidence query categories. Codes absent from this map
# (dbQSelect 0, dbQCrosstab 16, dbQSQLPassThrough 112, dbQSetOperation 128, ...)
# keep the historical default category "select".
_DAO_QUERY_CATEGORY: dict[int, str] = {
    32: "action",  # dbQDelete
    48: "action",  # dbQUpdate
    64: "action",  # dbQAppend
    80: "ddl",  # dbQMakeTable (historical category retained)
    96: "ddl",  # dbQDDL
}


class SchemaInspector:
    """DAO schema read operations via ComDispatcher."""

    def __init__(self, dispatcher: Any) -> None:
        """Initialize schema operations with a dispatcher."""
        self._dispatcher = dispatcher

    def get_tables(self) -> list[TableInfo]:
        """Return user tables with fields, or [] when disconnected."""
        if not self._dispatcher.is_connected():
            return []

        def _do() -> list[TableInfo]:
            db = self._dispatcher.current_db
            tables: list[TableInfo] = []
            for tdef in db.TableDefs:
                name = tdef.Name
                # Skip system and hidden tables
                if name.startswith("MSys") or name.startswith("~"):
                    continue
                if tdef.Attributes & 0x80000000:
                    continue
                fields = []
                for fld in tdef.Fields:
                    fields.append(
                        FieldInfo(
                            name=fld.Name,
                            type=_access_type_name(fld.Type),
                            size=fld.Size,
                            required=bool(fld.Required),
                            allow_zero_length=bool(fld.AllowZeroLength),
                            default_value=_com_string(fld.DefaultValue),
                        )
                    )
                tables.append(TableInfo(name=name, fields=fields))
            return tables

        return self._dispatcher.call(_do)

    def get_system_tables(self) -> list[TableInfo]:
        """Return system tables."""
        if not self._dispatcher.is_connected():
            return []

        def _do() -> list[TableInfo]:
            db = self._dispatcher.current_db
            tables: list[TableInfo] = []
            for tdef in db.TableDefs:
                name = tdef.Name
                if name.startswith("MSys"):
                    fields = []
                    for fld in tdef.Fields:
                        fields.append(
                            FieldInfo(
                                name=fld.Name,
                                type=_access_type_name(fld.Type),
                                size=fld.Size,
                                required=bool(fld.Required),
                                allow_zero_length=bool(fld.AllowZeroLength),
                            )
                        )
                    tables.append(TableInfo(name=name, fields=fields))
            return tables

        return self._dispatcher.call(_do)

    def get_queries(self) -> list[QueryInfo]:
        """Return saved queries from QueryDefs."""
        if not self._dispatcher.is_connected():
            return []

        def _do() -> list[QueryInfo]:
            db = self._dispatcher.current_db
            queries: list[QueryInfo] = []
            for qdef in db.QueryDefs:
                name = qdef.Name
                if name.startswith("~") or name.startswith("MSys"):
                    continue
                sql = ""
                try:
                    sql = qdef.SQL or ""
                except Exception:
                    pass
                qtype = "select"
                try:
                    qtype = _DAO_QUERY_CATEGORY.get(int(qdef.Type), "select")
                except Exception:
                    pass
                queries.append(QueryInfo(name=name, sql=sql, type=qtype))
            return queries

        return self._dispatcher.call(_do)

    def get_query_parameters(self, query_name: str) -> list[QueryParameterInfo] | None:
        """Return a query's declared parameters, or None when they are not exposed.

        An empty list is a known empty: the QueryDef exposes a parameters collection
        with zero entries. ``None`` means the producer cannot supply parameter metadata
        at all (disconnected, or a backend without a QueryDefs-equivalent), so the
        evidence seam must record ``unavailable`` rather than fabricate an empty list.
        A failed lookup or a failed per-parameter read propagates so the caller records
        an explicit ``failed`` fact instead of silently reporting emptiness.
        """
        if not self._dispatcher.is_connected():
            return None

        def _do() -> list[QueryParameterInfo] | None:
            db = self._dispatcher.current_db
            qdef = db.QueryDefs[query_name]
            parameters: list[QueryParameterInfo] = []
            for parameter in qdef.Parameters:
                type_code = int(parameter.Type)
                parameters.append(
                    QueryParameterInfo(
                        name=_com_string(parameter.Name),
                        type=type_code,
                        type_name=_access_type_name(type_code),
                    )
                )
            return parameters

        return self._dispatcher.call(_do)

    def get_table_link_metadata(self, table_name: str) -> dict[str, Any] | None:
        """Return local link metadata for an attached table, or None when it is local.

        Reads only the local catalogue (``Attributes``, ``Connect``,
        ``SourceTableName``); the linked source is never resolved, opened, refreshed,
        or contacted.
        """
        if not self._dispatcher.is_connected():
            return None

        def _do() -> dict[str, Any] | None:
            db = self._dispatcher.current_db
            try:
                tdef = db.TableDefs[table_name]
            except Exception:
                return None
            if not (tdef.Attributes & _DAO_ATTACHED_TABLE):
                return None
            return {
                "connect": _com_string(getattr(tdef, "Connect", "")),
                "source_table_name": _com_string(getattr(tdef, "SourceTableName", "")),
            }

        return self._dispatcher.call(_do)

    def get_indexes(self, table_name: str) -> list[IndexInfo]:
        """Return indexes for a table."""
        if not self._dispatcher.is_connected():
            return []

        def _do() -> list[IndexInfo]:
            db = self._dispatcher.current_db
            indexes: list[IndexInfo] = []
            try:
                tdef = db.TableDefs[table_name]
            except Exception:
                return indexes
            for idx in tdef.Indexes:
                # Skip column-less indexes
                col_names = [f.Name for f in idx.Fields]
                if not col_names:
                    continue
                indexes.append(
                    IndexInfo(
                        name=idx.Name,
                        columns=col_names,
                        is_unique=bool(getattr(idx, "Unique", False)),
                        is_primary=bool(getattr(idx, "Primary", False)),
                        ignore_nulls=bool(getattr(idx, "IgnoreNulls", False)),
                    )
                )
            return indexes

        return self._dispatcher.call(_do)

    def get_relationships(self) -> list[RelationshipInfo]:
        """Return relationships (columns=child, foreign_columns=parent)."""
        if not self._dispatcher.is_connected():
            return []

        def _do() -> list[RelationshipInfo]:
            db = self._dispatcher.current_db
            rels: list[RelationshipInfo] = []
            for rel in db.Relations:
                name = rel.Name
                if name.startswith("~") or name.startswith("MSys"):
                    continue
                parent_cols: list[str] = []
                child_cols: list[str] = []
                for j in range(rel.Fields.Count):
                    parent_cols.append(rel.Fields(j).Name)
                    child_cols.append(rel.Fields(j).ForeignName)
                rels.append(
                    RelationshipInfo(
                        name=name,
                        table=rel.ForeignTable,
                        foreign_table=rel.Table,
                        attributes=str(rel.Attributes),
                        columns=child_cols,
                        foreign_columns=parent_cols,
                    )
                )
            return rels

        return self._dispatcher.call(_do)

    def get_object_metadata(self, object_name: str) -> ObjectMetadata | None:
        """Return object metadata (tables, forms, reports, macros)."""
        if not self._dispatcher.is_connected():
            return None

        def _do() -> ObjectMetadata | None:
            db = self._dispatcher.current_db
            # Check tables first with direct collection lookup.
            try:
                db.TableDefs[object_name]
                return ObjectMetadata(name=object_name, type="table", properties={})
            except Exception:
                pass

            # Check forms
            try:
                access_app = self._dispatcher.access_app
                for form in access_app.CurrentProject.AllForms:
                    if form.Name == object_name:
                        return ObjectMetadata(name=object_name, type="form", properties={})
            except Exception:
                pass

            # Check reports
            try:
                access_app = self._dispatcher.access_app
                for report in access_app.CurrentProject.AllReports:
                    if report.Name == object_name:
                        return ObjectMetadata(name=object_name, type="report", properties={})
            except Exception:
                pass

            # Check macros
            try:
                access_app = self._dispatcher.access_app
                for macro in access_app.CurrentProject.AllMacros:
                    if macro.Name == object_name:
                        return ObjectMetadata(name=object_name, type="macro", properties={})
            except Exception:
                pass

            return None

        return self._dispatcher.call(_do)

    def get_table_schema_plan(self) -> tuple[list[TableSchema], UnknownMetadata]:
        """Return full table schemas with detected metadata flags."""
        if not self._dispatcher.is_connected():
            return ([], UnknownMetadata())

        def _do() -> tuple[list[TableSchema], UnknownMetadata]:
            db = self._dispatcher.current_db
            unknown = UnknownMetadata()

            # Collect all relationships indexed by foreign_table
            fk_by_table: dict[str, list[ForeignKeySchema]] = {}
            for rel in db.Relations:
                if rel.Name.startswith("~") or rel.Name.startswith("MSys"):
                    continue
                parent_cols: list[str] = []
                child_cols: list[str] = []
                for j in range(rel.Fields.Count):
                    parent_cols.append(rel.Fields(j).ForeignName)
                    child_cols.append(rel.Fields(j).Name)
                fk = ForeignKeySchema(
                    name=rel.Name,
                    columns=parent_cols,
                    referenced_table=rel.Table,
                    referenced_columns=child_cols,
                )
                foreign_table = rel.ForeignTable
                if foreign_table not in fk_by_table:
                    fk_by_table[foreign_table] = []
                fk_by_table[foreign_table].append(fk)

            schemas: list[TableSchema] = []
            for tdef in db.TableDefs:
                name = tdef.Name
                if name.startswith("MSys") or name.startswith("~"):
                    continue
                if tdef.Attributes & 0x80000000:
                    continue

                columns: list[ColumnSchema] = []
                for fld in tdef.Fields:
                    default_value: str | None = None
                    try:
                        default_value = str(fld.DefaultValue)
                    except Exception:
                        unknown.defaults = True
                    max_length: int | None = fld.Size if fld.Size > 0 else None
                    is_autoincrement = bool(fld.Attributes & 0x10)
                    if is_autoincrement:
                        unknown.autoincrement = True
                    columns.append(
                        ColumnSchema(
                            name=fld.Name,
                            source_type=_access_type_name(fld.Type),
                            max_length=max_length,
                            allow_null=not bool(fld.Required),
                            is_autoincrement=is_autoincrement,
                            default_value=default_value,
                        )
                    )

                # Primary key and indexes
                primary_key: list[str] = []
                indexes: list[IndexSchema] = []
                for idx in tdef.Indexes:
                    col_names = [f.Name for f in idx.Fields]
                    if not col_names:
                        continue
                    if getattr(idx, "Primary", False):
                        primary_key = col_names
                    else:
                        indexes.append(
                            IndexSchema(
                                name=idx.Name,
                                columns=col_names,
                                is_unique=bool(getattr(idx, "Unique", False)),
                            )
                        )

                if not primary_key:
                    unknown.primary_keys = True

                table_fks = fk_by_table.get(name, [])
                if not table_fks:
                    unknown.foreign_keys = True
                if not indexes:
                    unknown.indexes = True

                schemas.append(
                    TableSchema(
                        name=name,
                        columns=columns,
                        primary_key=primary_key,
                        foreign_keys=table_fks,
                        indexes=indexes,
                    )
                )

            return (schemas, unknown)

        return self._dispatcher.call(_do)

    def generate_sql(self, output_path: str) -> GenerateSqlResult:
        """Generate SQL DDL and write to the specified output path.

        Uses direct db access inside _do() to avoid nested-dispatch deadlock
        (per oracle schema_inspector.py:670-672).
        """
        if not self._dispatcher.is_connected():
            return GenerateSqlResult(success=False, error="Not connected")

        def _do() -> GenerateSqlResult:
            db = self._dispatcher.current_db
            # Collect tables with enriched FieldInfo (is_autoincrement via attrs & 0x10)
            # and TableInfo.primary_key from idx.Primary
            tables: list[TableInfo] = []
            for tdef in db.TableDefs:
                name = tdef.Name
                if name.startswith("MSys") or name.startswith("~"):
                    continue
                if tdef.Attributes & 0x80000000:
                    continue

                fields: list[FieldInfo] = []
                primary_key: list[str] = []
                for fld in tdef.Fields:
                    is_autoincrement = bool(fld.Attributes & 0x10)
                    fields.append(
                        FieldInfo(
                            name=fld.Name,
                            type=_access_type_name(fld.Type),
                            size=fld.Size,
                            required=bool(fld.Required),
                            allow_zero_length=bool(fld.AllowZeroLength),
                            is_autoincrement=is_autoincrement,
                        )
                    )

                for idx in tdef.Indexes:
                    col_names = [f.Name for f in idx.Fields]
                    if not col_names:
                        continue
                    if getattr(idx, "Primary", False):
                        primary_key = col_names

                tables.append(
                    TableInfo(
                        name=name,
                        fields=fields,
                        primary_key=primary_key,
                    )
                )

            # Collect relationships → ForeignKeyInfo
            relationships: list[RelationshipInfo] = []
            foreign_keys: list[ForeignKeyInfo] = []
            for rel in db.Relations:
                if rel.Name.startswith("~") or rel.Name.startswith("MSys"):
                    continue
                parent_cols: list[str] = []
                child_cols: list[str] = []
                for j in range(rel.Fields.Count):
                    parent_cols.append(rel.Fields(j).Name)
                    child_cols.append(rel.Fields(j).ForeignName)
                relationships.append(
                    RelationshipInfo(
                        name=rel.Name,
                        table=rel.ForeignTable,
                        foreign_table=rel.Table,
                        attributes=str(rel.Attributes),
                        columns=child_cols,
                        foreign_columns=parent_cols,
                    )
                )
                foreign_keys.append(
                    ForeignKeyInfo(
                        name=rel.Name,
                        columns=child_cols,
                        foreign_table=rel.Table,
                        foreign_columns=parent_cols,
                    )
                )

            # Generate DDL
            generator = JetSqlGenerator(tables, relationships, foreign_keys)
            statements = generator.generate()

            # Write to output path
            try:
                content = "\n".join(statements) + "\n"
                with Path(output_path).open("w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                return GenerateSqlResult(success=False, error=str(e))

            return GenerateSqlResult(
                success=True,
                path=output_path,
                statements=len(statements),
                tables=len(tables),
            )

        return self._dispatcher.call(_do)

    def get_database_statistics(self) -> DatabaseStatistics:
        """Return database statistics."""
        if not self._dispatcher.is_connected():
            return DatabaseStatistics(
                success=True,
                objects={
                    "tables": 0,
                    "queries": 0,
                    "forms": 0,
                    "reports": 0,
                    "macros": 0,
                    "modules": 0,
                },
                file={"name": "", "size_bytes": 0, "modified": ""},
                system={"access_version": "", "com_available": False},
            )

        def _do() -> DatabaseStatistics:
            db = self._dispatcher.current_db
            com_available = True
            try:
                access_app = self._dispatcher.access_app
                all_forms_count = sum(1 for _ in access_app.CurrentProject.AllForms)
                all_reports_count = sum(1 for _ in access_app.CurrentProject.AllReports)
                all_macros_count = sum(1 for _ in access_app.CurrentProject.AllMacros)
            except Exception:
                com_available = False
                all_forms_count = 0
                all_reports_count = 0
                all_macros_count = 0

            try:
                tables_count = db.TableDefs.Count
                queries_count = db.QueryDefs.Count
            except Exception:
                tables_count = 0
                queries_count = 0

            file_info: dict[str, Any] = {"name": "", "size_bytes": 0, "modified": ""}
            try:
                db_path = db.Name
                if db_path and Path(db_path).exists():
                    stat = Path(db_path).stat()
                    file_info = {
                        "name": db_path,
                        "size_bytes": stat.st_size,
                        "modified": str(stat.st_mtime),
                    }
            except Exception:
                pass

            try:
                version = self._dispatcher.access_app.Version
            except Exception:
                version = ""

            return DatabaseStatistics(
                success=True,
                objects={
                    "tables": tables_count,
                    "queries": queries_count,
                    "forms": all_forms_count,
                    "reports": all_reports_count,
                    "macros": all_macros_count,
                    "modules": 0,
                },
                file=file_info,
                system={"access_version": version, "com_available": com_available},
            )

        return self._dispatcher.call(_do)

    def create_relationship(
        self,
        table_name: str,
        relationship_name: str,
        columns: list[str],
        foreign_table: str,
        foreign_columns: list[str],
    ) -> RelationshipResult:
        """Create a relationship between tables."""
        if not self._dispatcher.is_connected():
            return RelationshipResult(success=False, error="Not connected")

        def _do() -> RelationshipResult:
            db = self._dispatcher.current_db
            try:
                rel = db.CreateRelation(relationship_name, table_name, foreign_table)
                for i in range(len(columns)):
                    f = rel.CreateField(columns[i])
                    f.ForeignName = foreign_columns[i]
                    rel.Fields.Append(f)
                db.Relations.Append(rel)
                return RelationshipResult(success=True)
            except Exception as e:
                return RelationshipResult(success=False, error=str(e))

        return self._dispatcher.call(_do)

    def delete_relationship(self, table_name: str, relationship_name: str) -> RelationshipResult:
        """Delete a relationship by name."""
        if not self._dispatcher.is_connected():
            return RelationshipResult(success=False, error="Not connected")

        def _do() -> RelationshipResult:
            db = self._dispatcher.current_db
            try:
                db.Relations.Delete(relationship_name)
                return RelationshipResult(success=True)
            except Exception as e:
                return RelationshipResult(success=False, error=str(e))

        return self._dispatcher.call(_do)
