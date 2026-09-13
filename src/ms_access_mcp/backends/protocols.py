"""Structural Protocols for backend consumers.

These protocols capture what each consumer actually uses from ComDispatcher,
avoiding the need for test fakes to inherit from the concrete class.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


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
