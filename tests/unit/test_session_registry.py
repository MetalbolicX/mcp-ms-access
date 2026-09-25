"""SessionRegistry tracks live per-call use cases and closes them all."""

import gc

from ms_access_mcp.mcp_tools._session_registry import SessionRegistry


class FakeUseCase:
    def __init__(self) -> None:
        self.closed = False

    def close_database(self) -> None:
        self.closed = True


def test_close_all_closes_tracked() -> None:
    reg = SessionRegistry()
    a, b = FakeUseCase(), FakeUseCase()
    reg.track(a)
    reg.track(b)

    reg.close_all()

    assert a.closed and b.closed


def test_weakref_gone_instance_is_skipped() -> None:
    reg = SessionRegistry()
    reg.track(FakeUseCase())
    gc.collect()

    reg.close_all()


def test_close_all_swallows_individual_failures() -> None:
    class BrokenUseCase(FakeUseCase):
        def close_database(self) -> None:
            raise RuntimeError("close failed")

    reg = SessionRegistry()
    broken, healthy = BrokenUseCase(), FakeUseCase()
    reg.track(broken)
    reg.track(healthy)

    reg.close_all()

    assert healthy.closed
