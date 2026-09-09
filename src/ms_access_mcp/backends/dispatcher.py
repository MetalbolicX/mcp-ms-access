"""Dedicated STA thread dispatcher for all COM operations.

WinCOM objects have apartment affinity — they must be created and used on the same thread.
This dispatcher serializes all COM calls through a single STA thread so that
any async worker can drive the adapter without thread-affinity errors.
"""

from __future__ import annotations

import concurrent.futures
import logging
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# DAO DBEngine.Execute option flags
DAO_DB_FAIL_ON_ERROR = 128

# Default locale / version for blank `.accdb` creation
DEFAULT_DB_LOCALE = ";LANGID=0x0409;CP=1252;COUNTRY=0"
DEFAULT_DB_VERSION = 128  # dbVersion120


class ComDispatcher:
    """Owns a dedicated STA thread for all COM operations.

    WinCOM objects have apartment affinity — they must be created and used on the same thread.
    This dispatcher serializes all COM calls through a single STA thread so that
    any async worker can drive the adapter without thread-affinity errors.
    """

    DISPATCH_TIMEOUT = 120.0  # seconds

    def __init__(self) -> None:
        self._call_queue: queue.Queue[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any], concurrent.futures.Future[Any]]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False
        self._stopping = False

        # COM objects — owned by the STA thread only
        self._access_app: Any | None = None
        self._current_db: Any | None = None
        self._ado_conn: Any | None = None
        self._db_path: str | None = None
        self._is_healthy: bool = True

    @property
    def access_app(self) -> Any:
        return self._access_app

    @property
    def current_db(self) -> Any:
        return self._current_db

    @property
    def ado_conn(self) -> Any:
        return self._ado_conn

    @property
    def db_path(self) -> str | None:
        return self._db_path

    def start(self) -> None:
        """Start the STA dispatcher thread (idempotent, reentrant after shutdown)."""
        if self._started:
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="ComDispatcher-STA", daemon=True)
        self._thread.start()
        self._started = True

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute fn(*args, **kwargs) on the STA thread. Returns the result.

        Raises TimeoutError if the call takes longer than DISPATCH_TIMEOUT seconds.
        Raises whatever exception fn raises.
        """
        if not self._started or self._thread is None:
            raise RuntimeError("ComDispatcher has not been started")

        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._call_queue.put((fn, args, kwargs, future))
        return future.result(timeout=self.DISPATCH_TIMEOUT)

    def is_connected(self) -> bool:
        """Check if the dispatcher has an active DAO Database handle."""
        return self._current_db is not None

    def is_healthy(self) -> bool:
        """Return True if the dispatcher is in a healthy state."""
        return self._is_healthy

    def mark_unhealthy(self) -> None:
        """Mark the connection as unhealthy. Idempotent."""
        self._is_healthy = False

    def reset_health(self) -> None:
        """Reset the unhealthy flag. Called after a successful reconnect."""
        self._is_healthy = True

    def open_dao_database(
        self,
        db_path: str,
        password: str = "",
        read_only: bool = False,
    ) -> Any:
        """Open a DAO Database directly, without Access.Application.

        Runs on the STA thread, replaces any existing handle in
        self._current_db (closing it first), and returns the new handle.
        """

        def _do() -> Any:
            import win32com.client  # type: ignore[import-not-found]

            engine = win32com.client.Dispatch("DAO.DBEngine.120")
            connect_str = f";PWD={password}" if password else ""
            if self._current_db is not None:
                try:
                    self._current_db.Close()
                except Exception:
                    pass
                self._current_db = None
            self._current_db = engine.OpenDatabase(db_path, False, read_only, connect_str)
            self._db_path = db_path
            self._is_healthy = True
            return self._current_db

        return self.call(_do)

    def close_dao_database(self) -> None:
        """Close the DAO Database currently held by the dispatcher.

        No-op if no handle is open.
        """
        if not self._started or self._thread is None:
            self._current_db = None
            return

        def _do() -> None:
            if self._current_db is None:
                return
            try:
                self._current_db.Close()
            except Exception:
                pass
            self._current_db = None

        self.call(_do)

    def create_dao_database(self, db_path: str) -> Any:
        """Create a blank .accdb at db_path on the STA thread.

        Primary: DAO.DBEngine.120.CreateDatabase.
        Fallback: Access.Application.NewCurrentDatabase for hosts without DAO.
        """
        if sys.platform != "win32":
            raise RuntimeError(
                "PlatformUnsupported: create_dao_database requires Windows (DAO/COM)"
            )

        def _do() -> Any:
            import win32com.client  # type: ignore[import-not-found]

            application: Any = None
            dao_error: BaseException | None = None
            try:
                engine = win32com.client.Dispatch("DAO.DBEngine.120")
                new_db = engine.CreateDatabase(
                    db_path, DEFAULT_DB_LOCALE, DEFAULT_DB_VERSION
                )
                try:
                    new_db.Close()
                except Exception:
                    pass
                return new_db
            except Exception as e:
                dao_error = e

            # Fallback: Access.Application.NewCurrentDatabase
            try:
                application = win32com.client.Dispatch("Access.Application")
                application.NewCurrentDatabase(db_path)
            except Exception as access_error:
                raise RuntimeError(
                    f"create_dao_database failed for {db_path}: "
                    f"DAO={dao_error}; Access={access_error}"
                ) from access_error
            finally:
                if application is not None:
                    try:
                        application.CloseCurrentDatabase()
                    except Exception:
                        pass
                    try:
                        application.Quit()
                    except Exception:
                        pass

        return self.call(_do)

    def set_db_path(self, db_path: str) -> None:
        """Set the database path (called by adapter.connect before opening)."""
        self._db_path = db_path

    def shutdown(self) -> None:
        """Signal the dispatcher thread to stop and clean up COM objects.

        FIX (plan 002): do NOT null _access_app/_current_db/_ado_conn/_db_path
        from the caller thread when join times out. The STA thread's _run
        finally block (_release_com_safe) owns handle release. Caller-thread
        nulling after a timed-out join races with the STA thread's finally
        block and can cause access violations.
        """
        self._stopping = True
        self._flush_pending_futures()
        # Put a sentinel to wake the thread
        self._call_queue.put((lambda: None, (), {}, concurrent.futures.Future()))
        if self._thread is not None:
            self._thread.join(timeout=15.0)
        # NOTE: do NOT null _access_app/_current_db/_ado_conn/_db_path here.
        # The STA thread's _run finally block (_release_com_safe) owns release.
        # Nulling from the caller thread after a timed-out join races with
        # the STA thread's finally block.
        self._is_healthy = True
        self._started = False

    def _flush_pending_futures(self) -> None:
        """Cancel all pending futures in the queue to prevent hanging calls."""
        cancelled = 0
        while True:
            try:
                fn, args, kwargs, future = self._call_queue.get_nowait()
                if fn is not None:
                    try:
                        future.cancel()
                        cancelled += 1
                    except Exception:
                        pass
            except queue.Empty:
                break
        if cancelled > 0:
            logger.debug(f"Flushed {cancelled} pending futures during shutdown")

    # ------------------------------------------------------------------ #
    # Internal: runs on the STA thread
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        """STA thread main loop. Initializes COM and processes call queue."""
        import pythoncom

        pythoncom.CoInitialize()

        try:
            while not self._stopping:
                try:
                    fn, args, kwargs, future = self._call_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                if self._stopping:
                    break

                try:
                    result = fn(*args, **kwargs)
                    try:
                        future.set_result(result)
                    except concurrent.futures.InvalidStateError:
                        pass
                except Exception as e:
                    try:
                        future.set_exception(e)
                    except concurrent.futures.InvalidStateError:
                        pass
        finally:
            # Clean up COM on the same thread — this is where handle
            # ownership truly lies; _release_com_safe nulls the references.
            self._release_com_safe()
            pythoncom.CoUninitialize()

    def _release_com_safe(self) -> None:
        """Release all COM objects in order: ADO -> DAO -> Access Application.

        Uses a watchdog thread to avoid hanging on Access.Quit().
        Falls back to taskkill /F if graceful shutdown fails on Windows.
        """
        errors: list[str] = []

        # 1. Close ADO connection explicitly
        if self._ado_conn is not None:
            try:
                self._ado_conn.Close()
            except Exception as e:
                errors.append(f"ADO Close: {e}")
            self._ado_conn = None

        # 2. Close DAO database explicitly
        if self._current_db is not None:
            try:
                self._current_db.Close()
            except Exception as e:
                errors.append(f"DAO Close: {e}")
            self._current_db = None

        # 3. Close and quit Access Application via COM
        if self._access_app is not None:
            try:
                self._access_app.CloseCurrentDatabase()
            except Exception as e:
                errors.append(f"CloseCurrentDatabase: {e}")

            # 4. Quit() with 5s watchdog
            app = self._access_app
            quit_ok = False
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(lambda: app.Quit())
                    fut.result(timeout=5.0)
                    quit_ok = True
            except concurrent.futures.TimeoutError:
                errors.append("Access.Quit() timed out after 5s")
            except Exception as e:
                errors.append(f"Access.Quit(): {e}")

            self._access_app = None

            # 5. Force-kill fallback (Windows only) — PID-scoped
            if not quit_ok and sys.platform == "win32":
                pid_killed = False
                try:
                    import win32process

                    hwnd = app.hWndAccessApp()
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    pid_killed = True
                except Exception as e:
                    errors.append(f"PID-scoped taskkill: {e}")

                if not pid_killed:
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/IM", "MSACCESS.EXE"],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                    except Exception as e:
                        errors.append(f"taskkill /IM fallback: {e}")

        if errors:
            logger.warning(f"Cleanup completed with {len(errors)} warning(s): {'; '.join(errors)}")

    @staticmethod
    def _dismiss_access_dialogs() -> None:
        """Dismiss modal dialogs shown by Microsoft Access.

        Uses Win32 API to find and close dialog windows (#32770 class).
        """
        try:
            import win32con
            import win32gui
            import win32process

            access_pids: set[int] = set()

            def collect_access_pids(hwnd: int, _: object) -> None:
                cls = win32gui.GetClassName(hwnd)
                title = win32gui.GetWindowText(hwnd)
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if cls == "OMain" and title and "Access" in title:
                    access_pids.add(pid)

            win32gui.EnumWindows(collect_access_pids, None)

            def dismiss_dialogs(hwnd: int, _: object) -> None:
                import ctypes
                from ctypes import windll

                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                cls = win32gui.GetClassName(hwnd)
                if cls != "#32770":
                    return
                try:
                    windll.user32.SendMessageTimeoutW(
                        hwnd,
                        win32con.WM_COMMAND,
                        1,
                        0,
                        win32con.SMTO_ABORTIFHUNG | win32con.SMTO_NOTIMEOUTIFNOTHUNG,
                        500,
                        ctypes.byref(ctypes.c_ulong()),
                    )
                except Exception:
                    pass
                try:
                    windll.user32.SendMessageTimeoutW(
                        hwnd,
                        win32con.WM_COMMAND,
                        2,
                        0,
                        win32con.SMTO_ABORTIFHUNG | win32con.SMTO_NOTIMEOUTIFNOTHUNG,
                        500,
                        ctypes.byref(ctypes.c_ulong()),
                    )
                except Exception:
                    pass
                try:
                    windll.user32.SendMessageTimeoutW(
                        hwnd,
                        win32con.WM_CLOSE,
                        0,
                        0,
                        win32con.SMTO_ABORTIFHUNG | win32con.SMTO_NOTIMEOUTIFNOTHUNG,
                        500,
                        ctypes.byref(ctypes.c_ulong()),
                    )
                except Exception:
                    pass
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_CLOSE, 0)
                except Exception:
                    pass

            for _ in range(4):
                win32gui.EnumWindows(dismiss_dialogs, None)
                time.sleep(0.25)

        except ImportError:
            pass
        except Exception:
            pass
