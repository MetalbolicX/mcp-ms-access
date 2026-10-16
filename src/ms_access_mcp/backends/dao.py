"""DaoAdapter — Windows-only DAO backend owning a long-lived Database handle."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from types import TracebackType
from typing import Any

from ..errors import DaoOperationError
from ..models import DatabaseStatistics, GenerateSqlResult, ObjectMetadata, QueryResult, RelationshipResult, WriteResult
from .dao_sql import (
    _access_sql_type,
    _alter_table_add_column,
    _format_dao_value,
    _sanitize_where_string,
    _strip_password,
)
from .dispatcher import DAO_DB_FAIL_ON_ERROR, ComDispatcher
from .script import parse_script_lines

logger = logging.getLogger(__name__)

DAO_PARAMS_UNSUPPORTED_ERROR = (
    "DAO backend does not support query parameters; "
    "use the ODBC backend for parameterized queries"
)


# ============================================================================= #
# DaoSession — context manager for short-lived DAO handles
# ============================================================================= #


class DaoSession:
    """Context manager for a short-lived DAO Database handle.

    Args:
        db_path: Absolute path to the .accdb or .mdb file.
        password: Optional database password; appended as ``;PWD=...``.
        read_only: ``True`` (default) opens with ``ReadOnly=True``.

    """

    def __init__(
        self,
        db_path: str,
        password: str = "",
        read_only: bool = True,
    ) -> None:
        """Initialize a DAO session for the specified database path."""
        self.path: str = db_path
        self._password: str = password
        self.read_only: bool = read_only
        self.db: Any | None = None

    def __enter__(self) -> DaoSession:
        """Open the DAO database and return this session."""
        self.db = self._open_database()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the DAO database when leaving the context manager."""
        self._close()

    def close(self) -> None:
        """Close the DAO database if open. Idempotent."""
        self._close()

    def _open_database(self) -> Any:
        import win32com.client  # type: ignore[import-not-found]

        engine = win32com.client.Dispatch("DAO.DBEngine.120")
        connect_str = f";PWD={self._password}" if self._password else ""
        return engine.OpenDatabase(self.path, False, self.read_only, connect_str)

    def _close(self) -> None:
        if self.db is None:
            return
        try:
            self.db.Close()
        except Exception:
            pass
        finally:
            self.db = None


# ============================================================================= #
# DaoOperationError + DaoAdapter
# ============================================================================= #


