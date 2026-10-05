"""Characterization tests: golden-pin current ODBC CRUD SQL bytes.

These tests capture the exact SQL strings and parameter binding produced by
the live ODBC backend for representative CRUD operations.
"""

from __future__ import annotations

from ms_access_mcp.backends.odbc import OdbcAdapter

# ----------------------------------------------------------------------
# Fake cursor/connection helpers
# ----------------------------------------------------------------------


class FakeODBCConnection:
    """Captures executed SQL and params from OdbcAdapter."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, list]] = []
        self._connected = True

    def cursor(self):
        return FakeOODBCCursor(self)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class FakeOODBCCursor:
    def __init__(self, conn: FakeODBCConnection) -> None:
        self._conn = conn
        self.rowcount = 1

    def execute(self, sql: str, params=None) -> None:
        self._conn.executed.append((sql, list(params) if params else []))

    def close(self) -> None:
        pass


# ----------------------------------------------------------------------
# ODBC goldens — characterization of current SQL construction
# ----------------------------------------------------------------------


class TestOdbcAdapterCrudGoldens:
    """Pin exact SQL strings from OdbcAdapter CRUD operations."""

    def test_insert_single_row(self):
        """INSERT with single row dict."""
        adapter = OdbcAdapter("test.accdb")
        adapter._conn = FakeODBCConnection()
        adapter._connected = True

        adapter.insert_data("Users", {"name": "Alice", "age": 30})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "INSERT INTO [Users] ([name], [age]) VALUES (?, ?)"
        assert params == ["Alice", 30]

    def test_insert_multiple_rows(self):
        """INSERT with list of row dicts — one statement per row."""
        adapter = OdbcAdapter("test.accdb")
        adapter._conn = FakeODBCConnection()
        adapter._connected = True

        adapter.insert_data("Users", [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25},
        ])

        assert len(adapter._conn.executed) == 2

        sql1, params1 = adapter._conn.executed[0]
        assert sql1 == "INSERT INTO [Users] ([name], [age]) VALUES (?, ?)"
        assert params1 == ["Alice", 30]

        sql2, params2 = adapter._conn.executed[1]
        assert sql2 == "INSERT INTO [Users] ([name], [age]) VALUES (?, ?)"
        assert params2 == ["Bob", 25]

    def test_update_with_where(self):
        """UPDATE ... SET ... WHERE with parameterized values."""
        adapter = OdbcAdapter("test.accdb")
        adapter._conn = FakeODBCConnection()
        adapter._connected = True

        adapter.update_data("Users", {"age": 31}, {"name": "Alice"})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "UPDATE [Users] SET [age] = ? WHERE [name] = ?"
        assert params == [31, "Alice"]

    def test_update_without_where(self):
        """UPDATE ... SET with no WHERE clause."""
        adapter = OdbcAdapter("test.accdb")
        adapter._conn = FakeODBCConnection()
        adapter._connected = True

        adapter.update_data("Users", {"age": 31})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "UPDATE [Users] SET [age] = ?"
        assert params == [31]

    def test_update_multi_col_set(self):
        """UPDATE with multiple SET columns."""
        adapter = OdbcAdapter("test.accdb")
        adapter._conn = FakeODBCConnection()
        adapter._connected = True

        adapter.update_data("Users", {"name": "Alicia", "age": 32}, {"id": 1})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "UPDATE [Users] SET [name] = ?, [age] = ? WHERE [id] = ?"
        assert params == ["Alicia", 32, 1]

    def test_delete_with_where(self):
        """DELETE ... WHERE with parameterized values."""
        adapter = OdbcAdapter("test.accdb")
        adapter._conn = FakeODBCConnection()
        adapter._connected = True

        adapter.delete_data("Users", {"name": "Alice"})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "DELETE FROM [Users] WHERE [name] = ?"
        assert params == ["Alice"]

    def test_delete_without_where(self):
        """DELETE with no WHERE — deletes all rows."""
        adapter = OdbcAdapter("test.accdb")
        adapter._conn = FakeODBCConnection()
        adapter._connected = True

        adapter.delete_data("Users")

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "DELETE FROM [Users]"
        assert params == []

    def test_delete_multi_col_where(self):
        """DELETE ... WHERE with multiple conditions AND-ed."""
        adapter = OdbcAdapter("test.accdb")
        adapter._conn = FakeODBCConnection()
        adapter._connected = True

        adapter.delete_data("Users", {"name": "Alice", "age": 30})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "DELETE FROM [Users] WHERE [name] = ? AND [age] = ?"
        assert params == ["Alice", 30]
