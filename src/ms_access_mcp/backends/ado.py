"""ADO backend with direct ACE OLE DB connection and ANSI-92 SQL script execution.

Implements direct ACE OLE DB connection (Provider=Microsoft.ACE.OLEDB.12.0)
so scripts run WITHOUT MSACCESS.EXE.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ACE OLE DB provider string
ACE_PROVIDER = "Microsoft.ACE.OLEDB.12.0"


def _parse_script_lines(sql_script: str) -> list[str]:
    """Parse a SQL script into individual statements.

    Splits on semicolon boundaries, strips whitespace, and filters
    out empty lines and SQL comments (both comment-only lines and
    inline -- comments).
    """
    lines = sql_script.splitlines()
    # Remove comment-only lines (lines that are ONLY a SQL comment)
    non_comment = (
        re.sub(r"--.*$", "", line)  # strip inline -- comments
        for line in lines
        if not line.strip().startswith("--")
    )
    # Join, then split on semicolons
    text = " ".join(non_comment)
    statements = text.split(";")
    # Strip and filter empty
    return [s.strip() for s in statements if s.strip()]


class AdoAdapter:
    """ADO backend using direct ACE OLE DB connection (no MSACCESS.EXE required).

    Executes SQL scripts with ANSI-92 semantics preserved (supports
    DEFAULT, REFERENCES, NOW() etc. that DAO ANSI-97 rejects).
    """

    def __init__(self, db_path: str) -> None:
        """Initialize an ADO adapter for the specified database path."""
        self._db_path = db_path
        self._conn: Any = None
        self._connected = False

    def connect(self, db_path: str | None = None, password: str = "") -> None:
        """Open a direct ACE OLE DB connection without Access.Application."""
        import win32com.client  # type: ignore[import-not-found]

        target = db_path if db_path is not None else self._db_path
        provider = f"Provider={ACE_PROVIDER};Data Source={target};"
        if password:
            provider += f"Jet OLEDB:Database Password={password};"

        try:
            self._conn = win32com.client.Dispatch("ADODB.Connection")
            self._conn.Open(provider)
            self._connected = True
        except Exception:
            self._connected = False
            raise

    def disconnect(self) -> None:
        """Close the ADO connection."""
        if self._conn is not None:
            try:
                self._conn.Close()
            except Exception:
                pass
            self._conn = None
        self._connected = False

    def is_connected(self) -> bool:
        """Return whether the ADO connection is open."""
        return self._connected

    def execute_script(self, sql_script: str) -> dict:
        """Execute a SQL script containing multiple statements.

        Parses the script into individual statements and executes
        each one in order. Returns a summary of results.
        """
        if not self._connected or self._conn is None:
            return {"success": False, "error": "Not connected"}

        statements = _parse_script_lines(sql_script)
        results: list[dict] = []
        errors: list[str] = []

        for stmt in statements:
            if not stmt.strip():
                continue
            try:
                self._conn.Execute(stmt)
                results.append({"statement": stmt[:60], "success": True})
            except Exception as e:
                errors.append(str(e))
                results.append({"statement": stmt[:60], "success": False, "error": str(e)})

        return {
            "success": len(errors) == 0,
            "statements": len(statements),
            "results": results,
            "errors": errors,
        }

    def execute_query(self, sql: str, params: list | None = None) -> dict:
        """Execute a SELECT query and return results."""
        if not self._connected or self._conn is None:
            return {"success": False, "rows": [], "count": 0, "columns": [], "error": "Not connected"}

        try:
            rs = self._conn.Execute(sql)[0]
            if rs.EOF:
                rs.Close()
                return {"success": True, "rows": [], "count": 0, "columns": []}

            columns = [f.Name for f in rs.Fields]
            rows = []
            while not rs.EOF:
                row = {columns[i]: rs.Fields(i).Value for i in range(len(columns))}
                rows.append(row)
                rs.MoveNext()

            rs.Close()
            return {"success": True, "rows": rows, "count": len(rows), "columns": columns}
        except Exception as e:
            return {"success": False, "rows": [], "count": 0, "columns": [], "error": str(e)}

    def insert_data(self, table_name: str, data: dict | list[dict]) -> dict:
        """Insert rows via ADO."""
        if not self._connected or self._conn is None:
            return {"success": False, "error": "Not connected", "affected": 0}

        rows = data if isinstance(data, list) else [data]
        total = 0
        try:
            for row in rows:
                cols = list(row.keys())
                vals = list(row.values())
                placeholders = ", ".join(["?" for _ in vals])
                sql = f"INSERT INTO [{table_name}] ({', '.join(cols)}) VALUES ({placeholders})"
                self._conn.Execute(sql, vals)
                total += 1
            return {"success": True, "affected": total}
        except Exception as e:
            return {"success": False, "error": str(e), "affected": 0}

    def update_data(self, table_name: str, set_dict: dict, where_dict: dict | None = None) -> dict:
        """Update rows via ADO."""
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

            self._conn.Execute(sql, vals)
            return {"success": True, "affected": 1}
        except Exception as e:
            return {"success": False, "error": str(e), "affected": 0}

    def delete_data(self, table_name: str, where_dict: dict | None = None) -> dict:
        """Delete rows via ADO."""
        if not self._connected or self._conn is None:
            return {"success": False, "error": "Not connected", "affected": 0}

        try:
            sql = f"DELETE FROM [{table_name}]"
            vals = []
            if where_dict:
                where_clause = " AND ".join(f"[{k}] = ?" for k in where_dict.keys())
                sql += f" WHERE {where_clause}"
                vals = list(where_dict.values())

            self._conn.Execute(sql, vals)
            return {"success": True, "affected": 1}
        except Exception as e:
            return {"success": False, "error": str(e), "affected": 0}
