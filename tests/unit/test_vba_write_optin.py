"""Opt-in contract for VBA and macro code writes."""

import json

import pytest

from ms_access_mcp import cli
from ms_access_mcp.application.automation import AutomationUseCases, Err
from ms_access_mcp.mcp_tools import automation as mcp_automation


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def connect(self, *_args, **_kwargs):
        self.calls.append(("connect",))
        return True

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return True
        return call


GATED_CALLS = [
    ("set_vba_code", ("Module1", "Sub A()\nEnd Sub")),
    ("add_vba_procedure", ("Module1", "A", "Sub A()\nEnd Sub")),
    ("vba_replace_procedure", ("Module1", "A", "Sub A()\nEnd Sub")),
    ("create_module", ("Module2",)),
    ("rename_module", ("Module1", "Module2")),
    ("delete_module", ("Module1",)),
    ("create_macro", ("Macro1",)),
    ("rename_macro", ("Macro1", "Macro2")),
    ("delete_macro", ("Macro1",)),
    ("set_control_event_procedure", ("form", "Form1", "Button1", "Click", "Private Sub Button1_Click()\nEnd Sub")),
]


@pytest.mark.parametrize(("method", "args"), GATED_CALLS)
def test_vba_macro_writes_disabled_without_opt_in(monkeypatch, method, args):
    monkeypatch.delenv("ACCESS_MCP_ENABLE_VBA_WRITES", raising=False)
    adapter = FakeAdapter()
    use_cases = AutomationUseCases(adapter=adapter, readonly=False)

    result = getattr(use_cases, method)(*args, confirm=True)

    assert isinstance(result, Err)
    assert "ACCESS_MCP_ENABLE_VBA_WRITES" in result.message
    assert adapter.calls == []


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "YeS"])
def test_opt_in_truthy_values_allow_adapter_call(monkeypatch, value):
    monkeypatch.setenv("ACCESS_MCP_ENABLE_VBA_WRITES", value)
    adapter = FakeAdapter()
    result = AutomationUseCases(adapter=adapter, readonly=False).create_macro("Macro1", confirm=True)
    assert not isinstance(result, Err)
    assert any(call[0] == "create_macro" for call in adapter.calls)


def test_cli_surfaces_the_same_actionable_disabled_message(monkeypatch, capsys):
    monkeypatch.delenv("ACCESS_MCP_ENABLE_VBA_WRITES", raising=False)
    use_cases = AutomationUseCases(adapter=FakeAdapter(), readonly=False)
    expected = use_cases.set_vba_code("Module1", "x", confirm=True)
    assert isinstance(expected, Err)

    monkeypatch.setattr(cli, "_automation_use_cases", lambda _opts: use_cases)
    exit_code = cli.main(
        [
            "automation",
            "vba",
            "set-code",
            "--module",
            "Module1",
            "--code",
            "x",
            "--yes",
            "--allow-write",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload == {"success": False, "error": expected.message}


def test_mcp_tool_descriptions_disclose_the_opt_in():
    gated_tools = {
        "set_vba_code",
        "add_vba_procedure",
        "vba_replace_procedure",
        "create_module",
        "rename_module",
        "delete_module",
        "create_macro",
        "rename_macro",
        "delete_macro",
        "set_control_event_procedure",
    }

    for name in gated_tools:
        assert "ACCESS_MCP_ENABLE_VBA_WRITES=1" in mcp_automation._TOOLS[name][1]


def test_mcp_surfaces_actionable_disabled_message(monkeypatch):
    monkeypatch.delenv("ACCESS_MCP_ENABLE_VBA_WRITES", raising=False)
    adapter = FakeAdapter()
    uc = AutomationUseCases(adapter=adapter, readonly=False)
    monkeypatch.setattr(mcp_automation, "_new_automation", lambda readonly=True: uc)

    result = mcp_automation.set_vba_code("unused.accdb", {"module_name": "Module1", "code": "x"}, confirm=True)

    payload = json.loads(result.content[0].text)
    assert result.isError is True
    assert "ACCESS_MCP_ENABLE_VBA_WRITES" in payload["error"]
    assert adapter.calls == []
