"""Opt-in ODBC parameter contract probe against the bundled read-only fixture.

Skipped unless Windows, the Access ODBC driver, and the fixture ``test_db.accdb``
are available. The fixture is opened with ``readonly=True`` at the engine level and
only ``SELECT`` statements run: no writes, macros, VBA, user data, or Northwind.
Fixture trees are never created or deleted.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ms_access_mcp.backends.odbc import OdbcAdapter
from tests.integration.conftest import TEST_DB

IS_WINDOWS = os.name == "nt"


def _has_ace_odbc_driver() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import pyodbc
    except ImportError:
        return False
    return any("Access" in driver for driver in pyodbc.drivers())


pytestmark = [
    pytest.mark.odbc_integration,
    pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only test"),
    pytest.mark.skipif(not _has_ace_odbc_driver(), reason="Requires Access ODBC driver"),
    pytest.mark.skipif(not TEST_DB or not Path(TEST_DB).exists(), reason="No test fixture found"),
]


def _readonly_adapter() -> OdbcAdapter:
    """Open the fixture read-only and inject it into a real OdbcAdapter."""
    import pyodbc

    adapter = OdbcAdapter(TEST_DB)
    connection = pyodbc.connect(OdbcAdapter._connection_string(TEST_DB, ""), readonly=True)
    adapter._conn = connection
    adapter._connected = True
    return adapter


def test_readonly_query_without_params_executes_plain_sql():
    adapter = _readonly_adapter()
    try:
        result = adapter.execute_query("SELECT [CustomerID] FROM [Customers] ORDER BY [CustomerID]")
    finally:
        adapter.disconnect()

    assert result.success is True
    assert result.count == 5


def test_readonly_query_binds_nonempty_params():
    adapter = _readonly_adapter()
    try:
        result = adapter.execute_query(
            "SELECT [CustomerID] FROM [Customers] WHERE [Country] = ?",
            params=["Germany"],
        )
    finally:
        adapter.disconnect()

    assert result.success is True
    assert result.count == 1


def test_readonly_query_binds_none_element():
    adapter = _readonly_adapter()
    try:
        result = adapter.execute_query(
            "SELECT [CustomerID] FROM [Customers] WHERE [Region] = ?",
            params=[None],
        )
    finally:
        adapter.disconnect()

    assert result.success is True
    assert result.count == 0
