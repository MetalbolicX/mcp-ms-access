"""Shared test helpers for unit tests."""


class FakeDispatcher:
    """Run dispatcher calls synchronously while preserving their call contract."""

    def call(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)
