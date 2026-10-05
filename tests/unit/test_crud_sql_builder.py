"""Unit tests for the crud_sql builder.

These tests assert the exact SQL/params output by build_insert, build_update,
and build_delete — matching the golden bytes pinned from the ADO/ODBC backends.
"""

from __future__ import annotations

from ms_access_mcp.backends import crud_sql


class TestBuildInsert:
    """Tests for build_insert."""

    def test_insert_escapes_table_and_column_identifiers(self):
        sql, params = crud_sql.build_insert("t]x", {"c]y": 1})
        assert sql == "INSERT INTO [t]]x] ([c]]y]) VALUES (?)"
        assert params == [1]

    def test_insert_single_row(self):
        """Single row dict produces correct INSERT."""
        sql, params = crud_sql.build_insert("Users", {"name": "Alice", "age": 30})
        assert sql == "INSERT INTO [Users] ([name], [age]) VALUES (?, ?)"
        assert params == ["Alice", 30]

    def test_insert_multiple_rows(self):
        """List of row dicts — builder returns one (sql, params) per row."""
        data = [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25},
        ]
        result = crud_sql.build_insert("Users", data)
        # build_insert returns (sql, params) for a single row; caller iterates
        assert isinstance(result, tuple)
        assert len(result) == 2
        sql, params = result
        assert "INSERT INTO [Users]" in sql
        assert params == ["Alice", 30]


class TestBuildUpdate:
    """Tests for build_update."""

    def test_update_escapes_table_set_and_where_identifiers(self):
        sql, params = crud_sql.build_update("t]x", {"s]y": 1}, {"w]z": 2})
        assert sql == "UPDATE [t]]x] SET [s]]y] = ? WHERE [w]]z] = ?"
        assert params == [1, 2]

    def test_update_with_where(self):
        """UPDATE SET ... WHERE with parameterized values."""
        sql, params = crud_sql.build_update(
            "Users", {"age": 31}, {"name": "Alice"}
        )
        assert sql == "UPDATE [Users] SET [age] = ? WHERE [name] = ?"
        assert params == [31, "Alice"]

    def test_update_without_where(self):
        """UPDATE SET with no WHERE clause."""
        sql, params = crud_sql.build_update("Users", {"age": 31})
        assert sql == "UPDATE [Users] SET [age] = ?"
        assert params == [31]

    def test_update_multi_col_set(self):
        """UPDATE with multiple SET columns."""
        sql, params = crud_sql.build_update(
            "Users", {"name": "Alicia", "age": 32}, {"id": 1}
        )
        assert sql == "UPDATE [Users] SET [name] = ?, [age] = ? WHERE [id] = ?"
        assert params == ["Alicia", 32, 1]


class TestBuildDelete:
    """Tests for build_delete."""

    def test_delete_escapes_table_and_where_identifiers(self):
        sql, params = crud_sql.build_delete("t]x", {"w]z": 2})
        assert sql == "DELETE FROM [t]]x] WHERE [w]]z] = ?"
        assert params == [2]

    def test_delete_with_where(self):
        """DELETE ... WHERE with parameterized values."""
        sql, params = crud_sql.build_delete("Users", {"name": "Alice"})
        assert sql == "DELETE FROM [Users] WHERE [name] = ?"
        assert params == ["Alice"]

    def test_delete_without_where(self):
        """DELETE with no WHERE clause."""
        sql, params = crud_sql.build_delete("Users")
        assert sql == "DELETE FROM [Users]"
        assert params == []

    def test_delete_multi_col_where(self):
        """DELETE ... WHERE with multiple conditions AND-ed."""
        sql, params = crud_sql.build_delete("Users", {"name": "Alice", "age": 30})
        assert sql == "DELETE FROM [Users] WHERE [name] = ? AND [age] = ?"
        assert params == ["Alice", 30]
