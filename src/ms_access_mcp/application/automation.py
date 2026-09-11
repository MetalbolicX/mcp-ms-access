"""Automation use cases — forms, reports, macros, VBA.

Constructor-injected backends, immutable result envelopes.
All destructive ops gated via safety.require_acknowledgment + write policy.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from ..errors import Err, Ok, Result
from ..errors import err as _err
from ..errors import ok as _ok
from ..safety import WritePolicy
from ._adapter_lifecycle import AdapterLifecycle
from .protocols import AutomationAdapterProtocol

if TYPE_CHECKING:
    pass


class AutomationUseCases(AdapterLifecycle[AutomationAdapterProtocol]):
    """Constructor-injected automation operations.

    No globals, no container. Each instance holds a reference to the
    active backend adapter and write policy.
    """

    _backend = "com"
    _capabilities_name = "AUTOMATION_CAPS"

    def __init__(
        self,
        adapter: AutomationAdapterProtocol | None = None,
        readonly: bool = True,
        db_path: str | None = None,
    ) -> None:
        """Initialize with optional adapter injection and policy.

        Args:
            adapter: Injected adapter instance. If None, one will be created
                     on demand via BackendSelector on first use.
            readonly: If True, all write operations are rejected.
            db_path: Path to the .accdb file (required if adapter is None).
        """
        self._adapter = adapter
        self._policy = WritePolicy(readonly=readonly)
        self._db_path = db_path

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _confirm_gate(self, operation: str, confirm: bool) -> Err | None:
        """Run confirmation gate. Returns Err if blocked, None if allowed."""
        if ack := self._policy.require_acknowledgment(operation, confirm):
            return _err(ack)
        return None

    def _write_gate(self) -> Err | None:
        """Run write gate. Returns Err if blocked, None if allowed."""
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        return None

    def _dangerous(
        self, operation: str, confirm: bool, needs_write: bool = True
    ) -> Err | None:
        """Combined confirm + write gate for dangerous operations.

        Args:
            operation: Operation name for confirmation message.
            confirm: User confirmed the operation.
            needs_write: Whether this op also requires write permission.

        Returns:
            Err if blocked, None if allowed.
        """
        if err := self._confirm_gate(operation, confirm):
            return err
        if needs_write:
            if err := self._write_gate():
                return err
        return None

    def _call_adapter(self, method_name: str, *args, **kwargs) -> Err | Ok:
        """Call adapter method, converting failures to Err.

        Returns:
            Ok on success, Err on failure.
        """
        if err := self._ensure_adapter():
            return err
        method = getattr(self._adapter, method_name, None)
        if method is None:
            return _err(f"adapter has no method {method_name}")
        try:
            result = method(*args, **kwargs)
            return _ok(result)
        except Exception as e:
            return _err(str(e))

    def _call_adapter_bool(
        self,
        method_name: str,
        *args,
        rowcount: int = 1,
        **kwargs,
    ) -> Err | Ok:
        """Call adapter method expected to return bool.

        Returns:
            Ok if truthy result, Err if falsy or exception.
        """
        if err := self._ensure_adapter():
            return err
        method = getattr(self._adapter, method_name, None)
        if method is None:
            return _err(f"adapter has no method {method_name}")
        try:
            result = method(*args, **kwargs)
            if result:
                return _ok(rowcount=rowcount)
            return _err(f"operation failed: {method_name} returned {result!r}")
        except Exception as e:
            return _err(str(e))

    def _to_dict(self, obj: Any) -> Any:
        """Convert dataclass to dict if needed."""
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)
        return obj

    # -------------------------------------------------------------------------
    # Object-kind dispatch
    # -------------------------------------------------------------------------

    _FORM_METHODS = {
        "list": "get_forms",
        "exists": "form_exists",
        "open": "open_form",
        "close": "close_form",
        "create": "create_form",
        "rename": "rename_form",
        "delete": "delete_form",
        "get_properties": "get_form_properties",
        "set_property": "set_form_property",
        "set_properties": "set_form_properties",
        "get_controls": "get_form_controls",
        "get_control_properties": "get_control_properties",
        "set_control_property": "set_control_property",
        "set_control_properties": "set_control_properties",
        "add_control": "add_control",
        "remove_control": "remove_control",
        "get_event_procedures": "get_control_event_procedures",
        "set_event_procedure": "set_control_event_procedure",
        "get_sections": "get_form_sections",
        "get_section_properties": "get_form_section_properties",
        "set_section_property": "set_form_section_property",
        "set_section_properties": "set_form_section_properties",
        "export": "export_form_to_text",
        "import": "import_form_from_text",
    }

    _REPORT_METHODS = {
        "list": "get_reports",
        "exists": "report_exists",
        "open": None,  # not supported
        "close": None,  # not supported
        "create": "create_report",
        "rename": "rename_report",
        "delete": "delete_report",
        "get_properties": "get_report_properties",
        "set_property": "set_report_property",
        "set_properties": "set_report_properties",
        "get_controls": "get_report_controls",
        "get_control_properties": "get_report_control_properties",
        "set_control_property": "set_report_control_property",
        "set_control_properties": "set_report_control_properties",
        "add_control": "add_report_control",
        "remove_control": "remove_report_control",
        "get_event_procedures": None,  # reports don't have control events
        "set_event_procedure": None,
        "get_sections": "get_report_sections",
        "get_section_properties": "get_report_section_properties",
        "set_section_property": "set_report_section_property",
        "set_section_properties": "set_report_section_properties",
        "export": "export_report_to_text",
        "import": "import_report_from_text",
    }

    def _dispatch(self, object_kind: str, operation: str) -> str | Err:
        """Dispatch object_kind + operation to adapter method name.

        Returns:
            Method name string, or Err if invalid kind / not supported.
        """
        if object_kind == "form":
            method = self._FORM_METHODS.get(operation)
            if method is None:
                return _err(f"operation '{operation}' not supported for forms")
            return method
        if object_kind == "report":
            method = self._REPORT_METHODS.get(operation)
            if method is None:
                return _err(f"operation '{operation}' not supported for reports")
            return method
        return _err(f"unknown object_kind: {object_kind}")

    # -------------------------------------------------------------------------
    # Object-kind consolidated use cases (forms + reports)
    # -------------------------------------------------------------------------

    def list_objects(self, object_kind: Literal["form", "report"]) -> Result:
        """List all forms or reports.

        Returns:
            Ok with list of object dicts, or Err.
        """
        method_name = self._dispatch(object_kind, "list")
        if isinstance(method_name, Err):
            return method_name
        if err := self._ensure_adapter():
            return err
        result = getattr(self._adapter, method_name)()
        items = [self._to_dict(item) for item in result]
        return _ok(data=items, rowcount=len(items))

    def object_exists(self, object_kind: Literal["form", "report"], name: str) -> Result:
        """Check if a form or report exists.

        Returns:
            Ok with boolean, or Err.
        """
        method_name = self._dispatch(object_kind, "exists")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter(method_name, name)

    def open_object(self, object_kind: Literal["form", "report"], name: str) -> Result:
        """Open a form or report.

        For reports, returns Err (open/close not supported).

        Returns:
            Ok on success, or Err.
        """
        if object_kind == "report":
            return _err("open/close not supported for reports — use design view operations")
        method_name = self._dispatch(object_kind, "open")
        if isinstance(method_name, Err):
            return method_name
        if method_name is None:
            return _err("open/close not supported for reports — use design view operations")
        return self._call_adapter_bool(method_name, name)

    def close_object(self, object_kind: Literal["form", "report"], name: str) -> Result:
        """Close a form or report.

        For reports, returns Err (open/close not supported).

        Returns:
            Ok on success, or Err.
        """
        if object_kind == "report":
            return _err("open/close not supported for reports — use design view operations")
        method_name = self._dispatch(object_kind, "close")
        if isinstance(method_name, Err):
            return method_name
        if method_name is None:
            return _err("open/close not supported for reports — use design view operations")
        return self._call_adapter_bool(method_name, name)

    def create_object(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        record_source: str = "",
        properties: dict | None = None,
        confirm: bool = False,
    ) -> Result:
        """Create a form or report.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"create_{object_kind}", confirm):
            return err
        method_name = self._dispatch(object_kind, "create")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name, record_source, properties)

    def rename_object(
        self,
        object_kind: Literal["form", "report"],
        old_name: str,
        new_name: str,
        confirm: bool = False,
    ) -> Result:
        """Rename a form or report.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"rename_{object_kind}", confirm):
            return err
        method_name = self._dispatch(object_kind, "rename")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, old_name, new_name)

    def delete_object(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        confirm: bool = False,
    ) -> Result:
        """Delete a form or report.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"delete_{object_kind}", confirm):
            return err
        method_name = self._dispatch(object_kind, "delete")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name)

    def get_properties(self, object_kind: Literal["form", "report"], name: str) -> Result:
        """Get properties of a form or report.

        Returns:
            Ok with properties dict, or Err.
        """
        method_name = self._dispatch(object_kind, "get_properties")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter(method_name, name)

    def set_property(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        property_name: str,
        value: Any,
        confirm: bool = False,
    ) -> Result:
        """Set a property on a form or report.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"set_{object_kind}_property", confirm):
            return err
        method_name = self._dispatch(object_kind, "set_property")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name, property_name, value)

    def set_properties(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        properties: dict,
        confirm: bool = False,
    ) -> Result:
        """Set multiple properties on a form or report.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"set_{object_kind}_properties", confirm):
            return err
        method_name = self._dispatch(object_kind, "set_properties")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name, properties)

    def get_controls(self, object_kind: Literal["form", "report"], name: str) -> Result:
        """Get controls on a form or report.

        Returns:
            Ok with list of control dicts, or Err.
        """
        method_name = self._dispatch(object_kind, "get_controls")
        if isinstance(method_name, Err):
            return method_name
        if err := self._ensure_adapter():
            return err
        result = getattr(self._adapter, method_name)(name)
        items = [self._to_dict(item) if hasattr(item, "__dataclass_fields__") else item for item in result]
        return _ok(data=items, rowcount=len(items))

    def get_control_properties(
        self, object_kind: Literal["form", "report"], name: str, control_name: str
    ) -> Result:
        """Get properties of a control on a form or report.

        Returns:
            Ok with properties dict, or Err.
        """
        method_name = self._dispatch(object_kind, "get_control_properties")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter(method_name, name, control_name)

    def set_control_property(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        control_name: str,
        property_name: str,
        value: Any,
        confirm: bool = False,
    ) -> Result:
        """Set a property on a control.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"set_{object_kind}_control_property", confirm):
            return err
        method_name = self._dispatch(object_kind, "set_control_property")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name, control_name, property_name, value)

    def set_control_properties(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        control_name: str,
        properties: dict,
        confirm: bool = False,
    ) -> Result:
        """Set multiple properties on a control.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"set_{object_kind}_control_properties", confirm):
            return err
        method_name = self._dispatch(object_kind, "set_control_properties")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name, control_name, properties)

    def add_control(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        control_type: str,
        control_name: str,
        section: int = 0,
        properties: dict | None = None,
        confirm: bool = False,
    ) -> Result:
        """Add a control to a form or report.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"add_{object_kind}_control", confirm):
            return err
        method_name = self._dispatch(object_kind, "add_control")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name, control_type, control_name, section, properties)

    def remove_control(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        control_name: str,
        confirm: bool = False,
    ) -> Result:
        """Remove a control from a form or report.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"remove_{object_kind}_control", confirm):
            return err
        method_name = self._dispatch(object_kind, "remove_control")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name, control_name)

    def get_control_event_procedures(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        control_name: str = "",
    ) -> Result:
        """Get event procedures for a control on a form (reports not supported).

        Returns:
            Ok with list of event procedure dicts, or Err.
        """
        if object_kind == "report":
            return _err("control event procedures not supported for reports")
        method_name = self._dispatch(object_kind, "get_event_procedures")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter(method_name, name, control_name)

    def set_control_event_procedure(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        control_name: str,
        event_name: str,
        code: str,
        confirm: bool = False,
    ) -> Result:
        """Set an event procedure on a control.

        Returns:
            Ok on success, or Err.
        """
        if object_kind == "report":
            return _err("control event procedures not supported for reports")
        if err := self._dangerous(f"set_{object_kind}_control_event", confirm):
            return err
        method_name = self._dispatch(object_kind, "set_event_procedure")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name, control_name, event_name, code)

    def get_sections(self, object_kind: Literal["form", "report"], name: str) -> Result:
        """Get sections of a form or report.

        Returns:
            Ok with list of section IDs, or Err.
        """
        method_name = self._dispatch(object_kind, "get_sections")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter(method_name, name)

    def get_section_properties(
        self, object_kind: Literal["form", "report"], name: str, section_id: int
    ) -> Result:
        """Get properties of a section.

        Returns:
            Ok with properties dict, or Err.
        """
        method_name = self._dispatch(object_kind, "get_section_properties")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter(method_name, name, section_id)

    def set_section_property(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        section_id: int,
        property_name: str,
        value: Any,
        confirm: bool = False,
    ) -> Result:
        """Set a property on a section.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"set_{object_kind}_section_property", confirm):
            return err
        method_name = self._dispatch(object_kind, "set_section_property")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name, section_id, property_name, value)

    def set_section_properties(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        section_id: int,
        properties: dict,
        confirm: bool = False,
    ) -> Result:
        """Set multiple properties on a section.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"set_{object_kind}_section_properties", confirm):
            return err
        method_name = self._dispatch(object_kind, "set_section_properties")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name, section_id, properties)

    def export_to_text(self, object_kind: Literal["form", "report"], name: str) -> Result:
        """Export a form or report to text.

        Returns:
            Ok with {"object_kind": ..., "name": ..., "text": ...}, or Err.
        """
        method_name = self._dispatch(object_kind, "export")
        if isinstance(method_name, Err):
            return method_name
        if err := self._ensure_adapter():
            return err
        text = getattr(self._adapter, method_name)(name)
        return _ok(data={"object_kind": object_kind, "name": name, "text": text})

    def import_from_text(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        text: str,
        confirm: bool = False,
    ) -> Result:
        """Import a form or report from text.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous(f"import_{object_kind}_from_text", confirm):
            return err
        method_name = self._dispatch(object_kind, "import")
        if isinstance(method_name, Err):
            return method_name
        return self._call_adapter_bool(method_name, name, text)

    # -------------------------------------------------------------------------
    # Macro use cases
    # -------------------------------------------------------------------------

    def get_macros(self) -> Result:
        """Get list of macros.

        Returns:
            Ok with list of macro dicts, or Err.
        """
        if err := self._ensure_adapter():
            return err
        assert self._adapter is not None
        result = self._adapter.get_macros()
        items = [self._to_dict(item) for item in result]
        return _ok(data=items, rowcount=len(items))

    def macro_exists(self, name: str) -> Result:
        """Check if a macro exists.

        Returns:
            Ok with boolean, or Err.
        """
        return self._call_adapter("macro_exists", name)

    def create_macro(self, name: str, confirm: bool = False) -> Result:
        """Create a macro.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous("create_macro", confirm):
            return err
        return self._call_adapter_bool("create_macro", name)

    def rename_macro(
        self, old_name: str, new_name: str, confirm: bool = False
    ) -> Result:
        """Rename a macro.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous("rename_macro", confirm):
            return err
        return self._call_adapter_bool("rename_macro", old_name, new_name)

    def delete_macro(self, name: str, confirm: bool = False) -> Result:
        """Delete a macro.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous("delete_macro", confirm):
            return err
        return self._call_adapter_bool("delete_macro", name)

    def run_macro(self, name: str, confirm: bool = False) -> Result:
        """Run a macro.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous("run_macro", confirm, needs_write=True):
            return err
        return self._call_adapter_bool("run_macro", name)

    def get_macro_properties(self, name: str) -> Result:
        """Get properties of a macro.

        Returns:
            Ok with properties dict, or Err.
        """
        return self._call_adapter("get_macro_properties", name)

    # -------------------------------------------------------------------------
    # VBA use cases
    # -------------------------------------------------------------------------

    def get_vba_project_name(self) -> Result:
        """Get the VBA project name.

        Returns:
            Ok with project name string, or Err.
        """
        return self._call_adapter("get_vba_project_name")

    def get_modules(self) -> Result:
        """Get list of VBA modules.

        Returns:
            Ok with list of module dicts, or Err.
        """
        if err := self._ensure_adapter():
            return err
        assert self._adapter is not None
        result = self._adapter.get_modules()
        items = [self._to_dict(item) for item in result]
        return _ok(data=items, rowcount=len(items))

    def get_vba_code(self, module_name: str) -> Result:
        """Get VBA code from a module.

        Returns:
            Ok with code string, or Err.
        """
        return self._call_adapter("get_vba_code", module_name)

    def set_vba_code(
        self, module_name: str, code: str, confirm: bool = False
    ) -> Result:
        """Set VBA code in a module.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous("set_vba_code", confirm):
            return err
        return self._call_adapter_bool("set_vba_code", module_name, code)

    def add_vba_procedure(
        self,
        module_name: str,
        procedure_name: str,
        code: str,
        confirm: bool = False,
    ) -> Result:
        """Add a VBA procedure to a module.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous("add_vba_procedure", confirm):
            return err
        return self._call_adapter_bool("add_vba_procedure", module_name, procedure_name, code)

    def delete_module(self, module_name: str, confirm: bool = False) -> Result:
        """Delete a VBA module.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous("delete_module", confirm):
            return err
        return self._call_adapter_bool("delete_module", module_name)

    def create_module(
        self, module_name: str, module_type: int = 1, confirm: bool = False
    ) -> Result:
        """Create a VBA module.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous("create_module", confirm):
            return err
        return self._call_adapter_bool("create_module", module_name, module_type)

    def rename_module(
        self, old_name: str, new_name: str, confirm: bool = False
    ) -> Result:
        """Rename a VBA module.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous("rename_module", confirm):
            return err
        return self._call_adapter_bool("rename_module", old_name, new_name)

    def module_exists(self, module_name: str) -> Result:
        """Check if a module exists.

        Returns:
            Ok with boolean, or Err.
        """
        return self._call_adapter("module_exists", module_name)

    def save_database(self, confirm: bool = False) -> Result:
        """Save the database.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous("save_database", confirm):
            return err
        return self._call_adapter_bool("save_database")

    def compile_vba(self) -> Result:
        """Compile the VBA project.

        Returns:
            Ok with {"compileErrors": [...]} on success,
            Err with error message on failure.
        """
        if err := self._ensure_adapter():
            return err
        assert self._adapter is not None
        # compile_vba needs confirm because it can trigger modal dialogs
        # but doesn't need write gate (compile is read-only-safe)
        if err := self._confirm_gate("compile_vba", True):
            return err
        try:
            result = self._adapter.compile_vba()
            if isinstance(result, dict):
                if not result.get("success", False):
                    return _err(result.get("error", "compile failed"))
                return _ok(data={"compileErrors": result.get("compileErrors", [])})
            return _err(f"compile_vba returned unexpected type: {type(result)}")
        except Exception as e:
            return _err(str(e))

    def vba_list_procedures(self, module_name: str) -> Result:
        """List procedures in a VBA module.

        Returns:
            Ok with list of procedure names, or Err.
        """
        return self._call_adapter("vba_list_procedures", module_name)

    def vba_get_procedure(self, module_name: str, procedure_name: str) -> Result:
        """Get a VBA procedure.

        Returns:
            Ok with procedure dict, or Err.
        """
        return self._call_adapter("vba_get_procedure", module_name, procedure_name)

    def vba_replace_procedure(
        self,
        module_name: str,
        procedure_name: str,
        code: str,
        confirm: bool = False,
    ) -> Result:
        """Replace a VBA procedure.

        Returns:
            Ok on success, or Err.
        """
        if err := self._dangerous("vba_replace_procedure", confirm):
            return err
        return self._call_adapter_bool("vba_replace_procedure", module_name, procedure_name, code)
