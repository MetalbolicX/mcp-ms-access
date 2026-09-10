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
