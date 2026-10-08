from ms_access_mcp import backends
from ms_access_mcp.application._adapter_lifecycle import AdapterLifecycle
from ms_access_mcp.errors import AccessMcpError, DaoOperationError, Err


class DaoLikeFake:
    def __init__(self) -> None:
        self._connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0

    def connect(self, db_path: str | None = None, /) -> None:
        self.connect_calls += 1
        self._connected = True

    @property
    def connected(self) -> bool:
        # Before 065, adapters lacked connected and getattr defaulted False, reconnecting each call.
        return self._connected

    def is_connected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False


class LifecycleConsumer(AdapterLifecycle):
    def __init__(self, adapter: DaoLikeFake) -> None:
        self._adapter = adapter
        self._db_path = "test.accdb"


def test_connected_adapter_is_not_reconnected_for_each_guarded_operation() -> None:
    adapter = DaoLikeFake()
    consumer = LifecycleConsumer(adapter)

    assert consumer._ensure_adapter() is None
    assert consumer._ensure_adapter() is None

    assert adapter.connect_calls == 1


def test_close_disconnects_adapter_and_clears_it(monkeypatch) -> None:
    adapter = DaoLikeFake()
    consumer = LifecycleConsumer(adapter)

    consumer.close()

    assert adapter.disconnect_calls == 1
    assert consumer._adapter is None
    monkeypatch.setattr(backends.BackendSelector, "get_adapter", lambda **kwargs: adapter)
    assert consumer._ensure_adapter() is None
    assert adapter.connect_calls == 1


def test_close_is_idempotent() -> None:
    adapter = DaoLikeFake()
    consumer = LifecycleConsumer(adapter)

    consumer.close()
    consumer.close()

    assert adapter.disconnect_calls == 1


def test_close_allows_adapter_without_disconnect() -> None:
    class AdapterWithoutDisconnect:
        pass

    consumer = LifecycleConsumer(DaoLikeFake())
    consumer._adapter = AdapterWithoutDisconnect()  # type: ignore[assignment]

    consumer.close()


def test_close_without_adapter_is_noop() -> None:
    consumer = LifecycleConsumer(DaoLikeFake())
    consumer._adapter = None

    consumer.close()


def test_dao_operation_error_is_shared_with_kernel() -> None:
    from ms_access_mcp.backends.dao import DaoOperationError as backend_err  # noqa: N813
    from ms_access_mcp.errors import DaoOperationError as kernel_err  # noqa: N813

    assert backend_err is kernel_err
    assert issubclass(kernel_err, AccessMcpError)


def test_lazy_adapter_dao_connect_error_has_typed_message(monkeypatch) -> None:
    class AdapterWithDaoConnectError(DaoLikeFake):
        def connect(self, db_path: str | None = None, /) -> None:
            raise DaoOperationError("connect boom")

    adapter = AdapterWithDaoConnectError()
    consumer = LifecycleConsumer(adapter)
    consumer._adapter = None
    monkeypatch.setattr(backends.BackendSelector, "get_adapter", lambda **kwargs: adapter)

    result = consumer._ensure_adapter()

    assert isinstance(result, Err)
    assert result.message.startswith("DAO operation failed:")
    assert "connect boom" in result.message
