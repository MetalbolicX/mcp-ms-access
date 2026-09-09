"""DAO SQL helper functions — single source for SQL formatting, sanitization, and DDL.

These five helpers are deduplicated from the oracle's dao.py and wincom.py:
  _format_dao_value   (dao:495 vs wincom:321)
  _sanitize_where_string (dao:520 vs wincom:93)
  _access_sql_type     (dao:536 vs wincom:1839)
  _alter_table_add_column (dao:1079 vs wincom:1774)
  _strip_password      (dao:1172 vs wincom:1243)

These are the maintained copies in the oracle.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# -------------------------------------------------------------------------- #
# SQL injection allowlist — shared by DAO write operations
# -------------------------------------------------------------------------- #

_WHERE_ALLOWLIST_RE = r"^[\w\s\.\,\=\<\>\(\)\'\"\-%]+$"

_DANGEROUS_WHERE_PATTERNS = (
    r"--",  # SQL single-line comment
    r"/\*",  # SQL block comment start
    r"\bDROP\b",
    r"\bDELETE\b",
    r"\bINSERT\b",
    r"\bUPDATE\b",
    r"\bALTER\b",
    r"\bCREATE\b",
    r"\bTRUNCATE\b",
    r"\bEXEC\b",
    r"\bEXECUTE\b",
    r"\bUNION\b",
    r"^\s*OR\s+\d+\s*=\s*\d+",
)


def _format_dao_value(val: object) -> str:
    """Format a Python value for inline use in DAO SQL.

    DAO.Execute does NOT support ``?`` parameter placeholders the way
    ADO does, so values are formatted as SQL literals:

    * ``None`` → ``NULL``
    * ``bool`` → ``-1`` (True) or ``0`` (False) — Access convention
    * ``int`` / ``float`` → ``str(val)``
    * ``datetime`` → ``#YYYY-MM-DD HH:MM:SS#`` (Access date literal)
    * ``str`` → single-quoted with internal ``'`` doubled
    """
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "-1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, datetime):
        return f"#{val.strftime('%Y-%m-%d %H:%M:%S')}#"
    s = str(val).replace("'", "''")
    return f"'{s}'"


def _sanitize_where_string(where_str: str) -> str | None:
    """Validate a raw WHERE string against the SQL injection allowlist.

    Returns the validated string if it passes, or ``None`` if it
    contains characters or keywords outside the policy. The caller
    is responsible for turning ``None`` into a structured error
    response.
    """
    if not re.match(_WHERE_ALLOWLIST_RE, where_str):
        return None
    for pattern in _DANGEROUS_WHERE_PATTERNS:
        if re.search(pattern, where_str, re.IGNORECASE):
            return None
    return where_str


def _access_sql_type(access_type: str, size: int = 255) -> str:
    """Map an Access type name to its Jet SQL DDL string."""
    type_map = {
        "Text": f"VARCHAR({size})",
        "Long Integer": "INTEGER",
        "Integer": "SMALLINT",
        "Byte": "BYTE",
        "Currency": "MONEY",
        "Single": "SINGLE",
        "Double": "DOUBLE",
        "Date/Time": "DATETIME",
        "Memo": "MEMO",
        "Boolean": "BIT",
        "Binary": "BINARY",
        "GUID": "GUID",
        "Big Integer": "BIGINT",
        "Unsigned Byte": "BYTE",
        "Unsigned Integer": "INTEGER",
        "Unsigned Long Integer": "INTEGER",
        "Decimal": "DECIMAL",
        "Counter": "COUNTER",
        "AutoNumber": "COUNTER",
    }
    return type_map.get(access_type, "VARCHAR(255)")


def _alter_table_add_column(
    db: Any,
    table_name: str,
    params: dict,
) -> dict:
    """Execute ``ALTER TABLE ADD COLUMN`` via DAO DDL."""
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

    sql = f"ALTER TABLE [{table_name}] ADD COLUMN {col_def}"
    db.Execute(sql, 128)  # DAO_DB_FAIL_ON_ERROR
    return {"success": True}


def _strip_password(connect_string: str) -> str:
    """Strip PWD=... from a DAO connect string.

    Uses a simple regex approach to remove password from ODBC and other
    connect strings. This prevents passwords from being persisted in
    linked table definitions.
    """
    import re

    # Strip PWD=... (handles both ;PWD= and PWD= forms)
    stripped = re.sub(r"(?<=[;])PWD=[^;]*", "", connect_string)
    stripped = re.sub(r"\bPWD=[^;]*", "", stripped)
    # Clean up leading/trailing semicolons
    return stripped.strip(";")
