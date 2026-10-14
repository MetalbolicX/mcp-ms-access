"""Engine read-only open seam for the documentation-inspection path.

The documentation-inspect connection must open the Access database engine-level
read-only (DAO ``OpenDatabase(..., read_only=True)``). Every other tool keeps its
existing read-write open semantics.

These tests pin the ``read_only`` argument at the DAO boundary through a
recording dispatcher double — the same structural seam used by
``tests/unit/test_backend_dao.py`` (``open_dao_database`` records
``(db_path, password, read_only)``). The double inherits the production
``reopen_dao_database_read_only`` so the assertions prove production behavior
end to end rather than restating the double.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from ms_access_mcp.application.documentation_inspect import DaoInspectionPort
from ms_access_mcp.backends.access import AccessAdapter
from ms_access_mcp.backends.dao import DaoAdapter
from ms_access_mcp.backends.dispatcher import ComDispatcher


class RecordingDispatcher(ComDispatcher):
    """ComDispatcher double: records opens, holds a fake handle, no STA thread."""

    def __init__(self) -> None:
        super().__init__()
        self.open_calls: list[tuple[str, str, bool]] = []

    def start(self) -> None:
        return None

    def open_dao_database(self, db_path: str, password: str = "", read_only: bool = False) -> Any:
        self.open_calls.append((db_path, password, read_only))
        self._db_path = db_path
        self._db_password = password
        self._current_db = object()
        return self._current_db

    def close_dao_database(self) -> None:
        self._current_db = None

    def shutdown(self) -> None:
        return None

    def mark_unhealthy(self) -> None:
        return None


def test_documentation_inspection_open_requests_engine_read_only() -> None:
    """The inspection path reopens the database with read_only=True at the DAO boundary."""
    dispatcher = RecordingDispatcher()
    adapter = DaoAdapter("C:\\db\\core.accdb", dispatcher=dispatcher)
    adapter.connect()  # regular tool open path reused by DatabaseUseCases
    assert dispatcher.open_calls == [("C:\\db\\core.accdb", "", False)]

    from ms_access_mcp.application import documentation_inspect

    open_inspection_port = getattr(documentation_inspect, "open_inspection_port", None)
    assert open_inspection_port is not None, (
        "the documentation-inspect path must request an engine read-only open "
        f"(recorded open_dao_database calls: {dispatcher.open_calls})"
    )
    port = open_inspection_port(dispatcher)

    assert isinstance(port, DaoInspectionPort)
    assert dispatcher.open_calls == [
        ("C:\\db\\core.accdb", "", False),
        ("C:\\db\\core.accdb", "", True),
    ]


def test_reopen_read_only_reuses_recorded_password() -> None:
    """A read-only reopen keeps the credentials of the original open (encrypted DBs)."""
    dispatcher = RecordingDispatcher()
    dispatcher.open_dao_database("C:\\db\\secure.accdb", password="s3cret", read_only=False)

    dispatcher.reopen_dao_database_read_only()

    assert dispatcher.open_calls[-1] == ("C:\\db\\secure.accdb", "s3cret", True)


def test_reopen_read_only_refuses_when_connection_is_not_live() -> None:
    """N1: reopen after close must refuse instead of silently resurrecting a handle.

    ``close_dao_database`` retains ``_db_path``, so the old ``_db_path is None``
    gate let a stale path reopen a fresh read-only connection. The gate must read
    live connection state.
    """
    dispatcher = RecordingDispatcher()
    adapter = DaoAdapter("C:\\db\\core.accdb", dispatcher=dispatcher)
    adapter.connect()
    dispatcher.close_dao_database()

    assert dispatcher.is_connected() is False
    with pytest.raises(RuntimeError):
        dispatcher.reopen_dao_database_read_only()
    assert dispatcher.is_connected() is False
    assert dispatcher.open_calls == [("C:\\db\\core.accdb", "", False)]


class _DirectCallDispatcher(ComDispatcher):
    """ComDispatcher double that runs ``call`` inline and records DAO opens."""

    def __init__(self) -> None:
        super().__init__()
        self.open_calls: list[tuple[str, str, bool]] = []

    def start(self) -> None:
        return None

    def call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    def shutdown(self) -> None:
        return None

    def capture_access_pid(self) -> None:
        return None

    def open_dao_database(self, db_path: str, password: str = "", read_only: bool = False) -> Any:
        self.open_calls.append((db_path, password, read_only))
        self._db_path = db_path
        self._db_password = password
        self._current_db = object()
        return self._current_db

    def close_dao_database(self) -> None:
        self._current_db = None


class _FakeCurrentProject:
    Connection = object()


class _FakeAccessApp:
    def __init__(self) -> None:
        self.Visible = True
        self.opened: tuple[str, bool, str] | None = None

    def OpenCurrentDatabase(self, target: str, exclusive: bool, password: str) -> None:
        self.opened = (target, exclusive, password)

    def CurrentDb(self) -> Any:
        return object()

    CurrentProject = _FakeCurrentProject()


def _install_fake_access_app(monkeypatch: pytest.MonkeyPatch) -> _FakeAccessApp:
    app = _FakeAccessApp()
    client = types.ModuleType("win32com.client")
    client.Dispatch = lambda _progid: app  # type: ignore[attr-defined]
    win32com = types.ModuleType("win32com")
    win32com.client = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    monkeypatch.setattr(ComDispatcher, "_dismiss_access_dialogs", staticmethod(lambda: None))
    return app


def test_access_com_connect_records_password_for_read_only_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N2: the Access/COM backend records the password for the read-only reopen.

    The COM path opened via ``Access.Application`` (which carried the password) but
    recorded only the path, so the read-only reopen used ``password=''`` and an
    encrypted database failed. Recording the credential restores parity with DAO.
    """
    app = _install_fake_access_app(monkeypatch)
    dispatcher = _DirectCallDispatcher()
    adapter = AccessAdapter("C:\\db\\secure.accdb")
    adapter._dispatcher = dispatcher

    adapter.connect(password="s3cret")

    assert app.opened == ("C:\\db\\secure.accdb", False, "s3cret")
    dispatcher.reopen_dao_database_read_only()
    assert dispatcher.open_calls[-1] == ("C:\\db\\secure.accdb", "s3cret", True)


def test_access_com_connect_keeps_empty_password_for_read_only_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRESERVE: an unencrypted COM open keeps ``password=''`` on the reopen."""
    app = _install_fake_access_app(monkeypatch)
    dispatcher = _DirectCallDispatcher()
    adapter = AccessAdapter("C:\\db\\plain.accdb")
    adapter._dispatcher = dispatcher

    adapter.connect()

    assert app.opened == ("C:\\db\\plain.accdb", False, "")
    dispatcher.reopen_dao_database_read_only()
    assert dispatcher.open_calls[-1] == ("C:\\db\\plain.accdb", "", True)
