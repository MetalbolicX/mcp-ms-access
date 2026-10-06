"""Live DAO empty-result column pin.

The ODBC path already preserves empty-result columns through
``cursor.description``; this DAO pin protects backend parity.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.com_integration,
]


def test_dao_empty_query_preserves_columns(temp_db_copy: str) -> None:
    from ms_access_mcp.backends.dao import DaoAdapter

    adapter = DaoAdapter(temp_db_copy)
    adapter.connect()
    try:
        result = adapter.execute_query(
            "SELECT CategoryID, CategoryName FROM Categories WHERE 1=0"
        )

        assert result.success is True, result.error
        assert result.columns == ["CategoryID", "CategoryName"]
        assert result.rows == []
    finally:
        adapter.disconnect()
