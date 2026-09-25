"""ODBC adapter with parameter binding and schema introspection.

NOTE: ComOnlyAdapterMixin has been removed per plan 002.
Unsupported operations raise BackendCapabilityMismatchError directly.
"""

from __future__ import annotations

import logging
from typing import Any

import pyodbc

from ..errors import BackendCapabilityMismatchError
from ..models import IndexInfo
from .crud_sql import build_delete, build_insert, build_update
from .odbc_schema_reader import OdbcSchemaReader

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
        """Initialize an ODBC adapter for the specified database path."""
        self._db_path = db_path
        self._conn: Any = None
        self._connected = False
        self._schema_reader: OdbcSchemaReader | None = None

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

    @property
    def connected(self) -> bool:
        """Return the lifecycle protocol connection state."""
        return self._connected

    def is_connected(self) -> bool:
        """Return whether the ODBC connection is open."""
        return self.connected

    def compact_repair(self, path: str, mode: str) -> dict[str, Any]:
        """Reject compact and repair because ODBC does not provide DAO support."""
        msg = (
            "Compact/repair requires the access (COM) backend; "
            "ODBC does not support DAO DBEngine.CompactDatabase."
        )
        raise NotImplementedError(msg)

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
                sql, vals = build_insert(table_name, row)
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
            sql, vals = build_update(table_name, set_dict, where_dict)
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
            sql, vals = build_delete(table_name, where_dict)
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

    def get_indexes(self, table_name: str) -> list[IndexInfo]:
        """Return index metadata via SQLStatistics + SQLPrimaryKeys.

        Best-effort — returns ``[]`` on any failure.
        """
        if not self._connected or self._conn is None:
            return []

        indexes: list[IndexInfo] = []

        # SQLStatistics — NON_UNIQUE=0 means unique index
        try:
            cursor = self._conn.cursor()
            cursor.statistics(table=table_name)
            for row in cursor.fetchall():
                if getattr(row, "column_name", None) is None:
                    continue
                index_name = getattr(row, "index_name", "") or ""
                column_name = getattr(row, "column_name", "") or ""
                non_unique = getattr(row, "non_unique", None)
                is_unique = False if non_unique is None else not bool(non_unique)
                if column_name:
                    indexes.append(
                        IndexInfo(
                            name=index_name,
                            columns=[column_name],
                            is_unique=is_unique,
                            is_primary=False,
                            ignore_nulls=False,
                        )
                    )
            cursor.close()
        except Exception:
            pass

        # SQLPrimaryKeys — synthesize a PK IndexInfo
        try:
            cursor = self._conn.cursor()
            cursor.primaryKeys(table=table_name)
            pk_cols: list[str] = []
            pk_index_name = f"PK_{table_name}"
            for row in cursor.fetchall():
                col = getattr(row, "column_name", None)
                if col:
                    pk_cols.append(col)
            cursor.close()
            if pk_cols:
                indexes.append(
                    IndexInfo(
                        name=pk_index_name,
                        columns=pk_cols,
                        is_unique=True,
                        is_primary=True,
                        ignore_nulls=False,
                    )
                )
        except Exception:
            pass

        return indexes

    def get_relationships(self) -> list:
        """Return foreign key relationships from MSysRelationships.

        Best-effort — degrades to ``[]`` when MSysRelationships is
        hidden or inaccessible.
        """
        if not self._connected or self._conn is None:
            return []

        if self._schema_reader is None:
            self._schema_reader = OdbcSchemaReader(self._conn, logger)

        return self._schema_reader.get_relationships()

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
        """Reject table creation because ODBC does not support Access DDL here."""
        msg = (
            "ODBC backend does not support CREATE TABLE. "
            "Use DAO or COM backend for DDL operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def delete_table(self, table_name: str) -> dict:
        """Reject table deletion because ODBC does not support Access DDL here."""
        msg = (
            "ODBC backend does not support DROP TABLE. "
            "Use DAO or COM backend for DDL operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def alter_table(self, table_name: str, operations: list[dict]) -> dict:
        """Reject table alteration because ODBC does not support Access DDL here."""
        msg = (
            "ODBC backend does not support ALTER TABLE. "
            "Use DAO or COM backend for DDL operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def create_index(self, table_name: str, index_name: str, columns: list[str], **kwargs) -> dict:
        """Reject index creation because ODBC does not support Access DDL here."""
        msg = (
            "ODBC backend does not support CREATE INDEX. "
            "Use DAO or COM backend for DDL operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def drop_index(self, table_name: str, index_name: str) -> dict:
        """Reject index deletion because ODBC does not support Access DDL here."""
        msg = (
            "ODBC backend does not support DROP INDEX. "
            "Use DAO or COM backend for DDL operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def create_linked_table(self, name: str, source_table: str, connect_string: str) -> dict:
        """Reject linked-table creation because ODBC does not support it."""
        msg = (
            "ODBC backend does not support linked tables. "
            "Use DAO backend for linked table operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def refresh_linked_table(self, name: str, connect_string: str | None = None) -> dict:
        """Reject linked-table refresh because ODBC does not support it."""
        msg = (
            "ODBC backend does not support linked tables. "
            "Use DAO backend for linked table operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def recreate_linked_table(self, name: str, source_table: str, connect_string: str) -> dict:
        """Reject linked-table recreation because ODBC does not support it."""
        msg = (
            "ODBC backend does not support linked tables. "
            "Use DAO backend for linked table operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def unlink_table(self, name: str) -> dict:
        """Reject linked-table removal because ODBC does not support it."""
        msg = (
            "ODBC backend does not support linked tables. "
            "Use DAO backend for linked table operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def get_linked_tables(self) -> dict:
        """Reject linked-table listing because ODBC does not support it."""
        msg = (
            "ODBC backend does not support linked tables. "
            "Use DAO backend for linked table operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def create_relationship(self, **kwargs) -> dict:
        """Reject relationship creation because ODBC does not support it."""
        msg = (
            "ODBC backend does not support relationships. "
            "Use DAO or COM backend for relationship operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def delete_relationship(self, **kwargs) -> dict:
        """Reject relationship deletion because ODBC does not support it."""
        msg = (
            "ODBC backend does not support relationships. "
            "Use DAO or COM backend for relationship operations."
        )
        raise BackendCapabilityMismatchError(msg)

    def get_database_properties(self, names: list[str] | None = None) -> dict:
        """Return database properties, which are unavailable through ODBC."""
        return {}

    def set_database_property(self, name: str, value: str, type: str | None = None) -> bool:
        """Reject database property changes because ODBC does not support them."""
        msg = "ODBC backend does not support database properties."
        raise BackendCapabilityMismatchError(msg)

    def create_database(self, path: str) -> bool:
        """Reject database creation because ODBC does not provide it."""
        msg = (
            "ODBC backend cannot create databases. "
            "Use DAO backend with COM dispatcher for database creation."
        )
        raise BackendCapabilityMismatchError(msg)

    def create_query(self, name: str, sql: str) -> dict:
        """Reject saved-query creation because ODBC does not support it."""
        msg = (
            "ODBC backend does not support saved queries. "
            "Use DAO backend for query management."
        )
        raise BackendCapabilityMismatchError(msg)

    def set_query_sql(self, name: str, sql: str) -> dict:
        """Reject saved-query updates because ODBC does not support them."""
        msg = (
            "ODBC backend does not support saved queries. "
            "Use DAO backend for query management."
        )
        raise BackendCapabilityMismatchError(msg)

    def delete_query(self, name: str) -> dict:
        """Reject saved-query deletion because ODBC does not support it."""
        msg = (
            "ODBC backend does not support saved queries. "
            "Use DAO backend for query management."
        )
        raise BackendCapabilityMismatchError(msg)

    def get_object_metadata(self, object_name: str) -> dict:
        """Return object metadata, which is unavailable through ODBC."""
        return {}

    def get_table_schema_plan(self) -> tuple:
        """Return the unavailable ODBC table schema plan sentinel."""
        return ([], "unknown")

    def generate_sql(self, output_path: str) -> dict:
        """Return the unsupported SQL generation result."""
        return {"success": False, "error": "Not implemented"}

    def get_database_statistics(self) -> dict:
        """Return database statistics, which are unavailable through ODBC."""
        return {}

    def execute_raw_sql(self, sql: str) -> int:
        """Reject raw SQL execution because this adapter exposes parameterized operations."""
        msg = "ODBC backend execute_raw_sql is not supported."
        raise BackendCapabilityMismatchError(msg)

    def execute_script(self, sql_script: str) -> dict:
        """Reject SQL script execution via ODBC."""
        msg = "ODBC backend execute_script is not supported."
        raise BackendCapabilityMismatchError(msg)

    def export_data(self, sql: str, file_path: str, format: str = "csv", **options) -> dict:
        """Return the unsupported ODBC export result."""
        return {"success": False, "error": "Export not supported via ODBC"}

    # ------------------------------------------------------------------ #
    # Versioning stubs (COM-only — SaveAsText / LoadFromText / VBE)
    # ------------------------------------------------------------------ #

    def compile_vba(self) -> dict:
        """Reject VBA compilation because it requires COM automation."""
        msg = "Versioning operations require COM automation; use the access backend."
        raise NotImplementedError(msg)

    def export_form_to_text(self, form_name: str) -> str:
        """Reject form export because it requires COM automation."""
        msg = "Versioning operations require COM automation; use the access backend."
        raise NotImplementedError(msg)

    def import_form_from_text(self, form_name: str, text_data: str) -> bool:
        """Reject form import because it requires COM automation."""
        msg = "Versioning operations require COM automation; use the access backend."
        raise NotImplementedError(msg)

    def export_report_to_text(self, report_name: str) -> str:
        """Reject report export because it requires COM automation."""
        msg = "Versioning operations require COM automation; use the access backend."
        raise NotImplementedError(msg)

    def import_report_from_text(self, report_name: str, text_data: str) -> bool:
        """Reject report import because it requires COM automation."""
        msg = "Versioning operations require COM automation; use the access backend."
        raise NotImplementedError(msg)

    def export_macro_to_text(self, macro_name: str) -> str:
        """Reject macro export because it requires COM automation."""
        msg = "Versioning operations require COM automation; use the access backend."
        raise NotImplementedError(msg)

    def import_macro_from_text(self, macro_name: str, text_data: str) -> bool:
        """Reject macro import because it requires COM automation."""
        msg = "Versioning operations require COM automation; use the access backend."
        raise NotImplementedError(msg)

    def export_query_to_text(self, query_name: str) -> str:
        """Reject query export because it requires COM automation."""
        msg = "Versioning operations require COM automation; use the access backend."
        raise NotImplementedError(msg)

    def import_query_from_text(self, query_name: str, text_data: str) -> bool:
        """Reject query import because it requires COM automation."""
        msg = "Versioning operations require COM automation; use the access backend."
        raise NotImplementedError(msg)

    def export_module_to_text(self, module_name: str) -> str:
        """Reject module export because it requires COM automation."""
        msg = "Versioning operations require COM automation; use the access backend."
        raise NotImplementedError(msg)

    def import_module_from_text(self, module_name: str, code: str) -> bool:
        """Reject module import because it requires COM automation."""
        msg = "Versioning operations require COM automation; use the access backend."
        raise NotImplementedError(msg)
