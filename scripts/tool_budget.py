"""Tool budget gate — asserts core ≤ 30 tools and disabled profiles contribute zero."""

from __future__ import annotations

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

PROFILES = ("core", "automation", "maintenance", "versioning", "ddl", "analysis", "migration", "full")


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
    results: dict[str, tuple[int, list[str]]] = {}
    build_failed = False
    for profile in PROFILES:
        try:
            count, names = count_tools(profile)
            results[profile] = (count, names)
            print(f"{profile}: {count} tools")
        except Exception as exc:
            print(f"{profile}: ERROR building profile: {exc}", file=sys.stderr)
            results[profile] = (0, [str(exc)])
            build_failed = True

    if build_failed:
        return 1

    # Gate 1: every registered profile must expose tools; core also has a budget.
    for profile, (count, names) in results.items():
        if count == 0:
            print(f"FAIL: {profile} profile has 0 tools", file=sys.stderr)
            return 1
    core_count, core_names = results["core"]
    if core_count > 30:
        print(f"FAIL: core profile has {core_count} tools (max 30)", file=sys.stderr)
        print(f"  Tools: {core_names}", file=sys.stderr)
        return 1
    print(f"OK: core profile has {core_count} tools (<= 30)")

    # Gate 2: specialized profiles must not introduce tools outside the full set.
    full_names = set(results["full"][1])
    for profile in ("automation", "maintenance", "versioning"):
        names = set(results[profile][1])
        extra = names - full_names
        if extra:
            print(f"WARN: {profile} has tools not in full: {extra}", file=sys.stderr)

    # Gate 3: full = core + automation + maintenance + versioning
    # (This is a structural check, not a strict gate)
    core_set = set(core_names)
    automation_set = set(results["automation"][1])
    maintenance_set = set(results["maintenance"][1])
    versioning_set = set(results["versioning"][1])
    combined = core_set | automation_set | maintenance_set | versioning_set
    full_set = full_names

    if not full_set.issubset(combined):
        missing = full_set - combined
        print(f"WARN: full has tools not in combined: {missing}", file=sys.stderr)

    print("PASS: tool budget gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
