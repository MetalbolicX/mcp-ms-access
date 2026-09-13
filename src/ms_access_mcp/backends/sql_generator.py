"""Jet SQL DDL Generator for Microsoft Access databases.

SRP-split parallel to ``db_operations.py`` / ``dao_sql.py``:
this module owns "Jet type map + topological-sort DDL generation".
"""

from __future__ import annotations

from ..models import (
    FieldInfo,
    ForeignKeyInfo,
    RelationshipInfo,
    TableInfo,
)

# Jet SQL type mapping — verbatim from oracle sql_generator.py:7-24
JET_TYPE_MAP: dict[str, str] = {
    "Text": "VARCHAR({size})",
    "Memo": "MEMO",
    "Long Integer": "LONG",
    "Integer": "SHORT",
    "Byte": "BYTE",
    "Boolean": "YESNO",
    "Date/Time": "DATETIME",
    "Currency": "CURRENCY",
    "Counter": "AUTOINCREMENT",
    "AutoNumber": "AUTOINCREMENT",
    "Single": "SINGLE",
    "Double": "DOUBLE",
    "Decimal": "DECIMAL",
    "OLE Object": "LONGBINARY",
    "GUID": "CHAR(38)",
    "Binary": "VARBINARY",
}


class JetSqlGenerator:
    """Generates Jet SQL DDL from Access schema information."""

    def __init__(
        self,
        tables: list[TableInfo],
        relationships: list[RelationshipInfo],
        foreign_keys: list[ForeignKeyInfo],
    ) -> None:
        """Initialize the generator with tables, relationships, and foreign keys."""
        self.tables = tables
        self.relationships = relationships
        self.foreign_keys = foreign_keys

    def generate(self) -> list[str]:
        """Generate CREATE TABLE statements ordered by FK dependencies."""
        if not self.tables:
            return []

        ordered_tables = self._topological_sort()
        statements = []
        for table in ordered_tables:
            statements.append(self._generate_create_table(table))
        return statements

    def _map_type(self, field: FieldInfo) -> str:
        """Map Access DAO type to Jet SQL type."""
        dao_type = field.type
        template = JET_TYPE_MAP.get(dao_type, "VARCHAR(255)")
        if "{size}" in template:
            size = field.size if field.size > 0 else 255
            return template.format(size=size)
        return template

    def _generate_column_ddl(self, field: FieldInfo) -> str:
        """Generate DDL for a single column."""
        col_parts = [f"[{field.name}]", self._map_type(field)]

        if field.is_autoincrement:
            col_parts.append("AUTOINCREMENT")
        elif field.default_value is not None:
            default = field.default_value
            if isinstance(default, bool):
                default = -1 if default else 0
            elif isinstance(default, str):
                default = f"'{default}'"
            else:
                default = str(default)
            col_parts.append(f"DEFAULT {default}")

        if field.required:
            col_parts.append("NOT NULL")

        return " ".join(col_parts)

    def _generate_pk_constraint(self, table: TableInfo) -> str | None:
        """Generate PRIMARY KEY constraint if table has PK columns."""
        if not table.primary_key:
            return None
        pk_cols = ", ".join(f"[{col}]" for col in table.primary_key)
        return f"PRIMARY KEY ({pk_cols})"

    def _generate_fk_constraints(self, table: TableInfo) -> list[str]:
        """Generate FOREIGN KEY constraints for a table."""
        constraints = []
        table_field_names = {f.name for f in table.fields}

        for fk in self.foreign_keys:
            fk_col_set = set(fk.columns)
            if not fk_col_set.intersection(table_field_names):
                continue

            ref_table = fk.foreign_table
            if ref_table not in [t.name for t in self.tables]:
                continue

            local_cols = [col for col in fk.columns if col in table_field_names]
            local_cols_str = ", ".join(f"[{col}]" for col in local_cols)
            ref_cols_str = ", ".join(f"[{col}]" for col in fk.foreign_columns)

            constraint = (
                f"CONSTRAINT [{fk.name}] FOREIGN KEY ({local_cols_str}) "
                f"REFERENCES [{ref_table}]({ref_cols_str})"
            )
            constraints.append(constraint)

        return constraints

    def _topological_sort(self) -> list[TableInfo]:
        """Order tables by FK dependencies (parent before child)."""
        table_map = {t.name: t for t in self.tables}
        in_degree = {t.name: 0 for t in self.tables}
        adj_list: dict[str, list[str]] = {t.name: [] for t in self.tables}

        for fk in self.foreign_keys:
            if fk.foreign_table in table_map and fk.columns:
                for col in fk.columns:
                    for table in self.tables:
                        if any(f.name == col for f in table.fields):
                            if fk.foreign_table != table.name:
                                adj_list[fk.foreign_table].append(table.name)
                                break

        for sources in adj_list.values():
            for target in sources:
                in_degree[target] += 1

        queue = [name for name, degree in in_degree.items() if degree == 0]
        sorted_names: list[str] = []

        while queue:
            node = queue.pop(0)
            sorted_names.append(node)
            for neighbor in adj_list[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_names) != len(self.tables):
            return list(table_map.values())

        return [table_map[name] for name in sorted_names if name in table_map]

    def _generate_create_table(self, table: TableInfo) -> str:
        """Generate CREATE TABLE statement for a table."""
        column_defs = [self._generate_column_ddl(field) for field in table.fields]

        pk_constraint = self._generate_pk_constraint(table)
        if pk_constraint:
            column_defs.append(pk_constraint)

        fk_constraints = self._generate_fk_constraints(table)
        column_defs.extend(fk_constraints)

        columns_sql = ",\n  ".join(column_defs)
        return f"CREATE TABLE [{table.name}] (\n  {columns_sql}\n);"
