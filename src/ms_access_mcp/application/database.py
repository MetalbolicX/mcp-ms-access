"""Database use cases — constructor-injected backends, immutable result envelopes.

All file paths validated via safety.is_path_allowed.
All destructive ops gated via safety.require_acknowledgment.
"""

from __future__ import annotations

import dataclasses
import inspect
from functools import cache
from pathlib import Path
from typing import Any, Literal, cast

from ..backends import DATA_READ_CAPS, BackendSelector
from ..errors import Err, Ok, Result, err, ok  # noqa: F401 — re-exported for backward compatibility
from ..models import QueryResult
from ..safety import WritePolicy, is_path_allowed
from .db_admin import create_access_database as _create_db
from .db_admin import get_database_properties as _get_db_props
from .db_admin import get_database_statistics as _get_db_stats
from .db_admin import set_database_property as _set_db_prop
from .db_analysis import generate_sql as _gen_sql
from .db_analysis import get_er_diagram as _get_er
from .db_export import export_data as _export_data

# =============================================================================
# Result Envelopes — re-exported from the shared kernel (errors module)
# =============================================================================

_ok = ok  # backward-compat aliases; remove in a later cleanup
_err = err


@cache
def _method_signature(method: Any) -> inspect.Signature:
    """Cached inspect.signature lookup for adapter query methods."""
    return inspect.signature(method)


# =============================================================================
# Database Use Cases
# =============================================================================


