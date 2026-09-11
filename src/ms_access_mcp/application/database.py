"""Database use cases — constructor-injected backends, immutable result envelopes.

All file paths validated via safety.is_path_allowed.
All destructive ops gated via safety.require_acknowledgment.
"""

from __future__ import annotations

import csv
import inspect
import json
from functools import cache
from pathlib import Path
from typing import Any, Literal, cast

from ..backends import DATA_READ_CAPS, BackendSelector
from ..errors import Err, Ok, Result, err, ok
from ..safety import WritePolicy, is_path_allowed
from .sql_builder import build_select

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

        try:
            self._adapter = BackendSelector.get_adapter(
                db_path=db_path,
                backend=cast(Literal["odbc", "com", "dao", "auto"] | None, self._backend_name),
                capabilities=DATA_READ_CAPS,
            )
            self._adapter.connect(db_path)
            self._db_path = db_path
            self._connections[self._active_connection] = self._adapter
            return _ok(data={"path": db_path, "connection": self._active_connection})
        except Exception as e:
            import traceback

            traceback.print_exc()
            return _err(f"connect failed: {e!r}")

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
            return _ok(data=tables, rowcount=len(tables))
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
        """Get schema for a table.

        Args:
            table_name: Name of the table
            connection_name: Connection to use

        Returns:
            Ok with table schema, or Err
        """
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            schema = adapter.get_table_schema(table_name)
            return _ok(data=schema)
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
            return _ok(data=indexes, rowcount=len(indexes))
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
            return _ok(data=rels, rowcount=len(rels))
        except Exception as e:
            return _err(str(e))

    def get_object_metadata(self, object_name: str, object_type: str = "table", connection_name: str = "default") -> Result:
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
            meta = adapter.get_object_metadata(object_name, object_type)
            return _ok(data=meta)
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
            queries = adapter.get_queries()
            return _ok(data=queries, rowcount=len(queries))
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
        try:
            props = adapter.get_database_properties()
            return _ok(data=props)
        except Exception as e:
            return _err(str(e))

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
        try:
            stats = adapter.get_database_statistics()
            return _ok(data=stats)
        except Exception as e:
            return _err(str(e))

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
            if isinstance(result, dict):
                if "rows" in result:
                    return _ok(data=result.get("rows", []), rowcount=result.get("rowcount", 0))
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
            rowcount = adapter.insert_data(table_name, data)
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
            rowcount = adapter.update_data(table_name, data, where=where)
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
            rowcount = adapter.delete_data(table_name, where=where)
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
            adapter.create_relationship(relationship_name, source_table, source_column, dest_table, dest_column)
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
            adapter.delete_relationship(relationship_name)
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))

    # -------------------------------------------------------------------------
    # Export
    # -------------------------------------------------------------------------

    def export_data(
        self,
        table_or_query: str,
        output_path: str,
        format: Literal["csv", "json"] = "csv",
        connection_name: str = "default",
    ) -> Result:
        """Export table or query data to a file.

        Args:
            table_or_query: Table name or query name
            output_path: Output file path
            format: Export format ("csv" or "json")
            connection_name: Connection to use

        Returns:
            Ok with rowcount, or Err
        """
        if not is_path_allowed(output_path, self._allowed_dirs):
            return _err(f"Output path not allowed: {output_path}")

        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")

        try:
            # Fetch the data
            result = self.query_data(build_select(table_or_query, None, None, None), connection_name=connection_name)
            if isinstance(result, Err):
                return result

            rows = result.data if isinstance(result, Ok) else []

            # Export to file
            if format == "csv":
                with open(output_path, "w", newline="", encoding="utf-8") as f:
                    if rows:
                        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                        writer.writeheader()
                        writer.writerows(rows)
                    else:
                        f.write("")
            else:  # json
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(rows, f, indent=2, default=str)

            return _ok(rowcount=len(rows))
        except Exception as e:
            return _err(str(e))

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
        if ack := self._policy.require_acknowledgment("set_database_property", confirm):
            return _err(ack)
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        adapter = self._get_adapter(connection_name)
        if adapter is None:
            return _err("Not connected")
        try:
            adapter.set_database_property(name, value)
            return _ok(rowcount=1)
        except Exception as e:
            return _err(str(e))