class DaoAdapter:
    """Windows-only DAO backend with a long-lived Database handle.

    The adapter drives the shared ComDispatcher STA thread via
    ``open_dao_database`` / ``close_dao_database`` so the handle
    is reused across all operations.

    Lifecycle:
        A fresh adapter is NOT connected. Call connect() to open the
        long-lived handle, disconnect() to close it.
    """

    def __init__(
        self,
        db_path: str,
        dispatcher: ComDispatcher | None = None,
    ) -> None:
        """Initialize a DAO adapter with an optional shared dispatcher."""
        self._db_path: str = db_path
        self._owns_dispatcher = dispatcher is None
        self._dispatcher: ComDispatcher = dispatcher if dispatcher is not None else ComDispatcher()
        self._connected: bool = False
        self._schema: Any = None  # initialized on first use
        self._db_ops: Any = None

    # -------------------------------------------------------------------------- #
    # Connection lifecycle
    # -------------------------------------------------------------------------- #

    def connect(
        self,
        db_path: str | None = None,
        password: str = "",
    ) -> None:
        """Open a long-lived DAO Database handle on the STA thread."""
        target_path = db_path if db_path is not None else self._db_path
        self._dispatcher.start()
        try:
            self._dispatcher.open_dao_database(
                target_path,
                password=password,
                read_only=False,
            )
        except Exception as e:
            self._dispatcher.mark_unhealthy()
            self._connected = False
            if self._owns_dispatcher:
                self._dispatcher.shutdown()
            msg = f"DAO connect failed for {target_path}: {e}"
            raise DaoOperationError(
                msg,
                cause=e,
            ) from e
        self._connected = True
        if db_path is not None:
            self._db_path = db_path

    def disconnect(self) -> None:
        """Close the long-lived DAO Database handle. Idempotent."""
        try:
            self._dispatcher.close_dao_database()
        except Exception as e:
            logger.warning("DaoAdapter.disconnect close_dao_database failed: %s", e)
        finally:
            self._connected = False
            if self._owns_dispatcher:
                self._dispatcher.shutdown()

    def create_database(self, path: str, *, format: str = "accdb") -> bool:
        """Create a blank Access database at path via the shared dispatcher.

        Args:
            path: Target file path.
            format: "accdb" (default, ACE/.accdb) or "mdb" (Jet 4 format).

        """
        self._dispatcher.start()
        try:
            self._dispatcher.create_dao_database(path, format=format)
            return True
        except Exception as e:
            self._dispatcher.mark_unhealthy()
            self._connected = False
            msg = f"DAO create_database failed for {path}: {e}"
            raise DaoOperationError(
                msg,
                cause=e,
            ) from e

    @property
    def connected(self) -> bool:
        """Return the lifecycle protocol connection state."""
        return self._connected

    def is_connected(self) -> bool:
        """Return True if this adapter holds an open DAO handle."""
        return self.connected

    # -------------------------------------------------------------------------- #
    # Schema read surface — delegated to SchemaInspector (lazy init)
    # -------------------------------------------------------------------------- #

    @property
    def _schema_inspector(self) -> Any:
        if self._schema is None:
            from .schema_inspector import SchemaInspector
            self._schema = SchemaInspector(self._dispatcher)
        return self._schema

    def get_tables(self) -> list:
        """Return tables from the schema inspector."""
        return self._schema_inspector.get_tables()

    def get_system_tables(self) -> list:
        """Return system tables from the schema inspector."""
        return self._schema_inspector.get_system_tables()

    def get_queries(self) -> list:
        """Return saved queries from the schema inspector."""
        return self._schema_inspector.get_queries()

    def get_indexes(self, table_name: str) -> list:
        """Return indexes for a table from the schema inspector."""
        return self._schema_inspector.get_indexes(table_name)

    def get_relationships(self) -> list:
        """Return relationships from the schema inspector."""
        return self._schema_inspector.get_relationships()

    def get_object_metadata(self, object_name: str) -> ObjectMetadata | None:
        """Return metadata for an Access object."""
        return self._schema_inspector.get_object_metadata(object_name)

    def get_table_schema_plan(self) -> tuple:
        """Return the table schema plan from the schema inspector."""
        return self._schema_inspector.get_table_schema_plan()

    def generate_sql(self, output_path: str) -> GenerateSqlResult:
        """Return the SQL generation result for the output path."""
        return self._schema_inspector.generate_sql(output_path)

    def get_database_statistics(self) -> DatabaseStatistics:
        """Return database statistics from the schema inspector."""
        return self._schema_inspector.get_database_statistics()

    # -------------------------------------------------------------------------- #
    # Query CRUD
    # -------------------------------------------------------------------------- #

    def create_query(self, name: str, sql: str) -> dict:
        """Create a saved query (QueryDef) on the current database."""
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            try:
                db = self._dispatcher.current_db
                qdef = db.CreateQueryDef(name, sql)
                db.QueryDefs.Append(qdef)
                return {"success": True}
            except Exception as e:
                return {"success": False, "error": _strip_password(str(e))}

        return self._dispatcher.call(_do)

    def set_query_sql(self, name: str, sql: str) -> dict:
        """Update the SQL of an existing saved query."""
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            try:
                qdef = self._dispatcher.current_db.QueryDefs(name)
                qdef.SQL = sql
                return {"success": True}
            except Exception as e:
                return {"success": False, "error": _strip_password(str(e))}

        return self._dispatcher.call(_do)

    def delete_query(self, name: str) -> dict:
        """Delete a saved query from the current database."""
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            try:
                self._dispatcher.current_db.QueryDefs.Delete(name)
                return {"success": True}
            except Exception as e:
                return {"success": False, "error": _strip_password(str(e))}

        return self._dispatcher.call(_do)

    def create_relationship(
        self,
        table_name: str,
        relationship_name: str,
        columns: list[str],
        foreign_table: str,
        foreign_columns: list[str],
    ) -> RelationshipResult:
        """Create a foreign-key relationship via DAO Relations."""
        if not self._dispatcher.is_connected():
            return RelationshipResult(success=False, error="Not connected")
        if len(columns) != len(foreign_columns):
            return RelationshipResult(
                success=False,
                error="columns and foreign_columns must have same length",
            )
        return self._schema_inspector.create_relationship(
            table_name,
            relationship_name,
            columns,
            foreign_table,
            foreign_columns,
        )

    def delete_relationship(self, table_name: str, relationship_name: str) -> RelationshipResult:
        """Delete a foreign-key relationship via DAO Relations."""
        return self._schema_inspector.delete_relationship(table_name, relationship_name)

    # -------------------------------------------------------------------------- #
    # Database properties
    # -------------------------------------------------------------------------- #

    @property
    def _db_operations(self) -> Any:
        if self._db_ops is None:
            from .db_operations import DbOperations
            self._db_ops = DbOperations(self._dispatcher)
        return self._db_ops

    def get_database_properties(self, names: list[str] | None = None) -> dict:
        """Return the requested database properties."""
        return self._db_operations.get_database_properties(names)

    def set_database_property(self, name: str, value: str, type: str | None = None) -> bool:
        """Set a database property and return whether the operation succeeded."""
        return self._db_operations.set_database_property(name, value, type)

    # -------------------------------------------------------------------------- #
    # Row CRUD via DAO
    # -------------------------------------------------------------------------- #

    def execute_query(self, sql: str, params: list | None = None, limit: int | None = None) -> QueryResult:
        """Execute a SQL SELECT via DAO OpenRecordset with an optional row limit.

        DAO cannot bind parameters. Any non-empty ``params`` fails closed with an
        explicit error directing callers to the ODBC backend, before any SQL runs;
        ``None`` and ``[]`` keep the existing no-parameter behavior.
        """
        if params:
            return QueryResult(
                success=False,
                rows=[],
                count=0,
                columns=[],
                error=DAO_PARAMS_UNSUPPORTED_ERROR,
            )
        if not self._dispatcher.is_connected():
            return QueryResult(
                success=False,
                rows=[],
                count=0,
                columns=[],
                error="Not connected",
            )

        query_sql = sql
        if limit is not None:
            select_prefix = re.match(
                r"^(\s*SELECT\s+(?:DISTINCT\s+)?)(?!DISTINCTROW\b|ALL\b)",
                sql,
                re.IGNORECASE,
            )
            if select_prefix is not None:
                query_sql = (
                    f"{select_prefix.group(1)}TOP {limit} "
                    f"{sql[select_prefix.end():]}"
                )

        def _do() -> QueryResult:
            rs = None
            try:
                db = self._dispatcher.current_db
                rs = db.OpenRecordset(query_sql)
                try:
                    columns = [rs.Fields(i).Name for i in range(rs.Fields.Count)]
                except Exception:
                    if not rs.EOF:
                        raise
                    columns = []

                if rs.EOF:
                    return QueryResult(success=True, rows=[], count=0, columns=columns)

                results: list[dict] = []
                while not rs.EOF:
                    row = {
                        col: (
                            val.isoformat()
                            if val is not None and hasattr(val, "strftime")
                            else val
                        )
                        for col, val in ((columns[i], rs.Fields(i).Value) for i in range(len(columns)))
                    }
                    results.append(row)
                    rs.MoveNext()

                if limit is not None:
                    results = results[:limit]
                return QueryResult(
                    success=True,
                    rows=results,
                    count=len(results),
                    columns=columns,
                )
            except Exception as e:
                return QueryResult(
                    success=False,
                    rows=[],
                    count=0,
                    columns=[],
                    error=_strip_password(str(e)),
                )
            finally:
                if rs is not None:
                    try:
                        rs.Close()
                    except Exception:
                        pass

        return self._dispatcher.call(_do)

    def iter_query(
        self, sql: str, params: list | None = None, chunk: int = 1000
    ) -> Iterator[list[dict]]:
        """Yield SELECT rows in bounded batches, reading DAO only on the STA thread.

        The recordset is retained in this method's closure between dispatcher
        calls, but every COM operation (including Close) runs inside ``call``.

        DAO cannot bind parameters: any non-empty ``params`` raises before SQL runs.
        """
        if params:
            raise DaoOperationError(DAO_PARAMS_UNSUPPORTED_ERROR)
        if chunk <= 0:
            msg = "chunk must be positive"
            raise ValueError(msg)
        if not self._dispatcher.is_connected():
            msg = "Not connected"
            raise DaoOperationError(msg)

        rs = None
        columns: list[str] = []
        finished = False

        def _next_chunk() -> list[dict] | None:
            nonlocal rs, columns, finished
            if finished:
                return None
            try:
                if rs is None:
                    rs = self._dispatcher.current_db.OpenRecordset(sql)
                    if rs.EOF:
                        finished = True
                        rs.Close()
                        rs = None
                        return None
                    columns = [rs.Fields(i).Name for i in range(rs.Fields.Count)]

                rows: list[dict] = []
                while not rs.EOF and len(rows) < chunk:
                    rows.append(
                        {
                            col: (
                                val.isoformat()
                                if val is not None and hasattr(val, "strftime")
                                else val
                            )
                            for col, val in (
                                (columns[i], rs.Fields(i).Value)
                                for i in range(len(columns))
                            )
                        }
                    )
                    rs.MoveNext()
                if rs.EOF:
                    finished = True
                    rs.Close()
                    rs = None
                return rows or None
            except Exception as e:
                finished = True
                if rs is not None:
                    try:
                        rs.Close()
                    except Exception:
                        pass
                    rs = None
                raise DaoOperationError(_strip_password(str(e))) from e

        while True:
            batch = self._dispatcher.call(_next_chunk)
            if batch is None:
                return
            yield batch

    def insert_data(self, table_name: str, data: dict | list[dict]) -> WriteResult:
        """Insert one or more rows via DAO Execute with inline values."""
        if not self._dispatcher.is_connected():
            return WriteResult(success=False, error="Not connected", affected=0)

        def _do() -> WriteResult:
            try:
                db = self._dispatcher.current_db
                rows = data if isinstance(data, list) else [data]
                total_affected = 0
                for row in rows:
                    cols = ", ".join(f"[{c}]" for c in row.keys())
                    vals = ", ".join(_format_dao_value(v) for v in row.values())
                    sql = f"INSERT INTO [{table_name}] ({cols}) VALUES ({vals})"
                    db.Execute(sql, DAO_DB_FAIL_ON_ERROR)
                    total_affected += db.RecordsAffected
                return WriteResult(success=True, affected=total_affected)
            except Exception as e:
                return WriteResult(success=False, error=_strip_password(str(e)), affected=0)

        return self._dispatcher.call(_do)

    def update_data(
        self,
        table_name: str,
        set_dict: dict,
        where_dict: dict | str | None = None,
    ) -> WriteResult:
        """Update rows via DAO Execute with inline values."""
        if not self._dispatcher.is_connected():
            return WriteResult(success=False, error="Not connected", affected=0)

        if isinstance(where_dict, str):
            if _sanitize_where_string(where_dict) is None:
                return WriteResult(
                    success=False,
                    error="where_dict contains disallowed characters — SQL injection blocked",
                    affected=0,
                )

        def _do() -> WriteResult:
            try:
                db = self._dispatcher.current_db
                set_clause = ", ".join(
                    f"[{c}] = {_format_dao_value(v)}" for c, v in set_dict.items()
                )
                sql = f"UPDATE [{table_name}] SET {set_clause}"
                if where_dict is not None:
                    if isinstance(where_dict, str):
                        sql += f" WHERE {where_dict}"
                    else:
                        where_clause = " AND ".join(
                            f"[{c}] = {_format_dao_value(v)}" for c, v in where_dict.items()
                        )
                        sql += f" WHERE {where_clause}"
                db.Execute(sql, DAO_DB_FAIL_ON_ERROR)
                return WriteResult(success=True, affected=db.RecordsAffected)
            except Exception as e:
                return WriteResult(success=False, error=_strip_password(str(e)), affected=0)

        return self._dispatcher.call(_do)

    def delete_data(
        self,
        table_name: str,
        where_dict: dict | str | None = None,
    ) -> WriteResult:
        """Delete rows via DAO Execute."""
        if not self._dispatcher.is_connected():
            return WriteResult(success=False, error="Not connected", affected=0)

        if isinstance(where_dict, str):
            if _sanitize_where_string(where_dict) is None:
                return WriteResult(
                    success=False,
                    error="where_dict contains disallowed characters — SQL injection blocked",
                    affected=0,
                )

        def _do() -> WriteResult:
            try:
                db = self._dispatcher.current_db
                sql = f"DELETE FROM [{table_name}]"
                if where_dict is not None:
                    if isinstance(where_dict, str):
                        sql += f" WHERE {where_dict}"
                    else:
                        where_clause = " AND ".join(
                            f"[{c}] = {_format_dao_value(v)}" for c, v in where_dict.items()
                        )
                        sql += f" WHERE {where_clause}"
                db.Execute(sql, DAO_DB_FAIL_ON_ERROR)
                return WriteResult(success=True, affected=db.RecordsAffected)
            except Exception as e:
                return WriteResult(success=False, error=_strip_password(str(e)), affected=0)

        return self._dispatcher.call(_do)

    def execute_raw_sql(self, sql: str) -> int:
        """Execute arbitrary SQL via DAO Execute. Returns records affected."""
        if not self._dispatcher.is_connected():
            msg = "Not connected"
            raise RuntimeError(msg)

        def _do() -> int:
            db = self._dispatcher.current_db
            db.Execute(sql, DAO_DB_FAIL_ON_ERROR)
            return db.RecordsAffected

        return self._dispatcher.call(_do)

    def execute_script(self, sql_script: str) -> dict:
        """Execute a SQL script containing multiple statements.

        Parses the script into individual statements and executes
        each one in order via DAO Execute. Returns a summary of results.
        """
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            db = self._dispatcher.current_db
            statements = parse_script_lines(sql_script)
            results: list[dict] = []
            errors: list[str] = []

            for stmt in statements:
                if not stmt.strip():
                    continue
                try:
                    db.Execute(stmt, DAO_DB_FAIL_ON_ERROR)
                    results.append({"statement": stmt[:60], "success": True})
                except Exception as e:
                    scrubbed_error = _strip_password(str(e))
                    scrubbed_stmt = _strip_password(stmt)[:60]
                    errors.append(scrubbed_error)
                    results.append({"statement": scrubbed_stmt, "success": False, "error": scrubbed_error})

            return {
                "success": len(errors) == 0,
                "statements": len(statements),
                "results": results,
                "errors": errors,
            }

        return self._dispatcher.call(_do)

    # -------------------------------------------------------------------------- #
    # Table/index DDL
    # -------------------------------------------------------------------------- #

    def create_table(self, table_name: str, columns: list[dict]) -> dict:
        """Create a new table via DAO Execute with Jet DDL."""
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            try:
                db = self._dispatcher.current_db
                col_defs: list[str] = []
                pk_col: str | None = None
                for col in columns:
                    col_name = col["name"]
                    col_type = col.get("type", "Text")
                    col_size = col.get("size", 255)
                    required = col.get("required", False)
                    is_autoincrement = col.get("is_autoincrement", False)
                    is_pk = col.get("primary_key", False)

                    type_sql = _access_sql_type(col_type, col_size)
                    col_def = f"[{col_name}] {type_sql}"
                    if is_autoincrement or is_pk:
                        col_def += " NOT NULL"
                        if is_autoincrement:
                            pk_col = col_name
                    elif required:
                        col_def += " NOT NULL"
                    col_defs.append(col_def)

                if pk_col:
                    col_defs.append(f"PRIMARY KEY ([{pk_col}])")

                sql = f"CREATE TABLE [{table_name}] ({', '.join(col_defs)})"
                db.Execute(sql, DAO_DB_FAIL_ON_ERROR)
                return {"success": True}
            except Exception as e:
                return {"success": False, "error": _strip_password(str(e))}

        return self._dispatcher.call(_do)

    def delete_table(self, table_name: str) -> dict:
        """Drop a table via DAO Execute after removing referencing relations."""
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            try:
                db = self._dispatcher.current_db
                for i in range(db.Relations.Count - 1, -1, -1):
                    rel = db.Relations(i)
                    if rel.Table == table_name or rel.ForeignTable == table_name:
                        db.Relations.Delete(rel.Name)
                db.Execute(f"DROP TABLE [{table_name}]", DAO_DB_FAIL_ON_ERROR)
                return {"success": True}
            except Exception as e:
                return {"success": False, "error": _strip_password(str(e))}

        return self._dispatcher.call(_do)

    def create_index(
        self,
        table_name: str,
        index_name: str,
        columns: list[str],
        unique: bool = False,
        ignore_nulls: bool = False,
    ) -> dict:
        """Create an index via Jet DDL."""
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            try:
                db = self._dispatcher.current_db
                col_list = ", ".join(f"[{col}]" for col in columns)
                sql = "CREATE "
                if unique:
                    sql += "UNIQUE "
                sql += f"INDEX [{index_name}] ON [{table_name}] ({col_list})"
                if ignore_nulls:
                    sql += " WITH IGNORE NULL"
                db.Execute(sql, DAO_DB_FAIL_ON_ERROR)
                return {"success": True}
            except Exception as e:
                return {"success": False, "error": _strip_password(str(e))}

        return self._dispatcher.call(_do)

    def drop_index(self, table_name: str, index_name: str) -> dict:
        """Drop an index via Jet DDL."""
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            try:
                db = self._dispatcher.current_db
                db.Execute(
                    f"DROP INDEX [{index_name}] ON [{table_name}]",
                    DAO_DB_FAIL_ON_ERROR,
                )
                return {"success": True}
            except Exception as e:
                return {"success": False, "error": _strip_password(str(e))}

        return self._dispatcher.call(_do)

    def alter_table(self, table_name: str, operations: list[dict]) -> dict:
        """Apply a list of schema modifications to a table."""
        if not self._dispatcher.is_connected():
            return {"success": False, "operations": [], "error": "Not connected"}

        valid_actions = {
            "add_column",
            "drop_column",
            "modify_column",
            "rename_table",
            "rename_column",
        }
        results: list[dict] = []

        def _do() -> dict:
            for op in operations:
                action = op.get("action")
                params = op.get("params", {})

                if action not in valid_actions:
                    results.append(
                        {"action": action, "success": False, "error": f"Unknown action: {action}"}
                    )
                    continue

                try:
                    if action == "add_column":
                        result = _alter_table_add_column(
                            self._dispatcher.current_db, table_name, params
                        )
                    elif action == "drop_column":
                        name = params["name"]
                        sql = f"ALTER TABLE [{table_name}] DROP COLUMN [{name}]"
                        self._dispatcher.current_db.Execute(sql, DAO_DB_FAIL_ON_ERROR)
                        result = {"success": True}
                    elif action == "modify_column":
                        name = params["name"]
                        col_type = params.get("type", "Text")
                        size = params.get("size", 255)
                        nullable = params.get("nullable", True)
                        type_sql = _access_sql_type(col_type, size)
                        col_def = f"[{name}] {type_sql}"
                        if not nullable:
                            col_def += " NOT NULL"
                        else:
                            col_def += " NULL"
                        sql = f"ALTER TABLE [{table_name}] ALTER COLUMN {col_def}"
                        self._dispatcher.current_db.Execute(sql, DAO_DB_FAIL_ON_ERROR)
                        result = {"success": True}
                    elif action == "rename_table":
                        new_name = params["new_name"]
                        tdef = self._dispatcher.current_db.TableDefs(table_name)
                        tdef.Name = new_name
                        result = {"success": True}
                    elif action == "rename_column":
                        old_name = params["name"]
                        new_name = params["new_name"]
                        tdef = self._dispatcher.current_db.TableDefs(table_name)
                        field = tdef.Fields(old_name)
                        field.Name = new_name
                        result = {"success": True}
                    else:
                        result = {"success": False, "error": f"Unhandled action: {action}"}
                    results.append({"action": action, **result})
                except Exception as e:
                    results.append({"action": action, "success": False, "error": _strip_password(str(e))})

            overall = all(r["success"] for r in results)
            return {"success": overall, "operations": results}

        return self._dispatcher.call(_do)

    # -------------------------------------------------------------------------- #
    # Linked tables
    # -------------------------------------------------------------------------- #

    def get_linked_tables(self) -> list[dict]:
        """Return linked (attached) tables directly, without a result envelope."""
        if not self._dispatcher.is_connected():
            return []

        def _do() -> list[dict]:
            linked_tables: list[dict] = []
            try:
                db = self._dispatcher.current_db
                for i in range(db.TableDefs.Count):
                    tdef = db.TableDefs(i)
                    if tdef.Attributes & 0x80000000:
                        connect_str = _strip_password(tdef.Connect or "")
                        if connect_str.startswith("ODBC"):
                            table_type = "ODBC"
                        elif connect_str.startswith("Access"):
                            table_type = "Access"
                        elif connect_str.startswith("Excel"):
                            table_type = "Excel"
                        else:
                            table_type = "ODBC"
                        linked_tables.append(
                            {
                                "name": tdef.Name,
                                "source_table": tdef.SourceTableName,
                                "connect_string": connect_str,
                                "type": table_type,
                                "attributes": tdef.Attributes,
                            }
                        )
            except Exception as e:
                raise DaoOperationError(_strip_password(str(e))) from e
            return linked_tables

        return self._dispatcher.call(_do)

    def create_linked_table(
        self, name: str, source_table: str, connect_string: str
    ) -> dict:
        """Create a new linked (attached) table and strip the password."""
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            try:
                db = self._dispatcher.current_db
                tdef = db.CreateTableDef(name)
                tdef.SourceTableName = source_table
                tdef.Connect = connect_string
                tdef.Attributes = 0x80000000  # dbLinkAttachedTable
                db.TableDefs.Append(tdef)
                tdef.Connect = _strip_password(connect_string)
                return {"success": True}
            except Exception as e:
                err = str(e)
                if connect_string in err:
                    err = err.replace(connect_string, "[sanitized]")
                err = _strip_password(err)
                return {"success": False, "error": err}

        return self._dispatcher.call(_do)

    def refresh_linked_table(self, name: str, connect_string: str | None = None) -> dict:
        """Re-authenticate a linked table and strip the new password."""
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            try:
                tdef = self._dispatcher.current_db.TableDefs(name)
                if connect_string is not None:
                    tdef.Connect = connect_string
                tdef.RefreshLink()
                tdef.Connect = _strip_password(tdef.Connect or "")
                return {"success": True}
            except Exception as e:
                err = str(e)
                if connect_string and connect_string in err:
                    err = err.replace(connect_string, "[sanitized]")
                err = _strip_password(err)
                return {"success": False, "error": err}

        return self._dispatcher.call(_do)

    def recreate_linked_table(
        self, name: str, source_table: str, connect_string: str
    ) -> dict:
        """Recreate a linked table preserving hidden attributes."""
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            try:
                db = self._dispatcher.current_db
                old_tdef = db.TableDefs(name)
                old_attrs = old_tdef.Attributes
                db.TableDefs.Delete(name)
                new_tdef = db.CreateTableDef(name)
                new_tdef.SourceTableName = source_table
                new_tdef.Connect = connect_string
                new_tdef.Attributes = old_attrs
                db.TableDefs.Append(new_tdef)
                new_tdef.Connect = _strip_password(connect_string)
                return {"success": True}
            except Exception as e:
                err = str(e)
                if connect_string in err:
                    err = err.replace(connect_string, "[sanitized]")
                err = _strip_password(err)
                return {"success": False, "error": err}

        return self._dispatcher.call(_do)

    def unlink_table(self, name: str) -> dict:
        """Unlink (delete) a linked table from the database."""
        if not self._dispatcher.is_connected():
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            try:
                self._dispatcher.current_db.TableDefs.Delete(name)
                return {"success": True}
            except Exception as e:
                return {"success": False, "error": _strip_password(str(e))}

        return self._dispatcher.call(_do)

    # -------------------------------------------------------------------------- #
    # Salvage — recovery of tables from a corrupt database
    # -------------------------------------------------------------------------- #

    def salvage_tables(
        self,
        corrupt_path: str,
        path: str,
        engine: Any = None,
    ) -> list[dict[str, Any]]:
        """Recover tables from a corrupt .accdb/.mdb into a fresh database.

        Args:
            corrupt_path: Path to the damaged database file.
            path: Path to the freshly-created target database.
            engine: Optional DAO engine instance; if None, creates one via COM.

        Returns:
            A list of per-table result dicts with keys:
            - ``table_name``: name of the source table
            - ``salvaged_rows``: rows successfully copied (0 if table unreadable)
            - ``error``: None on success, error string on failure

        """

        def _do(engine: Any) -> list[dict[str, Any]]:
            import win32com.client  # type: ignore[import-not-found]

            if engine is None:
                actual_engine = win32com.client.Dispatch("DAO.DBEngine.120")
            else:
                actual_engine = engine
            table_reports: list[dict[str, Any]] = []

            # Open corrupt database
            try:
                corrupt_db = actual_engine.OpenDatabase(corrupt_path)
            except Exception as e:
                return [{"table_name": "<init>", "salvaged_rows": 0, "error": f"Cannot open corrupt database: {e}"}]

            # Open fresh database
            try:
                fresh_db = actual_engine.OpenDatabase(path)
            except Exception as e:
                corrupt_db.Close()
                return [{"table_name": "<init>", "salvaged_rows": 0, "error": f"Cannot open fresh database: {e}"}]

            try:
                for table_def in corrupt_db.TableDefs:
                    tname = table_def.Name
                    if tname.startswith("MSys"):
                        continue
                    src_rs = None
                    dst_rs = None
                    try:
                        # Read source rows via recordset
                        src_rs = corrupt_db.OpenRecordset(f"SELECT * FROM [{tname}]", 2)
                        field_count = src_rs.Fields.Count
                        field_definitions = [
                            (
                                src_rs.Fields(i).Name,
                                src_rs.Fields(i).Type,
                                src_rs.Fields(i).Size,
                            )
                            for i in range(field_count)
                        ]
                        rows: list[list[Any]] = []
                        while not src_rs.EOF:
                            rows.append([src_rs.Fields(i).Value for i in range(field_count)])
                            src_rs.MoveNext()

                        if not rows:
                            table_reports.append({
                                "table_name": tname,
                                "salvaged_rows": 0,
                                "error": None,
                            })
                            continue

                        # Build DDL from FieldDefs captured from the source read.
                        ddl_parts = []
                        for fname_value, ftype, fsize in field_definitions:
                            fname = f"[{fname_value}]"

                            # Map DAO type to Access DDL
                            if ftype == 1:  # YESNO
                                col_ddl = f"{fname} YESNO"
                            elif ftype == 2:  # BYTE
                                col_ddl = f"{fname} BYTE"
                            elif ftype == 3:  # INTEGER
                                col_ddl = f"{fname} INTEGER"
                            elif ftype == 4:  # LONG
                                col_ddl = f"{fname} LONG"
                            elif ftype == 5:  # CURRENCY
                                col_ddl = f"{fname} CURRENCY"
                            elif ftype == 6:  # SINGLE
                                col_ddl = f"{fname} SINGLE"
                            elif ftype == 7:  # DOUBLE
                                col_ddl = f"{fname} DOUBLE"
                            elif ftype == 8:  # DATETIME
                                col_ddl = f"{fname} DATETIME"
                            elif ftype == 10:  # TEXT
                                col_ddl = f"{fname} TEXT({fsize})" if fsize else f"{fname} TEXT"
                            elif ftype == 11:  # LONGBINARY
                                col_ddl = f"{fname} LONGBINARY"
                            elif ftype == 12:  # MEMO
                                col_ddl = f"{fname} MEMO"
                            elif ftype == 15:  # GUID
                                col_ddl = f"{fname} GUID"
                            elif ftype == 20:  # DECIMAL
                                col_ddl = f"{fname} DECIMAL(28,8)"
                            else:
                                col_ddl = f"{fname} MEMO"
                            ddl_parts.append(col_ddl)

                        create_sql = f"CREATE TABLE [{tname}] ({', '.join(ddl_parts)})"
                        try:
                            fresh_db.Execute(create_sql)
                        except Exception as create_err:
                            table_reports.append({
                                "table_name": tname,
                                "salvaged_rows": 0,
                                "error": f"CREATE TABLE failed: {create_err}",
                            })
                            continue

                        # Insert rows via DAO recordset
                        dst_rs = fresh_db.OpenRecordset(tname, 2)  # dbOpenDynaset
                        salvaged = 0
                        insert_errors: list[str] = []
                        for row in rows:
                            try:
                                dst_rs.AddNew()
                                for i, val in enumerate(row):
                                    dst_rs.Fields(i).Value = val
                                dst_rs.Update()
                                salvaged += 1
                            except Exception as insert_err:
                                insert_errors.append(str(insert_err))
                                try:
                                    dst_rs.CancelUpdate()
                                except Exception:
                                    pass

                        err_msg = "; ".join(insert_errors[:3]) if insert_errors else None
                        table_reports.append({
                            "table_name": tname,
                            "salvaged_rows": salvaged,
                            "error": err_msg,
                        })
                    except Exception as table_err:
                        table_reports.append({
                            "table_name": tname,
                            "salvaged_rows": 0,
                            "error": str(table_err),
                        })
                    finally:
                        for recordset in (src_rs, dst_rs):
                            if recordset is not None:
                                try:
                                    recordset.Close()
                                except Exception:
                                    pass
            finally:
                try:
                    corrupt_db.Close()
                except Exception:
                    pass
                try:
                    fresh_db.Close()
                except Exception:
                    pass

            return table_reports

        # Use the dispatcher's call mechanism to support test injection
        return self._dispatcher.call(_do, engine=engine)
