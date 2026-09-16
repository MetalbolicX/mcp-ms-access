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

    # --- Case 9: case-hardened strip ---

    def test_strip_pwd_lowercase(self):
        """pwd= (lowercase) is stripped."""
        result = _strip_password("ODBC;pwd=secret;DBQ=x.accdb")
        assert "pwd" not in result

    def test_strip_Password_keyword(self):
        """Password= is stripped (case-insensitive)."""
        result = _strip_password("ODBC;Password=hunter2;DBQ=x.accdb")
        assert "Password" not in result

    def test_strip_mixed_case_pwd(self):
        """Pwd= mixed case is stripped."""
        result = _strip_password("ODBC;Pwd=mixed;DBQ=x.accdb")
        assert "Pwd" not in result

    def test_strip_jet_oledb_password(self):
        """Jet OLEDB:Database Password= is stripped."""
        result = _strip_password("Jet OLEDB:Database Password=hunter2;Provider=...")
        assert "Password" not in result

    def test_strip_token_at_start(self):
        """Password token at string start is stripped."""
        result = _strip_password("PWD=first;other=data")
        assert "PWD" not in result

    def test_strip_token_mid_string(self):
        """Password token in mid-string position is stripped."""
        result = _strip_password("Server=srv;Password=mid;Port=1433")
        assert "Password" not in result

    def test_strip_multiple_password_tokens(self):
        """Multiple password tokens in one string are all stripped."""
        result = _strip_password("ODBC;PWD=first;Pwd=second;Password=third;DBQ=x")
        assert "PWD" not in result
        assert "pwd" not in result
        assert "Password" not in result

    # --- Case 10: non-credential strings pass through ---

    def test_no_credentials_dsn(self):
        """DSN= without password passes through byte-identical."""
        original = "ODBC;DSN=mydsn;UID=me"
        result = _strip_password(original)
        assert result == original

    def test_no_credentials_database_path(self):
        """DATABASE= with a file path passes through unchanged."""
        original = "DATABASE=C:\\data\\db.accdb"
        result = _strip_password(original)
        assert result == original

    def test_no_credentials_access_driver(self):
        """Driver= with Access driver passes through unchanged."""
        original = "Driver={Microsoft Access Driver (*.mdb, *.accdb)}"
        result = _strip_password(original)
        assert result == original

    # --- Legacy compatibility (keep existing) ---

    def test_strip_simple_pwd(self):
        # PWD=secret is removed; the adjacent semicolons collapse into one
        assert _strip_password("ODBC;PWD=secret;DBQ=x.accdb") == "ODBC;DBQ=x.accdb"
        assert "PWD" not in _strip_password("ODBC;PWD=secret;DBQ=x.accdb")

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


# =============================================================================
# Step 2 RED: DAO adapter linked-table read-strip + error scrub
# Cases 11-12 from plan 040
# =============================================================================


class TestDaoAdapterLinkedTableReadStrip:
    """Case 11: get_linked_tables scrubs password at read time."""

    def test_get_linked_tables_strips_password(self, monkeypatch):
        """connect_string in returned dict must not contain pwd= even if tdef.Connect has it."""
        from ms_access_mcp.backends.dao import DaoAdapter

        # Build a minimal fake DAO database with a linked table bearing a password
        fake_tdef = MagicMock()
        fake_tdef.Attributes = 0x80000000  # dbLinkAttachedTable
        fake_tdef.Name = "RemoteOrders"
        fake_tdef.SourceTableName = "Orders"
        # Configure Connect as a string so _strip_password works
        fake_tdef.configure_mock(Connect="ODBC;DSN=srv;PWD=hunter2;Database=Northwind")

        fake_dao_db = MagicMock()
        fake_dao_db.TableDefs.Count = 1
        # Return our fake_tdef for both attribute access and indexing
        fake_dao_db.TableDefs.return_value = fake_tdef
        fake_dao_db.TableDefs.__getitem__.return_value = fake_tdef

        # Use a real-ish FakeDispatcher to avoid MagicMock attribute quirks
        class FakeDispatcher:
            def __init__(self, db):
                self._db = db

            def is_connected(self) -> bool:
                return self._db is not None

            @property
            def current_db(self):
                return self._db

            def call(self, fn):
                return fn()

        dispatcher = FakeDispatcher(fake_dao_db)

        adapter = DaoAdapter("fake.accdb", dispatcher=dispatcher)
        result = adapter.get_linked_tables()
        if not result["success"]:
            err = result.get("error", "")
            # Show repr to identify MagicMock
            raise AssertionError(f"get_linked_tables failed: error={err!r}, type={type(err)}")

        assert result["success"], f"get_linked_tables failed: {result.get('error')}"
        tables = result["linked_tables"]
        assert len(tables) == 1
        connect_str = tables[0]["connect_string"]
        assert "hunter2" not in connect_str, f"password leaked: {connect_str}"
        assert "PWD=" not in connect_str


class TestDaoAdapterLinkedTableErrorScrub:
    """Case 12: create_linked_table error scrubs password from error message."""

    def test_create_linked_table_error_does_not_leak_password(self, monkeypatch):
        """Error returned from failed create_linked_table must not contain the connect string."""
        from ms_access_mcp.backends.dao import DaoAdapter

        fake_dao_db = MagicMock()
        # make CreateTableDef raise an exception with the full connect string in the message
        fake_dao_db.CreateTableDef.side_effect = Exception(
            "create failed: ODBC;PWD=hunter2;Database=Northwind"
        )

        # Use a real-ish FakeDispatcher to avoid MagicMock attribute quirks
        class FakeDispatcher:
            def __init__(self, db):
                self._db = db

            def is_connected(self) -> bool:
                return self._db is not None

            @property
            def current_db(self):
                return self._db

            def call(self, fn):
                return fn()

        dispatcher = FakeDispatcher(fake_dao_db)

        adapter = DaoAdapter("fake.accdb", dispatcher=dispatcher)
        result = adapter.create_linked_table(
            name="Orders",
            source_table="Orders",
            connect_string="ODBC;PWD=hunter2;Database=Northwind",
        )

        assert result["success"] is False
        error_msg = result["error"]
        # Security requirement: password must not appear in error
        assert "hunter2" not in error_msg
        # Verify error is sanitized (either [sanitized] marker or _strip_password applied)
        assert error_msg != "create failed: ODBC;PWD=hunter2;Database=Northwind"
