"""ComDispatcher unit tests — ported from oracle test_com_dispatcher.py."""

from __future__ import annotations

import concurrent.futures
import sys
from unittest.mock import MagicMock, call, patch

import pytest


class TestComDispatcherPidScopedTaskkill:
    """Test that _release_com_safe uses /PID instead of /IM when graceful Quit fails."""

    def test_release_com_safe_uses_pid_based_taskkill_when_quit_fails(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher

        dispatcher = ComDispatcher()
        mock_app = MagicMock()
        mock_app.hWndAccessApp.return_value = 12345

        dispatcher._access_app = mock_app
        dispatcher._ado_conn = MagicMock()
        dispatcher._current_db = MagicMock()

        with patch("subprocess.run") as mock_run:
            with patch(
                "concurrent.futures.ThreadPoolExecutor"
            ) as mock_tpe_cls:
                mock_tpe = MagicMock()
                mock_future = MagicMock()
                mock_future.result.side_effect = concurrent.futures.TimeoutError("Quit timed out")
                mock_tpe.__enter__ = MagicMock(return_value=mock_tpe)
                mock_tpe.__exit__ = MagicMock(return_value=None)
                mock_tpe.submit.return_value = mock_future
                mock_tpe_cls.return_value = mock_tpe

                with patch("win32process.GetWindowThreadProcessId", return_value=(0, 9876)):
                    dispatcher._release_com_safe()

                    mock_run.assert_called_once()
                    call_args = mock_run.call_args[0][0]
                    assert "/PID" in call_args
                    assert "/IM" not in call_args
                    assert "9876" in call_args

    def test_release_com_safe_falls_back_to_im_when_hwnd_fails(self):
        from ms_access_mcp.backends.dispatcher import ComDispatcher

        dispatcher = ComDispatcher()
        mock_app = MagicMock()
        dispatcher._access_app = mock_app
        dispatcher._ado_conn = MagicMock()
        dispatcher._current_db = MagicMock()

        with patch("subprocess.run") as mock_run:
            with patch("concurrent.futures.ThreadPoolExecutor") as mock_tpe_cls:
                mock_tpe = MagicMock()
                mock_future = MagicMock()
                mock_future.result.side_effect = concurrent.futures.TimeoutError("Quit timed out")
                mock_tpe.__enter__ = MagicMock(return_value=mock_tpe)
                mock_tpe.__exit__ = MagicMock(return_value=None)
                mock_tpe.submit.return_value = mock_future
                mock_tpe_cls.return_value = mock_tpe

                with patch("win32process.GetWindowThreadProcessId", side_effect=Exception("HWND unavailable")):
                    dispatcher._release_com_safe()

                    call_args = mock_run.call_args[0][0]
                    assert "/IM" in call_args
                    assert "MSACCESS.EXE" in call_args


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
