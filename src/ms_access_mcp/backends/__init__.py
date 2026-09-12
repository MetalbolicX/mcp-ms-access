"""Backend selector — capabilities, errors, and adapter factory."""

import logging
import os
import sys
from enum import Flag, auto
from typing import Literal, cast

_logger = logging.getLogger(__name__)

_dao_fallback_warned: bool = False


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
    """Raised when requested capabilities require a backend that is unavailable."""



class BackendUnavailableError(Exception):
    """Raised when a backend is explicitly requested but unavailable on the platform."""



_VALID_BACKEND_VALUES = frozenset({"auto", "odbc", "com", "dao"})

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

MAINTENANCE_CAPS = (
    BackendCapabilities.CAN_COMPACT
    | BackendCapabilities.CAN_IMPORT_EXPORT_TEXT
)


def _normalize_backend(value: str) -> str:
    normalized = value.lower().strip()
    if normalized not in _VALID_BACKEND_VALUES:
        msg = (
            f"Invalid ACCESS_MCP_BACKEND value: {value!r}. "
            f"Expected one of: {', '.join(sorted(_VALID_BACKEND_VALUES))}."
        )
        raise ValueError(msg)
    return normalized


def _requires_com(capabilities: BackendCapabilities | None) -> bool:
    if capabilities is None:
        return False
    return bool(capabilities & _COM_ONLY_CAPS)


def _com_only_cap_names(capabilities: BackendCapabilities) -> list[str]:
    result: list[str] = []
    for flag in BackendCapabilities:
        if flag in capabilities & _COM_ONLY_CAPS:
            result.append(cast(str, flag.name))
    return result


def _is_dao_constructable() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401
    except Exception:
        return False
    return True


def backend_available(backend: str) -> bool:
    """Return whether the requested backend can be constructed on this platform."""
    normalized = _normalize_backend(backend)
    if normalized == "auto":
        return True
    if normalized == "odbc":
        return True
    if normalized == "com":
        return sys.platform == "win32"
    if normalized == "dao":
        return _is_dao_constructable()
    msg = f"Unknown backend: {backend!r}"
    raise ValueError(msg)


class BackendSelector:
    """Stateless factory for selecting and constructing adapter instances."""

    @staticmethod
    def get_adapter(
        db_path: str,
        backend: Literal["odbc", "com", "dao", "auto"] | None = None,
        capabilities: BackendCapabilities | None = None,
    ):
        """Construct an adapter selected by the explicit value, environment, and capabilities."""
        env_value = os.environ.get("ACCESS_MCP_BACKEND", "auto")
        env_explicit = "ACCESS_MCP_BACKEND" in os.environ
        if backend is not None:
            resolved = _normalize_backend(backend)
        else:
            resolved = _normalize_backend(env_value)

        requires_com_flag = _requires_com(capabilities)

        if resolved == "auto":
            if requires_com_flag:
                resolved = "com"
            elif _is_dao_constructable():
                resolved = "dao"
            else:
                resolved = "odbc"

        if resolved == "odbc" and requires_com_flag:
            assert capabilities is not None  # noqa: S101 - requires_com_flag guarantees capabilities is present
            cap_names = _com_only_cap_names(capabilities)
            cap_list = [f.name for f in BackendCapabilities if f in capabilities]
            msg = (
                f"ACCESS_MCP_BACKEND=odbc conflicts with capability "
                f"{cap_names[0]} which requires COM. "
                f"(Requested capabilities: {cap_list})"
            )
            raise BackendCapabilityMismatchError(msg)

        if resolved == "dao":
            if sys.platform != "win32":
                if backend is not None or env_explicit:
                    msg = (
                        "DAO backend is not available on non-Windows platforms. "
                        "Use backend='odbc' or ACCESS_MCP_BACKEND=odbc."
                    )
                    raise BackendUnavailableError(msg)
                resolved = "odbc"
            elif not _is_dao_constructable():
                global _dao_fallback_warned
                if not _dao_fallback_warned:
                    _logger.warning(
                        "DAO backend requested but win32com is not importable. "
                        "Falling back to OdbcAdapter. Install Microsoft Access or "
                        "the Access Runtime to enable DAO."
                    )
                    _dao_fallback_warned = True
                resolved = "odbc"
            else:
                from .dao import DaoAdapter

                return DaoAdapter(db_path=db_path)

        if resolved == "com":
            if sys.platform != "win32":
                explicit_com = backend is not None
                if explicit_com:
                    msg = (
                        "COM automation is not available on Linux. "
                        "Use backend='odbc' or ACCESS_MCP_BACKEND=odbc."
                    )
                    raise BackendUnavailableError(msg)
                else:
                    assert capabilities is not None  # noqa: S101 - COM resolution requires capabilities
                    cap_names = _com_only_cap_names(capabilities)
                    msg = (
                        f"Capabilities require COM backend ({cap_names[0]}), "
                        f"but COM automation is not available on Linux. "
                        f"Use ACCESS_MCP_BACKEND=odbc or remove the COM-only capability."
                    )
                    raise BackendCapabilityMismatchError(msg)
            from .access import AccessAdapter

            return AccessAdapter(db_path=db_path)

        from .odbc import OdbcAdapter

        return OdbcAdapter(db_path=db_path)
