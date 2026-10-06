"""MCP automation tools — forms, reports, controls, sections, VBA, macros."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from contextvars import ContextVar
from functools import wraps
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from ..application.automation import AutomationUseCases
from ..safety import allowed_dirs_from_env
from ._registry import ToolRegistry, register_tools
from ._session_registry import SessionOpenError, registry
from .core import _result_to_dict, to_mcp_result

__all__ = ["register", "to_mcp_result", "_TOOLS"]

_MODULE_TYPES = {"standard": 1, "class": 2}


class _ToolSession:
    def __init__(self, path: str) -> None:
        self.path = path
        self.stack = ExitStack()
        self.database = None

    def open(self, *, backend: str, readonly: bool = True):
        if self.database is None:
            self.database = self.stack.enter_context(
                registry.open_session(
                    self.path,
                    backend=backend,
                    readonly=readonly,
                    allowed_dirs=allowed_dirs_from_env(),
                )
            )
        return self.database


_active_session: ContextVar[_ToolSession | None] = ContextVar("automation_tool_session", default=None)
_CONFIRM_REQUIRED = {
    "set_control_property", "set_control_properties", "create_form", "rename_form", "delete_form",
    "set_form_property", "set_form_properties", "add_control", "remove_control",
    "set_control_event_procedure", "set_form_section_property", "set_form_section_properties",
    "import_form_from_text", "create_report", "rename_report", "delete_report",
    "set_report_property", "set_report_properties", "set_report_control_property",
    "set_report_control_properties", "add_report_control", "remove_report_control",
    "set_report_section_property", "set_report_section_properties", "import_report_from_text",
    "create_macro", "rename_macro", "delete_macro", "run_macro", "set_vba_code",
    "add_vba_procedure", "save_database", "delete_module", "vba_replace_procedure",
    "create_module", "rename_module",
}


def _connected_tool(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(path: str, *args: Any, **kwargs: Any) -> Any:
        confirm = kwargs.get("confirm", args[1] if len(args) > 1 else False)
        if function.__name__ in _CONFIRM_REQUIRED and not confirm:
            return function(path, *args, **kwargs)
        session = _ToolSession(path)
        token = _active_session.set(session)
        try:
            return function(path, *args, **kwargs)
        except SessionOpenError as exc:
            return to_mcp_result(_result_to_dict(exc.result))
        finally:
            _active_session.reset(token)
            session.stack.close()

    return wrapped


def _new_automation(readonly: bool = True) -> AutomationUseCases:
    session = _active_session.get()
    if session is not None:
        database = session.open(backend="com")
        return AutomationUseCases(
            adapter=database._get_adapter(),  # noqa: SLF001 - the registry-owned DB session manages this adapter
            readonly=readonly,
            db_path=session.path,
        )
    return registry.track(AutomationUseCases(readonly=readonly, db_path=None))


# =============================================================================
# Form Tools (object_kind="form")
# =============================================================================


def get_forms(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get all forms in the database."""
    uc = _new_automation(readonly=True)
    result = uc.list_objects("form")
    return to_mcp_result(_result_to_dict(result))


