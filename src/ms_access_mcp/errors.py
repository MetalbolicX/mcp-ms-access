"""Error taxonomy and shared result envelopes (dependency-free shared kernel)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


def strip_password(text: str) -> str:
    """Strip credential tokens from a DAO connect string or error message.

    Removes ``PWD=``, ``Password=``, and ``Jet OLEDB:Database Password=``
    tokens (case-insensitive), preserving the remaining connection string.
    """
    import re

    patterns = [
        re.compile(r"(?i)(?:^|;)\s*PWD\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^;]*)(?=[;:]|$)", re.IGNORECASE),
        re.compile(r"(?i)(?:^|;)\s*Password\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^;]*)(?=[;:]|$)", re.IGNORECASE),
        re.compile(r"(?i)(?:^|;)\s*Jet OLEDB:Database Password\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^;]*)(?=[;:]|$)", re.IGNORECASE),
    ]
    result = text
    for pattern in patterns:
        result = pattern.sub("", result)
    return result.strip(";")

# =============================================================================
# Result Envelopes
# =============================================================================


@dataclass(frozen=True)
class Ok:
    """Success result envelope."""

    data: Any = None
    rowcount: int = 0

    def map(self, fn: Callable[[Any], Any]) -> Ok:
        """Transform success data, preserving rowcount."""
        return Ok(data=fn(self.data), rowcount=self.rowcount)

    def bind(self, fn: Callable[[Any], Result]) -> Result:
        """Chain a Result-returning function on success data."""
        return fn(self.data)

    def fold(self, on_ok: Callable[[Any, int], Any], on_err: Callable[[str], Any]) -> Any:  # noqa: ARG002 - shared Result API callback is unused on success
        """Consume the envelope: on_ok(data, rowcount) or on_err(message)."""
        return on_ok(self.data, self.rowcount)


@dataclass(frozen=True)
class Err:
    """Error result envelope."""

    message: str

    def map(self, fn: Callable[[Any], Any]) -> Err:  # noqa: ARG002 - shared Result API callback is unused on errors
        """Short-circuit: errors pass through unchanged."""
        return self

    def bind(self, fn: Callable[[Any], Result]) -> Result:  # noqa: ARG002 - shared Result API callback is unused on errors
        """Short-circuit: errors pass through unchanged."""
        return self

    def fold(self, on_ok: Callable[[Any, int], Any], on_err: Callable[[str], Any]) -> Any:  # noqa: ARG002 - shared Result API callback is unused on errors
        """Consume the envelope: on_ok(data, rowcount) or on_err(message)."""
        return on_err(self.message)


Result = Ok | Err


def ok(data: Any = None, rowcount: int = 0) -> Ok:
    """Construct a success result."""
    return Ok(data=data, rowcount=rowcount)


def err(message: str) -> Err:
    """Construct an error result."""
    return Err(message=message)


# =============================================================================
# Exception Taxonomy
# =============================================================================


class AccessMcpError(Exception):
    """Base exception for ms-access-mcp."""


class BackendUnavailableError(AccessMcpError):
    """Raised when an explicitly requested backend is unavailable (REQ-6, REQ-19)."""


class BackendCapabilityMismatchError(AccessMcpError):
    """Raised when backend capabilities conflict (REQ-3, REQ-23, REQ-24)."""


class DaoOperationError(AccessMcpError):
    """Raised when a DAO operation fails."""

    def __init__(self, message: str, cause: BaseException | None = None):
        self.message = message
        self.cause = cause
        super().__init__(message)
        self.__cause__ = cause


class AccessObjectNotFoundError(AccessMcpError):
    """Raised when a form or report is not found in the database."""

    def __init__(self, object_kind: str, object_name: str) -> None:
        self.object_kind = object_kind
        self.object_name = object_name
        msg = f"{object_kind} '{object_name}' not found"
        super().__init__(msg)


class AccessBackendError(AccessMcpError):
    """Raised when a COM/backend operation fails for non-not-found reasons."""

    def __init__(self, operation: str, object_name: str, detail: str, cause: BaseException | None = None) -> None:
        self.operation = operation
        self.object_name = object_name
        self.detail = detail
        msg = f"{operation} '{object_name}' failed: {detail}"
        super().__init__(msg)
        self.__cause__ = cause


def operation_error(e: BaseException, label: str | None = None) -> Err:
    """Build a sanitized operation error with optional context or DAO labeling."""
    message = strip_password(str(e))
    if label is not None:
        return err(f"{label}: {message}")
    if isinstance(e, DaoOperationError):
        return err(f"DAO operation failed: {message}")
    return err(message)
