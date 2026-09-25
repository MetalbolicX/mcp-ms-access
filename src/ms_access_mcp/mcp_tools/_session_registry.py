"""Track per-call use cases so server and disconnect teardown can close them."""

from __future__ import annotations

import logging
import weakref
from typing import TypeVar

_Session = TypeVar("_Session")


_log = logging.getLogger(__name__)


class SessionRegistry:
    """Weakly track use cases and close active database sessions best-effort."""

    def __init__(self) -> None:
        self._sessions: weakref.WeakSet[object] = weakref.WeakSet()

    def track(self, session: _Session) -> _Session:
        self._sessions.add(session)
        return session

    def close_all(self) -> None:
        for session in list(self._sessions):
            try:
                close_database = getattr(session, "close_database", None)
                if callable(close_database):
                    close_database()
            except Exception:
                _log.debug("Error closing tracked use case", exc_info=True)
        self._sessions.clear()

    def close_for_path(self, path: str) -> None:
        """Close tracked sessions for a path when their use case exposes one."""
        for session in list(self._sessions):
            session_path = getattr(session, "_db_path", None)
            if session_path == path:
                try:
                    close_database = getattr(session, "close_database", None)
                    if callable(close_database):
                        close_database()
                except Exception:
                    _log.debug("Error closing tracked use case for %s", path, exc_info=True)
                self._sessions.discard(session)


registry = SessionRegistry()


def close_all_sessions() -> None:
    registry.close_all()


def close_sessions_for_path(path: str) -> None:
    registry.close_for_path(path)
