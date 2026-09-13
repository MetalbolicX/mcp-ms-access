"""Tests for mcp_tools/automation.py — forms, reports, VBA, macros."""

from __future__ import annotations

import inspect
import json

from mcp.types import TextContent

from ms_access_mcp.mcp_tools.automation import (
    _TOOLS,
    create_module,
    run_macro,
    set_vba_code,
)
from ms_access_mcp.mcp_tools.core import _TOOLS as CORE_TOOLS
from ms_access_mcp.profiles import load_profile


def _text(result) -> str:
    """Extract the single TextContent text from a CallToolResult.content list."""
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


class TestAutomationToolNames:
    """All verbatim oracle names are present."""

    EXPECTED_TOOLS = {
        # Forms
        "get_forms",
        "open_form",
        "close_form",
        "form_exists",
        "get_form_controls",
        "get_control_properties",
        "set_control_property",
        "set_control_properties",
        "create_form",
        "rename_form",
        "delete_form",
        "get_form_properties",
        "set_form_property",
        "set_form_properties",
        "add_control",
        "remove_control",
        "get_control_event_procedures",
        "set_control_event_procedure",
        "get_form_sections",
        "get_form_section_properties",
        "set_form_section_property",
        "set_form_section_properties",
        "export_form_to_text",
        "import_form_from_text",
        # Reports
        "report_exists",
        "get_reports",
        "create_report",
        "rename_report",
        "delete_report",
        "get_report_properties",
        "set_report_property",
        "set_report_properties",
        "get_report_controls",
        "get_report_control_properties",
        "set_report_control_property",
        "set_report_control_properties",
        "add_report_control",
        "remove_report_control",
        "get_report_sections",
        "get_report_section_properties",
        "set_report_section_property",
        "set_report_section_properties",
        "export_report_to_text",
        "import_report_from_text",
        # Macros
        "macro_exists",
        "get_macros",
        "get_macro_properties",
        "create_macro",
        "rename_macro",
        "delete_macro",
        "run_macro",
        # VBA
        "get_vba_projects",
        "get_modules",
        "get_vba_code",
        "set_vba_code",
        "add_vba_procedure",
        "compile_vba",
        "save_database",
        "delete_module",
        "vba_list_procedures",
        "vba_get_procedure",
        "vba_replace_procedure",
        "create_module",
        "rename_module",
        "module_exists",
    }

    def test_all_verbatim_names_registered(self):
        """Every verbatim oracle name appears in _TOOLS."""
        registered = set(_TOOLS.keys())
        missing = self.EXPECTED_TOOLS - registered
        assert len(missing) == 0, f"Missing tools: {missing}"

    def test_no_extra_tools_registered(self):
        """Only verbatim oracle names are registered."""
        registered = set(_TOOLS.keys())
        extra = registered - self.EXPECTED_TOOLS
        assert len(extra) == 0, f"Extra tools: {extra}"

    def test_tool_count_in_range(self):
        """Tool count should be in the expected range [40, 70] (forms+reports share use cases but each has its own wrapper)."""
        count = len(_TOOLS)
        assert 40 <= count <= 70, f"Expected 40-70 tools, got {count}"


class TestNoPasswordParameter:
    """No tool should accept a plaintext password parameter."""

    def test_no_password_parameter(self):
        """No function signature contains a 'password' parameter."""
        for name, (fn, _) in _TOOLS.items():
            sig = inspect.signature(fn)
            param_names = set(sig.parameters.keys())
            conflicts = param_names & {"password", "pwd", "passwd"}
            assert not conflicts, f"Tool {name} has password-like params: {conflicts}"


class TestToolSignatureConvention:
    """Every tool follows the (path, params, confirm) convention."""

    def test_each_tool_accepts_path_params_confirm(self):
        """Every tool's first three parameters are path, params, confirm."""
        for name, (fn, _) in _TOOLS.items():
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
            assert params[:3] == ["path", "params", "confirm"], (
                f"Tool {name} signature does not match convention: {params[:3]}"
            )


class TestAutomationConfirmGate:
    """Tools that modify state require confirm=True gate."""

    def test_set_vba_code_gate_without_confirm(self):
        """set_vba_code without confirm returns isError=True."""
        result = set_vba_code("", params={"module_name": "mod1", "code": "Sub Foo()\nEnd Sub"}, confirm=False)
        assert result.isError is True
        content = json.loads(_text(result))
        assert content["success"] is False
        assert "confirm=True" in content["error"]

    def test_run_macro_gate_without_confirm(self):
        """run_macro without confirm returns isError=True."""
        result = run_macro("", params={"name": "MyMacro"}, confirm=False)
        assert result.isError is True
        content = json.loads(_text(result))
        assert content["success"] is False
        assert "confirm=True" in content["error"]


class TestAutomationBadInputs:
    """Bad inputs return isError=True before hitting use case."""

    def test_create_module_invalid_module_type(self):
        """create_module with invalid module_type returns isError=True."""
        result = create_module("", params={"module_name": "mod1", "module_type": "invalid"}, confirm=True)
        assert result.isError is True
        content = json.loads(_text(result))
        assert content["success"] is False
        assert "invalid module_type" in content["error"]


class TestAutomationProfileIsolation:
    """Automation profile is properly isolated."""

    def test_core_profile_unchanged(self):
        """Core profile still has exactly 21 tools (no regression)."""
        assert len(CORE_TOOLS) == 21, f"Core profile regressed: {len(CORE_TOOLS)} tools"

    def test_automation_profile_includes_automation_module(self):
        """Automation profile includes the automation module."""
        modules = load_profile("automation")
        assert "ms_access_mcp.mcp_tools.automation" in modules

    def test_full_profile_includes_both(self):
        """Full profile includes both core and automation."""
        modules = load_profile("full")
        assert "ms_access_mcp.mcp_tools.core" in modules
        assert "ms_access_mcp.mcp_tools.automation" in modules

    def test_disabled_profile_zero_automation_tools(self):
        """Non-automation profiles do not include automation module."""
        for profile in ("core", "maintenance", "versioning"):
            modules = load_profile(profile)
            assert "ms_access_mcp.mcp_tools.automation" not in modules
