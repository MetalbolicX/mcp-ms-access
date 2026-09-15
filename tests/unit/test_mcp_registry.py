"""Unit tests for the shared MCP tool-registration helper."""

from ms_access_mcp.mcp_tools import _registry


class FakeMcp:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add_tool(self, fn, *, name, description):
        self.calls.append((fn, name, description))


def test_register_tools_registers_every_entry_with_name_and_description():
    def _a(): ...
    def _b(): ...
    mcp = FakeMcp()
    _registry.register_tools(mcp, {"alpha": (_a, "A tool"), "beta": (_b, "B tool")})
    assert mcp.calls == [(_a, "alpha", "A tool"), (_b, "beta", "B tool")]


def test_register_tools_preserves_insertion_order():
    def _f(): ...
    tools = {f"tool_{i}": (_f, f"desc {i}") for i in range(5)}
    mcp = FakeMcp()
    _registry.register_tools(mcp, tools)
    assert [c[1] for c in mcp.calls] == [f"tool_{i}" for i in range(5)]
