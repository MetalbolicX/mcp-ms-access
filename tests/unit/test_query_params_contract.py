"""Cross-layer contract tests for query parameter handling.

These tests drive the approved query-parameter contract through the public
seams only:

* ``DatabaseUseCases.query_data`` — a failed backend ``QueryResult`` must become
  ``Err`` (never a successful empty ``Ok``), with a sanitized message.
* CLI ``query`` — the rejection must reach the process exit code and stderr.
* MCP ``query_data`` — the rejection must surface as ``isError`` true.

Backends are faked at the connection/driver boundary (a fake DAO dispatcher and
fake adapter), never by stubbing ``DatabaseUseCases`` itself, so the real
facade, dispatch and result-envelope code all run.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from ms_access_mcp.application.database import DatabaseUseCases, Err, Ok
from ms_access_mcp.backends.dao import DaoAdapter
from ms_access_mcp.models import QueryResult

# ---------------------------------------------------------------------------
# Fake DAO boundary (dispatcher + Database/Recordset graph)
# ---------------------------------------------------------------------------


class _FakeFieldCollection:
    def __init__(self, recordset: _FakeRecordset) -> None:
        self._rs = recordset

    @property
    def Count(self) -> int:
        return len(self._rs._keys)

    def __call__(self, index: int) -> Any:
        name = self._rs._keys[index]
        row = self._rs._rows[self._rs._pos] if self._rs._pos < len(self._rs._rows) else {}
        return SimpleNamespace(Name=name, Value=row.get(name))


class _FakeRecordset:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._keys = list(rows[0].keys()) if rows else []
        self._pos = 0

    @property
    def EOF(self) -> bool:
        return self._pos >= len(self._rows)

    @property
    def Fields(self) -> _FakeFieldCollection:
        return _FakeFieldCollection(self)

    def MoveNext(self) -> None:
        self._pos += 1

    def Close(self) -> None:
        pass


class _FakeDaoDb:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.opened_sql: list[str] = []

    def OpenRecordset(self, sql: str) -> _FakeRecordset:
        self.opened_sql.append(sql)
        return _FakeRecordset(self.rows)


class _FakeDaoDispatcher:
    """Minimal ComDispatcher stand-in: only the slots DaoAdapter uses."""

    def __init__(self, db: _FakeDaoDb | None = None) -> None:
        self._db = db

    def start(self) -> None:
        pass

    def open_dao_database(self, db_path: str, password: str = "", read_only: bool = False) -> _FakeDaoDb:
        if self._db is None:
            self._db = _FakeDaoDb()
        return self._db

    def close_dao_database(self) -> None:
        self._db = None

    def shutdown(self) -> None:
        pass

    def call(self, fn: Any) -> Any:
        return fn()

    @property
    def current_db(self) -> _FakeDaoDb:
        assert self._db is not None, "dispatcher is not connected"
        return self._db

    def is_connected(self) -> bool:
        return self._db is not None

    def mark_unhealthy(self) -> None:
        pass


def _dao_backend(rows: list[dict[str, Any]] | None = None) -> DaoAdapter:
    """Build a real DaoAdapter over the fake dispatcher boundary."""
    return DaoAdapter("placeholder.accdb", dispatcher=_FakeDaoDispatcher(_FakeDaoDb(rows)))


def _wire_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, adapter: DaoAdapter) -> str:
    """Point DatabaseUseCases at ``adapter`` without touching use-case code."""
    db_path = tmp_path / "query_params.accdb"
    db_path.touch()
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    monkeypatch.setattr(
        "ms_access_mcp.application.database.BackendSelector.get_adapter",
        lambda **_kwargs: adapter,
    )
    monkeypatch.setattr("ms_access_mcp.credentials.resolve_password", lambda _path: None)
    return str(db_path)


# ---------------------------------------------------------------------------
# DatabaseUseCases.query_data — failure propagation
# ---------------------------------------------------------------------------


class _FailingQueryAdapter:
    def __init__(self, error: str) -> None:
        self.error = error
        self.calls: list[tuple[str, Any, Any]] = []

    def execute_query(self, sql: str, params: Any = None, limit: Any = None) -> QueryResult:
        self.calls.append((sql, params, limit))
        return QueryResult(success=False, rows=[], count=0, columns=[], error=self.error)


class _RecordingQueryAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, Any]] = []

    def execute_query(self, sql: str, params: Any = None, limit: Any = None) -> QueryResult:
        self.calls.append((sql, params, limit))
        return QueryResult(success=True, rows=[{"id": 1}], count=1, columns=["id"])


def _db_with(adapter: Any) -> DatabaseUseCases:
    db = DatabaseUseCases()
    db._connections["default"] = adapter
    return db


def test_query_data_propagates_failed_query_result_as_err():
    adapter = _FailingQueryAdapter(
        "DAO backend does not support query parameters; use the ODBC backend for parameterized queries"
    )
    result = _db_with(adapter).query_data("SELECT 1 WHERE id = ?", params=["x"])

    assert isinstance(result, Err)
    assert "ODBC" in result.message
    assert adapter.calls == [("SELECT 1 WHERE id = ?", ["x"], None)]


def test_query_data_failed_query_result_is_password_safe():
    adapter = _FailingQueryAdapter("connect failed;PWD=sekret;DBQ=x")
    result = _db_with(adapter).query_data("SELECT 1")

    assert isinstance(result, Err)
    assert "sekret" not in result.message
    assert "PWD=" not in result.message


def test_query_data_successful_result_still_maps_to_ok():
    result = _db_with(_RecordingQueryAdapter()).query_data("SELECT id FROM Users", limit=5)

    assert isinstance(result, Ok)
    assert result.data == [{"id": 1}]
    assert result.rowcount == 1


# ---------------------------------------------------------------------------
# CLI seam — DAO rejection must reach exit code + stderr
# ---------------------------------------------------------------------------


def test_cli_query_reports_dao_param_rejection(monkeypatch, tmp_path, capsys):
    from ms_access_mcp import cli

    db_path = _wire_backend(monkeypatch, tmp_path, _dao_backend())

    exit_code = cli.main(["query", "SELECT 1 WHERE id = ?", "--param", "x", "--db", db_path])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "ODBC" in captured.err
    assert captured.out == ""


def test_cli_query_preserves_successful_no_param_query(monkeypatch, tmp_path, capsys):
    from ms_access_mcp import cli

    db_path = _wire_backend(monkeypatch, tmp_path, _dao_backend([{"id": 7}]))

    exit_code = cli.main(["query", "SELECT id FROM t", "--db", db_path])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Success" in captured.out
    assert captured.err == ""


# ---------------------------------------------------------------------------
# MCP seam — DAO rejection must surface as isError
# ---------------------------------------------------------------------------


def test_mcp_query_data_reports_dao_param_rejection(monkeypatch, tmp_path):
    from ms_access_mcp.mcp_tools import core

    db_path = _wire_backend(monkeypatch, tmp_path, _dao_backend())

    result = core.query_data(db_path, "SELECT 1 WHERE id = ?", params=["x"])

    assert result.isError is True
    payload = json.loads(result.content[0].text)
    assert payload["success"] is False
    assert "ODBC" in payload["error"]


def test_mcp_query_data_preserves_successful_no_param_query(monkeypatch, tmp_path):
    from ms_access_mcp.mcp_tools import core

    db_path = _wire_backend(monkeypatch, tmp_path, _dao_backend([{"id": 3}]))

    result = core.query_data(db_path, "SELECT id FROM t")

    assert result.isError is False
    payload = json.loads(result.content[0].text)
    assert payload["success"] is True
    assert payload["data"] == [{"id": 3}]
