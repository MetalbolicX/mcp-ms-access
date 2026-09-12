"""Capability probe for the ACE Excel IISAM provider.

This module determines at runtime whether the Microsoft Access Database Engine
2016 Redistributable's Excel IISAM provider is registered on the host.
When registered, the IISAM SQL fallback path is preferred for DAO/ADO backends.
When absent, the DoCmd.TransferSpreadsheet path is used for Access backends.

Registry keys checked:
  HKLM\\SOFTWARE\\Microsoft\\Office\\16.0\\Access Connectivity Engine\\Engines\\Excel
  HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\Office\\16.0\\Access Connectivity Engine\\Engines\\Excel

Returns False on any exception (key absent, permissions, etc.).
Result is memoized for the process lifetime.
"""

from __future__ import annotations

import winreg

# Registry path constants (Access Connectivity Engine Excel provider)
_ACE_ENGINE_KEY = r"SOFTWARE\Microsoft\Office\16.0\Access Connectivity Engine\Engines\Excel"
_ACE_ENGINE_KEY_WOW = r"SOFTWARE\WOW6432Node\Microsoft\Office\16.0\Access Connectivity Engine\Engines\Excel"


class IisamProviderProbe:
    """Probe for the ACE Excel IISAM provider registration."""

    # Memoized result: None = not yet checked, True/False = cached verdict
    _cached: bool | None = None

    @classmethod
    def is_excel_provider_registered(cls) -> bool:
        """Return True if the ACE Excel IISAM provider is registered.

        Reads the registry to check for the Excel engine key.
        Returns False on any exception (key absent, permissions, etc.).
        Result is cached after first call for the process lifetime.
        """
        cached = getattr(cls, "_cached", None)
        if cached is not None:
            return cached

        verdict = cls._probe()
        cls._cached = verdict
        return verdict

    @staticmethod
    def _probe() -> bool:
        """Perform the actual registry probe. Returns True if registered."""
        for hkey_root, sub_key in (
            (winreg.HKEY_LOCAL_MACHINE, _ACE_ENGINE_KEY),
            (winreg.HKEY_LOCAL_MACHINE, _ACE_ENGINE_KEY_WOW),
        ):
            try:
                with winreg.OpenKey(hkey_root, sub_key, 0, winreg.KEY_READ):
                    # Key exists — provider is registered
                    # We don't need to read a value; existence of the key is sufficient
                    return True
            except (FileNotFoundError, PermissionError, OSError):
                pass
        return False
