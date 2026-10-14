"""CLI contract for the documentation-inspect error envelope.

B1: ``documentation inspect`` must map every failure of the inspection open to the
same ``Err`` envelope as the rest of the command. A read-only reopen failure
(transient DAO locks are real in this repository) must not escape as an unhandled
traceback.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import patch

from ms_access_mcp import cli
from ms_access_mcp.application.database import Ok


class _RaisingReopenDispatcher:
    """Connected dispatcher whose engine read-only reopen always fails."""

    def __init__(self) -> None:
        self._current_db = object()
        self._db_path = "C:\\db\\core.accdb"
        self._db_password = ""
        self.reopen_calls = 0

    def is_connected(self) -> bool:
        return True

    def reopen_dao_database_read_only(self) -> Any:
        self.reopen_calls += 1
        raise RuntimeError("reopen failed: database locked by another session")


class _OkDispatcher:
    """Connected dispatcher whose read-only reopen succeeds."""

    current_db = object()

    def is_connected(self) -> bool:
        return True

    def reopen_dao_database_read_only(self) -> Any:
        return object()

    def call(self, operation: Any) -> Any:
        return operation()


class _FakeAdapter:
    def __init__(self, dispatcher: Any) -> None:
        self._dispatcher = dispatcher


class _FakeDatabase:
    def __init__(self, dispatcher: Any) -> None:
        self._adapter = _FakeAdapter(dispatcher)
        self.closed = False

    def open_database(self, _path: str) -> Ok:
        return Ok(data={"path": _path})

    def close_database(self) -> Ok:
        self.closed = True
        return Ok()


class _FakeEvidence:
    status = "complete"

    def to_dict(self) -> dict[str, Any]:
        return {"tables": []}


class _FakePort:
    def close(self) -> None:
        return None


class _FakeInspectService:
    def inspect(self, _port: Any, *, tables: Any, queries: Any) -> _FakeEvidence:
        del tables, queries
        return _FakeEvidence()


def _run_cli(argv: list[str]) -> int:
    with patch.object(sys, "argv", ["ms-access-mcp", *argv]):
        return cli.main()


def test_inspect_reopen_failure_returns_err_envelope_without_raising(capsys) -> None:
    """A failing read-only reopen yields the formatted Err envelope, not a traceback."""
    dispatcher = _RaisingReopenDispatcher()
    database = _FakeDatabase(dispatcher)
    with patch.object(cli, "DatabaseUseCases", return_value=database):
        result = _run_cli(["documentation", "inspect", "--db", "core.accdb"])

    captured = capsys.readouterr()
    assert result == 1
    assert dispatcher.reopen_calls == 1
    assert "Error: reopen failed: database locked by another session" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
    assert database.closed is True


def test_inspect_success_still_returns_ok_envelope(capsys) -> None:
    """PRESERVE: a successful inspection open keeps the Ok envelope and exit 0."""
    dispatcher = _OkDispatcher()
    database = _FakeDatabase(dispatcher)
    with (
        patch.object(cli, "DatabaseUseCases", return_value=database),
        patch.object(cli, "open_inspection_port", return_value=_FakePort()),
        patch.object(cli, "DocumentationInspectService", _FakeInspectService),
    ):
        result = _run_cli(["documentation", "inspect", "--db", "core.accdb"])

    captured = capsys.readouterr()
    assert result == 0
    assert "Traceback" not in captured.err
    assert database.closed is True
