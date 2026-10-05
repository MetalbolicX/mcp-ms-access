"""Characterization tests: golden-pin current CRUD SQL bytes from ado and odbc backends.

These tests capture the EXACT SQL strings and parameter binding produced by the
ADO and ODBC backends for representative CRUD operations. They serve as
byte-parity proofs for the extraction refactor.

Run against main BEFORE any extraction changes — these pins must stay green
unmodified after refactoring (Step 4 proof).
"""

from __future__ import annotations

from ms_access_mcp.backends.ado import AdoAdapter
from ms_access_mcp.backends.odbc import OdbcAdapter

# ----------------------------------------------------------------------
# Fake cursor/connection helpers
# ----------------------------------------------------------------------


class FakeADOConnection:
    """Captures executed SQL and params from AdoAdapter."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, list]] = []
        self._connected = True

    def Execute(self, sql: str, params=None):
        self.executed.append((sql, list(params) if params else []))
        return (FakeADORecordset(),)

    def Close(self) -> None:
        pass


class FakeADORecordset:
    def __init__(self):
        self.EOF = True

    def Close(self) -> None:
        pass


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
# ADO goldens — characterization of current SQL construction
# ----------------------------------------------------------------------


class TestAdoAdapterCrudGoldens:
    """Pin exact SQL strings from AdoAdapter CRUD operations."""

    def test_insert_single_row(self):
        """INSERT with single row dict."""
        adapter = AdoAdapter("test.accdb")
        adapter._conn = FakeADOConnection()
        adapter._connected = True

        adapter.insert_data("Users", {"name": "Alice", "age": 30})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        # Column identifiers are bracket-quoted through the shared builder.
        assert sql == "INSERT INTO [Users] ([name], [age]) VALUES (?, ?)"
        assert params == ["Alice", 30]

    def test_insert_multiple_rows(self):
        """INSERT with list of row dicts — one statement per row."""
        adapter = AdoAdapter("test.accdb")
        adapter._conn = FakeADOConnection()
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
        adapter = AdoAdapter("test.accdb")
        adapter._conn = FakeADOConnection()
        adapter._connected = True

        adapter.update_data("Users", {"age": 31}, {"name": "Alice"})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        # SET columns are bracket-quoted, WHERE columns are bracket-quoted
        assert sql == "UPDATE [Users] SET [age] = ? WHERE [name] = ?"
        # SET values first, then WHERE values
        assert params == [31, "Alice"]

    def test_update_without_where(self):
        """UPDATE ... SET with no WHERE clause — all rows updated."""
        adapter = AdoAdapter("test.accdb")
        adapter._conn = FakeADOConnection()
        adapter._connected = True

        adapter.update_data("Users", {"age": 31})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "UPDATE [Users] SET [age] = ?"
        assert params == [31]

    def test_update_multi_col_set(self):
        """UPDATE with multiple SET columns."""
        adapter = AdoAdapter("test.accdb")
        adapter._conn = FakeADOConnection()
        adapter._connected = True

        adapter.update_data("Users", {"name": "Alicia", "age": 32}, {"id": 1})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "UPDATE [Users] SET [name] = ?, [age] = ? WHERE [id] = ?"
        assert params == ["Alicia", 32, 1]

    def test_delete_with_where(self):
        """DELETE ... WHERE with parameterized values."""
        adapter = AdoAdapter("test.accdb")
        adapter._conn = FakeADOConnection()
        adapter._connected = True

        adapter.delete_data("Users", {"name": "Alice"})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "DELETE FROM [Users] WHERE [name] = ?"
        assert params == ["Alice"]

    def test_delete_without_where(self):
        """DELETE with no WHERE — deletes all rows."""
        adapter = AdoAdapter("test.accdb")
        adapter._conn = FakeADOConnection()
        adapter._connected = True

        adapter.delete_data("Users")

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "DELETE FROM [Users]"
        assert params == []

    def test_delete_multi_col_where(self):
        """DELETE ... WHERE with multiple conditions AND-ed."""
        adapter = AdoAdapter("test.accdb")
        adapter._conn = FakeADOConnection()
        adapter._connected = True

        adapter.delete_data("Users", {"name": "Alice", "age": 30})

        assert len(adapter._conn.executed) == 1
        sql, params = adapter._conn.executed[0]
        assert sql == "DELETE FROM [Users] WHERE [name] = ? AND [age] = ?"
        assert params == ["Alice", 30]


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
