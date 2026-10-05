"""Tests for server.py — build_server, lifespan cleanup, no eager dispatch."""

from __future__ import annotations

from ms_access_mcp.server import _ServerState, build_server, lifespan


def test_mcp_subcommand_ignores_outer_argv(monkeypatch):
    from types import SimpleNamespace

    from ms_access_mcp import cli, server

    class FakeServer:
        def run(self):
            pass

    monkeypatch.setattr("sys.argv", ["ms-access-mcp", "mcp"])
    monkeypatch.setattr(server, "build_server", lambda profile: FakeServer())

    assert cli._run_mcp(SimpleNamespace(profile=None)) == 0


class TestBuildServer:
    def test_build_server_returns_fastmcp(self):
        """build_server returns a FastMCP instance (not started)."""
        server = build_server("core")
        assert server is not None
        assert hasattr(server, "run")

    def test_build_server_accepts_profile_arg(self):
        """build_server accepts profile name without error."""
        server = build_server("core")
        assert server is not None

    def test_build_server_does_not_eagerly_import_automation(self):
        """build_server('core') should not import automation tools."""
        import sys

        # Clear any cached imports
        modules_before = set(sys.modules.keys())
        build_server("core")
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        automation_modules = [m for m in new_modules if "automation" in m]
        assert len(automation_modules) == 0, f"Unexpected automation imports: {automation_modules}"

    def test_build_server_does_not_eagerly_import_maintenance(self):
        """build_server('core') should not import maintenance tools."""
        import sys

        modules_before = set(sys.modules.keys())
        build_server("core")
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        maintenance_modules = [m for m in new_modules if "maintenance" in m]
        assert len(maintenance_modules) == 0, f"Unexpected maintenance imports: {maintenance_modules}"

    def test_build_server_does_not_start_dispatcher(self):
        """build_server should not start the dispatcher (no thread creation)."""
        import threading

        threads_before = threading.active_count()
        build_server("core")
        threads_after = threading.active_count()
        # Should not create any new threads during build
        assert threads_after <= threads_before


class TestLifespan:
    def test_server_state_has_required_slots(self):
        """_ServerState should have db and dispatcher slots."""
        state = _ServerState()
        assert hasattr(state, "db")
        assert hasattr(state, "dispatcher")

    def test_lifespan_is_async_context_manager(self):
        """lifespan should be usable as an async context manager."""
        import asyncio
        from typing import Any, cast

        async def test_lifespan():
            server = cast(Any, type("MockServer", (), {})())
            async with lifespan(server) as state:
                assert state is not None
                assert isinstance(state, _ServerState)

        asyncio.run(test_lifespan())

    def test_lifespan_yields_state(self):
        """lifespan should yield a _ServerState instance (via asyncio.run)."""
        import asyncio
        from typing import Any, cast

        server = cast(Any, type("MockServer", (), {})())

        async def _test():
            async with lifespan(server) as state:
                assert isinstance(state, _ServerState)

        asyncio.run(_test())

    def test_lifespan_closes_tracked_use_cases(self):
        import asyncio
        from typing import Any, cast

        from ms_access_mcp.mcp_tools._session_registry import registry

        closed = []

        class FakeUseCase:
            def close_database(self):
                closed.append(True)

        use_case = FakeUseCase()
        registry.track(use_case)
        server = cast(Any, type("MockServer", (), {})())

        async def _test():
            async with lifespan(server):
                pass

        del use_case
        import gc
        gc.collect()

        asyncio.run(_test())
        assert closed == [True]
