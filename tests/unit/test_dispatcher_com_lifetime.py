"""COM lifetime tests for the dispatcher's STA call boundary.

An apartment-local COM proxy may only be released from the apartment that owns it —
the dispatcher's STA thread. These tests drive the public dispatcher API with Python
stand-ins whose ``__del__`` records the thread that releases them, so ownership
transfer across :meth:`ComDispatcher.call` is observable without a COM server.
"""

from __future__ import annotations

import gc
import threading
import time
from typing import Any

import pytest

from ms_access_mcp.backends.dispatcher import ComDispatcher


class _DispatchedError(RuntimeError):
    """Exception type used to prove the caller still receives what the callback raised."""


class _NoteHostileError(RuntimeError):
    """Failure whose PEP 678 ``add_note`` is broken on purpose.

    The dispatcher attaches the detached STA traceback text as a note before the
    caller sees a failure. An exception that refuses that note must not be able to
    take the STA loop down with it.
    """

    def add_note(self, note: str) -> None:
        """Refuse every note, emulating a pathological exception subclass."""
        msg = "add_note is hostile"
        raise RuntimeError(msg)


class _ApartmentObject:
    """Stand-in for an apartment-local COM proxy; records where it is released."""

    def __init__(self, releases: list[tuple[str, int]], name: str) -> None:
        self._releases = releases
        self._name = name

    def __del__(self) -> None:
        self._releases.append((self._name, threading.get_ident()))


def _started_dispatcher() -> tuple[ComDispatcher, int]:
    """Start a dispatcher and return it with the identity of its STA thread."""
    dispatcher = ComDispatcher()
    dispatcher.start()
    sta_thread = dispatcher.call(threading.get_ident)
    return dispatcher, sta_thread


def _iter_frames(traceback: Any) -> list[str]:
    """Return the code names of every frame in a traceback chain, head first."""
    names: list[str] = []
    entry = traceback
    while entry is not None:
        names.append(entry.tb_frame.f_code.co_name)
        entry = entry.tb_next
    return names


def test_failed_dispatch_releases_apartment_object_on_owner_thread() -> None:
    """A failing dispatched call must not hand an apartment object to its caller.

    The traceback of a failure keeps every STA frame alive, and those frames hold
    the apartment's COM handles in their locals. If that traceback reaches the
    caller, the caller thread releases a proxy it does not own — possibly after
    ``CoUninitialize`` already tore the apartment down, which is the reported
    "Win32 exception occurred releasing IUnknown" release fault.
    """
    releases: list[tuple[str, int]] = []
    dispatcher, sta_thread = _started_dispatcher()
    try:
        caller_thread = threading.get_ident()
        assert sta_thread != caller_thread

        def _failing_callback() -> None:
            proxy = _ApartmentObject(releases, "proxy")  # created in the STA apartment
            raise _DispatchedError(f"dispatched failure holding {proxy!r}")

        captured: BaseException | None = None
        try:
            dispatcher.call(_failing_callback)
        except _DispatchedError as exc:
            captured = exc
        assert captured is not None, "the callback's failure must still propagate"

        # Let the STA loop move past the failing call's own references, then drop the
        # exception the caller was handed. Anything released from here on belongs to
        # whichever thread performs the release.
        dispatcher.call(lambda: None)
        del captured
        gc.collect()

        assert releases == [("proxy", sta_thread)], (
            f"apartment object released on {releases}; its owner is the STA thread "
            f"{sta_thread}, but the caller thread is {caller_thread}"
        )
    finally:
        dispatcher.shutdown()


def test_failed_dispatch_releases_apartment_object_held_by_chained_exception() -> None:
    """A chained exception's frames are part of the same ownership rule.

    ``raise ... from inner`` carries the inner traceback — and every apartment COM
    handle held by its frames — across the dispatcher boundary, so an object kept
    alive only by the chained exception must also be released on the STA thread.
    """
    releases: list[tuple[str, int]] = []
    dispatcher, sta_thread = _started_dispatcher()
    try:
        caller_thread = threading.get_ident()

        def _inner_failing_callback() -> None:
            proxy = _ApartmentObject(releases, "chained-proxy")
            raise ValueError(f"inner failure holding {proxy!r}")

        def _outer_failing_callback() -> None:
            try:
                _inner_failing_callback()
            except ValueError as inner:
                raise _DispatchedError("outer failure") from inner

        captured: BaseException | None = None
        try:
            dispatcher.call(_outer_failing_callback)
        except _DispatchedError as exc:
            captured = exc
        assert captured is not None, "the callback's failure must still propagate"

        dispatcher.call(lambda: None)
        del captured
        gc.collect()

        assert releases == [("chained-proxy", sta_thread)], (
            f"apartment object released on {releases}; its owner is the STA thread "
            f"{sta_thread}, but the caller thread is {caller_thread}"
        )
    finally:
        dispatcher.shutdown()


