"""Tests for capabilities.py — BackendCapabilities Flag and resolve_backend."""

import sys
from unittest.mock import patch

import pytest

from ms_access_mcp.capabilities import (
    DATA_READ_CAPS,
    DATA_WRITE_CAPS,
    SCHEMA_CAPS,
    VBA_CAPS,
    BackendCapabilities,
    BackendCapabilityMismatchError,
    BackendUnavailableError,
    resolve_backend,
)


class TestBackendCapabilities:
    """Test BackendCapabilities Flag values."""

    def test_all_flags_defined(self):
        """All 10 flags must be defined per REQ-9."""
        flags = list(BackendCapabilities)
        assert len(flags) == 10

    def test_flag_values(self):
        """Verify each flag has a unique auto() value."""
        values = [f.value for f in BackendCapabilities]
        assert len(values) == len(set(values))

    def test_can_read_data(self):
        assert BackendCapabilities.CAN_READ_DATA.value != 0

    def test_can_write_data(self):
        assert BackendCapabilities.CAN_WRITE_DATA.value != 0

    def test_can_introspect_schema(self):
        assert BackendCapabilities.CAN_INTROSPECT_SCHEMA.value != 0

    def test_can_handle_vba(self):
        assert BackendCapabilities.CAN_HANDLE_VBA.value != 0

    def test_can_handle_forms(self):
        assert BackendCapabilities.CAN_HANDLE_FORMS.value != 0

    def test_can_handle_reports(self):
        assert BackendCapabilities.CAN_HANDLE_REPORTS.value != 0

    def test_can_handle_macros(self):
        assert BackendCapabilities.CAN_HANDLE_MACROS.value != 0

    def test_can_compact(self):
        assert BackendCapabilities.CAN_COMPACT.value != 0

    def test_can_create_linked_table(self):
        assert BackendCapabilities.CAN_CREATE_LINKED_TABLE.value != 0

    def test_can_import_export_text(self):
        assert BackendCapabilities.CAN_IMPORT_EXPORT_TEXT.value != 0


class TestCapabilityBundles:
    """Test predefined capability bundles."""

    def test_schema_caps(self):
        assert SCHEMA_CAPS == BackendCapabilities.CAN_INTROSPECT_SCHEMA

    def test_data_read_caps(self):
        expected = BackendCapabilities.CAN_READ_DATA | BackendCapabilities.CAN_INTROSPECT_SCHEMA
        assert DATA_READ_CAPS == expected

    def test_data_write_caps(self):
        expected = (
            BackendCapabilities.CAN_READ_DATA
            | BackendCapabilities.CAN_WRITE_DATA
            | BackendCapabilities.CAN_INTROSPECT_SCHEMA
        )
        assert DATA_WRITE_CAPS == expected

    def test_vba_caps(self):
        expected = BackendCapabilities.CAN_HANDLE_VBA | BackendCapabilities.CAN_INTROSPECT_SCHEMA
        assert VBA_CAPS == expected


class TestResolveBackend:
    """Test resolve_backend function."""

    def test_invalid_backend_raises(self):
        """Invalid backend name raises ValueError."""
        with pytest.raises(ValueError, match="Invalid backend value"):
            resolve_backend("invalid", BackendCapabilities.CAN_READ_DATA, sys.platform)

    def test_auto_resolves_based_on_platform(self):
        """Auto resolves based on actual platform capabilities."""
        # On any platform, this should not raise
        result = resolve_backend("auto", BackendCapabilities.CAN_READ_DATA, sys.platform)
        # Result should be one of the valid backends
        assert result in ("odbc", "dao", "access")

    def test_auto_resolves_to_odbc_when_dao_unavailable(self):
        """Auto on non-Windows resolves to odbc since DAO is Windows-only."""
        with patch("ms_access_mcp.capabilities._is_dao_constructable", return_value=False):
            result = resolve_backend("auto", BackendCapabilities.CAN_READ_DATA, "linux")
            assert result == "odbc"

    def test_auto_resolves_to_dao_when_constructable(self):
        """Auto on Windows with DAO constructable resolves to dao."""
        # This test will use the actual platform behavior
        if sys.platform == "win32":
            try:
                import win32com.client  # noqa: F401
                result = resolve_backend("auto", BackendCapabilities.CAN_READ_DATA, "win32")
                assert result == "dao"
            except ImportError:
                result = resolve_backend("auto", BackendCapabilities.CAN_READ_DATA, "win32")
                assert result == "odbc"

    def test_auto_resolves_to_access_for_com_caps(self):
        """Auto with COM-only capability resolves to access."""
        caps = BackendCapabilities.CAN_HANDLE_VBA | BackendCapabilities.CAN_READ_DATA
        if sys.platform == "win32":
            result = resolve_backend("auto", caps, "win32")
            assert result == "access"
        else:
            result = resolve_backend("auto", caps, "linux")
            assert result == "odbc"

    def test_odbc_rejects_com_only_caps(self):
        """ODBC backend rejects COM-only capabilities."""
        caps = BackendCapabilities.CAN_HANDLE_VBA | BackendCapabilities.CAN_READ_DATA
        with pytest.raises(BackendCapabilityMismatchError, match="requires COM"):
            resolve_backend("odbc", caps, "win32")

    def test_dao_unavailable_on_non_windows(self):
        """DAO backend raises BackendUnavailableError on non-Windows."""
        with pytest.raises(BackendUnavailableError, match="non-Windows"):
            resolve_backend("dao", BackendCapabilities.CAN_READ_DATA, "linux")

    def test_dao_available_on_windows_when_constructable(self):
        """DAO on Windows is available when win32com is importable."""
        if sys.platform == "win32":
            try:
                import win32com.client  # noqa: F401
                result = resolve_backend("dao", BackendCapabilities.CAN_READ_DATA, "win32")
                assert result == "dao"
            except ImportError:
                with pytest.raises(BackendUnavailableError, match="win32com"):
                    resolve_backend("dao", BackendCapabilities.CAN_READ_DATA, "win32")

    def test_access_unavailable_on_non_windows(self):
        """COM access backend raises BackendUnavailableError on non-Windows."""
        with pytest.raises(BackendUnavailableError, match="non-Windows"):
            resolve_backend("access", BackendCapabilities.CAN_READ_DATA, "linux")

    def test_access_available_on_windows(self):
        """COM access backend is available on Windows."""
        if sys.platform == "win32":
            result = resolve_backend("access", BackendCapabilities.CAN_READ_DATA, "win32")
            assert result == "access"

    def test_ado_resolves_to_ado(self):
        """ADO backend resolves to ado."""
        result = resolve_backend("ado", BackendCapabilities.CAN_READ_DATA, sys.platform)
        assert result == "ado"

    def test_odbc_resolves_to_odbc(self):
        """ODBC backend resolves to odbc."""
        result = resolve_backend("odbc", BackendCapabilities.CAN_READ_DATA, sys.platform)
        assert result == "odbc"

    def test_case_insensitive(self):
        """Backend names are case-insensitive."""
        result = resolve_backend("ODBC", BackendCapabilities.CAN_READ_DATA, sys.platform)
        assert result == "odbc"
        result = resolve_backend("Auto", BackendCapabilities.CAN_READ_DATA, sys.platform)
        assert result in ("odbc", "dao", "access")

    def test_whitespace_stripped(self):
        """Backend names are stripped of whitespace."""
        result = resolve_backend("  odbc  ", BackendCapabilities.CAN_READ_DATA, sys.platform)
        assert result == "odbc"
