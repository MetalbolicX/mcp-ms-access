"""SessionRegistry tracks live per-call use cases and closes them all."""

import gc

from ms_access_mcp.mcp_tools._session_registry import SessionRegistry


class FakeUseCase:
    def __init__(self) -> None:
        self.closed = False
        self.close_calls = 0

    def close_database(self) -> None:
        self.close_calls += 1
        self.closed = True


def test_close_all_closes_tracked() -> None:
    reg = SessionRegistry()
    a, b = FakeUseCase(), FakeUseCase()
    reg.track(a)
    reg.track(b)

    reg.close_all()

    assert a.closed and b.closed


def test_tracked_session_survives_gc_until_closed() -> None:
    reg = SessionRegistry()
    closed: list[bool] = []

    class TrackedUseCase(FakeUseCase):
        def close_database(self) -> None:
            super().close_database()
            closed.append(self.closed)

    session = TrackedUseCase()
    reg.track(session)
    del session
    gc.collect()

    reg.close_all()

    assert closed == [True]


def test_close_all_is_idempotent() -> None:
    reg = SessionRegistry()
    session = FakeUseCase()
    reg.track(session)

    reg.close_all()
    reg.close_all()

    assert session.close_calls == 1


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
