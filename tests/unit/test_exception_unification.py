import pytest

from ms_access_mcp.capabilities import BackendCapabilities, resolve_backend
from ms_access_mcp.errors import (
    BackendCapabilityMismatchError,
    BackendUnavailableError,
)


def test_capabilities_raises_canonical_capability_mismatch() -> None:
    with pytest.raises(BackendCapabilityMismatchError):
        resolve_backend(
            "odbc",
            BackendCapabilities.CAN_HANDLE_VBA,
            "linux",
        )


def test_capabilities_raises_canonical_backend_unavailable() -> None:
    with pytest.raises(BackendUnavailableError):
        resolve_backend("dao", BackendCapabilities(0), "linux")