def open_form(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Open a form."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.open_object("form", name)
    return to_mcp_result(_result_to_dict(result))


def close_form(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Close a form."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.close_object("form", name)
    return to_mcp_result(_result_to_dict(result))


def form_exists(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Check if a form exists."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.object_exists("form", name)
    return to_mcp_result(_result_to_dict(result))


def get_form_controls(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get all controls on a form."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.get_controls("form", name)
    return to_mcp_result(_result_to_dict(result))


def get_control_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get properties of a control on a form."""
    p = params or {}
    name = p.get("name", "")
    control_name = p.get("control_name", "")
    uc = _new_automation(readonly=True)
    result = uc.get_control_properties("form", name, control_name)
    return to_mcp_result(_result_to_dict(result))


def set_control_property(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set a property on a form control."""
    p = params or {}
    name = p.get("name", "")
    control_name = p.get("control_name", "")
    prop_name = p.get("property_name", "")
    value = p.get("value")
    uc = _new_automation(readonly=False)
    result = uc.set_control_property("form", name, control_name, prop_name, value, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def set_control_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set multiple properties on a form control."""
    p = params or {}
    name = p.get("name", "")
    control_name = p.get("control_name", "")
    props = p.get("properties", {})
    uc = _new_automation(readonly=False)
    result = uc.set_control_properties("form", name, control_name, props, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def create_form(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Create a new form.

    ``template_name`` is accepted for compatibility but currently has no effect.
    """
    p = params or {}
    name = p.get("name", "")
    record_source = p.get("record_source", "")
    properties = p.get("properties", None)
    uc = _new_automation(readonly=False)
    result = uc.create_object("form", name, record_source=record_source, properties=properties, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def rename_form(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Rename a form."""
    p = params or {}
    old = p.get("old_name", "")
    new = p.get("new_name", "")
    uc = _new_automation(readonly=False)
    result = uc.rename_object("form", old, new, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def delete_form(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Delete a form."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=False)
    result = uc.delete_object("form", name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def get_form_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get properties of a form."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.get_properties("form", name)
    return to_mcp_result(_result_to_dict(result))


def set_form_property(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set a property on a form."""
    p = params or {}
    name = p.get("name", "")
    prop_name = p.get("property_name", "")
    value = p.get("value")
    uc = _new_automation(readonly=False)
    result = uc.set_property("form", name, prop_name, value, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def set_form_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set multiple properties on a form."""
    p = params or {}
    name = p.get("name", "")
    props = p.get("properties", {})
    uc = _new_automation(readonly=False)
    result = uc.set_properties("form", name, props, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def add_control(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Add a control to a form."""
    p = params or {}
    name = p.get("name", "")
    control_type = p.get("control_type", "")
    control_name = p.get("control_name", "")
    section = p.get("section", 0) or 0
    properties = p.get("properties")
    uc = _new_automation(readonly=False)
    result = uc.add_control("form", name, control_type, control_name, section=section, properties=properties, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def remove_control(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Remove a control from a form."""
    p = params or {}
    name = p.get("name", "")
    control_name = p.get("control_name", "")
    uc = _new_automation(readonly=False)
    result = uc.remove_control("form", name, control_name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def get_control_event_procedures(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get event procedures for a form control."""
    p = params or {}
    name = p.get("name", "")
    control_name = p.get("control_name", "")
    uc = _new_automation(readonly=True)
    result = uc.get_control_event_procedures("form", name, control_name=control_name)
    return to_mcp_result(_result_to_dict(result))


def set_control_event_procedure(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set an event procedure on a form control."""
    p = params or {}
    name = p.get("name", "")
    control_name = p.get("control_name", "")
    event_name = p.get("event_name", "")
    code = p.get("code", "")
    uc = _new_automation(readonly=False)
    result = uc.set_control_event_procedure("form", name, control_name, event_name, code, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def get_form_sections(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get all sections of a form."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.get_sections("form", name)
    return to_mcp_result(_result_to_dict(result))


def get_form_section_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get properties of a form section."""
    p = params or {}
    name = p.get("name", "")
    section_id = p.get("section_id", 0)
    uc = _new_automation(readonly=True)
    result = uc.get_section_properties("form", name, section_id)
    return to_mcp_result(_result_to_dict(result))


def set_form_section_property(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set a property on a form section."""
    p = params or {}
    name = p.get("name", "")
    section_id = p.get("section_id", 0)
    prop_name = p.get("property_name", "")
    value = p.get("value")
    uc = _new_automation(readonly=False)
    result = uc.set_section_property("form", name, section_id, prop_name, value, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def set_form_section_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set multiple properties on a form section."""
    p = params or {}
    name = p.get("name", "")
    section_id = p.get("section_id", 0)
    props = p.get("properties", {})
    uc = _new_automation(readonly=False)
    result = uc.set_section_properties("form", name, section_id, props, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def export_form_to_text(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Export a form definition to text."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.export_to_text("form", name)
    return to_mcp_result(_result_to_dict(result))


def import_form_from_text(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Import a form definition from text."""
    p = params or {}
    name = p.get("name", "")
    text = p.get("text", "")
    uc = _new_automation(readonly=False)
    result = uc.import_from_text("form", name, text, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


# =============================================================================
# Report Tools (object_kind="report")
# =============================================================================


def report_exists(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Check if a report exists."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.object_exists("report", name)
    return to_mcp_result(_result_to_dict(result))


def get_reports(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get all reports in the database."""
    uc = _new_automation(readonly=True)
    result = uc.list_objects("report")
    return to_mcp_result(_result_to_dict(result))


def create_report(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Create a new report.

    ``template_name`` is accepted for compatibility but currently has no effect.
    """
    p = params or {}
    name = p.get("name", "")
    record_source = p.get("record_source", "")
    properties = p.get("properties", None)
    uc = _new_automation(readonly=False)
    result = uc.create_object("report", name, record_source=record_source, properties=properties, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def rename_report(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Rename a report."""
    p = params or {}
    old = p.get("old_name", "")
    new = p.get("new_name", "")
    uc = _new_automation(readonly=False)
    result = uc.rename_object("report", old, new, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def delete_report(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Delete a report."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=False)
    result = uc.delete_object("report", name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def get_report_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get properties of a report."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.get_properties("report", name)
    return to_mcp_result(_result_to_dict(result))


def set_report_property(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set a property on a report."""
    p = params or {}
    name = p.get("name", "")
    prop_name = p.get("property_name", "")
    value = p.get("value")
    uc = _new_automation(readonly=False)
    result = uc.set_property("report", name, prop_name, value, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def set_report_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set multiple properties on a report."""
    p = params or {}
    name = p.get("name", "")
    props = p.get("properties", {})
    uc = _new_automation(readonly=False)
    result = uc.set_properties("report", name, props, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def get_report_controls(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get all controls on a report."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.get_controls("report", name)
    return to_mcp_result(_result_to_dict(result))


def get_report_control_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get properties of a control on a report."""
    p = params or {}
    name = p.get("name", "")
    control_name = p.get("control_name", "")
    uc = _new_automation(readonly=True)
    result = uc.get_control_properties("report", name, control_name)
    return to_mcp_result(_result_to_dict(result))


def set_report_control_property(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set a property on a report control."""
    p = params or {}
    name = p.get("name", "")
    control_name = p.get("control_name", "")
    prop_name = p.get("property_name", "")
    value = p.get("value")
    uc = _new_automation(readonly=False)
    result = uc.set_control_property("report", name, control_name, prop_name, value, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def set_report_control_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set multiple properties on a report control."""
    p = params or {}
    name = p.get("name", "")
    control_name = p.get("control_name", "")
    props = p.get("properties", {})
    uc = _new_automation(readonly=False)
    result = uc.set_control_properties("report", name, control_name, props, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def add_report_control(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Add a control to a report."""
    p = params or {}
    name = p.get("name", "")
    control_type = p.get("control_type", "")
    control_name = p.get("control_name", "")
    section = p.get("section", 0) or 0
    properties = p.get("properties")
    uc = _new_automation(readonly=False)
    result = uc.add_control("report", name, control_type, control_name, section=section, properties=properties, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def remove_report_control(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Remove a control from a report."""
    p = params or {}
    name = p.get("name", "")
    control_name = p.get("control_name", "")
    uc = _new_automation(readonly=False)
    result = uc.remove_control("report", name, control_name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def get_report_sections(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get all sections of a report."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.get_sections("report", name)
    return to_mcp_result(_result_to_dict(result))


def get_report_section_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get properties of a report section."""
    p = params or {}
    name = p.get("name", "")
    section_id = p.get("section_id", 0)
    uc = _new_automation(readonly=True)
    result = uc.get_section_properties("report", name, section_id)
    return to_mcp_result(_result_to_dict(result))


def set_report_section_property(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set a property on a report section."""
    p = params or {}
    name = p.get("name", "")
    section_id = p.get("section_id", 0)
    prop_name = p.get("property_name", "")
    value = p.get("value")
    uc = _new_automation(readonly=False)
    result = uc.set_section_property("report", name, section_id, prop_name, value, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def set_report_section_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set multiple properties on a report section."""
    p = params or {}
    name = p.get("name", "")
    section_id = p.get("section_id", 0)
    props = p.get("properties", {})
    uc = _new_automation(readonly=False)
    result = uc.set_section_properties("report", name, section_id, props, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def export_report_to_text(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Export a report definition to text."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.export_to_text("report", name)
    return to_mcp_result(_result_to_dict(result))


def import_report_from_text(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Import a report definition from text."""
    p = params or {}
    name = p.get("name", "")
    text = p.get("text", "")
    uc = _new_automation(readonly=False)
    result = uc.import_from_text("report", name, text, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


# =============================================================================
# Macro Tools
# =============================================================================


def macro_exists(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Check if a macro exists."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.macro_exists(name)
    return to_mcp_result(_result_to_dict(result))


def get_macros(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get all macros in the database."""
    uc = _new_automation(readonly=True)
    result = uc.get_macros()
    return to_mcp_result(_result_to_dict(result))


def get_macro_properties(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get properties of a macro."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=True)
    result = uc.get_macro_properties(name)
    return to_mcp_result(_result_to_dict(result))


def create_macro(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Create a new macro."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=False)
    result = uc.create_macro(name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def rename_macro(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Rename a macro."""
    p = params or {}
    old = p.get("old_name", "")
    new = p.get("new_name", "")
    uc = _new_automation(readonly=False)
    result = uc.rename_macro(old, new, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def delete_macro(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Delete a macro."""
    name = (params or {}).get("name", "")
    uc = _new_automation(readonly=False)
    result = uc.delete_macro(name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def run_macro(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Run a macro."""
    name = (params or {}).get("name", "")
    if not confirm:
        return to_mcp_result({
            "success": False,
            "error": "write policy blocks this; pass confirm=True and enable writes",
        })
    uc = _new_automation(readonly=False)
    result = uc.run_macro(name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


# =============================================================================
# VBA Tools
# =============================================================================


def get_vba_projects(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get all VBA projects in the database."""
    uc = _new_automation(readonly=True)
    result = uc.get_vba_project_name()
    return to_mcp_result(_result_to_dict(result))


def get_modules(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get all VBA modules in the database."""
    uc = _new_automation(readonly=True)
    result = uc.get_modules()
    return to_mcp_result(_result_to_dict(result))


def get_vba_code(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get VBA code from a module."""
    module_name = (params or {}).get("module_name", "")
    uc = _new_automation(readonly=True)
    result = uc.get_vba_code(module_name)
    return to_mcp_result(_result_to_dict(result))


def set_vba_code(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Set VBA code in a module."""
    if not confirm:
        return to_mcp_result({
            "success": False,
            "error": "write policy blocks this; pass confirm=True and enable writes",
        })
    p = params or {}
    module_name = p.get("module_name", "")
    code = p.get("code", "")
    uc = _new_automation(readonly=False)
    result = uc.set_vba_code(module_name, code, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def add_vba_procedure(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Add a VBA procedure to a module."""
    p = params or {}
    module_name = p.get("module_name", "")
    procedure_name = p.get("procedure_name", "")
    code = p.get("code", "")
    uc = _new_automation(readonly=False)
    result = uc.add_vba_procedure(module_name, procedure_name, code, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def compile_vba(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Compile all VBA code in the database."""
    uc = _new_automation(readonly=False)
    result = uc.compile_vba()
    return to_mcp_result(_result_to_dict(result))


def save_database(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Save the database."""
    uc = _new_automation(readonly=False)
    result = uc.save_database(confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def delete_module(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Delete a VBA module."""
    module_name = (params or {}).get("module_name", "")
    uc = _new_automation(readonly=False)
    result = uc.delete_module(module_name, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def vba_list_procedures(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """List all procedures in a VBA module."""
    module_name = (params or {}).get("module_name", "")
    uc = _new_automation(readonly=True)
    result = uc.vba_list_procedures(module_name)
    return to_mcp_result(_result_to_dict(result))


def vba_get_procedure(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Get a specific VBA procedure from a module."""
    p = params or {}
    module_name = p.get("module_name", "")
    procedure_name = p.get("procedure_name", "")
    uc = _new_automation(readonly=True)
    result = uc.vba_get_procedure(module_name, procedure_name)
    return to_mcp_result(_result_to_dict(result))


def vba_replace_procedure(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Replace a VBA procedure in a module."""
    p = params or {}
    module_name = p.get("module_name", "")
    procedure_name = p.get("procedure_name", "")
    code = p.get("code", "")
    uc = _new_automation(readonly=False)
    result = uc.vba_replace_procedure(module_name, procedure_name, code, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def create_module(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Create a new VBA module."""
    p = params or {}
    module_name = p.get("module_name", "")
    module_type = p.get("module_type", "standard")
    if module_type not in _MODULE_TYPES:
        return to_mcp_result({
            "success": False,
            "error": f"invalid module_type {module_type!r}; must be one of {list(_MODULE_TYPES.keys())}",
        })
    uc = _new_automation(readonly=False)
    result = uc.create_module(module_name, module_type=_MODULE_TYPES[module_type], confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def rename_module(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Rename a VBA module."""
    p = params or {}
    old = p.get("old_name", "")
    new = p.get("new_name", "")
    uc = _new_automation(readonly=False)
    result = uc.rename_module(old, new, confirm=confirm)
    return to_mcp_result(_result_to_dict(result))


def module_exists(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Check if a VBA module exists."""
    module_name = (params or {}).get("module_name", "")
    uc = _new_automation(readonly=True)
    result = uc.module_exists(module_name)
    return to_mcp_result(_result_to_dict(result))


# =============================================================================
# Tool Registry
# =============================================================================

_TOOLS: ToolRegistry = {
    # Forms
    "get_forms": (get_forms, "Get all forms in the database."),
    "open_form": (open_form, "Open a form."),
    "close_form": (close_form, "Close a form."),
    "form_exists": (form_exists, "Check if a form exists."),
    "get_form_controls": (get_form_controls, "Get all controls on a form."),
    "get_control_properties": (get_control_properties, "Get properties of a form control."),
    "set_control_property": (set_control_property, "Set a property on a form control."),
    "set_control_properties": (set_control_properties, "Set multiple properties on a form control."),
    "create_form": (create_form, "Create a new form."),
    "rename_form": (rename_form, "Rename a form."),
    "delete_form": (delete_form, "Delete a form."),
    "get_form_properties": (get_form_properties, "Get properties of a form."),
    "set_form_property": (set_form_property, "Set a property on a form."),
    "set_form_properties": (set_form_properties, "Set multiple properties on a form."),
    "add_control": (add_control, "Add a control to a form."),
    "remove_control": (remove_control, "Remove a control from a form."),
    "get_control_event_procedures": (get_control_event_procedures, "Get event procedures for a form control."),
    "set_control_event_procedure": (set_control_event_procedure, "Set an event procedure on a form control."),
    "get_form_sections": (get_form_sections, "Get all sections of a form."),
    "get_form_section_properties": (get_form_section_properties, "Get properties of a form section."),
    "set_form_section_property": (set_form_section_property, "Set a property on a form section."),
    "set_form_section_properties": (set_form_section_properties, "Set multiple properties on a form section."),
    "export_form_to_text": (export_form_to_text, "Export a form definition to text."),
    "import_form_from_text": (import_form_from_text, "Import a form definition from text."),
    # Reports
    "report_exists": (report_exists, "Check if a report exists."),
    "get_reports": (get_reports, "Get all reports in the database."),
    "create_report": (create_report, "Create a new report."),
    "rename_report": (rename_report, "Rename a report."),
    "delete_report": (delete_report, "Delete a report."),
    "get_report_properties": (get_report_properties, "Get properties of a report."),
    "set_report_property": (set_report_property, "Set a property on a report."),
    "set_report_properties": (set_report_properties, "Set multiple properties on a report."),
    "get_report_controls": (get_report_controls, "Get all controls on a report."),
    "get_report_control_properties": (get_report_control_properties, "Get properties of a control on a report."),
    "set_report_control_property": (set_report_control_property, "Set a property on a report control."),
    "set_report_control_properties": (set_report_control_properties, "Set multiple properties on a report control."),
    "add_report_control": (add_report_control, "Add a control to a report."),
    "remove_report_control": (remove_report_control, "Remove a control from a report."),
    "get_report_sections": (get_report_sections, "Get all sections of a report."),
    "get_report_section_properties": (get_report_section_properties, "Get properties of a report section."),
    "set_report_section_property": (set_report_section_property, "Set a property on a report section."),
    "set_report_section_properties": (set_report_section_properties, "Set multiple properties on a report section."),
    "export_report_to_text": (export_report_to_text, "Export a report definition to text."),
    "import_report_from_text": (import_report_from_text, "Import a report definition from text."),
    # Macros
    "macro_exists": (macro_exists, "Check if a macro exists."),
    "get_macros": (get_macros, "Get all macros in the database."),
    "get_macro_properties": (get_macro_properties, "Get properties of a macro."),
    "create_macro": (create_macro, "Create a new macro."),
    "rename_macro": (rename_macro, "Rename a macro."),
    "delete_macro": (delete_macro, "Delete a macro."),
    "run_macro": (run_macro, "Run a macro."),
    # VBA
    "get_vba_projects": (get_vba_projects, "Get all VBA projects in the database."),
    "get_modules": (get_modules, "Get all VBA modules in the database."),
    "get_vba_code": (get_vba_code, "Get VBA code from a module."),
    "set_vba_code": (set_vba_code, "Set VBA code in a module."),
    "add_vba_procedure": (add_vba_procedure, "Add a VBA procedure to a module."),
    "compile_vba": (compile_vba, "Compile all VBA code in the database."),
    "save_database": (save_database, "Save the database."),
    "delete_module": (delete_module, "Delete a VBA module."),
    "vba_list_procedures": (vba_list_procedures, "List all procedures in a VBA module."),
    "vba_get_procedure": (vba_get_procedure, "Get a specific VBA procedure from a module."),
    "vba_replace_procedure": (vba_replace_procedure, "Replace a VBA procedure in a module."),
    "create_module": (create_module, "Create a new VBA module."),
    "rename_module": (rename_module, "Rename a VBA module."),
    "module_exists": (module_exists, "Check if a VBA module exists."),
}

for _tool_name, (_tool, _description) in tuple(_TOOLS.items()):
    _wrapped_tool = _connected_tool(_tool)
    _TOOLS[_tool_name] = (_wrapped_tool, _description)
    globals()[_tool.__name__] = _wrapped_tool


def register(mcp: FastMCP) -> None:
    """Register all automation tools with the FastMCP server."""
    register_tools(mcp, _TOOLS)
