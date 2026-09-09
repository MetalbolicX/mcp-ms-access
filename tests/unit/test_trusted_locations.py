"""Tests for trusted_locations.py — capture/restore Trusted Locations registry keys."""

import sys
from unittest.mock import MagicMock

import pytest


class TestCaptureTrustedLocations:
    """Test capture_trusted_locations() behavior."""

    def test_returns_empty_list_on_non_windows(self, monkeypatch):
        """Returns [] immediately when sys.platform != 'win32'."""
        import ms_access_mcp.backends.trusted_locations as tl_module
        monkeypatch.setattr(tl_module.sys, "platform", "linux")
        result = tl_module.capture_trusted_locations()
        assert result == []

    def test_returns_empty_when_winreg_import_fails(self, monkeypatch):
        """Returns [] when winreg cannot be imported (ImportError caught)."""
        if sys.platform == "win32":
            try:
                import winreg  # noqa: F401 - verify winreg is available
                pytest.skip("winreg is available on this Windows system — cannot test ImportError path")
            except ImportError:
                pass
        import ms_access_mcp.backends.trusted_locations as tl_module
        monkeypatch.setattr(tl_module.sys, "platform", "win32")
        result = tl_module.capture_trusted_locations()
        assert result == []

    def test_platform_check_early_return(self):
        """Non-Windows platforms return [] without attempting registry access."""
        import ms_access_mcp.backends.trusted_locations as tl_module
        # If already on non-Windows, should return [] immediately
        if sys.platform != "win32":
            result = tl_module.capture_trusted_locations()
            assert result == []


class TestRestoreTrustedLocations:
    """Test restore_trusted_locations() behavior."""

    def test_returns_false_on_non_windows(self, monkeypatch):
        """Returns False immediately when sys.platform != 'win32'."""
        import ms_access_mcp.backends.trusted_locations as tl_module
        monkeypatch.setattr(tl_module.sys, "platform", "linux")
        result = tl_module.restore_trusted_locations([{"path": "C:\\Test", "description": "Test"}])
        assert result is False

    def test_returns_true_when_locations_empty(self, monkeypatch):
        """Returns True when given an empty list (no-op)."""
        import ms_access_mcp.backends.trusted_locations as tl_module
        monkeypatch.setattr(tl_module.sys, "platform", "win32")
        result = tl_module.restore_trusted_locations([])
        assert result is True

    def test_returns_false_when_winreg_import_fails(self, monkeypatch):
        """Returns False when winreg cannot be imported."""
        if sys.platform == "win32":
            try:
                import winreg  # noqa: F401
                pytest.skip("winreg is available on this Windows system — cannot test ImportError path")
            except ImportError:
                pass
        import ms_access_mcp.backends.trusted_locations as tl_module
        monkeypatch.setattr(tl_module.sys, "platform", "win32")
        result = tl_module.restore_trusted_locations([{"path": "C:\\Test", "description": "Test"}])
        assert result is False

    def test_platform_check_early_return_restore(self):
        """Non-Windows platforms return False without attempting registry access."""
        import ms_access_mcp.backends.trusted_locations as tl_module
        if sys.platform != "win32":
            result = tl_module.restore_trusted_locations([{"path": "C:\\Test", "description": "Test"}])
            assert result is False


class TestTrustedLocationsIntegration:
    """Integration tests for trusted_locations with mock registry (when on Windows)."""

    def test_capture_calls_registry_keys(self, monkeypatch):
        """Calls HKLM and HKCU registry keys when reading Trusted Locations."""
        if sys.platform != "win32":
            pytest.skip("Windows-only test")

        import ms_access_mcp.backends.trusted_locations as tl_module

        mock_winreg = MagicMock()
        mock_parent_key = MagicMock()

        # Track which keys were opened
        opened_paths = []

        def fake_open_key(hkey, path, *args):
            opened_paths.append((hkey, path))
            return mock_parent_key

        def fake_enum_key(parent_key, index):
            if index == 0:
                return "Location0"
            raise OSError("No more keys")

        def fake_query_value_ex(key, name):
            if name == "Path":
                return ("C:\\TrustedTest", None)
            elif name == "Description":
                return ("Test description", None)
            raise FileNotFoundError(f"Key not found: {name}")

        mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
        mock_winreg.HKEY_CURRENT_USER = 0x80000001
        mock_winreg.KEY_READ = 1
        mock_winreg.OpenKey = fake_open_key
        mock_winreg.EnumKey = fake_enum_key
        mock_winreg.QueryValueEx = fake_query_value_ex
        mock_winreg.CloseKey = MagicMock()

        import sys as real_sys
        real_sys.modules["winreg"] = mock_winreg

        try:
            result = tl_module.capture_trusted_locations()
            # Should have found 2 locations (1 per registry hive)
            assert len(result) == 2
            assert all("TrustedTest" in loc["path"] for loc in result)
            # Verify HKLM and HKCU were both queried
            assert any(hk in (0x80000002, 0x80000001) for hk, _ in opened_paths)
        finally:
            if "winreg" in real_sys.modules and isinstance(real_sys.modules["winreg"], MagicMock):
                del real_sys.modules["winreg"]

    def test_restore_writes_to_registry(self, monkeypatch):
        """Writes Trusted Locations to both HKLM and HKCU registry keys."""
        if sys.platform != "win32":
            pytest.skip("Windows-only test")

        import ms_access_mcp.backends.trusted_locations as tl_module

        mock_winreg = MagicMock()
        mock_parent_key = MagicMock()
        written = []

        def fake_create_key(hkey, path):
            written.append((hkey, path))
            return mock_parent_key

        mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
        mock_winreg.HKEY_CURRENT_USER = 0x80000001
        mock_winreg.REG_SZ = 1
        mock_winreg.CreateKey = fake_create_key
        mock_winreg.SetValueEx = MagicMock()
        mock_winreg.CloseKey = MagicMock()

        import sys as real_sys
        real_sys.modules["winreg"] = mock_winreg

        try:
            locations = [{"path": "C:\\Trusted1", "description": "Desc1"}]
            result = tl_module.restore_trusted_locations(locations)
            assert result is True
            # Should have written to HKLM and HKCU
            hkeys_written = [h for h, p in written if h in (0x80000002, 0x80000001)]
            assert len(hkeys_written) >= 2
        finally:
            if "winreg" in real_sys.modules and isinstance(real_sys.modules["winreg"], MagicMock):
                del real_sys.modules["winreg"]

    def test_restore_skips_empty_description(self, monkeypatch):
        """Only writes Description key if description is non-empty."""
        if sys.platform != "win32":
            pytest.skip("Windows-only test")

        import ms_access_mcp.backends.trusted_locations as tl_module

        mock_winreg = MagicMock()
        mock_parent_key = MagicMock()
        set_calls = []

        def fake_create_key(hkey, path):
            return mock_parent_key

        def fake_set_value_ex(key, name, *args):
            set_calls.append(name)

        mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
        mock_winreg.HKEY_CURRENT_USER = 0x80000001
        mock_winreg.REG_SZ = 1
        mock_winreg.CreateKey = fake_create_key
        mock_winreg.SetValueEx = fake_set_value_ex
        mock_winreg.CloseKey = MagicMock()

        import sys as real_sys
        real_sys.modules["winreg"] = mock_winreg

        try:
            locations = [{"path": "C:\\NoDesc", "description": ""}]
            tl_module.restore_trusted_locations(locations)
            # Description should NOT be written when empty
            assert "Description" not in set_calls
        finally:
            if "winreg" in real_sys.modules and isinstance(real_sys.modules["winreg"], MagicMock):
                del real_sys.modules["winreg"]
