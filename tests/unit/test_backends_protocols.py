"""Protocol conformance tests for CrudBackendProtocol.

These tests verify that AdoAdapter, OdbcAdapter, and DaoAdapter all conform
to the CrudBackendProtocol structural protocol.
"""

from __future__ import annotations

from ms_access_mcp.backends.ado import AdoAdapter
from ms_access_mcp.backends.dao import DaoAdapter
from ms_access_mcp.backends.odbc import OdbcAdapter
from ms_access_mcp.backends.protocols import CrudBackendProtocol


class TestCrudBackendProtocolConformance:
    """Verify all three adapters conform to CrudBackendProtocol."""

    def test_ado_adapter_conforms(self):
        """AdoAdapter must be a CrudBackendProtocol."""
        adapter = AdoAdapter("test.accdb")
        assert isinstance(adapter, CrudBackendProtocol)

    def test_odbc_adapter_conforms(self):
        """OdbcAdapter must be a CrudBackendProtocol."""
        adapter = OdbcAdapter("test.accdb")
        assert isinstance(adapter, CrudBackendProtocol)

    def test_dao_adapter_conforms(self):
        """DaoAdapter must be a CrudBackendProtocol."""
        adapter = DaoAdapter("test.accdb")
        assert isinstance(adapter, CrudBackendProtocol)


class TestCrudBackendProtocolDriftGuard:
    """Ensure any future adapter exposing CRUD surface conforms to the protocol."""

    def test_all_known_adapters_enumerated_and_conform(self):
        """Enumerated adapters must all conform — this is the drift guard."""
        adapters = [
            AdoAdapter("test.accdb"),
            OdbcAdapter("test.accdb"),
            DaoAdapter("test.accdb"),
        ]
        for adapter in adapters:
            assert isinstance(adapter, CrudBackendProtocol)
