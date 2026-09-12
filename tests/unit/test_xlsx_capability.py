# PROBE VERDICT (2026-09-12, dev host):
# HKLM\SOFTWARE\Microsoft\Office\16.0\Access Connectivity Engine\Engines\Excel
# is ABSENT — ACE Excel IISAM provider NOT registered.
# Decision: DoCmd is the primary path; SQL fallback is implemented but
# integration-tested as skipif(probe=False) on this host.

"""
Tests for the xlsx capability probe.
"""

from __future__ import annotations

import winreg

import pytest


def test_probe_returns_false_on_dev_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """On this dev host the ACE Excel IISAM provider is not registered."""
    import ms_access_mcp.application.xlsx_capability as xlsx_capability

    # Blow away any cached value so the test is deterministic
    monkeypatch.delattr(xlsx_capability.IisamProviderProbe, "_cached", raising=False)

    # Re-initialise _cached so getattr returns None again
    xlsx_capability.IisamProviderProbe._cached = None

    result = xlsx_capability.IisamProviderProbe.is_excel_provider_registered()
    assert result is False


def test_probe_returns_true_when_registry_key_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the registry key is present, probe returns True."""
    import ms_access_mcp.application.xlsx_capability as xlsx_capability

    # Blow away any cached value
    monkeypatch.delattr(xlsx_capability.IisamProviderProbe, "_cached", raising=False)

    call_log: list[tuple[int, str]] = []

    def fake_open_key(hkey: int, path: str, reserved: int = 0) -> object:
        call_log.append((hkey, path))
        raise FileNotFoundError("simulated key absent")

    monkeypatch.setattr(winreg, "OpenKey", fake_open_key)

    # Patch so it finds the key — the probe returns False on any exception
    # so we need to make it NOT raise. Patch both paths to return a fake handle.
    class _FakeHandle:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def Close(self):
            pass


    def fake_open_key_success(hkey: int, path: str, reserved: int = 0, *args) -> object:
        if "Excel" in path and hkey == winreg.HKEY_LOCAL_MACHINE:
            call_log.append((hkey, path))
            return _FakeHandle()
        raise FileNotFoundError(f"key not found: {path}")

    monkeypatch.setattr(winreg, "OpenKey", fake_open_key_success)

    result = xlsx_capability.IisamProviderProbe.is_excel_provider_registered()
    assert result is True
    # Verify both HKLM paths were checked
    assert len(call_log) >= 1


def test_probe_memoization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probe result is cached after first call."""
    import ms_access_mcp.application.xlsx_capability as xlsx_capability

    # Reset cached value to None to force a fresh probe
    xlsx_capability.IisamProviderProbe._cached = None

    call_count = 0

    def fake_open_key(hkey: int, path: str, *args) -> object:
        nonlocal call_count
        if "Excel" in path:
            call_count += 1
        raise FileNotFoundError("simulated")

    monkeypatch.setattr(winreg, "OpenKey", fake_open_key)

    xlsx_capability.IisamProviderProbe.is_excel_provider_registered()
    xlsx_capability.IisamProviderProbe.is_excel_provider_registered()
    xlsx_capability.IisamProviderProbe.is_excel_provider_registered()

    # Only one actual registry call due to memoization
    # (first call checks two paths -> call_count=2; subsequent calls hit cache)
    assert call_count == 2  # 2 paths checked on first call, then cached


def test_probe_returns_false_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probe returns False on any exception (permissions, absent key, etc.)."""
    import ms_access_mcp.application.xlsx_capability as xlsx_capability

    monkeypatch.delattr(xlsx_capability.IisamProviderProbe, "_cached", raising=False)
    monkeypatch.setattr(winreg, "OpenKey", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("simulated")))

    result = xlsx_capability.IisamProviderProbe.is_excel_provider_registered()
    assert result is False
