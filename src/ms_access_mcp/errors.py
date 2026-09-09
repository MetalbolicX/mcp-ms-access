"""Error taxonomy ported from oracle."""


class AccessMcpError(Exception):
    """Base exception for ms-access-mcp."""


class BackendUnavailableError(AccessMcpError):
    """Raised when a required backend (COM/ODBC) is not available."""


class BackendCapabilityMismatchError(AccessMcpError):
    """Raised when a backend lacks a required capability."""


class DaoOperationError(AccessMcpError):
    """Raised when a DAO operation fails."""

    def __init__(self, message: str, cause: BaseException | None = None):
        self.message = message
        self.cause = cause
        super().__init__(message)
        self.__cause__ = cause
