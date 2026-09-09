"""Hidden Access.Application adapter — minimal connect/teardown for plan 002.

Ports the connect/teardown handshake from oracle wincom.py:170-262:
  Dispatch("Access.Application"), Visible=False, OpenCurrentDatabase,
  and reuse dispatcher teardown (CloseCurrentDatabase + Quit + watchdog + PID kill).

Full UI operations come in plan 006.
"""

from __future__ import annotations

import logging
from typing import Any

from .dispatcher import ComDispatcher

logger = logging.getLogger(__name__)


class AccessAdapter:
    """Access.Application adapter with hidden-mode connect and dispatcher teardown.

    This adapter:
    - Opens Access.Application in hidden mode (Visible=False)
    - Opens the target database via OpenCurrentDatabase
    - Reuses ComDispatcher's _release_com_safe for teardown
    - Falls back to PID-scoped taskkill if Quit() times out
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._dispatcher = ComDispatcher()
        self._connected = False

    def connect(self, db_path: str | None = None, password: str = "") -> None:
        """Connect to Access.Application in hidden mode and open the database."""
        target = db_path if db_path is not None else self._db_path
        self._dispatcher.start()
        self._dispatcher.set_db_path(target)

        def _do() -> None:
            import win32com.client  # type: ignore[import-not-found]

            app = win32com.client.Dispatch("Access.Application")
            app.Visible = False
            app.OpenCurrentDatabase(target, False, password)
            self._dispatcher._access_app = app
            self._dispatcher._current_db = app.CurrentDb()
            self._dispatcher._ado_conn = app.CurrentProject.Connection
            self._dispatcher._db_path = target
            ComDispatcher._dismiss_access_dialogs()

        self._dispatcher.call(_do)
        self._connected = True

    def disconnect(self) -> None:
        """Disconnect via dispatcher teardown."""
        if not self._connected:
            return
        self._dispatcher.shutdown()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------ #
    # Basic Access object model passthrough (for plan 006)
    # ------------------------------------------------------------------ #

    def get_current_db(self) -> Any:
        """Return the current DAO Database object."""
        return self._dispatcher.current_db

    def get_current_project(self) -> Any:
        """Return the current Access Project object (stubbed for plan 006)."""
        return self._dispatcher.access_app.CurrentProject


def connect_hidden(db_path: str) -> AccessAdapter:
    """Convenience: create and connect an AccessAdapter in hidden mode.

    Returns the connected adapter. Caller must call disconnect() when done.
    """
    adapter = AccessAdapter(db_path)
    adapter.connect()
    return adapter