class DatabaseUseCases:
    """Constructor-injected database operations.

    No globals, no container. Each instance holds a reference to the
    active backend adapter and write policy.
    """

    def __init__(
        self,
        backend: str = "auto",
        readonly: bool = True,
        allowed_dirs: list[Path] | None = None,
    ) -> None:
        """Initialize with backend selection and policy.

        Args:
            backend: Backend name ("auto", "dao", "odbc", "access")
            readonly: If True, all write operations are rejected
            allowed_dirs: List of allowed directories for file operations
        """
        self._backend_name = backend
        self._policy = WritePolicy(readonly=readonly)
        self._allowed_dirs = allowed_dirs or []
        self._adapter: Any = None
        self._db_path: str | None = None
        self._connections: dict[str, Any] = {}
        self._active_connection: str = "default"

    @property
    def readonly(self) -> bool:
        """Whether write operations are rejected (public read of the write policy)."""
        return self._policy.readonly

    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------

    def open_database(self, db_path: str) -> Result:
        """Open a database connection.

        Args:
            db_path: Path to the .accdb or .mdb file

        Returns:
            Ok with connection info, or Err with error message
        """
        if not is_path_allowed(db_path, self._allowed_dirs):
            return _err(f"Path not allowed: {db_path}")

        # Resolve server-side credentials (never from MCP tool params)
        from ..credentials import resolve_password

        password = resolve_password(db_path)

        try:
            self._adapter = BackendSelector.get_adapter(
                db_path=db_path,
                backend=cast(Literal["odbc", "com", "dao", "auto"] | None, self._backend_name),
                capabilities=DATA_READ_CAPS,
            )
            if password:
                self._adapter.connect(db_path, password=password)
            else:
                self._adapter.connect(db_path)
            self._db_path = db_path
            self._connections[self._active_connection] = self._adapter
            return _ok(data={"path": db_path, "connection": self._active_connection})
        except Exception as e:
            import traceback

            traceback.print_exc()
            message = f"connect failed: {e!r}"
            if password:
                message = message.replace(password, "***")
            return _err(message)

    def close_database(self, connection_name: str = "default") -> Result:
        """Close a database connection.

        Args:
            connection_name: Name of the connection to close

        Returns:
            Ok on success, or Err with error message
        """
        adapter = self._connections.pop(connection_name, None)
        if adapter is None:
            return _err(f"Connection not found: {connection_name}")

        try:
            adapter.disconnect()
            if self._active_connection == connection_name:
                self._active_connection = "default"
            return _ok()
        except Exception as e:
            return _err(str(e))

    def list_connections(self) -> Result:
        """List all open connections.

        Returns:
            Ok with list of connection names, or Err
        """
        return _ok(data=list(self._connections.keys()))

    def set_active(self, connection_name: str) -> Result:
        """Set the active connection.

        Args:
            connection_name: Name of the connection to make active

        Returns:
            Ok on success, or Err if connection not found
        """
        if connection_name not in self._connections:
            return _err(f"Connection not found: {connection_name}")
        self._active_connection = connection_name
        return _ok()

    def get_active(self) -> Result:
        """Get the name of the active connection.

        Returns:
            Ok with active connection name, or Err if none
        """
        return _ok(data=self._active_connection)

    def is_connected(self, connection_name: str = "default") -> Result:
        """Check if a connection is active.

        Args:
            connection_name: Name of the connection to check

        Returns:
            Ok with boolean, or Err if connection not found
        """
        adapter = self._connections.get(connection_name)
        return _ok(data=adapter is not None)

    def _get_adapter(self, connection_name: str = "default") -> Any:
        """Get adapter for a connection, or the active one."""
        name = connection_name if connection_name in self._connections else self._active_connection
        return self._connections.get(name)

    # -------------------------------------------------------------------------
    # Schema Introspection
    # -------------------------------------------------------------------------

    def get_tables(self, connection_name: str = "default") -> Result:
        """Get list of user tables.

        Args:
            connection_name: Connection to use

        Returns:
            Ok with list of table names, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            tables = adapter.get_tables()
            return _ok(data=[dataclasses.asdict(t) for t in tables], rowcount=len(tables))
        except Exception as e:
            return _err(str(e))

    def get_system_tables(self, connection_name: str = "default") -> Result:
        """Get list of system tables.

        Args:
            connection_name: Connection to use

        Returns:
            Ok with list of system table names, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            tables = adapter.get_system_tables()
            return _ok(data=tables, rowcount=len(tables))
        except Exception as e:
            return _err(str(e))

    def get_table_schema(self, table_name: str, connection_name: str = "default") -> Result:
        """Get schema for a table (composed from get_tables per oracle mcp/schema.py)."""
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            tables = adapter.get_tables()
            for table in tables:
                if table.name == table_name:
                    return _ok(data=dataclasses.asdict(table))
            return _err(f"Table '{table_name}' not found")
        except Exception as e:
            return _err(str(e))

    def get_indexes(self, table_name: str, connection_name: str = "default") -> Result:
        """Get indexes for a table.

        Args:
            table_name: Name of the table
            connection_name: Connection to use

        Returns:
            Ok with list of indexes, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            indexes = adapter.get_indexes(table_name)
            return _ok(data=[dataclasses.asdict(i) for i in indexes], rowcount=len(indexes))
        except Exception as e:
            return _err(str(e))

    def get_relationships(self, connection_name: str = "default") -> Result:
        """Get all relationships.

        Args:
            connection_name: Connection to use

        Returns:
            Ok with list of relationships, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            rels = adapter.get_relationships()
            return _ok(data=[dataclasses.asdict(r) for r in rels], rowcount=len(rels))
        except Exception as e:
            return _err(str(e))

    def get_object_metadata(self, object_name: str, _object_type: str = "table", connection_name: str = "default") -> Result:
        """Get metadata for an object.

        Args:
            object_name: Name of the object
            object_type: Type of object ("table", "query", etc.)
            connection_name: Connection to use

        Returns:
            Ok with object metadata, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            meta = adapter.get_object_metadata(object_name)
            return _ok(data=dataclasses.asdict(meta) if meta is not None else {})
        except Exception as e:
            return _err(str(e))

    def get_queries(self, connection_name: str = "default") -> Result:
        """Get list of saved queries.

        Args:
            connection_name: Connection to use

        Returns:
            Ok with list of query names, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            from dataclasses import asdict
            queries = adapter.get_queries()
            return _ok(data=[asdict(q) for q in queries], rowcount=len(queries))
        except Exception as e:
            return _err(str(e))

    def get_database_properties(self, connection_name: str = "default") -> Result:
        """Get database properties.

        Args:
            connection_name: Connection to use

        Returns:
            Ok with database properties, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        return _get_db_props(adapter)

    def get_database_statistics(self, connection_name: str = "default") -> Result:
        """Get database statistics.

        Args:
            connection_name: Connection to use

        Returns:
            Ok with statistics, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        return _get_db_stats(adapter)

    def get_linked_tables(self, connection_name: str = "default") -> Result:
        """Get list of linked tables.

        Args:
            connection_name: Connection to use

        Returns:
            Ok with list of linked table info, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            linked = adapter.get_linked_tables()
            return _ok(data=linked, rowcount=len(linked))
        except Exception as e:
            return _err(str(e))

    # -------------------------------------------------------------------------
    # Query Operations
    # -------------------------------------------------------------------------

    def create_query(self, query_name: str, sql: str, connection_name: str = "default", confirm: bool = False) -> Result:
        """Create a saved query.

        Args:
            query_name: Name for the query
            sql: SQL statement
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("create_query", confirm):
            return _err(ack)
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            adapter.create_query(query_name, sql)
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def set_query_sql(self, query_name: str, sql: str, connection_name: str = "default", confirm: bool = False) -> Result:
        """Update SQL for a saved query.

        Args:
            query_name: Name of the query
            sql: New SQL statement
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("set_query_sql", confirm):
            return _err(ack)
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            adapter.set_query_sql(query_name, sql)
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def delete_query(self, query_name: str, connection_name: str = "default", confirm: bool = False) -> Result:
        """Delete a saved query.

        Args:
            query_name: Name of the query to delete
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("delete_query", confirm):
            return _err(ack)
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            adapter.delete_query(query_name)
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def query_data(
        self,
        sql: str,
        params: list[Any] | None = None,
        limit: int | None = None,
        connection_name: str = "default",
    ) -> Result:
        """Execute a SELECT query.

        Args:
            sql: SQL SELECT statement
            params: Optional query parameters
            limit: Optional row limit
            connection_name: Connection to use

        Returns:
            Ok with rows and rowcount, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            method = getattr(adapter, "query_data", None) or getattr(adapter, "execute_query", None)
            if method is None:
                return _err(f"{type(adapter).__name__} does not support query_data")
            sig = _method_signature(getattr(method, "__func__", method))
            kwargs: dict[str, Any] = {}
            if "params" in sig.parameters:
                kwargs["params"] = params
            if "limit" in sig.parameters:
                kwargs["limit"] = limit
            result = method(sql, **kwargs)
            if isinstance(result, QueryResult):
                result = dataclasses.asdict(result)
            if isinstance(result, dict):
                if "rows" in result:
                    return _ok(data=result.get("rows", []), rowcount=result.get("count", 0))
                if "data" in result:
                    return _ok(data=result.get("data", []), rowcount=result.get("rowcount", 0))
                return _ok(data=result, rowcount=len(result))
            return _ok(data=result, rowcount=len(result) if result else 0)
        except Exception as e:
            return _err(str(e))

    # -------------------------------------------------------------------------
    # Data Modification
    # -------------------------------------------------------------------------

    def insert_data(
        self,
        table_name: str,
        data: dict[str, Any],
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Insert a row into a table.

        Args:
            table_name: Target table
            data: Column-value pairs
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok with rowcount, or Err
        """
        if ack := self._policy.require_acknowledgment("insert_data", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            result = adapter.insert_data(table_name, data)
            rowcount = dataclasses.asdict(result)["affected"]
            return _ok(rowcount=rowcount)
        except Exception as e:
            return _err(str(e))

    def update_data(
        self,
        table_name: str,
        data: dict[str, Any],
        where: str | None = None,
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Update rows in a table.

        Args:
            table_name: Target table
            data: Column-value pairs to update
            where: WHERE clause
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok with rowcount, or Err
        """
        if ack := self._policy.require_acknowledgment("update_data", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            result = adapter.update_data(table_name, data, where=where)
            rowcount = dataclasses.asdict(result)["affected"]
            return _ok(rowcount=rowcount)
        except Exception as e:
            return _err(str(e))

    def delete_data(
        self,
        table_name: str,
        where: str | None = None,
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Delete rows from a table.

        Args:
            table_name: Target table
            where: WHERE clause
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok with rowcount, or Err
        """
        if ack := self._policy.require_acknowledgment("delete_data", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            result = adapter.delete_data(table_name, where=where)
            rowcount = dataclasses.asdict(result)["affected"]
            return _ok(rowcount=rowcount)
        except Exception as e:
            return _err(str(e))

    # -------------------------------------------------------------------------
    # DDL Operations
    # -------------------------------------------------------------------------

    def create_table(
        self,
        table_name: str,
        columns: dict[str, str],
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Create a new table.

        Args:
            table_name: Name for the new table
            columns: Dict of column_name -> sql_type (e.g. {"id": "INT", "name": "TEXT"})
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("create_table", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            adapter.create_table(table_name, columns)
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def alter_table(
        self,
        table_name: str,
        action: str,
        column_name: str | None = None,
        sql_type: str | None = None,
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Alter a table (add/drop column).

        Args:
            table_name: Table to alter
            action: "add" or "drop"
            column_name: Column to add or drop
            sql_type: SQL type for add action
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("alter_table", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            adapter.alter_table(table_name, action, column_name=column_name, sql_type=sql_type)
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def delete_table(self, table_name: str, connection_name: str = "default", confirm: bool = False) -> Result:
        """Delete a table.

        Args:
            table_name: Table to delete
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("delete_table", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            adapter.delete_table(table_name)
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def create_index(
        self,
        table_name: str,
        index_name: str,
        columns: list[str],
        unique: bool = False,
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Create an index on a table.

        Args:
            table_name: Table to index
            index_name: Name for the index
            columns: List of column names
            unique: Whether index is unique
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("create_index", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            adapter.create_index(table_name, index_name, columns, unique=unique)
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def drop_index(self, table_name: str, index_name: str, connection_name: str = "default", confirm: bool = False) -> Result:
        """Drop an index from a table.

        Args:
            table_name: Table containing the index
            index_name: Name of the index to drop
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("drop_index", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            adapter.drop_index(table_name, index_name)
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def create_relationship(
        self,
        relationship_name: str,
        source_table: str,
        source_column: str,
        dest_table: str,
        dest_column: str,
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Create a relationship between tables.

        Args:
            relationship_name: Name for the relationship
            source_table: Parent table
            source_column: Parent column
            dest_table: Child table
            dest_column: Child column
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("create_relationship", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            result = adapter.create_relationship(relationship_name, source_table, source_column, dest_table, dest_column)
            result_dict = dataclasses.asdict(result)
            if not result_dict.get("success", False):
                return _err(result_dict.get("error", "create_relationship failed"))
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def delete_relationship(self, relationship_name: str, connection_name: str = "default", confirm: bool = False) -> Result:
        """Delete a relationship.

        Args:
            relationship_name: Name of the relationship to delete
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("delete_relationship", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            result = adapter.delete_relationship(relationship_name)
            result_dict = dataclasses.asdict(result)
            if not result_dict.get("success", False):
                return _err(result_dict.get("error", "delete_relationship failed"))
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    # -------------------------------------------------------------------------
    # Export helpers
    # -------------------------------------------------------------------------

    #: Maximum length of an Excel sheet name
    _MAX_SHEET_NAME_LENGTH = 31

    # -------------------------------------------------------------------------
    # Export
    # -------------------------------------------------------------------------

    def export_data(
        self,
        table_or_query: str,
        output_path: str,
        format: Literal["csv", "json", "xlsx"] = "csv",
        connection_name: str = "default",
        sheet_name: str = "Sheet1",
    ) -> Result:
        """Export table or query data to a file.

        Args:
            table_or_query: Table name or query name
            output_path: Output file path
            format: Export format ("csv", "json", or "xlsx")
            connection_name: Connection to use
            sheet_name: Sheet name for xlsx export (default "Sheet1");
                must be 1-31 chars; ] not allowed; trailing $ is stripped.

        Returns:
            Ok with rowcount, or Err
        """
        if not is_path_allowed(output_path, self._allowed_dirs):
            return _err(f"Output path not allowed: {output_path}")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        return _export_data(adapter, self._policy, self._allowed_dirs, table_or_query, output_path, format, sheet_name)

    # -------------------------------------------------------------------------
    # Database Properties
    # -------------------------------------------------------------------------

    def set_database_property(self, name: str, value: Any, connection_name: str = "default", confirm: bool = False) -> Result:
        """Set a database property.

        Args:
            name: Property name
            value: Property value
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        return _set_db_prop(adapter, self._policy, name, value, confirm)

    # -------------------------------------------------------------------------
    # Linked Table Operations (thin wrappers — DAO backend at backends/dao.py)
    # -------------------------------------------------------------------------

    def create_linked_table(
        self,
        name: str,
        source_table: str,
        connect_string: str,
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Create a linked (attached) table.

        Args:
            name: Name for the new linked table in this database
            source_table: Name of the source table on the remote database
            connect_string: ODBC or DAO connection string to the remote database
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("create_linked_table", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            result = adapter.create_linked_table(name, source_table, connect_string)
            if isinstance(result, dict) and not result.get("success", False):
                return _err(result.get("error", "create_linked_table failed"))
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def refresh_linked_table(
        self,
        name: str,
        connect_string: str | None = None,
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Re-authenticate a linked table.

        Args:
            name: Name of the linked table to refresh
            connect_string: Updated connection string (optional)
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("refresh_linked_table", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            result = adapter.refresh_linked_table(name, connect_string)
            if isinstance(result, dict) and not result.get("success", False):
                return _err(result.get("error", "refresh_linked_table failed"))
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def unlink_table(
        self,
        name: str,
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Remove a linked table without deleting the remote source table.

        Args:
            name: Name of the linked table to unlink
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("unlink_table", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            result = adapter.unlink_table(name)
            if isinstance(result, dict) and not result.get("success", False):
                return _err(result.get("error", "unlink_table failed"))
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    def upsert_linked_table(
        self,
        name: str,
        source_table: str,
        connect_string: str,
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Recreate a linked table (preserves hidden attributes).

        Args:
            name: Name of the linked table to upsert
            source_table: Name of the source table on the remote database
            connect_string: ODBC or DAO connection string to the remote database
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        if ack := self._policy.require_acknowledgment("upsert_linked_table", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            result = adapter.recreate_linked_table(name, source_table, connect_string)
            if isinstance(result, dict) and not result.get("success", False):
                return _err(result.get("error", "upsert_linked_table failed"))
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    # -------------------------------------------------------------------------
    # Database Creation
    # -------------------------------------------------------------------------

    def create_access_database(
        self,
        path: str,
        confirm: bool = False,
    ) -> Result:
        """Create a blank Access database at the given path.

        Args:
            path: Target path for the new .accdb or .mdb file
            confirm: Must be True to execute

        Returns:
            Ok on success, or Err
        """
        return _create_db(self._policy, self._allowed_dirs, path, self._backend_name, confirm)

    # -------------------------------------------------------------------------
    # Analysis Tools (plan 025)
    # -------------------------------------------------------------------------

    def get_er_diagram(self, connection_name: str = "default") -> Result:
        """Get the database schema as nodes and edges for ER diagram visualization.

        Args:
            connection_name: Connection to use

        Returns:
            Ok with dict containing nodes, edges, node_count, edge_count, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        return _get_er(
            adapter,
            lambda: self.get_tables(connection_name),
            lambda: self.get_relationships(connection_name),
        )

    def generate_sql(
        self,
        output_path: str,
        _table_names: list[str] | None = None,
        connection_name: str = "default",
    ) -> Result:
        """Generate SQL DDL script for the database schema.

        Args:
            output_path: Path to write the SQL script
            _table_names: Optional list of table names (currently unused — adapter limitation)
            connection_name: Connection to use

        Returns:
            Ok on success, or Err
        """
        if not is_path_allowed(output_path, self._allowed_dirs):
            return _err(f"Output path not allowed: {output_path}")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        return _gen_sql(adapter, self._policy, self._allowed_dirs, output_path, _table_names)

    def execute_sql_script(
        self,
        sql: str,
        connection_name: str = "default",
        confirm: bool = False,
    ) -> Result:
        """Execute a SQL script containing multiple statements.

        Args:
            sql: SQL script with multiple statements
            connection_name: Connection to use
            confirm: Must be True to execute

        Returns:
            Ok with execution summary, or Err
        """
        if ack := self._policy.require_acknowledgment("execute_sql_script", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")

        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")

        try:
            execute_script = getattr(adapter, "execute_script", None)
            if execute_script is None:
                return _err(f"{type(adapter).__name__} does not support execute_script")
            result = execute_script(sql)
            return _ok(data=result)
        except Exception as e:
            return _err(str(e))
