from ms_access_mcp.application._adapter_lifecycle import AdapterLifecycle


class DaoLikeFake:
    def __init__(self) -> None:
        self._connected = False
        self.connect_calls = 0

    def connect(self, db_path: str | None = None, /) -> None:
        self.connect_calls += 1
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected


class LifecycleConsumer(AdapterLifecycle[DaoLikeFake]):
    def __init__(self, adapter: DaoLikeFake) -> None:
        self._adapter = adapter
        self._db_path = "test.accdb"


def test_connected_adapter_is_not_reconnected_for_each_guarded_operation() -> None:
    adapter = DaoLikeFake()
    consumer = LifecycleConsumer(adapter)

    assert consumer._ensure_adapter() is None
    assert consumer._ensure_adapter() is None

    assert adapter.connect_calls == 1
