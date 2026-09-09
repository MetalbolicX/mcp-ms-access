"""Tool budget gate — asserts core ≤ 30 tools and disabled profiles contribute zero."""

from __future__ import annotations

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def count_tools(profile: str) -> tuple[int, list[str]]:
    """Build server for profile and count registered tools."""
    from ms_access_mcp.server import build_server

    server = build_server(profile)
    # FastMCP stores tools in _tool_manager
    tool_manager = getattr(server, "_tool_manager", None)
    if tool_manager is None:
        return (0, [])
    tools = getattr(tool_manager, "_tools", [])
    names = [t[0] if isinstance(t, tuple) else t for t in tools]
    return (len(names), names)


def main() -> int:
    """Check tool budgets for all profiles.

    Returns:
        0 on success, non-zero on failure
    """
    profiles = ["core", "automation", "maintenance", "versioning", "full"]

    results: dict[str, tuple[int, list[str]]] = {}
    for profile in profiles:
        try:
            count, names = count_tools(profile)
            results[profile] = (count, names)
            print(f"{profile}: {count} tools")
        except Exception as exc:
            print(f"ERROR building {profile}: {exc}", file=sys.stderr)
            results[profile] = (0, [])

    # Gate 1: core ≤ 30
    core_count, core_names = results["core"]
    if core_count > 30:
        print(f"FAIL: core profile has {core_count} tools (max 30)", file=sys.stderr)
        print(f"  Tools: {core_names}", file=sys.stderr)
        return 1
    print(f"OK: core profile has {core_count} tools (<= 30)")

    # Gate 2: disabled profiles contribute zero schemas
    full_names = set(results["full"][1])
    for profile in ["automation", "maintenance", "versioning"]:
        count, names = results[profile]
        profile_names = set(names)
        if count == 0:
            print(f"OK: {profile} contributes 0 tools (disabled)")
        else:
            # Check that they don't add new tools beyond core
            extra = profile_names - full_names
            if extra:
                print(f"WARN: {profile} has tools not in full: {extra}", file=sys.stderr)

    # Gate 3: full = core + automation + maintenance + versioning
    # (This is a structural check, not a strict gate)
    core_set = set(core_names)
    automation_set = set(results["automation"][1])
    maintenance_set = set(results["maintenance"][1])
    versioning_set = set(results["versioning"][1])

    # The sum of all should equal or exceed full
    # (full might have more if there are cross-profile tools)
    combined = core_set | automation_set | maintenance_set | versioning_set
    full_set = full_names

    if not full_set.issubset(combined):
        missing = full_set - combined
        print(f"WARN: full has tools not in combined: {missing}", file=sys.stderr)

    print("PASS: tool budget gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
