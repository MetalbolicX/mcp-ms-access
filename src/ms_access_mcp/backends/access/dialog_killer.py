"""Dialog killer thread for suppressing Access 'Save As' dialogs.

Extracted from AccessAdapter (plan 060). This is a standalone function
that runs in a background thread and clicks OK/Save on save dialogs.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


def start_dialog_killer(stop_event: threading.Event, access_pid: int | None) -> None:
    """Background thread that clicks accept buttons on this Access PID's save dialogs.

    Args:
        stop_event: Set when the dialog killer should stop.
        access_pid: PID of the Access instance; ``None`` disables dismissal.

    """
    if access_pid is None:
        logger.warning("dialog killer disabled: Access PID is unavailable")
        return

    try:
        import win32con
        import win32gui
        import win32process

        while not stop_event.is_set():
            def find_and_click(hwnd: int, _: object) -> bool:
                if stop_event.is_set():
                    return False
                cls = win32gui.GetClassName(hwnd)
                title = win32gui.GetWindowText(hwnd)
                if cls != "#32770" or "save" not in title.lower():
                    return True
                _, owner_pid = win32process.GetWindowThreadProcessId(hwnd)
                if owner_pid != access_pid:
                    return True
                btn = win32gui.FindWindowEx(hwnd, None, "Button", None)
                while btn:
                    btn_title = win32gui.GetWindowText(btn).strip()
                    if btn_title in ("OK", "Save", "&OK", "&Save"):
                        win32gui.PostMessage(hwnd, win32con.WM_COMMAND,
                                             win32gui.GetWindowLong(btn, win32con.GWL_ID), 0)
                        return True
                    btn = win32gui.FindWindowEx(hwnd, btn, "Button", None)
                win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, 0x0D, 0)
                return True

            win32gui.EnumWindows(find_and_click, None)
            if not stop_event.is_set():
                time.sleep(0.1)

    except ImportError:
        pass
    except Exception as exc:
        logger.debug("dialog killer thread error: %s", exc)
        pass
