"""ODBC adapter with parameter binding and schema introspection.

NOTE: ComOnlyAdapterMixin has been removed per plan 002.
Unsupported operations raise BackendCapabilityMismatchError directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyodbc

from ..errors import BackendCapabilityMismatchError

logger = logging.getLogger(__name__)


class OdbcAdapter:
    """ODBC backend for cross-platform Access connectivity.

    Uses parameter binding (?, ?,) for SELECT/CRUD operations.
    Schema introspection via SQLTables/SQLColumns.
    """

    CAN_READ = True
    CAN_WRITE = True
    CAN_INTROSPECT = True

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Any = None
        self._connected = False

    def connect(self, db_path: str | None = None, password: str = "") -> None:
        """Open an ODBC connection to the Access database."""
        target = db_path if db_path is not None else self._db_path
        conn_str_parts = [f"DBQ={target}"]
        if password:
            conn_str_parts.append(f"PWD={password}")
        conn_str_parts.append("DRIVER={Microsoft Access Driver (*.mdb, *.accdb)}")
        conn_str = ";".join(conn_str_parts)

        try:
            self._conn = pyodbc.connect(conn_str)
            self._connected = True
        except Exception:
            self._connected = False
            raise

    def disconnect(self) -> None:
        """Close the ODBC connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------ #
    # Parameterized CRUD
    # ------------------------------------------------------------------ #

    def execute_query(self, sql: str, params: Any = None) -> dict:
        """Execute a SELECT with parameter binding."""
        if not self._connected or self._conn is None:
            return {"success": False, "rows": [], "count": 0, "columns": [], "error": "Not connected"}

        try:
            cursor = self._conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            results = [dict(zip(columns, row)) for row in rows]
            cursor.close()
            return {"success": True, "rows": results, "count": len(results), "columns": columns}
        except Exception as e:
            return {"success": False, "rows": [], "count": 0, "columns": [], "error": str(e)}

    def insert_data(self, table_name: str, data: dict | list[dict]) -> dict:
        """Insert rows with ? parameter binding."""
        if not self._connected or self._conn is None:
            return {"success": False, "error": "Not connected", "affected": 0}

        try:
            cursor = self._conn.cursor()
            rows = data if isinstance(data, list) else [data]
            total = 0
            for row in rows:
                cols = list(row.keys())
                vals = list(row.values())
                placeholders = ", ".join(["?" for _ in vals])
                sql = f"INSERT INTO [{table_name}] ({', '.join(cols)}) VALUES ({placeholders})"
                cursor.execute(sql, vals)
                total += cursor.rowcount
            self._conn.commit()
            cursor.close()
            return {"success": True, "affected": total}
        except Exception as e:
            self._conn.rollback()
            return {"success": False, "error": str(e), "affected": 0}

    def update_data(
        self,
        table_name: str,
        set_dict: dict,
        where_dict: dict | None = None,
    ) -> dict:
        """Update rows with ? parameter binding."""
        if not self._connected or self._conn is None:
            return {"success": False, "error": "Not connected", "affected": 0}

        try:
            set_clause = ", ".join(f"[{k}] = ?" for k in set_dict)
            vals = list(set_dict.values())
            sql = f"UPDATE [{table_name}] SET {set_clause}"
            if where_dict:
                where_clause = " AND ".join(f"[{k}] = ?" for k in where_dict.keys())
                sql += f" WHERE {where_clause}"
                vals.extend(where_dict.values())

            cursor = self._conn.cursor()
            cursor.execute(sql, vals)
            self._conn.commit()
            affected = cursor.rowcount
            cursor.close()
            return {"success": True, "affected": affected}
        except Exception as e:
            self._conn.rollback()
            return {"success": False, "error": str(e), "affected": 0}

    def delete_data(self, table_name: str, where_dict: dict | None = None) -> dict:
        """Delete rows with ? parameter binding."""
        if not self._connected or self._conn is None:
            return {"success": False, "error": "Not connected", "affected": 0}

        try:
            sql = f"DELETE FROM [{table_name}]"
            vals: list[Any] = []
            if where_dict:
                where_clause = " AND ".join(f"[{k}] = ?" for k in where_dict.keys())
                sql += f" WHERE {where_clause}"
                vals = list(where_dict.values())

            cursor = self._conn.cursor()
            cursor.execute(sql, vals)
            self._conn.commit()
            affected = cursor.rowcount
            cursor.close()
            return {"success": True, "affected": affected}
        except Exception as e:
            self._conn.rollback()
            return {"success": False, "error": str(e), "affected": 0}

    # ------------------------------------------------------------------ #
    # Schema introspection via SQLTables/SQLColumns
    # ------------------------------------------------------------------ #

    def get_tables(self) -> list:
        """Return user tables via SQLTables."""
        if not self._connected or self._conn is None:
            return []
        return self._query_table_list("TABLE")

    def get_system_tables(self) -> list:
        """Return system tables via SQLTables."""
        if not self._connected or self._conn is None:
            return []
        return self._query_table_list("SYSTEM TABLE")

    def get_queries(self) -> list:
        """Return saved queries via SQLTables."""
        if not self._connected or self._conn is None:
            return []
        return self._query_table_list("VIEW")

    def get_columns(self, table_name: str) -> list:
        """Return column metadata via SQLColumns."""
        if not self._connected or self._conn is None:
            return []

        try:
            cursor: Any = self._conn.cursor()
            cursor.columns(table=table_name)
            rows = cursor.fetchall()
            cursor.close()
            return [
                {
                    "name": row.column_name,
                    "type": row.type_name,
                    "size": row.column_size,
                    "nullable": row.nullable == 1,
                }
                for row in rows
            ]
        except Exception:
            return []

    def get_indexes(self, table_name: str) -> list:
        """Return index metadata via SQLPrimaryKeys/SQLStatistics."""
        if not self._connected or self._conn is None:
            return []
        return []

    def get_relationships(self) -> list:
        """Return foreign key relationships."""
        if not self._connected or self._conn is None:
            return []
        return []

    def _query_table_list(self, table_type: str) -> list:
        """Query SQLTables for tables of a given type."""
        try:
            cursor: Any = self._conn.cursor()
            cursor.tables(tableType=table_type)
            rows = cursor.fetchall()
            cursor.close()
            return [row.table_name for row in rows if row.table_name]
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Schema write operations (not supported via ODBC for Access)
    # ------------------------------------------------------------------ #

    def create_table(self, table_name: str, columns: list[dict]) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support CREATE TABLE. "
            "Use DAO or COM backend for DDL operations."
        )

    def delete_table(self, table_name: str) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support DROP TABLE. "
            "Use DAO or COM backend for DDL operations."
        )

    def alter_table(self, table_name: str, operations: list[dict]) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support ALTER TABLE. "
            "Use DAO or COM backend for DDL operations."
        )

    def create_index(self, table_name: str, index_name: str, columns: list[str], **kwargs) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support CREATE INDEX. "
            "Use DAO or COM backend for DDL operations."
        )

    def drop_index(self, table_name: str, index_name: str) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support DROP INDEX. "
            "Use DAO or COM backend for DDL operations."
        )

    def create_linked_table(self, name: str, source_table: str, connect_string: str) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support linked tables. "
            "Use DAO backend for linked table operations."
        )

    def refresh_linked_table(self, name: str, connect_string: str | None = None) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support linked tables. "
            "Use DAO backend for linked table operations."
        )

    def recreate_linked_table(self, name: str, source_table: str, connect_string: str) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support linked tables. "
            "Use DAO backend for linked table operations."
        )

    def unlink_table(self, name: str) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support linked tables. "
            "Use DAO backend for linked table operations."
        )

    def get_linked_tables(self) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support linked tables. "
            "Use DAO backend for linked table operations."
        )

    def create_relationship(self, **kwargs) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support relationships. "
            "Use DAO or COM backend for relationship operations."
        )

    def delete_relationship(self, **kwargs) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support relationships. "
            "Use DAO or COM backend for relationship operations."
        )

    def get_database_properties(self, names: list[str] | None = None) -> dict:
        return {}

    def set_database_property(self, name: str, value: str, type: str | None = None) -> bool:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support database properties."
        )

    def create_database(self, path: str) -> bool:
        raise BackendCapabilityMismatchError(
            "ODBC backend cannot create databases. "
            "Use DAO backend with COM dispatcher for database creation."
        )

    def create_query(self, name: str, sql: str) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support saved queries. "
            "Use DAO backend for query management."
        )

    def set_query_sql(self, name: str, sql: str) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support saved queries. "
            "Use DAO backend for query management."
        )

    def delete_query(self, name: str) -> dict:
        raise BackendCapabilityMismatchError(
            "ODBC backend does not support saved queries. "
            "Use DAO backend for query management."
        )

    def get_object_metadata(self, object_name: str) -> dict:
        return {}

    def get_table_schema_plan(self) -> tuple:
        return ([], "unknown")

    def generate_sql(self, output_path: str) -> dict:
        return {"success": False, "error": "Not implemented"}

    def get_database_statistics(self) -> dict:
        return {}

    def execute_raw_sql(self, sql: str) -> int:
        raise BackendCapabilityMismatchError(
            "ODBC backend execute_raw_sql is not supported."
        )

    def export_data(self, sql: str, file_path: str, format: str = "csv", **options) -> dict:
        return {"success": False, "error": "Export not supported via ODBC"}
