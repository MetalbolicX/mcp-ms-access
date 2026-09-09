"""Backend capabilities and resolution — port from oracle backend_selector.py.

REQ-9: All 10 flags defined here.
"""

import sys
from enum import Flag, auto
from typing import Literal


class BackendCapabilities(Flag):
    """Flags describing what operations a backend must support."""

    CAN_READ_DATA = auto()
    CAN_WRITE_DATA = auto()
    CAN_INTROSPECT_SCHEMA = auto()
    CAN_HANDLE_VBA = auto()
    CAN_HANDLE_FORMS = auto()
    CAN_HANDLE_REPORTS = auto()
    CAN_HANDLE_MACROS = auto()
    CAN_COMPACT = auto()
    CAN_CREATE_LINKED_TABLE = auto()
    CAN_IMPORT_EXPORT_TEXT = auto()


# Capability bundles
SCHEMA_CAPS = BackendCapabilities.CAN_INTROSPECT_SCHEMA
DATA_READ_CAPS = (
    BackendCapabilities.CAN_READ_DATA | BackendCapabilities.CAN_INTROSPECT_SCHEMA
)
DATA_WRITE_CAPS = (
    BackendCapabilities.CAN_READ_DATA
    | BackendCapabilities.CAN_WRITE_DATA
    | BackendCapabilities.CAN_INTROSPECT_SCHEMA
)
VBA_CAPS = BackendCapabilities.CAN_HANDLE_VBA | BackendCapabilities.CAN_INTROSPECT_SCHEMA


class BackendCapabilityMismatchError(Exception):
    """Raised when requested capabilities require a backend that is unavailable.

    REQ-3, REQ-23, REQ-24.
    """

    pass


class BackendUnavailableError(Exception):
    """Raised when a backend is explicitly requested but unavailable on the platform.

    REQ-6, REQ-19.
    """

    pass


# Capabilities that require the COM backend.
_COM_ONLY_CAPS = (
    BackendCapabilities.CAN_HANDLE_VBA
    | BackendCapabilities.CAN_HANDLE_FORMS
    | BackendCapabilities.CAN_HANDLE_REPORTS
    | BackendCapabilities.CAN_HANDLE_MACROS
    | BackendCapabilities.CAN_COMPACT
    | BackendCapabilities.CAN_IMPORT_EXPORT_TEXT
)

AUTOMATION_CAPS = (
    BackendCapabilities.CAN_HANDLE_FORMS
    | BackendCapabilities.CAN_HANDLE_REPORTS
    | BackendCapabilities.CAN_HANDLE_VBA
    | BackendCapabilities.CAN_HANDLE_MACROS
)


def _requires_com(capabilities: BackendCapabilities | None) -> bool:
    """Return True if any flag in capabilities is COM-only."""
    if capabilities is None:
        return False
    return bool(capabilities & _COM_ONLY_CAPS)


def _is_dao_constructable() -> bool:
    """Return True if DAO engine can be constructed on this host.

    Cheap probe: non-Windows → False; Windows needs win32com importable.
    """
    if sys.platform != "win32":
        return False
    try:

        return True
    except Exception:
        return False


def resolve_backend(
    requested: str,
    caps: BackendCapabilities,
    platform: str,
) -> Literal["odbc", "dao", "ado", "access"]:
    """Resolve backend based on requested name and required capabilities.

    Args:
        requested: Backend name ("auto", "odbc", "dao", "ado", "access")
        caps: Required capabilities
        platform: sys.platform value

    Returns:
        Resolved backend name

    Raises:
        BackendUnavailableError: If requested backend unavailable on platform
        BackendCapabilityMismatchError: If ODBC conflicts with COM-only capabilities
    """
    normalized = requested.lower().strip()

    if normalized not in {"auto", "odbc", "dao", "ado", "access"}:
        raise ValueError(
            f"Invalid backend value: {requested!r}. "
            f"Expected one of: auto, odbc, dao, ado, access."
        )

    requires_com_flag = _requires_com(caps)

    # Auto-resolution
    if normalized == "auto":
        if requires_com_flag:
            return "access"  # COM automation
        elif _is_dao_constructable():
            return "dao"
        else:
            return "odbc"

    # Explicit backend validation
    if normalized == "odbc":
        if requires_com_flag:
            cap_names = [f.name for f in BackendCapabilities if f in caps & _COM_ONLY_CAPS]
            raise BackendCapabilityMismatchError(
                f"backend=odbc conflicts with capability {cap_names[0]} which requires COM. "
                f"(Requested capabilities: {[f.name for f in BackendCapabilities if f in caps]})"
            )
        return "odbc"

    if normalized == "dao":
        if platform != "win32":
            raise BackendUnavailableError(
                "DAO backend is not available on non-Windows platforms. "
                "Use backend='odbc'."
            )
        if not _is_dao_constructable():
            raise BackendUnavailableError(
                "DAO backend requires win32com.client. "
                "Ensure Microsoft Access or Access Runtime is installed."
            )
        return "dao"

    if normalized == "ado":
        return "ado"

    if normalized == "access":
        if platform != "win32":
            raise BackendUnavailableError(
                "COM automation is not available on non-Windows platforms. "
                "Use backend='odbc'."
            )
        return "access"

    # Defensive - should not reach here
    raise ValueError(f"Unknown backend: {requested!r}")
