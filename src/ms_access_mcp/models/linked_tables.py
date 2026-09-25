"""Pydantic param models for linked-table tool boundaries (mcp_tools/ddl.py).

Field defaults mirror the raw ``p.get`` pulls they replace — presence
semantics are preserved exactly (e.g. refresh's connect_string may be
absent → None).
"""

from __future__ import annotations

from pydantic import BaseModel

__all__ = [
    "CreateLinkedTableParams",
    "RefreshLinkedTableParams",
    "UnlinkTableParams",
    "UpsertLinkedTableParams",
]


class CreateLinkedTableParams(BaseModel):
    """Params for the create_linked_table tool boundary.

    Attributes:
        name: Local name for the linked table.
        source_table: Remote table name.
        connect_string: Literal ODBC connect string (or empty to use connect_ref).
        connect_ref: Named credential profile reference.
        connection_name: Server-side connection profile name.
    """

    name: str = ""
    source_table: str = ""
    connect_string: str = ""
    connect_ref: str = ""
    connection_name: str = "default"


class RefreshLinkedTableParams(BaseModel):
    """Params for the refresh_linked_table tool boundary.

    Attributes:
        name: Local name of the linked table.
        connect_string: Optional new connect string; None keeps the existing one.
        connect_ref: Named credential profile reference.
        connection_name: Server-side connection profile name.
    """

    name: str = ""
    connect_string: str | None = None
    connect_ref: str = ""
    connection_name: str = "default"


class UnlinkTableParams(BaseModel):
    """Params for the unlink_table tool boundary.

    Attributes:
        name: Local name of the linked table.
        connection_name: Server-side connection profile name.
    """

    name: str = ""
    connection_name: str = "default"


class UpsertLinkedTableParams(BaseModel):
    """Params for the upsert_linked_table tool boundary (create shape).

    Attributes:
        name: Local name for the linked table.
        source_table: Remote table name.
        connect_string: Literal ODBC connect string (or empty to use connect_ref).
        connect_ref: Named credential profile reference.
        connection_name: Server-side connection profile name.
    """

    name: str = ""
    source_table: str = ""
    connect_string: str = ""
    connect_ref: str = ""
    connection_name: str = "default"
