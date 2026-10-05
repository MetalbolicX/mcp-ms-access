"""ComDispatcher unit tests — ported from oracle test_com_dispatcher.py."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, call, patch

import pytest


class TestComDispatcherStaThreadQuit:
    """Test that Quit runs on the dispatcher STA thread; ThreadPoolExecutor must NOT be used."""

    def test_release_com_safe_quits_on_dispatcher_thread(self):
        """RED: Quit goes through ThreadPoolExecutor today; after fix it runs directly."""
        from ms_access_mcp.backends.dispatcher import ComDispatcher

        dispatcher = ComDispatcher()
        mock_app = MagicMock()
        dispatcher._access_app = mock_app
        dispatcher._ado_conn = MagicMock()
        dispatcher._current_db = MagicMock()

        with patch("concurrent.futures.ThreadPoolExecutor") as mock_tpe_cls:
            with patch("subprocess.run"):
                dispatcher._release_com_safe()
                # After the fix: Quit is called directly on the STA thread,
                # so the ThreadPoolExecutor bypass means no pool construction.
                # RED today: pool WAS constructed (Quit routed through pool).
                mock_tpe_cls.assert_not_called()
                mock_app.Quit.assert_called_once()

    def test_release_com_safe_collects_garbage_before_com_uninit(self):
        """gc.collect() must run before CoUninitialize to flush proxy Release calls."""
        from ms_access_mcp.backends.dispatcher import ComDispatcher

        dispatcher = ComDispatcher()
        mock_app = MagicMock()
        dispatcher._access_app = mock_app
        dispatcher._ado_conn = MagicMock()
        dispatcher._current_db = MagicMock()

        with patch("gc.collect") as mock_gc:
            with patch("subprocess.run"):
                dispatcher._release_com_safe()
                mock_gc.assert_called_once()


class TestComDispatcherShutdownLivenessKill:
    """Post-shutdown PID liveness kill — PID-scoped only, never /IM."""

    def test_shutdown_kills_by_pid_only_never_im(self):
        """RED today: _release_com_safe does the kill with /IM fallback.
        After fix: shutdown() kills by PID after thread join, /IM never appears."""
        from ms_access_mcp.backends.dispatcher import ComDispatcher

        dispatcher = ComDispatcher()
        dispatcher._access_app = MagicMock()
        dispatcher._ado_conn = MagicMock()
        dispatcher._current_db = MagicMock()
        dispatcher._started = True
        dispatcher._thread = None  # thread already dead
        dispatcher._access_pid = 9876

        # tasklist reports the PID alive once, then dead after kill
        tasklist_responses = [
            b"Image Name: convhw.exe\r\n PID: 9876\r\n",  # alive
        ]

        def run_side_effect(*args, **kwargs):
            cmd = args[0]
            if cmd[0] == "tasklist" and "/FI" in cmd:
                return MagicMock(returncode=0, stdout=tasklist_responses.pop(0))
            return MagicMock(returncode=0, stdout=b"")

        with patch("subprocess.run", side_effect=run_side_effect) as mock_run:
            dispatcher.shutdown()
            # After fix: shutdown() polls liveness and kills by PID only.
            # Today (pre-fix RED): no post-join kill exists yet.
            for call_args in mock_run.call_args_list:
                cmd = call_args[0][0]
                assert "/IM" not in cmd, f"/IM must never appear in shutdown kill: {cmd}"

    def test_shutdown_skips_kill_when_pid_absent_or_dead(self):
        """No subprocess calls when _access_pid is None; zero kills when poll shows dead."""
        import subprocess

        from ms_access_mcp.backends.dispatcher import ComDispatcher

        # Case A: PID absent
        dispatcher = ComDispatcher()
        dispatcher._access_app = MagicMock()
        dispatcher._ado_conn = MagicMock()
        dispatcher._current_db = MagicMock()
        dispatcher._started = True
        dispatcher._thread = None
        dispatcher._access_pid = None

        with patch("subprocess.run") as mock_run:
            dispatcher.shutdown()
            mock_run.assert_not_called()

        # Case B: PID present but poll shows already dead
        dispatcher2 = ComDispatcher()
        dispatcher2._access_app = MagicMock()
        dispatcher2._ado_conn = MagicMock()
        dispatcher2._current_db = MagicMock()
        dispatcher2._started = True
        dispatcher2._thread = None
        dispatcher2._access_pid = 9876

        dead_response = "No tasks are running"  # tasklist shows dead

        def run_side_effect(*args, **kwargs):
            cmd = args[0]
            if cmd[0] == "tasklist" and "/FI" in cmd:
                return subprocess.CompletedProcess(args, returncode=1, stdout=dead_response, stderr="")
            return MagicMock(returncode=0, stdout=b"")

        with patch("subprocess.run", side_effect=run_side_effect) as mock_run:
            dispatcher2.shutdown()
            # No taskkill should fire because the process was already dead
            kill_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "taskkill"]
            assert len(kill_calls) == 0


class TestComDispatcherLogging:
    """Test that _release_com_safe logs warnings instead of silently passing."""

    def test_release_com_safe_logs_cleanup_warnings(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher

        dispatcher = ComDispatcher()
        dispatcher._access_app = None
        dispatcher._ado_conn = MagicMock()
        dispatcher._current_db = MagicMock()
        dispatcher._ado_conn.Close.side_effect = RuntimeError("ADO already closed")

        with patch("ms_access_mcp.backends.dispatcher.logger") as mock_logger:
            dispatcher._release_com_safe()
            mock_logger.warning.assert_called()


class TestDispatcherRestartSafety:
    def test_start_does_not_spawn_second_live_worker_after_join_timeout(self, monkeypatch):
        from ms_access_mcp.backends import dispatcher as dispatcher_module
        from ms_access_mcp.backends.dispatcher import ComDispatcher

        workers = []

        class SlowWorker:
            def __init__(self, **kwargs):
                self.started = False
                workers.append(self)

            def start(self):
                self.started = True

            def is_alive(self):
                return self.started

            def join(self, timeout=None):
                pass

        monkeypatch.setattr(dispatcher_module.threading, "Thread", SlowWorker)
        dispatcher = ComDispatcher()
        dispatcher.start()
        dispatcher.shutdown()
        dispatcher.start()

        assert len(workers) == 1

    def test_timed_out_call_does_not_prevent_later_shutdown(self, monkeypatch):
        import threading

        from ms_access_mcp.backends.dispatcher import ComDispatcher

        dispatcher = ComDispatcher()
        dispatcher.DISPATCH_TIMEOUT = 0.01
        started = threading.Event()
        release = threading.Event()
        dispatcher.start()
        try:
            with pytest.raises(TimeoutError):
                dispatcher.call(lambda: (started.set(), release.wait(1)))
            assert started.wait(1)
            release.set()
        finally:
            dispatcher.shutdown()
        assert dispatcher._thread is not None
        assert not dispatcher._thread.is_alive()


class TestComDispatcherHealthTracking:
    """Test the unhealthy-state flag lifecycle."""

    def test_starts_healthy(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher
        dispatcher = ComDispatcher()
        assert dispatcher.is_healthy() is True

    def test_mark_unhealthy_flips_flag(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher
        dispatcher = ComDispatcher()
        dispatcher.mark_unhealthy()
        assert dispatcher.is_healthy() is False

    def test_mark_unhealthy_is_idempotent(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher
        dispatcher = ComDispatcher()
        dispatcher.mark_unhealthy()
        dispatcher.mark_unhealthy()
        assert dispatcher.is_healthy() is False

    def test_reset_health_restores_flag(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher
        dispatcher = ComDispatcher()
        dispatcher.mark_unhealthy()
        dispatcher.reset_health()
        assert dispatcher.is_healthy() is True

    def test_shutdown_resets_health(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher
        dispatcher = ComDispatcher()
        dispatcher.mark_unhealthy()
        dispatcher.shutdown()
        assert dispatcher.is_healthy() is True


class TestComDispatcherIsConnectedDaoOnly:
    """Test is_connected() in DAO-only mode."""

    def test_is_connected_false_when_nothing_open(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher
        dispatcher = ComDispatcher()
        assert dispatcher.is_connected() is False

    def test_is_connected_true_for_dao_only_mode(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher
        dispatcher = ComDispatcher()
        dispatcher._access_app = None
        dispatcher._current_db = MagicMock()
        assert dispatcher.is_connected() is True

    def test_is_connected_true_for_access_mode(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher
        dispatcher = ComDispatcher()
        dispatcher._access_app = MagicMock()
        dispatcher._current_db = MagicMock()
        assert dispatcher.is_connected() is True

    def test_is_connected_false_for_access_mode_with_missing_db(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher
        dispatcher = ComDispatcher()
        dispatcher._access_app = MagicMock()
        dispatcher._current_db = None
        assert dispatcher.is_connected() is False


def _make_inline_dispatcher():
    """Build a dispatcher that runs call() synchronously."""
    from ms_access_mcp.backends.dispatcher import ComDispatcher
    dispatcher = ComDispatcher()
    dispatcher._started = True
    dispatcher._thread = None  # type: ignore[assignment]

    def _inline_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    dispatcher.call = _inline_call  # type: ignore[method-assign]
    return dispatcher


class TestComDispatcherCreateDaoDatabase:
    """ComDispatcher.create_dao_database() tests."""

    def test_create_dao_database_dispatches_dao_primary(self):
        from ms_access_mcp.backends.dispatcher import (
            DEFAULT_DB_LOCALE,
            DEFAULT_DB_VERSION,
        )

        dispatcher = _make_inline_dispatcher()
        new_db = MagicMock()
        engine = MagicMock()
        engine.CreateDatabase.return_value = new_db
        win32_module = MagicMock()
        win32_module.client.Dispatch.return_value = engine

        with patch.dict(sys.modules, {"win32com": win32_module, "win32com.client": win32_module.client}):
            dispatcher.create_dao_database(r"C:\new\new.accdb")

        engine.CreateDatabase.assert_called_once_with(
            r"C:\new\new.accdb", DEFAULT_DB_LOCALE, DEFAULT_DB_VERSION
        )
        new_db.Close.assert_called_once()
        assert win32_module.client.Dispatch.call_args_list == [call("DAO.DBEngine.120")]

    def test_create_dao_database_falls_back_to_access_application(self):
        dispatcher = _make_inline_dispatcher()
        application = MagicMock()

        def _dispatch(progId: str):
            if progId == "DAO.DBEngine.120":
                raise OSError("DAO not registered")
            return application

        win32_module = MagicMock()
        win32_module.client.Dispatch.side_effect = _dispatch

        with patch.dict(sys.modules, {"win32com": win32_module, "win32com.client": win32_module.client}):
            dispatcher.create_dao_database(r"C:\new\new.accdb")

        application.NewCurrentDatabase.assert_called_once_with(r"C:\new\new.accdb")
        application.CloseCurrentDatabase.assert_called_once()
        application.Quit.assert_called_once()

    def test_create_dao_database_releases_handles_on_failure(self):
        dispatcher = _make_inline_dispatcher()
        application = MagicMock()
        application.NewCurrentDatabase.side_effect = RuntimeError("Access broken")

        def _dispatch(progId: str):
            if progId == "DAO.DBEngine.120":
                raise OSError("DAO not registered")
            return application

        win32_module = MagicMock()
        win32_module.client.Dispatch.side_effect = _dispatch

        with patch.dict(sys.modules, {"win32com": win32_module, "win32com.client": win32_module.client}):
            with pytest.raises(RuntimeError, match="create_dao_database failed"):
                dispatcher.create_dao_database(r"C:\new\new.accdb")

        application.CloseCurrentDatabase.assert_called_once()
        application.Quit.assert_called_once()

    def test_create_dao_database_rejects_non_windows(self, monkeypatch):
        from ms_access_mcp.backends.dispatcher import ComDispatcher
        monkeypatch.setattr(sys, "platform", "linux")
        dispatcher = ComDispatcher()

        with patch.dict(sys.modules, {"win32com": None, "win32com.client": None, "pythoncom": None}):
            with pytest.raises(RuntimeError, match="PlatformUnsupported"):
                dispatcher.create_dao_database(r"/tmp/x.accdb")


class TestComDispatcherDbLocaleConstants:
    """Pin the default locale/version constants."""

    def test_default_db_locale_value(self):
        from ms_access_mcp.backends.dispatcher import DEFAULT_DB_LOCALE
        assert DEFAULT_DB_LOCALE == ";LANGID=0x0409;CP=1252;COUNTRY=0"

    def test_default_db_version_value(self):
        from ms_access_mcp.backends.dispatcher import DEFAULT_DB_VERSION
        assert DEFAULT_DB_VERSION == 128
