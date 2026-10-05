"""PID scoping tests for the Access dialog killer."""

from __future__ import annotations

import sys
import threading
import types
from typing import Any

import pytest

from ms_access_mcp.backends.access import dialog_killer


@pytest.mark.parametrize(
    ("window_class", "owner_pid", "should_dismiss"),
    [
        ("#32770", 101, True),
        ("#32770", 202, False),
        ("MainWindow", 101, False),
    ],
)
def test_only_access_save_dialog_is_dismissed(
    monkeypatch: pytest.MonkeyPatch,
    window_class: str,
    owner_pid: int,
    should_dismiss: bool,
) -> None:
    access_pid = 101
    posted: list[tuple[Any, ...]] = []

    class FakeWin32Gui:
        @staticmethod
        def EnumWindows(callback: Any, extra: object) -> None:
            callback(1, extra)

        @staticmethod
        def GetClassName(hwnd: int) -> str:
            return window_class

        @staticmethod
        def GetWindowText(hwnd: int) -> str:
            return "Save" if hwnd == 2 else "Save changes?"

        @staticmethod
        def FindWindowEx(hwnd: int, child: int | None, cls: str, title: object) -> int:
            return 2 if child is None else 0

        @staticmethod
        def GetWindowLong(hwnd: int, index: int) -> int:
            return 1

        @staticmethod
        def PostMessage(*args: Any) -> None:
            posted.append(args)

    class FakeWin32Process:
        @staticmethod
        def GetWindowThreadProcessId(hwnd: int) -> tuple[int, int]:
            return (10, owner_pid)

    monkeypatch.setitem(sys.modules, "win32gui", FakeWin32Gui)
    monkeypatch.setitem(sys.modules, "win32process", FakeWin32Process)
    monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(WM_COMMAND=0x111, GWL_ID=-12))
    stop_event = threading.Event()

    original_enum_windows = FakeWin32Gui.EnumWindows

    def stop_after_enumeration(callback: Any, extra: object) -> None:
        original_enum_windows(callback, extra)
        stop_event.set()

    monkeypatch.setattr(FakeWin32Gui, "EnumWindows", stop_after_enumeration)

    dialog_killer.start_dialog_killer(stop_event, access_pid)

    assert bool(posted) is should_dismiss


def test_missing_access_pid_fails_closed(caplog: pytest.LogCaptureFixture) -> None:
    dialog_killer.start_dialog_killer(threading.Event(), None)

    assert "Access PID is unavailable" in caplog.text
