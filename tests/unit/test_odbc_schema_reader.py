"""Tests for OdbcSchemaReader — row grouping, FK name synthesis, exception handling."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from ms_access_mcp.backends.odbc_schema_reader import OdbcSchemaReader


class TestOdbcSchemaReaderGroupRows:
    """Row-grouping logic via _group_rows static method."""

    def test_single_column_fk_produces_one_relationshipinfo(self) -> None:
        rows = [("Orders", "CustomerID", "Customers", "ID")]
        result = OdbcSchemaReader._group_rows(rows)
        assert len(result) == 1
        r = result[0]
        assert r.name == "FK_Orders_Customers"
        assert r.table == "Orders"
        assert r.foreign_table == "Customers"
        assert r.columns == ["CustomerID"]
        assert r.foreign_columns == ["ID"]

    def test_multi_column_fk_merges_into_one_relationshipinfo(self) -> None:
        # Two rows for the same FK — same (child, parent) pair with different columns
        rows = [
            ("Orders", "CustomerID", "Customers", "ID"),
            ("Orders", "AddressID", "Customers", "ID"),
        ]
        result = OdbcSchemaReader._group_rows(rows)
        assert len(result) == 1
        r = result[0]
        assert r.columns == ["CustomerID", "AddressID"]
        assert r.foreign_columns == ["ID", "ID"]

    def test_multiple_fks_grouped_separately(self) -> None:
        rows = [
            ("Orders", "CustomerID", "Customers", "ID"),
            ("Orders", "EmployeeID", "Employees", "ID"),
        ]
        result = OdbcSchemaReader._group_rows(rows)
        assert len(result) == 2
        result.sort(key=lambda r: r.name)
        assert result[0].name == "FK_Orders_Customers"
        assert result[1].name == "FK_Orders_Employees"

    def test_deterministic_sort_by_name(self) -> None:
        rows = [
            ("B", "X", "A", "X"),
            ("A", "X", "B", "X"),
        ]
        result = OdbcSchemaReader._group_rows(rows)
        # Sort by name: FK_A_B comes before FK_B_A alphabetically
        assert [r.name for r in result] == ["FK_A_B", "FK_B_A"]

    def test_empty_rows_returns_empty_list(self) -> None:
        result = OdbcSchemaReader._group_rows([])
        assert result == []

    def test_fk_name_synthesis_format(self) -> None:
        rows = [("ChildTable", "FKCol", "ParentTable", "PKCol")]
        result = OdbcSchemaReader._group_rows(rows)
        assert result[0].name == "FK_ChildTable_ParentTable"


class TestOdbcSchemaReaderGetRelationships:
    """Integration-style tests with mocked connection."""

    def test_connection_none_returns_empty(self) -> None:
        reader = OdbcSchemaReader(None, logging.getLogger())
        assert reader.get_relationships() == []

    def test_query_exception_returns_empty_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = Exception("Access denied")
        mock_conn.cursor.return_value = mock_cursor

        reader = OdbcSchemaReader(mock_conn, logging.getLogger())
        with caplog.at_level(logging.WARNING):
            result = reader.get_relationships()

        assert result == []
        assert any("get_relationships via MSysRelationships failed" in r.message for r in caplog.records)

    def test_successful_query_returns_grouped_relationships(self) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("Orders", "CustomerID", "Customers", "ID"),
        ]
        mock_conn.cursor.return_value = mock_cursor

        reader = OdbcSchemaReader(mock_conn, logging.getLogger())
        result = reader.get_relationships()

        assert len(result) == 1
        assert result[0].name == "FK_Orders_Customers"
        mock_cursor.close.assert_called_once()
