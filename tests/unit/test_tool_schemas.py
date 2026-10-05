"""Schema validation for core tools — all params are concrete typed properties.

These tests assert that every core tool has per-parameter JSON Schema
properties (FastMCP generates these from typed signatures).  An opaque
``params`` object property that swallows all arguments would FAIL this test.

The 21 core tool names are also pinned so that rename drift is caught.
"""

from __future__ import annotations

from ms_access_mcp.server import build_server

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _get_core_tools() -> dict[str, object]:
    """Build a core-profile server and return the dict of name -> Tool."""
    server = build_server("core")
    tool_manager = getattr(server, "_tool_manager", None)
    if tool_manager is None:
        return {}
    return getattr(tool_manager, "_tools", {})


def _get_profile_tools(profile: str) -> dict[str, object]:
    server = build_server(profile)
    tool_manager = getattr(server, "_tool_manager", None)
    if tool_manager is None:
        return {}
    return getattr(tool_manager, "_tools", {})


def _schema_properties(tool) -> dict:
    """Return the JSON Schema properties dict for a tool's parameters (FastMCP)."""
    return tool.parameters.get("properties", {})


def _has_opaque_params(props: dict) -> bool:
    """Return True if props has a bare 'params' that is an opaque object.

    Detects both:
    - ``params: {type: 'object'}``
    - ``params: {anyOf: [{type: 'object'}, {type: 'null'}]}``  (generic wrapper pattern)
    """
    if "params" not in props:
        return False
    p = props["params"]
    # Direct object type
    if p.get("type") == "object":
        return True
    # anyOf with an object inside (the generic wrapper pattern)
    any_of = p.get("anyOf", [])
    for schema in any_of:
        if schema.get("type") == "object":
            return True
    return False


# ----------------------------------------------------------------------
# Tool-name pin
# ----------------------------------------------------------------------


def test_core_tool_names_pinned():
    """The 21 core tool names must not change (rename drift guard)."""
    EXPECTED = {
        "connect_access",
        "disconnect_access",
        "list_connections",
        "get_tables",
        "get_table_schema",
        "get_indexes",
        "get_relationships",
        "get_queries",
        "create_query",
        "set_query_sql",
        "delete_query",
        "save_query",
        "query_data",
        "insert_data",
        "update_data",
        "delete_data",
        "execute_raw_sql",
        "export_data",
        "get_database_statistics",
        "get_system_tables",
        "get_linked_tables",
    }
    tools = _get_core_tools()
    names = set(tools.keys())
    assert len(names) == 21, f"Expected 21 tools, got {len(names)}: {sorted(names)}"
    assert names == EXPECTED, f"Tool names changed.\n  Expected: {sorted(EXPECTED)}\n  Got:      {sorted(names)}"


# ----------------------------------------------------------------------
# Schema shape guards
# ----------------------------------------------------------------------


def test_no_tool_takes_opaque_params_object():
    """No core tool may have its entire parameter surface as a single 'params' object.

    FastMCP generates ``parameters`` JSON Schema from function signatures.
    A signature ``(path, params: dict | None, confirm: bool)`` produces
    ``params: {anyOf: [{type: 'object'}, {type: 'null'}]}`` — an opaque blob.
    A typed signature produces concrete per-parameter properties instead.
    This test FAILs on the current generic-wrapper implementation.
    """
    tools = _get_core_tools()
    failures: list[str] = []

    for name, tool in tools.items():
        props = _schema_properties(tool)
        if _has_opaque_params(props):
            failures.append(
                f"  {name}: has opaque 'params' object property — "
                f"signature is generic, not typed. "
                f"Properties: {list(props.keys())}"
            )

    assert not failures, (
        "The following tools take an opaque 'params' object "
        "(typed signatures required):\n" + "\n".join(failures)
    )


def test_all_versioning_tools_declare_path_property():
    tools = _get_profile_tools("versioning")
    expected = {
        "versioning_export_object",
        "versioning_export_all",
        "versioning_compare",
        "versioning_export_schema_ddl",
        "versioning_import_object",
        "versioning_import_all",
    }

    assert expected.issubset(tools)
    for name in expected:
        assert "path" in _schema_properties(tools[name]), f"{name} has no path property"


def test_every_core_tool_has_path_property():
    """Every core tool that requires a database path must accept a 'path' parameter.

    disconnect_access and list_connections are connection-scoped and have no path.
    All other tools must accept path: str.
    """
    tools = _get_core_tools()
    # Tools that do NOT need a path (connection-scoped operations)
    no_path = {"disconnect_access", "list_connections"}
    failures: list[str] = []

    for name, tool in tools.items():
        props = _schema_properties(tool)
        if name not in no_path and "path" not in props:
            failures.append(f"  {name}: missing 'path' property. Props: {list(props.keys())}")

    assert not failures, (
        "These tools are missing a 'path' property:\n" + "\n".join(failures)
    )
