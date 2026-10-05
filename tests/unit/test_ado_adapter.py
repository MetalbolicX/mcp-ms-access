"""Focused ADO query limit contract tests."""

from __future__ import annotations

from ms_access_mcp.backends.ado import AdoAdapter
from ms_access_mcp.backends.script import parse_script_lines


class _Field:
    def __init__(self, name: str, value=None) -> None:
        self.Name = name
        self.Value = value


class _Fields:
    def __init__(self, recordset) -> None:
        self.recordset = recordset

    def __iter__(self):
        return iter([_Field(name) for name in self.recordset.rows[0]])

    def __call__(self, index: int):
        name = list(self.recordset.rows[0])[index]
        return _Field(name, self.recordset.rows[self.recordset.position][name])


class _Recordset:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.position = 0
        self.Fields = _Fields(self)
        self.closed = False

    @property
    def EOF(self) -> bool:
        return self.position >= len(self.rows)

    def MoveNext(self) -> None:
        self.position += 1

    def Close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, recordset: _Recordset) -> None:
        self.recordset = recordset
        self.executed: list[str] = []

    def Execute(self, sql: str):
        self.executed.append(sql)
        return [self.recordset]


def test_parse_script_lines_preserves_comment_marker_inside_literal():
    assert parse_script_lines("INSERT INTO t (a) VALUES ('a--b')") == [
        "INSERT INTO t (a) VALUES ('a--b')"
    ]


def test_parse_script_lines_strips_trailing_comment_and_keeps_next_statement():
    script = "UPDATE t SET a = 1 -- trailing comment\nUPDATE t SET b = 2;"
    assert parse_script_lines(script) == [
        "UPDATE t SET a = 1  UPDATE t SET b = 2"
    ]


def test_parse_script_lines_handles_carriage_return_after_comment():
    script = "UPDATE t SET a = 1 -- comment\rUPDATE t SET b = 2;"
    assert parse_script_lines(script) == [
        "UPDATE t SET a = 1  UPDATE t SET b = 2"
    ]


def test_parse_script_lines_does_not_resurrect_semicolon_after_comment():
    script = "UPDATE t SET a = 1 -- trailing comment; UPDATE t SET b = 2;"
    assert parse_script_lines(script) == ["UPDATE t SET a = 1"]


def test_parse_script_lines_splits_only_on_semicolon_outside_literal():
    script = "INSERT INTO t (a) VALUES ('semi;colon'); SELECT 1;"
    assert parse_script_lines(script) == [
        "INSERT INTO t (a) VALUES ('semi;colon')",
        "SELECT 1",
    ]


def test_parse_script_lines_handles_escaped_quotes_in_literal():
    script = "INSERT INTO t (a) VALUES ('it''s -- literal;'); SELECT 1;"
    assert parse_script_lines(script) == [
        "INSERT INTO t (a) VALUES ('it''s -- literal;')",
        "SELECT 1",
    ]


def test_ado_execute_query_applies_limit_by_slicing_only():
    sql = "SELECT id FROM Users"
    recordset = _Recordset([{"id": row_id} for row_id in range(20)])
    connection = _Connection(recordset)
    adapter = AdoAdapter("unused.accdb")
    adapter._conn = connection
    adapter._connected = True

    result = adapter.execute_query(sql, limit=5)

    assert result.success is True
    assert len(result.rows) == result.count == 5
    assert connection.executed == [sql]
    assert recordset.closed is True
