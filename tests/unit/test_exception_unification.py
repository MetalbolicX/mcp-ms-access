import pytest

from ms_access_mcp.backends import BackendCapabilities, BackendSelector
from ms_access_mcp.errors import BackendCapabilityMismatchError, BackendUnavailableError


def test_live_selector_raises_canonical_capability_mismatch() -> None:
    with pytest.raises(BackendCapabilityMismatchError):
        BackendSelector.get_adapter(
            "unused.accdb",
            backend="odbc",
            capabilities=BackendCapabilities.CAN_HANDLE_VBA,
        )


def test_live_selector_raises_canonical_backend_unavailable(monkeypatch) -> None:
    import ms_access_mcp.backends as backends

    monkeypatch.setattr(backends.sys, "platform", "linux")
    with pytest.raises(BackendUnavailableError):
        BackendSelector.get_adapter("unused.accdb", backend="dao")


def test_live_selector_rejects_invalid_backend_value() -> None:
    with pytest.raises(ValueError, match="Invalid ACCESS_MCP_BACKEND value"):
        BackendSelector.get_adapter("unused.accdb", backend="invalid")
