"""Golden tests for the shared SQL script parser."""

from __future__ import annotations

from ms_access_mcp.backends.script import parse_script_lines


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


def test_parse_script_lines_basic():
    from ms_access_mcp.backends.script import parse_script_lines

    script = "SELECT 1; SELECT 2; SELECT 3"
    result = parse_script_lines(script)
    assert result == ["SELECT 1", "SELECT 2", "SELECT 3"]


def test_parse_script_lines_strips_whitespace():
    from ms_access_mcp.backends.script import parse_script_lines

    script = "  SELECT 1  ;  SELECT 2  "
    result = parse_script_lines(script)
    assert result == ["SELECT 1", "SELECT 2"]


def test_parse_script_lines_filters_empty():
    from ms_access_mcp.backends.script import parse_script_lines

    script = "SELECT 1; ; SELECT 2"
    result = parse_script_lines(script)
    assert result == ["SELECT 1", "SELECT 2"]


def test_parse_script_lines_removes_comments():
    from ms_access_mcp.backends.script import parse_script_lines

    script = "SELECT 1; -- this is a comment\nSELECT 2"
    result = parse_script_lines(script)
    assert result == ["SELECT 1", "SELECT 2"]


def test_parse_script_lines_empty_script():
    from ms_access_mcp.backends.script import parse_script_lines

    assert parse_script_lines("") == []
    assert parse_script_lines(";") == []
    assert parse_script_lines("  ") == []
