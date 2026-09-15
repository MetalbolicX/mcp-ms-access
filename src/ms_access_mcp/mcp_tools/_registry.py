"""Shared MCP tool-registration helper.

Every profile module declares a ``_TOOLS`` dispatch table and a public
``register(mcp)`` entry point (called dynamically by ``server.build_server``).
The registration loop lives here exactly once so the 7 modules cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeAlias

from mcp.server.fastmcp import FastMCP

ToolFn: TypeAlias = Callable[..., Any]
ToolRegistry: TypeAlias = dict[str, tuple[ToolFn, str]]


def register_tools(mcp: FastMCP, tools: ToolRegistry) -> None:
    """Register every (name -> (fn, description)) entry with the FastMCP server."""
    for name, (fn, description) in tools.items():
        mcp.add_tool(fn, name=name, description=description)
