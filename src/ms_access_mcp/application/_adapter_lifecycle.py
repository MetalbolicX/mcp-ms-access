"""Shared adapter lifecycle for constructor-injected use cases.

Every use case that talks to a backend adapter needs the same reuse-or-reconnect
mechanism: keep an injected adapter if it is connected, reconnect it if it is
not, or lazily build one via ``BackendSelector`` when none was injected. That
mechanism lives here exactly once so the three use-case modules cannot drift.

The per-module backend name, backend capabilities, and "not connected" error
message are supplied as class attributes by each consumer.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar, cast

from .database import Err, _err
from .protocols import AdapterLifecycleProtocol

_AdapterT = TypeVar("_AdapterT", bound=AdapterLifecycleProtocol)

_BackendName = Literal["odbc", "com", "dao", "auto"]

NOT_CONNECTED_ERROR = "Backend unavailable: db_path is required to create an adapter"


class AdapterLifecycle(Generic[_AdapterT]):
    """Mixin providing the shared ``_ensure_adapter`` mechanism."""

    _backend: str = "auto"
    _capabilities_name: str = "DATA_READ_CAPS"
    _not_connected_error: str = NOT_CONNECTED_ERROR

    _adapter: _AdapterT | None
    _db_path: str | None

    def close(self) -> None:
        """Release the active adapter, if one was created. Idempotent."""
        adapter, self._adapter = self._adapter, None
        disconnect = getattr(adapter, "disconnect", None)
        if callable(disconnect):
            disconnect()

    def _ensure_adapter(self) -> Err | None:
        """Ensure the adapter is connected, creating it lazily if needed.

        Returns ``None`` on success or an ``Err`` envelope on failure.
        """
        if self._adapter is not None:
            if not self._adapter.connected:
                self._adapter.connect(self._db_path)
            return None

        if self._db_path is None:
            return _err(self._not_connected_error)

        from .. import backends
        from ..errors import BackendCapabilityMismatchError, BackendUnavailableError

        try:
            self._adapter = cast(
                _AdapterT,
                backends.BackendSelector.get_adapter(
                    db_path=self._db_path,
                    backend=cast(_BackendName, self._backend),
                    capabilities=getattr(backends, self._capabilities_name),
                ),
            )
            self._adapter.connect(self._db_path)
            return None
        except BackendUnavailableError as e:
            self._adapter = None
            return _err(f"Backend unavailable: {e}")
        except BackendCapabilityMismatchError as e:
            self._adapter = None
            return _err(f"Backend unavailable: {e}")
        except Exception as e:
            self._adapter = None
            return _err(f"Backend unavailable: {e}")
