"""MCP server built on FastMCP — stdio transport, no stdout banners, logs to stderr."""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from .application.database import DatabaseUseCases
from .backends.dispatcher import ComDispatcher
from .mcp_tools._session_registry import close_all_sessions
from .mcp_tools.prompts import register_prompts
from .profiles import load_profile, resolve_profile
from .safety import allowed_dirs_from_env

__all__ = ["build_server", "main"]


# =============================================================================
# Logging
# =============================================================================

_log = logging.getLogger("ms_access_mcp.server")


# =============================================================================
# Lifespan — startup/cleanup for COM STA teardown
# =============================================================================


class _ServerState:
    """Server-scoped state holding the active db session and dispatcher."""

    __slots__ = ("db", "dispatcher")

    def __init__(self) -> None:
        self.db: DatabaseUseCases | None = None
        self.dispatcher: ComDispatcher | None = None


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Async context manager for server lifespan.

    Yields the server state. On exit, performs STA teardown:
    - Closes the active database connection
    - Shuts down the backend dispatcher
    """
    state = _ServerState()
    state.db = DatabaseUseCases(readonly=True, allowed_dirs=allowed_dirs_from_env())
    state.dispatcher = ComDispatcher()

    _log.debug("Server lifespan: starting")
    try:
        yield state
    finally:
        _log.debug("Server lifespan: shutting down")
        # Close active connection
        if state.db is not None:
            try:
                state.db.close_database()
            except Exception as exc:
                _log.warning("Error closing database on shutdown: %s", exc)
        # Shutdown dispatcher (STA teardown)
        if state.dispatcher is not None:
            try:
                state.dispatcher.shutdown()
            except Exception as exc:
                _log.warning("Error shutting down dispatcher: %s", exc)
        close_all_sessions()
        _log.debug("Server lifespan: complete")


# =============================================================================
# Server Builder
# =============================================================================


def build_server(profile: str = "core") -> FastMCP:
    """Build and return a FastMCP server for the given profile.

    Args:
        profile: Profile name ("core", "automation", etc.)

    Returns:
        Configured FastMCP instance (not yet started)
    """
    mcp = FastMCP("ms-access", lifespan=lifespan)
    register_prompts(mcp)

    # Load and register tools for the profile
    for module_name in load_profile(profile):
        try:
            from importlib import import_module

            module = import_module(module_name)
            if hasattr(module, "register"):
                module.register(mcp)
                _log.debug("Registered tools from %s", module_name)
        except ImportError as exc:
            _log.warning("Could not import profile module %s: %s", module_name, exc)
            continue

    return mcp


# =============================================================================
# CLI Entry Point
# =============================================================================


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse --profile argument from argv."""
    parser = argparse.ArgumentParser(
        prog="ms-access-mcp mcp",
        description="Run the MS Access MCP server over stdio.",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help=(
            "MCP tool profile to expose. "
            "Token-cost rationale: core <= 30 tools keeps LLM context lean; "
            "additional profiles (automation, maintenance, versioning, ddl, analysis, migration) are opt-in. "
            "Valid values: core, automation, maintenance, versioning, ddl, analysis, migration, full. "
            "Default: core. Can also be set via ACCESS_MCP_PROFILE env var."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the MCP server.

    Parses --profile, builds the server, and runs it over stdio.
    No banners, no API keys, no stdout output beyond JSON-RPC protocol.
    All logging goes to stderr.
    """
    args = _parse_args(argv)
    resolved = resolve_profile(explicit=args.profile)

    # Validate profile early
    from .profiles import PROFILES

    if resolved not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        print(f"Error: Unknown profile {resolved!r}. Valid: {valid}", file=sys.stderr)
        return 1

    _log.info("Starting MCP server with profile: %s", resolved)
    server = build_server(resolved)
    server.run()  # stdio transport by default
    return 0


if __name__ == "__main__":
    sys.exit(main())
