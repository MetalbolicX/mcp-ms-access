"""Profile registry for MCP tools — maps profile names to tool modules."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__ = ["PROFILES", "load_profile", "resolve_profile"]


PROFILES: frozenset[str] = frozenset({"core", "automation", "maintenance", "versioning", "ddl", "analysis", "full"})


def _profile_modules(profile: str) -> list[str]:
    """Return the list of module names for a given profile."""
    # Core profile: database introspection + read operations
    core = [
        "ms_access_mcp.mcp_tools.core",
    ]
    # Automation profile (plan 006): forms, reports, VBA
    automation = [
        "ms_access_mcp.mcp_tools.automation",
    ]
    # Maintenance profile (plan 007): compact, repair, backup
    maintenance = [
        "ms_access_mcp.mcp_tools.maintenance",
    ]
    # Versioning profile (plan 007): version history, diff
    versioning = [
        "ms_access_mcp.mcp_tools.versioning",
    ]
    # DDL profile (plan 022): DDL, linked-table, and database-admin tools
    ddl = [
        "ms_access_mcp.mcp_tools.ddl",
    ]
    # Analysis profile (plan 025): ER diagram, query analysis, SQL generation
    analysis = [
        "ms_access_mcp.mcp_tools.analysis",
    ]

    dispatch: dict[str, list[str]] = {
        "core": core,
        "automation": automation,
        "maintenance": maintenance,
        "versioning": versioning,
        "ddl": ddl,
        "analysis": analysis,
        "full": core + automation + maintenance + versioning + ddl + analysis,
    }
    return dispatch.get(profile, [])


def load_profile(name: str) -> list[str]:
    """Load tool modules for the given profile.

    Args:
        name: Profile name ("core", "automation", "maintenance", "versioning", "ddl", "analysis", "full")

    Returns:
        List of module names to register

    Raises:
        ValueError: If profile name is unknown
    """
    if name not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile {name!r}. Valid profiles: {valid}")
    return _profile_modules(name)


def resolve_profile(explicit: str | None = None) -> str:
    """Resolve profile name from explicit arg or environment.

    Resolution order:
    1. Explicit argument (--profile)
    2. ACCESS_MCP_PROFILE environment variable
    3. Default: "core"

    Args:
        explicit: Explicit --profile argument value

    Returns:
        Resolved profile name
    """
    if explicit is not None:
        return explicit
    return os.environ.get("ACCESS_MCP_PROFILE", "core")


# Module-level import for type hints (avoids circular imports at runtime)
ToolModule: type = object  # placeholder — actual type is inferred at registration
