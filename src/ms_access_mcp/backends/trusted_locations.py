"""Trusted Locations management for Microsoft Access VBA operations.

Ported from oracle: ms_access_mcp/adapters/trusted_locations.py
Difference: uses stdlib logging instead of ..logging.get_logger.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

_logger = logging.getLogger(__name__)


def capture_trusted_locations() -> list[dict]:
    """Capture current Trusted Locations from Windows registry.

    Uses PowerShell to read HKLM and HKCU Trusted Locations registry keys.
    Returns list of dicts: [{"path": "...", "description": "..."}] or empty list.

    Returns:
        List of Trusted Location dicts, or empty list on non-Windows or error.

    """
    if sys.platform != "win32":
        return []

    try:
        import winreg
    except ImportError:
        _logger.debug("winreg not available, skipping Trusted Locations capture")
        return []

    locations: list[dict] = []

    def read_key(hkey: Any, subkey_path: str) -> None:
        """Read Trusted Locations from a specific registry key."""
        try:
            key = winreg.OpenKey(hkey, subkey_path, 0, winreg.KEY_READ)
            try:
                i = 0
                while True:
                    try:
                        loc_name = winreg.EnumKey(key, i)
                        loc_path_key = winreg.OpenKey(key, loc_name, 0, winreg.KEY_READ)
                        try:
                            path_val, _ = winreg.QueryValueEx(loc_path_key, "Path")
                            desc_val, _ = winreg.QueryValueEx(loc_path_key, "Description")
                            locations.append({
                                "path": path_val,
                                "description": desc_val if desc_val else "",
                            })
                        except FileNotFoundError:
                            # Path not found, skip silently
                            pass
                        except Exception:
                            # Skip entries we can't read
                            pass
                        finally:
                            winreg.CloseKey(loc_path_key)
                        i += 1
                    except OSError:
                        break
            finally:
                winreg.CloseKey(key)
        except FileNotFoundError:
            # Key doesn't exist, nothing to capture
            pass
        except Exception:
            pass

    try:
        read_key(winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\Microsoft\Office\16.0\Access\Security\Trusted Locations")
        read_key(winreg.HKEY_CURRENT_USER,
                 r"SOFTWARE\Microsoft\Office\16.0\Access\Security\Trusted Locations")
    except Exception:
        pass

    return locations


def restore_trusted_locations(locations: list[dict]) -> bool:
    """Restore Trusted Locations to Windows registry.

    Args:
        locations: List of dicts with "path" and "description" keys.

    Returns:
        True on success, False on failure.

    """
    if sys.platform != "win32":
        return False

    if not locations:
        return True

    try:
        import winreg
    except ImportError:
        _logger.debug("winreg not available, skipping Trusted Locations restore")
        return False

    def write_location(hkey: Any, subkey_path: str, locs: list[dict]) -> None:
        """Write Trusted Locations to a specific registry key."""
        try:
            key = winreg.CreateKey(hkey, subkey_path)
            for idx, loc in enumerate(locs):
                loc_name = f"Location{idx + 1}"
                loc_key = winreg.CreateKey(key, loc_name)
                try:
                    winreg.SetValueEx(loc_key, "Path", 0, winreg.REG_SZ, loc.get("path", ""))
                    desc = loc.get("description", "")
                    if desc:
                        winreg.SetValueEx(loc_key, "Description", 0, winreg.REG_SZ, desc)
                finally:
                    winreg.CloseKey(loc_key)
            winreg.CloseKey(key)
        except Exception:
            pass

    try:
        hklm_path = r"SOFTWARE\Microsoft\Office\16.0\Access\Security\Trusted Locations"
        hkcu_path = r"SOFTWARE\Microsoft\Office\16.0\Access\Security\Trusted Locations"
        write_location(winreg.HKEY_LOCAL_MACHINE, hklm_path, locations)
        write_location(winreg.HKEY_CURRENT_USER, hkcu_path, locations)
        return True
    except Exception as e:
        _logger.warning(f"Failed to restore Trusted Locations: {e}")
        return False
