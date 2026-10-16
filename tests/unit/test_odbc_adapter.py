"""Unit tests for ODBC adapter."""

from __future__ import annotations

import pytest

from ms_access_mcp.backends.odbc import OdbcAdapter
from ms_access_mcp.errors import BackendCapabilityMismatchError
from ms_access_mcp.models import DatabaseStatistics, FieldInfo, TableInfo


class TestOdbcAdapterReadModels:
    def test_read_methods_return_dao_typed_models(self, tmp_path):
        class FakeRow:
            table_name = "Users"

        class FakeColumn:
            column_name = "id"
            type_name = "INTEGER"
            column_size = 4
            nullable = 0

        class FakeCursor:
            columns_called: bool = False

            def tables(self, **_kwargs):
                pass

            def columns(self, **_kwargs):
                pass

            def fetchall(self):
                return [FakeColumn()] if self.columns_called else [FakeRow()]

            def close(self):
                pass

            def statistics(self, **_kwargs):
                pass

            def primaryKeys(self, **_kwargs):
                pass

        class FakeConnection:
            def cursor(self):
                cursor = FakeCursor()
                original_columns = cursor.columns

                def columns(**kwargs):
                    cursor.columns_called = True
                    original_columns(**kwargs)

                cursor.columns = columns
                return cursor

        db_path = tmp_path / "fixture.accdb"
        db_path.write_bytes(b"database")
        adapter = OdbcAdapter(str(db_path))
        adapter._connected = True
        adapter._conn = FakeConnection()

        tables = adapter.get_tables()
        stats = adapter.get_database_statistics()

        assert len(tables) == 1
        assert isinstance(tables[0], TableInfo)
        assert isinstance(tables[0].fields[0], FieldInfo)
        assert tables[0].name == "Users"
        assert isinstance(stats, DatabaseStatistics)
        assert set(stats.__dict__) == {"success", "objects", "file", "system"}


class TestOdbcAdapterBasics:
    """Basic ODBC adapter tests."""

    def test_not_connected_initially(self):
        adapter = OdbcAdapter("test.accdb")
        assert adapter.is_connected() is False

    def test_insert_data_raises_when_not_connected(self):
        adapter = OdbcAdapter("test.accdb")
        result = adapter.insert_data("tbl", {"col": "val"})
        assert result.success is False
        assert result.error is not None
        assert "Not connected" in result.error

    def test_update_data_raises_when_not_connected(self):
        adapter = OdbcAdapter("test.accdb")
        result = adapter.update_data("tbl", {"col": "val"})
        assert result.success is False

    def test_delete_data_raises_when_not_connected(self):
        adapter = OdbcAdapter("test.accdb")
        result = adapter.delete_data("tbl")
        assert result.success is False

    def test_execute_query_raises_when_not_connected(self):
        adapter = OdbcAdapter("test.accdb")
        result = adapter.execute_query("SELECT 1")
        assert result.success is False
        assert result.error is not None
        assert "Not connected" in result.error

    def test_get_tables_raises_when_not_connected(self):
        adapter = OdbcAdapter("test.accdb")
        assert adapter.get_tables() == []

    def test_disconnect_when_not_connected(self):
        adapter = OdbcAdapter("test.accdb")
        adapter.disconnect()  # should not raise
        assert adapter.is_connected() is False


class TestOdbcAdapterQueryLimit:
    @pytest.mark.parametrize(
        ("sql", "expected_prefix"),
        [
            ("select id from t", "SELECT TOP 5"),
            ("select distinct id from t", "SELECT DISTINCT TOP 5"),
            ("SELECT DISTINCTROW id FROM t", None),
            ("UPDATE t SET id = 1", None),
        ],
    )
    def test_execute_query_limit_rewrites_select_and_slices_rows(self, sql, expected_prefix):
        class FakeCursor:
            description = [("id",)]

            def __init__(self):
                self.executed: list[tuple[str, object]] = []

            def execute(self, query, params=None):
                self.executed.append((query, params))

            def fetchall(self):
                return [(row_id,) for row_id in range(20)]

            def close(self):
                pass

        class FakeConnection:
            def __init__(self):
                self.fake_cursor = FakeCursor()

            def cursor(self):
                return self.fake_cursor

        adapter = OdbcAdapter("test.accdb")
        adapter._connected = True
        adapter._conn = FakeConnection()

        result = adapter.execute_query(sql, limit=5)

        assert result.success is True
        assert len(result.rows) == 5
        assert result.count == 5
        executed_sql = adapter._conn.fake_cursor.executed[0][0]
        if expected_prefix is None:
            assert executed_sql == sql
        else:
            assert executed_sql.upper().startswith(expected_prefix)


class TestOdbcAdapterDdlRaises:
    """DDL operations must raise BackendCapabilityMismatchError."""

    def test_create_table_raises(self):
        adapter = OdbcAdapter("test.accdb")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.create_table("NewTable", [])

    def test_delete_table_raises(self):
        adapter = OdbcAdapter("test.accdb")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.delete_table("SomeTable")

    def test_alter_table_raises(self):
        adapter = OdbcAdapter("test.accdb")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.alter_table("SomeTable", [])

    def test_create_index_raises(self):
        adapter = OdbcAdapter("test.accdb")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.create_index("tbl", "idx", ["col"])

    def test_drop_index_raises(self):
        adapter = OdbcAdapter("test.accdb")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.drop_index("tbl", "idx")

    def test_linked_table_ops_raise(self):
        adapter = OdbcAdapter("test.accdb")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.create_linked_table("lnk", "src", "conn")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.get_linked_tables()
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.unlink_table("lnk")

    def test_relationship_ops_raise(self):
        adapter = OdbcAdapter("test.accdb")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.create_relationship(table_name="t", relationship_name="r", columns=[], foreign_table="f", foreign_columns=[])
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.delete_relationship(table_name="t", relationship_name="r")

    def test_query_mgmt_raises(self):
        adapter = OdbcAdapter("test.accdb")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.create_query("q", "SELECT 1")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.set_query_sql("q", "SELECT 1")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.delete_query("q")

    def test_database_props_raise(self):
        adapter = OdbcAdapter("test.accdb")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.set_database_property("prop", "val")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.create_database("/tmp/new.accdb")

    def test_execute_raw_sql_raises(self):
        adapter = OdbcAdapter("test.accdb")
        with pytest.raises(BackendCapabilityMismatchError):
            adapter.execute_raw_sql("SELECT 1")
