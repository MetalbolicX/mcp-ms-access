"""Data-driven dialect specs for DDL rendering.

One pure renderer parameterized by an immutable per-dialect spec replaces the
four near-identical render_table_ddl copies. Byte-parity with the pre-refactor
output is pinned by tests/unit/test_connector_ddl_golden.py.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ....models import ColumnSchema, TableSchema

AUTOINC_TYPES = frozenset({"Long Integer", "Integer", "Big Integer"})


@dataclass(frozen=True)
class DialectSpec:
    """Everything that differs between dialect DDL renderers."""

    name: str
    type_mapper: Callable[[ColumnSchema], str]
    quote: Callable[[str], str]
    autoincrement_clause: str  # e.g. "PRIMARY KEY AUTOINCREMENT", "IDENTITY(1,1)"
    inline_autoincrement_consumes_pk: bool  # True only for sqlite
    inline_foreign_keys: bool = False


def render_table_ddl(spec: DialectSpec, table: TableSchema) -> str:
    """Render a CREATE TABLE DDL statement for the given dialect spec.

    This function is PURE: no I/O, no env reads, no adapter access.
    """
    columns_ddl: list[str] = []
    pk_consumed = False
    for col in table.columns:
        col_ddl = f"{spec.quote(col.name)} {spec.type_mapper(col)}"
        if not col.allow_null:
            col_ddl += " NOT NULL"
        if col.default_value is not None:
            col_ddl += f" DEFAULT {col.default_value}"
        if (
            col.is_autoincrement
            and col.source_type in AUTOINC_TYPES
            and len(table.primary_key) == 1
            and table.primary_key[0] == col.name
        ):
            col_ddl += f" {spec.autoincrement_clause}"
            pk_consumed = spec.inline_autoincrement_consumes_pk
        columns_ddl.append(col_ddl)

    if table.primary_key and not pk_consumed:
        pk_parts = [spec.quote(pk) for pk in table.primary_key]
        columns_ddl.append(f"PRIMARY KEY ({', '.join(pk_parts)})")

    if spec.inline_foreign_keys:
        for foreign_key in table.foreign_keys:
            fk_columns = ", ".join(spec.quote(column) for column in foreign_key.columns)
            referenced_columns = ", ".join(
                spec.quote(column) for column in foreign_key.referenced_columns
            )
            columns_ddl.append(
                f"FOREIGN KEY ({fk_columns}) REFERENCES "
                f"{spec.quote(foreign_key.referenced_table)}({referenced_columns})"
            )

    col_defs = ",\n    ".join(columns_ddl)
    return f"CREATE TABLE {spec.quote(table.name)} (\n    {col_defs}\n)"
