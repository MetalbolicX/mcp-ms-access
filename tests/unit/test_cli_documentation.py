"""CLI contract for the documentation-inspect error envelope.

B1: ``documentation inspect`` must map every failure of the supervised inspection
(including a read-only reopen failure inside the owned worker) to the same ``Err``
envelope as the rest of the command. A transient DAO lock is real in this repository, so
it must never escape as an unhandled traceback.
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import patch

from ms_access_mcp import cli
from ms_access_mcp.application.documentation_inspect import inspect_open_database


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

    def open_database(self, _path: str) -> Any:
        from ms_access_mcp.application.database import Ok

        return Ok(data={"path": _path})

    def close_database(self) -> Any:
        from ms_access_mcp.application.database import Ok

        self.closed = True
        return Ok()


class _FakeEvidence:
    status = "complete"

    def to_dict(self) -> dict[str, Any]:
        return {"tables": []}


def _run_cli(argv: list[str]) -> int:
    with patch.object(sys, "argv", ["ms-access-mcp", *argv]):
        return cli.main()


def test_inspect_open_database_reopen_failure_propagates_before_any_evidence() -> None:
    """The worker extraction seam keeps a failing read-only reopen visible, not silent."""
    dispatcher = _RaisingReopenDispatcher()
    database = _FakeDatabase(dispatcher)
    try:
        inspect_open_database(database, tables=None, queries=None)
    except RuntimeError as error:
        assert "database locked by another session" in str(error)
    else:  # pragma: no cover - the raise is the assertion
        raise AssertionError("a failing read-only reopen must not yield evidence")
    assert dispatcher.reopen_calls == 1


def test_inspect_worker_failure_returns_err_envelope_without_raising(capsys) -> None:
    """A failing worker inspection yields the formatted Err envelope, not a traceback."""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("reopen failed: database locked by another session")

    with patch.object(cli, "run_documentation_inspection", _boom):
        result = _run_cli(["documentation", "inspect", "--db", "core.accdb"])

    captured = capsys.readouterr()
    assert result == 1
    assert "Error: reopen failed: database locked by another session" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_inspect_success_still_returns_ok_envelope(capsys) -> None:
    """PRESERVE: a successful supervised inspection keeps the Ok envelope and exit 0."""
    with patch.object(cli, "run_documentation_inspection", lambda *a, **k: _FakeEvidence()):
        result = _run_cli(["documentation", "inspect", "--db", "core.accdb"])

    captured = capsys.readouterr()
    assert result == 0
    assert "Traceback" not in captured.err


def test_inspect_forwards_the_sql_opt_in_flag_and_defaults_off(capsys) -> None:
    """S4/S5: ``--include-query-sql`` is store_true and defaults false at the CLI seam."""
    seen: list[bool] = []

    def _capture(*_args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["include_query_sql"])
        return _FakeEvidence()

    with patch.object(cli, "run_documentation_inspection", _capture):
        assert _run_cli(["documentation", "inspect", "--db", "core.accdb"]) == 0
        assert _run_cli(["documentation", "inspect", "--db", "core.accdb", "--include-query-sql"]) == 0

    capsys.readouterr()
    assert seen == [False, True]


def test_inspect_help_documents_the_opt_in_and_its_default() -> None:
    """The new flag is discoverable and states it is off by default."""
    parser = cli.build_parser()
    family = next(action for action in parser._actions if action.dest == "command").choices["documentation"]
    actions = next(item for item in family._actions if item.dest == "documentation_action").choices
    action = next(item for item in actions["inspect"]._actions if item.dest == "include_query_sql")
    assert action.option_strings == ["--include-query-sql"]
    assert action.const is True and action.default is False, action.default
    help_text = (action.help or "").lower()
    assert "default" in help_text and "sql" in help_text


def test_inspect_forwards_the_app_logic_opt_in_flag_and_defaults_off(capsys) -> None:
    """S4/requirement 6: ``--include-app-logic`` is store_true and defaults false at the CLI seam."""
    seen: list[bool] = []

    def _capture(*_args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["include_app_logic"])
        return _FakeEvidence()

    with patch.object(cli, "run_documentation_inspection", _capture):
        assert _run_cli(["documentation", "inspect", "--db", "core.accdb", "--json"]) == 0
        default_out = capsys.readouterr().out
        assert (
            _run_cli(["documentation", "inspect", "--db", "core.accdb", "--include-app-logic", "--json"]) == 0
        )
        opted_in_out = capsys.readouterr().out

    assert seen == [False, True]
    # Default off adds no category: the emitted data keeps exactly the v1 members.
    default_envelope = json.loads(default_out)
    assert default_envelope["success"] is True
    assert set(default_envelope["data"]) == {"tables", "status"}
    assert "app_logic" not in default_out
    assert set(json.loads(opted_in_out)["data"]) == {"tables", "status"}


def test_inspect_help_documents_the_app_logic_opt_in_and_its_default() -> None:
    """The app-logic flag is discoverable, is store_true, and states its default."""
    parser = cli.build_parser()
    family = next(action for action in parser._actions if action.dest == "command").choices["documentation"]
    actions = next(item for item in family._actions if item.dest == "documentation_action").choices
    action = next(item for item in actions["inspect"]._actions if item.dest == "include_app_logic")
    assert action.option_strings == ["--include-app-logic"]
    assert action.const is True and action.default is False, action.default
    help_text = (action.help or "").lower()
    assert "default" in help_text and "app-logic" in help_text
