"""Unit tests for dao_sql helper functions."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from ms_access_mcp.backends.dao_sql import (
    _access_sql_type,
    _alter_table_add_column,
    _format_dao_value,
    _sanitize_where_string,
    _strip_password,
)


class TestFormatDaoValue:
    """Round-trip value formatting for DAO SQL."""

    @pytest.mark.parametrize("val,expected", [
        (None, "NULL"),
        (True, "-1"),
        (False, "0"),
        (42, "42"),
        (3.14, "3.14"),
        ("hello", "'hello'"),
        ("it's", "'it''s'"),
        (datetime(2024, 1, 15, 10, 30, 0), "#2024-01-15 10:30:00#"),
    ])
    def test_format_dao_value_roundtrip(self, val, expected):
        result = _format_dao_value(val)
        assert result == expected

    def test_format_dao_value_string_escape(self):
        assert _format_dao_value("O'Brien") == "'O''Brien'"
        assert _format_dao_value("a'b'c") == "'a''b''c'"


class TestSanitizeWhereString:
    """SQL injection allowlist tests."""

    def test_valid_simple_condition(self):
        assert _sanitize_where_string("name = 'John'") == "name = 'John'"

    def test_valid_multiple_conditions(self):
        assert _sanitize_where_string("age > 18 AND status = 'active'") is not None

    def test_valid_wildcard(self):
        assert _sanitize_where_string("name LIKE '%Smith%'") is not None

    def test_reject_sql_comment(self):
        assert _sanitize_where_string("name = 'John' --") is None
        assert _sanitize_where_string("name = 'John' /*") is None

    def test_reject_dangerous_keywords(self):
        for kw in ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "UNION", "EXEC"]:
            assert _sanitize_where_string(f"name = '{kw}'") is None, f"Should reject {kw}"
            assert _sanitize_where_string(f"{kw} TABLE") is None, f"Should reject {kw} as prefix"

    def test_reject_injection_or_tautology(self):
        assert _sanitize_where_string("OR 1=1") is None
        assert _sanitize_where_string("OR 99=99") is None

    def test_reject_invalid_characters(self):
        assert _sanitize_where_string("name = 'John'; DROP TABLE") is None
        assert _sanitize_where_string("name = 'John' & whoami") is None


class TestAccessSqlType:
    """Type mapping tests."""

    @pytest.mark.parametrize("access_type,size,expected", [
        ("Text", 255, "VARCHAR(255)"),
        ("Text", 50, "VARCHAR(50)"),
        ("Long Integer", 0, "INTEGER"),
        ("Integer", 0, "SMALLINT"),
        ("Byte", 0, "BYTE"),
        ("Currency", 0, "MONEY"),
        ("Single", 0, "SINGLE"),
        ("Double", 0, "DOUBLE"),
        ("Date/Time", 0, "DATETIME"),
        ("Memo", 0, "MEMO"),
        ("Boolean", 0, "BIT"),
        ("Binary", 0, "BINARY"),
        ("GUID", 0, "GUID"),
        ("Big Integer", 0, "BIGINT"),
        ("Counter", 0, "COUNTER"),
        ("AutoNumber", 0, "COUNTER"),
    ])
    def test_type_mapping(self, access_type, size, expected):
        assert _access_sql_type(access_type, size) == expected

    def test_unknown_type_defaults_to_varchar_255(self):
        assert _access_sql_type("UnknownType", 255) == "VARCHAR(255)"


class TestStripPassword:
    """Password stripping from connect strings."""

    def test_strip_simple_pwd(self):
        assert _strip_password("ODBC;PWD=secret;DBQ=x.accdb") == "ODBC;;DBQ=x.accdb"

    def test_strip_password_no_trailing_semicolon(self):
        result = _strip_password("ODBC;PWD=secret")
        assert "PWD" not in result

    def test_strip_password_access_connect(self):
        result = _strip_password("Access;PWD=mypassword;DATABASE=x")
        assert "PWD" not in result

    def test_strip_multiple_passwords(self):
        result = _strip_password("ODBC;PWD=first;PWD=second;DBQ=x")
        assert "PWD" not in result

    def test_no_password(self):
        assert _strip_password("ODBC;DBQ=x.accdb") == "ODBC;DBQ=x.accdb"


class TestAlterTableAddColumn:
    """Tests for _alter_table_add_column DAO DDL helper."""

    def test_add_column_sql_generation(self):
        """Verify the SQL that would be generated for a column add."""
        mock_db = MagicMock()
        params = {"name": "NewCol", "type": "Text", "size": 100, "nullable": False}
        result = _alter_table_add_column(mock_db, "TestTable", params)
        assert result == {"success": True}
        call_args = mock_db.Execute.call_args[0]
        sql = call_args[0]
        assert "ALTER TABLE [TestTable] ADD COLUMN" in sql
        assert "[NewCol]" in sql
        assert "VARCHAR(100)" in sql
        assert "NOT NULL" in sql

    def test_add_column_nullable(self):
        mock_db = MagicMock()
        params = {"name": "OptCol", "type": "Integer", "nullable": True}
        _alter_table_add_column(mock_db, "TestTable", params)
        sql = mock_db.Execute.call_args[0][0]
        assert "NULL" in sql
