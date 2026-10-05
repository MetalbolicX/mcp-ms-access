"""Track per-call use cases so server and disconnect teardown can close them."""

from __future__ import annotations

import logging
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from ..application.database import DatabaseUseCases, Err

_Session = TypeVar("_Session")


_log = logging.getLogger(__name__)


class SessionOpenError(Exception):
    """An error result returned while opening a per-call database session."""

    def __init__(self, result: Err) -> None:
        super().__init__(result.message)
        self.result = result


class SessionRegistry:
    """Strongly track sessions until their explicit, idempotent teardown."""

    def __init__(self) -> None:
        self._sessions: set[object] = set()
        self._closed: weakref.WeakSet[object] = weakref.WeakSet()

    def track(self, session: _Session) -> _Session:
        self._sessions.add(session)
        return session

    @contextmanager
    def open_session(self, path: str, **uc_kwargs: Any) -> Iterator[DatabaseUseCases]:
        """Open and yield a database use case, closing and unregistering on exit."""
        session = DatabaseUseCases(**uc_kwargs)
        self.track(session)
        try:
            result = session.open_database(path)
            if isinstance(result, Err):
                raise SessionOpenError(result)
            yield session
        finally:
            self.close_session(session)

    def close_session(self, session: object) -> None:
        """Close a tracked session once, then release its strong registry reference."""
        if session in self._closed:
            self._sessions.discard(session)
            return
        self._sessions.discard(session)
        try:
            close_database = getattr(session, "close_database", None)
            if callable(close_database):
                close_database()
        except Exception:
            _log.debug("Error closing tracked use case", exc_info=True)
        finally:
            self._closed.add(session)

    def close_all(self) -> None:
        """Close a snapshot of all sessions; repeat calls are harmless."""
        for session in list(self._sessions):
            self.close_session(session)

    def snapshot(self) -> set[object]:
        """Return a strong snapshot for bounded invocation cleanup."""
        return set(self._sessions)

    def close_sessions_created_after(self, existing: set[object]) -> None:
        """Close sessions created during one bounded tool invocation."""
        for session in list(self._sessions - existing):
            self.close_session(session)

    def close_for_path(self, path: str) -> None:
        """Close tracked sessions for a path when their use case exposes one."""
        for session in list(self._sessions):
            if getattr(session, "_db_path", None) == path:
                self.close_session(session)


registry = SessionRegistry()


def close_all_sessions() -> None:
    registry.close_all()


def close_sessions_for_path(path: str) -> None:
    registry.close_for_path(path)
