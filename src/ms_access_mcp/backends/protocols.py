"""Structural Protocols for backend consumers.

These protocols capture what each consumer actually uses from ComDispatcher,
avoiding the need for test fakes to inherit from the concrete class.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


# ComDispatcher slots actually used by VersioningBackend._check_connected (line 48)
class VersioningDispatcherProtocol(Protocol):
    """What VersioningBackend actually needs from a dispatcher."""

    _started: bool

    @property
    def access_app(self) -> Any:
        """Return the Access.Application COM object."""
        ...

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute fn on the STA thread, returning the result."""
        ...


# ComDispatcher slots actually used by AccessAdapter (access.py)
class AccessDispatcherProtocol(Protocol):
    """What AccessAdapter actually needs from a dispatcher.

    Covers properties and methods called directly by AccessAdapter.
    Internal (_xxx) attributes are included because AccessAdapter
    assigns to them directly (e.g., _access_app, _current_db).
    """

    # Internal state — written directly by AccessAdapter
    _started: bool
    _access_app: Any | None
    _access_pid: int | None
    _current_db: Any | None
    _ado_conn: Any | None
    _db_path: str | None

    # Properties accessed by AccessAdapter
    @property
    def access_app(self) -> Any:
        """Return the Access.Application COM object."""
        ...

    @property
    def current_db(self) -> Any:
        """Return the current DAO Database object."""
        ...

    # Methods called by AccessAdapter
    def start(self) -> None:
        """Start the STA dispatcher thread."""
        ...

    def set_db_path(self, db_path: str) -> None:
        """Set the database path."""
        ...

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute fn on the STA thread, returning the result."""
        ...

    def shutdown(self) -> None:
        """Signal the dispatcher thread to stop and clean up COM objects."""
        ...

    def capture_access_pid(self) -> None:
        """Record the Access process PID for the post-shutdown liveness kill net."""
        ...


# ComDispatcher slots actually used by SchemaInspector
class SchemaDispatcherProtocol(Protocol):
    """What SchemaInspector actually needs from a dispatcher.

    Covers properties and methods called directly by SchemaInspector via
    ComDispatcher: is_connected, current_db, access_app, call.
    """

    _started: bool

    @property
    def access_app(self) -> Any:
        """Return the Access.Application COM object."""
        ...

    @property
    def current_db(self) -> Any:
        """Return the current DAO Database object."""
        ...

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute fn on the STA thread, returning the result."""
        ...

    def is_connected(self) -> bool:
        """Return True if the dispatcher is connected."""
        ...


@runtime_checkable
class CrudBackendProtocol(Protocol):
    """Structural protocol for backends that expose the CRUD surface.

    All three backends (ADO, ODBC, DAO) expose insert_data, update_data,
    delete_data, and execute_query with compatible signatures.
    """

    def execute_query(
        self,
        sql: str,
        params: list[Any] | tuple[Any, ...] | None = None,
        limit: int | None = None,
    ) -> Any:
        """Execute a SELECT query and return results.

        ``params=None`` or an empty sequence runs ``sql`` unchanged; a non-empty
        sequence binds its values (a ``None`` element binds as NULL). ``limit``
        caps returned rows where the backend supports it.
        """
        ...

    def insert_data(self, table_name: str, data: dict | list[dict]) -> Any:
        """Insert rows into a table."""
        ...

    def update_data(
        self, table_name: str, set_dict: dict, where_dict: dict | None = None
    ) -> Any:
        """Update rows in a table."""
        ...

    def delete_data(self, table_name: str, where_dict: dict | None = None) -> Any:
        """Delete rows from a table."""
        ...
