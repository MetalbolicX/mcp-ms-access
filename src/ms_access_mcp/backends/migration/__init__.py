"""Migration connector registry and public API.

Public API:
- get_connector(target_type, connection_string): returns a connected BaseConnector subclass
- render_schema_ddl(target_type, tables): returns list of DDL statements (no connection needed)

Lazy dialect resolution: dialect modules are imported only when first requested.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .connectors import BaseConnector

__all__ = ["get_connector", "render_schema_ddl", "INSTALL_MESSAGE"]

INSTALL_MESSAGE = "install ms-access-mcp[migrate]"

CONNECTOR_FACTORIES: dict[str, type] = {}


def _import_dialect_module(target_type: str) -> type:
    """Lazily import the dialect module and return the connector class."""
    # Postgres
    if target_type == "postgres":
        from .connectors.postgres import PostgresConnector

        return PostgresConnector
    # MySQL
    if target_type == "mysql":
        from .connectors.mysql import MySqlConnector

        return MySqlConnector
    # SQLite (stdlib)
    if target_type == "sqlite":
        from .connectors.sqlite import SqliteConnector

        return SqliteConnector
    # SQL Server
    if target_type == "sqlserver":
        from .connectors.sqlserver import SqlServerConnector

        return SqlServerConnector
    raise ValueError(f"Unknown target type: {target_type!r}. Valid: postgres, mysql, sqlite, sqlserver")


def get_connector(target_type: str, connection_string: str) -> BaseConnector:
    """Get a connected connector instance for the given target type.

    Args:
        target_type: One of 'postgres', 'mysql', 'sqlite', 'sqlserver'.
        connection_string: Dialect-specific connection string.

    Returns:
        A connected BaseConnector subclass instance.

    Raises:
        ValueError: If target_type is unknown.
        ImportError: If the required driver is not installed (lazy import).
    """
    if target_type not in CONNECTOR_FACTORIES:
        # Trigger lazy import to register the factory
        _import_dialect_module(target_type)

    factory = CONNECTOR_FACTORIES.get(target_type)
    if factory is None:
        # This shouldn't happen after _import_dialect_module, but be defensive
        raise ValueError(f"Unknown target type: {target_type!r}. Valid: postgres, mysql, sqlite, sqlserver")

    connector = factory(connection_string)
    connector.connect()
    return connector


def render_schema_ddl(target_type: str, tables: list) -> list[str]:
    """Render DDL for all tables in FK-topological order (no connection needed).

    Args:
        target_type: One of 'postgres', 'mysql', 'sqlite', 'sqlserver'.
        tables: List of TableSchema objects.

    Returns:
        List of DDL statements: CREATE TABLE in FK-topological order,
        then ALTER TABLE for FKs, then CREATE INDEX statements.
    """
    from ..schema_inspector import TableSchema

    # Filter to only TableSchema objects
    table_schemas = [t for t in tables if isinstance(t, TableSchema)]

    # Topological sort by FK dependencies
    sorted_tables = _topological_sort(table_schemas)

    # Import the dialect module for rendering
    if target_type == "postgres":
        from .connectors.postgres import quote_identifier, render_table_ddl
    elif target_type == "mysql":
        from .connectors.mysql import quote_identifier, render_table_ddl
    elif target_type == "sqlite":
        from .connectors.sqlite import quote_identifier, render_table_ddl
    elif target_type == "sqlserver":
        from .connectors.sqlserver import quote_identifier, render_table_ddl
    else:
        raise ValueError(f"Unknown target type: {target_type!r}. Valid: postgres, mysql, sqlite, sqlserver")

    ddl_statements: list[str] = []

    # Phase 1: CREATE TABLE statements
    for table in sorted_tables:
        ddl_statements.append(render_table_ddl(table))

    # Phase 2: ALTER TABLE for foreign keys
    for table in sorted_tables:
        for fk in table.foreign_keys:
            fk_col = fk.columns[0] if fk.columns else ""
            ref_col = fk.referenced_columns[0] if fk.referenced_columns else ""
            ref_table = fk.referenced_table
            fk_name = fk.name
            ddl = (
                f"ALTER TABLE {quote_identifier(table.name)} "
                f"ADD CONSTRAINT {quote_identifier(fk_name)} "
                f"FOREIGN KEY ({quote_identifier(fk_col)}) "
                f"REFERENCES {quote_identifier(ref_table)}({quote_identifier(ref_col)})"
            )
            ddl_statements.append(ddl)

    # Phase 3: CREATE INDEX statements (non-primary)
    for table in sorted_tables:
        # Build set of PK columns for fast lookup — IndexSchema has no is_primary flag
        pk_columns: frozenset[str] = frozenset(table.primary_key)
        for idx in table.indexes:
            # Skip indexes that cover the primary key (PKs rendered in render_table_ddl)
            if idx.columns and set(idx.columns) <= pk_columns:
                continue
            cols = ", ".join(quote_identifier(c) for c in idx.columns)
            unique = "UNIQUE " if idx.is_unique else ""
            ddl = f"CREATE {unique}INDEX {quote_identifier(idx.name)} ON {quote_identifier(table.name)} ({cols})"
            ddl_statements.append(ddl)

    return ddl_statements


def _topological_sort(tables: list) -> list:
    """Sort tables by FK dependencies (Kahn's algorithm).

    Tables with no FK dependencies come first.
    Tables referenced by other tables come before the tables that reference them.
    """
    from ..schema_inspector import TableSchema

    # Build adjacency: table -> tables that depend on it (reference it)
    table_map: dict[str, TableSchema] = {t.name: t for t in tables}
    in_degree: dict[str, int] = {t.name: 0 for t in tables}
    dependents: dict[str, list[str]] = {t.name: [] for t in tables}

    for table in tables:
        for fk in table.foreign_keys:
            ref_table = fk.referenced_table
            if ref_table in table_map:
                dependents[ref_table].append(table.name)
                in_degree[table.name] += 1

    # Start with tables that have no incoming edges (no FK dependencies)
    queue = [name for name, degree in in_degree.items() if degree == 0]
    sorted_tables: list[TableSchema] = []

    while queue:
        # Sort queue for deterministic output
        queue.sort()
        current = queue.pop(0)
        sorted_tables.append(table_map[current])

        for dependent in dependents[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # If we have remaining tables, there's a cycle or missing references
    # In that case, append them in original order
    for table in tables:
        if table not in sorted_tables:
            sorted_tables.append(table)

    return sorted_tables
