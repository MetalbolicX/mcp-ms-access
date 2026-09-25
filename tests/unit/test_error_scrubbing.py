from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from ms_access_mcp.application.database import DatabaseUseCases, Err
from ms_access_mcp.application.migration import MigrationUseCases
from ms_access_mcp.backends.dao import DaoAdapter
from ms_access_mcp.backends.dispatcher import ComDispatcher
from ms_access_mcp.backends.odbc import OdbcAdapter
from ms_access_mcp.doctor import (
    _check_access_com_probe,
    _check_ace_provider,
    _check_dao_engine,
    _check_deep_access,
    _check_odbc_driver,
    _check_temp_dir_writable,
)
from ms_access_mcp.safety import WritePolicy

MARKERS = ["PWD=sekret;DATABASE=x", "Password=sekret"]


class _Cursor:
    rowcount = 1
    description = None

    def execute(self, *_args):
        raise RuntimeError(self.message)

    def close(self):
        pass


class _OdbcConnection:
    def __init__(self, message):
        self.message = message

    def cursor(self):
        return _CursorWithError(self.message)

    def rollback(self):
        pass


class _CursorWithError(_Cursor):
    def __init__(self, message):
        self.message = message


@pytest.mark.parametrize("method,args", [
    ("execute_query", ("SELECT 1",)),
    ("insert_data", ("items", {"value": 1})),
    ("update_data", ("items", {"value": 1})),
    ("delete_data", ("items",)),
])
@pytest.mark.parametrize("marker", MARKERS)
def test_odbc_crud_scrubs_errors(method, args, marker):
    adapter = OdbcAdapter("unused")
    adapter._connected = True
    adapter._conn = _OdbcConnection(marker)
    result = getattr(adapter, method)(*args)
    assert result.success is False
    assert "sekret" not in result.error
    assert "PWD=" not in result.error and "Password=" not in result.error


@pytest.mark.parametrize("marker", MARKERS)
def test_dao_execute_query_scrubs_error(marker):
    class Dispatcher:
        current_db = SimpleNamespace(OpenRecordset=lambda _sql: (_ for _ in ()).throw(RuntimeError(marker)))

        def is_connected(self):
            return True

        def call(self, fn, engine=None):
            return fn()

    adapter = DaoAdapter("unused", dispatcher=cast(ComDispatcher, Dispatcher()))
    result = adapter.execute_query("SELECT 1")
    assert result.success is False
    assert "sekret" not in result.error
    assert "PWD=" not in result.error and "Password=" not in result.error


@pytest.mark.parametrize("error_type", [ImportError, Exception])
@pytest.mark.parametrize("marker", MARKERS)
def test_migration_connector_scrubs_error(monkeypatch, marker, error_type):
    import ms_access_mcp.application.migration as migration

    monkeypatch.setattr(migration, "get_connector", lambda *_: (_ for _ in ()).throw(error_type(marker)))
    result = MigrationUseCases()._get_connector("postgres", "unused")
    assert isinstance(result, Err)
    assert "sekret" not in result.message
    assert "PWD=" not in result.message and "Password=" not in result.message


@pytest.mark.parametrize("check,attribute", [
    (_check_access_com_probe, "Dispatch"),
    (_check_ace_provider, "Dispatch"),
    (_check_dao_engine, "Dispatch"),
    (_check_odbc_driver, "drivers"),
    (_check_deep_access, "Dispatch"),
    (_check_temp_dir_writable, "NamedTemporaryFile"),
])
@pytest.mark.parametrize("marker", MARKERS)
def test_doctor_scrubs_error(monkeypatch, check, attribute, marker):
    import ms_access_mcp.doctor as doctor

    monkeypatch.setattr(doctor.sys, "platform", "win32")
    if attribute == "NamedTemporaryFile":
        monkeypatch.setattr(doctor.tempfile, attribute, lambda **_: (_ for _ in ()).throw(RuntimeError(marker)))
    elif attribute == "drivers":
        import pyodbc
        monkeypatch.setattr(pyodbc, attribute, lambda: (_ for _ in ()).throw(RuntimeError(marker)))
    else:
        import pythoncom
        import win32com.client
        monkeypatch.setattr(pythoncom, "CoInitialize", lambda: None)
        monkeypatch.setattr(pythoncom, "CoUninitialize", lambda: None)
        monkeypatch.setattr(win32com.client, attribute, lambda *_: (_ for _ in ()).throw(RuntimeError(marker)))
    result = check()
    assert "sekret" not in result.message
    assert "PWD=" not in result.message and "Password=" not in result.message
    assert "sekret" not in result.details["error"]
    assert "PWD=" not in result.details["error"] and "Password=" not in result.details["error"]


@pytest.mark.parametrize("method,args", [
    ("create_linked_table", ("t", "s", "ODBC;DSN=x")),
    ("refresh_linked_table", ("t", "ODBC;DSN=x")),
    ("upsert_linked_table", ("t", "s", "ODBC;DSN=x")),
])
@pytest.mark.parametrize("marker", MARKERS)
def test_database_linked_table_scrubs_error(method, args, marker):
    class Adapter:
        def __getattr__(self, _name):
            return lambda *_args: (_ for _ in ()).throw(RuntimeError(marker))

    db = DatabaseUseCases(readonly=False)
    db._policy = WritePolicy(readonly=False)
    db._connections["default"] = Adapter()
    result = getattr(db, method)(*args, confirm=True)
    assert isinstance(result, Err)
    assert "sekret" not in result.message
    assert "PWD=" not in result.message and "Password=" not in result.message
