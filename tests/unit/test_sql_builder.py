"""Tests for sql_builder.py — SQL construction and row extraction."""

import pytest

from ms_access_mcp.application.sql_builder import (
    build_select,
    extract_rows,
    normalize_value,
    validate_columns,
)


class TestBuildSelect:
    """Test SELECT statement builder."""

    def test_select_star_when_no_columns(self):
        """columns=None yields SELECT *."""
        result = build_select("Users", None, None, None)
        assert result == "SELECT * FROM [Users]"

    def test_select_specific_columns(self):
        """Specific columns are comma-separated."""
        result = build_select("Users", ["id", "name"], None, None)
        assert result == "SELECT id, name FROM [Users]"

    def test_select_with_where(self):
        """WHERE clause is appended."""
        result = build_select("Users", None, "id = 1", None)
        assert result == "SELECT * FROM [Users] WHERE id = 1"

    def test_select_with_order_by(self):
        """ORDER BY clause is appended."""
        result = build_select("Users", None, None, ["name", "id"])
        assert result == "SELECT * FROM [Users] ORDER BY name, id"

    def test_select_with_where_and_order_by(self):
        """WHERE and ORDER BY both appended."""
        result = build_select("Users", ["name"], "id > 5", ["name"])
        assert result == "SELECT name FROM [Users] WHERE id > 5 ORDER BY name"

    def test_table_name_brackets(self):
        """Table name is wrapped in brackets."""
        result = build_select("My Table", None, None, None)
        assert result == "SELECT * FROM [My Table]"


class TestValidateColumns:
    """Test column validation."""

    def test_valid_columns_no_raise(self):
        """Valid columns do not raise."""
        validate_columns(["id", "name"], ["id", "name", "email"])
        # No exception

    def test_invalid_column_raises(self):
        """Invalid column raises ValueError."""
        with pytest.raises(ValueError, match="Invalid column 'bad'"):
            validate_columns(["id", "bad"], ["id", "name"])

    def test_empty_valid_list(self):
        """Empty valid list with empty requested raises."""
        with pytest.raises(ValueError, match="Invalid column 'bad'"):
            validate_columns(["bad"], [])


class TestExtractRows:
    """Test row extraction from query results."""

    def test_extract_from_dict_success(self):
        """Dict with success=True extracts rows."""
        result = {"success": True, "rows": [{"id": 1}, {"id": 2}]}
        rows = extract_rows(result)
        assert rows == [{"id": 1}, {"id": 2}]

    def test_extract_from_dict_failure(self):
        """Dict with success=False returns empty list."""
        result = {"success": False, "rows": [{"id": 1}]}
        rows = extract_rows(result)
        assert rows == []

    def test_extract_from_dict_no_rows_key(self):
        """Dict without rows key returns empty list."""
        result = {"success": True}
        rows = extract_rows(result)
        assert rows == []

    def test_extract_from_list(self):
        """List is returned as-is."""
        rows = [{"id": 1}, {"id": 2}]
        result = extract_rows(rows)
        assert result == rows

    def test_extract_from_empty_list(self):
        """Empty list is returned as-is."""
        result = extract_rows([])
        assert result == []


class TestNormalizeValue:
    """Test value normalization."""

    def test_normalize_none(self):
        """None becomes <NULL>."""
        assert normalize_value(None) == "<NULL>"

    def test_normalize_string(self):
        """String value is stringified."""
        assert normalize_value("test") == "test"

    def test_normalize_int(self):
        """Int value is stringified."""
        assert normalize_value(42) == "42"

    def test_normalize_float(self):
        """Float value is stringified."""
        assert normalize_value(3.14) == "3.14"

    def test_normalize_bool(self):
        """Bool value is stringified."""
        assert normalize_value(True) == "True"