def test_failed_dispatch_preserves_exception_and_its_sta_traceback_text() -> None:
    """Detaching the STA traceback must not silence the failure it describes.

    The caller still receives the same exception type, args, and message, and the
    original STA-side traceback text stays readable, while no STA frame is handed
    to the caller any more.
    """
    dispatcher, _ = _started_dispatcher()
    try:

        def _failing_callback() -> None:
            raise _DispatchedError("dao exploded", 5003073)

        captured: BaseException | None = None
        try:
            dispatcher.call(_failing_callback)
        except _DispatchedError as exc:
            captured = exc
        assert captured is not None, "the callback's failure must still propagate"

        assert captured.args == ("dao exploded", 5003073)
        assert "dao exploded" in str(captured)
        notes = "\n".join(getattr(captured, "__notes__", []))
        assert "_DispatchedError" in notes
        assert "dao exploded" in notes
        assert "_failing_callback" in notes
        assert "_failing_callback" not in _iter_frames(captured.__traceback__)
    finally:
        dispatcher.shutdown()


def test_successful_dispatch_returns_result_and_releases_object_on_owner_thread() -> None:
    """The success path keeps delivering results and releasing objects in the apartment."""
    releases: list[tuple[str, int]] = []
    dispatcher, sta_thread = _started_dispatcher()
    try:

        def _succeeding_callback() -> str:
            _ApartmentObject(releases, "success-proxy")
            return "done"

        assert dispatcher.call(_succeeding_callback) == "done"
        gc.collect()
        assert releases == [("success-proxy", sta_thread)]
    finally:
        dispatcher.shutdown()


def test_dispatch_stays_serialized_on_the_sta_thread_after_a_failing_call() -> None:
    """A failure must not break the serialized STA dispatch contract."""
    dispatcher, sta_thread = _started_dispatcher()
    try:

        def _failing_callback() -> None:
            raise _DispatchedError("dispatched failure")

        with pytest.raises(_DispatchedError, match="dispatched failure"):
            dispatcher.call(_failing_callback)

        state = {"inside": False}
        overlaps: list[bool] = []
        observed: list[int] = []

        def _probe() -> int:
            if state["inside"]:
                overlaps.append(True)
            state["inside"] = True
            time.sleep(0.02)
            state["inside"] = False
            return threading.get_ident()

        workers = [
            threading.Thread(target=lambda: observed.append(dispatcher.call(_probe)))
            for _ in range(4)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        assert overlaps == [], "dispatched callbacks must never run concurrently"
        assert set(observed) == {sta_thread}
    finally:
        dispatcher.shutdown()


def test_hostile_add_note_does_not_kill_the_sta_loop() -> None:
    """A raise inside the traceback detach must never break dispatch.

    ``_detach_sta_traceback`` formats the STA traceback and attaches it as a PEP 678
    note before the caller sees the failure. If that note step raises out of the
    dispatcher's exception handler, the STA loop dies and every later dispatch hangs
    until its timeout — a self-inflicted outage one exception wide. The caller must
    still receive the original exception, its apartment objects must still be
    released on the owner thread, and the loop must keep serving later calls.
    """
    releases: list[tuple[str, int]] = []
    dispatcher, sta_thread = _started_dispatcher()
    # A dead STA loop would otherwise make this test wait the production 120 s
    # timeout; the dispatcher exercises the same code path with a bounded wait.
    dispatcher.DISPATCH_TIMEOUT = 2.0
    try:

        def _hostile_callback() -> None:
            proxy = _ApartmentObject(releases, "hostile-proxy")
            try:
                raise ValueError(f"inner failure holding {proxy!r}")
            except ValueError as inner:
                raise _NoteHostileError("hostile failure") from inner

        captured: BaseException | None = None
        try:
            dispatcher.call(_hostile_callback)
        except _NoteHostileError as exc:
            captured = exc

        assert captured is not None, "the hostile failure must still reach its caller"
        assert type(captured) is _NoteHostileError
        assert captured.args == ("hostile failure",)
        assert str(captured) == "hostile failure"
        assert not getattr(captured, "__notes__", []), (
            "the fallback detach must not attach a note when the full detach failed"
        )

        # The fallback still clears the STA tracebacks, so the apartment object is
        # released by its owner; and the loop lives on to serve the next call.
        assert dispatcher.call(threading.get_ident) == sta_thread, "the STA loop must survive"
        del captured
        gc.collect()
        assert releases == [("hostile-proxy", sta_thread)]
    finally:
        dispatcher.shutdown()
