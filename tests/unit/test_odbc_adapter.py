"""Unit tests for ODBC adapter."""

from __future__ import annotations

import pytest

from ms_access_mcp.backends.odbc import OdbcAdapter
from ms_access_mcp.errors import BackendCapabilityMismatchError


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
