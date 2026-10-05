"""Hidden Access.Application adapter — plan 006: full UI/VBA surface.

Ports from oracle adapters:
  - ui_operations.py  (forms, reports, macros, controls, text export/import)
  - vba_operations.py (VBA project, modules, compilation, procedures)
  - trusted_locations.py (registry-based capture/restore)

Behavior invariants:
  - Every COM call goes through self._dispatcher.call(_do)
  - Visible=False set at connect time; never re-set to True
  - Teardown uses existing dispatcher (adapter.disconnect() → dispatcher.shutdown())
  - set_vba_code / compile_vba / vba_replace_procedure wrapped in _trusted_locations_wrap
  - compile_vba starts the PID-scoped dialog-killer daemon and stops it before returning
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from ...errors import AccessBackendError, AccessObjectNotFoundError
from ..dispatcher import ComDispatcher
from ..protocols import AccessDispatcherProtocol
from ..versioning import VersioningBackend
from . import control_types, dialog_killer, forms, macros, object_kinds, reports, trusted_locations, vba

logger = logging.getLogger(__name__)

# Compile command IDs by Access version (most likely first)
_COMPILE_CMD_IDS = [301, 206, 317, 232]

# Module-level cache for preserve_trusted_locations
_TRUSTED_LOCATIONS_PRESERVE = False

# XML template for creating empty macros (kept here for backward compatibility)
_MACRO_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-16" standalone="no"?>\n'
    "<Macros>\n"
    '  <Macro Name="TemplateMacro">\n'
    "    <Statements/>\n"
    "  </Macro>\n"
    "</Macros>\n"
)

_T = TypeVar("_T")


def _props_to_dict(props: Any) -> dict[str, str]:
    """Scrape a COM Properties collection into a plain dict.

    Per-property COM failures are logged at debug and skipped — one broken
    property must not poison the scrape, but it is no longer invisible.
    """
    result: dict[str, str] = {}
    for prop in props:
        try:
            result[prop.Name] = str(prop.Value)
        except Exception:
            logger.debug("property %s raised during value extraction, skipping", prop.Name)
    return result



class AccessAdapter:
    """Access.Application adapter with hidden-mode connect and dispatcher teardown.

    Adds UI/VBA operations on top of the plan-002 minimal adapter.
    """

    _dispatcher: AccessDispatcherProtocol

    def __init__(self, db_path: str) -> None:
        """Initialize an Access adapter for the specified database path."""
        self._db_path = db_path
        self._dispatcher = ComDispatcher()
        self._versioning = VersioningBackend(self._dispatcher)
        self._connected = False

    # ------------------------------------------------------------------ #
    # Connect / Disconnect (plan 002 — preserved verbatim)
    # ------------------------------------------------------------------ #

    def connect(self, db_path: str | None = None, password: str = "") -> None:
        """Connect to Access.Application in hidden mode and open the database."""
        target = db_path if db_path is not None else self._db_path
        self._dispatcher.start()
        self._dispatcher.set_db_path(target)

        def _do() -> None:
            import win32com.client  # type: ignore[import-not-found]

            app = win32com.client.Dispatch("Access.Application")
            app.Visible = False
            try:
                app.OpenCurrentDatabase(target, False, password)
            except Exception:
                try:
                    app.Quit()
                except Exception:
                    pass
                raise
            self._dispatcher._access_app = app
            self._dispatcher.capture_access_pid()  # plan 030: record PID for post-shutdown kill
            self._dispatcher._current_db = app.CurrentDb()
            self._dispatcher._ado_conn = app.CurrentProject.Connection
            self._dispatcher._db_path = target
            ComDispatcher._dismiss_access_dialogs()

        try:
            self._dispatcher.call(_do)
        except Exception:
            self._dispatcher.shutdown()
            raise
        self._connected = True

    def disconnect(self) -> None:
        """Disconnect via dispatcher teardown."""
        if not self._connected:
            return
        self._dispatcher.shutdown()
        self._connected = False

    @property
    def connected(self) -> bool:
        """Return the lifecycle protocol connection state."""
        return self._connected

    def is_connected(self) -> bool:
        """Return whether the Access application is connected."""
        return self.connected

    # ------------------------------------------------------------------ #
    # Access object model accessors
    # ------------------------------------------------------------------ #

    def get_current_db_name(self) -> str:
        """Return the current DAO Database name, read on the dispatcher's STA thread.

        COM objects vended by Access.Application are apartment-bound to the
        dispatcher's STA thread. Reading their attributes from any other thread
        raises AttributeError (RPC_E_WRONG_THREAD surfaced by pywin32 dynamic
        dispatch). Typed accessors like this one dispatch the read onto the
        STA thread; never expose raw COM handles across threads.
        """
        def _do() -> str:
            return str(self._dispatcher.current_db.Name)

        return self._dispatcher.call(_do)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _control_type_name(ctrl_type: int) -> str:
        """Map Access AcControlType integer to readable name."""
        return control_types.control_type_name(ctrl_type)

    @staticmethod
    def _control_type_id(name: str) -> int:
        """Map readable control type name to Access AcControlType integer."""
        return control_types.control_type_id(name)

    @staticmethod
    def _section_name(section_id: int) -> str:
        """Map Access AcSection integer to readable name."""
        return control_types.section_name(section_id)

    @staticmethod
    def _section_id(name: str) -> int:
        """Map readable section name to Access AcSection integer."""
        return control_types.section_id(name)

    # Registry for form/report clone-family deduplication.
    # Maps kind key to COM collection names and Access object-type constants.
    _OBJECT_KINDS = object_kinds.OBJECT_KINDS

    def _open_object_in_design(self, kind: str, object_name: str) -> None:
        """Open a form/report in design view via the kind's DoCmd verb."""
        object_kinds._open_object_in_design(self._dispatcher, kind, object_name)

    def _live_object(self, kind: str, object_name: str) -> Any:
        """Resolve the live form/report in design view (None when unavailable).

        Form side prefers Screen.ActiveForm and falls back to Forms(name);
        report side reads Reports(name) directly. A raising Forms(name)
        fallback propagates to the caller's handler, matching legacy behavior.
        """
        return object_kinds._live_object(self._dispatcher, kind, object_name)

    def _with_design_view(
        self,
        kind: str,
        object_name: str,
        *,
        save_flag: int,
        work: Callable[[Any], _T],
        on_error: Callable[[BaseException], _T],
        default: _T,
    ) -> _T:
        """Open kind/name in design view on the STA thread, run work(live_obj), always close once.

        Owns the guard / open / close lifecycle so the 14 call sites cannot drift.
        Close failures are swallowed (best-effort cleanup); work failures are routed
        through on_error (getters return a default, setters log-and-False).
        """
        return object_kinds._with_design_view(
            self._dispatcher,
            kind,
            object_name,
            save_flag=save_flag,
            work=work,
            on_error=on_error,
            default=default,
            open_object_in_design=object_kinds._open_object_in_design,
            live_object=object_kinds._live_object,
        )

    def _get_vb_project(self) -> Any:
        """Get the first VBA project via VBProjects enumeration.

        COM VBProjects collection uses 1-based indexing.
        """
        try:
            return vba.get_vb_project(self._dispatcher.access_app)
        except Exception as exc:
            logger.debug("vb project unavailable: %s", exc)
            return None

    def _save_object_to_text(self, object_type: int, object_name: str) -> str:
        """Export an Access object to text using SaveAsText.

        Returns the text content or empty string on failure.
        object_type: acForm=2, acReport=4, acModule=5, acMacro=8
        """
        return vba.save_object_to_text(
            self._dispatcher.access_app, object_type, object_name
        )

    def _load_object_from_text(self, object_type: int, object_name: str, text_data: str) -> bool:
        """Import an Access object from text data using LoadFromText.

        object_type: acForm=2, acReport=4, acModule=5, acMacro=8

        Encoding: VBA modules (acModule=5) expect the system ANSI codepage
        without BOM. Forms, reports, queries, and macros expect UTF-16-LE
        with BOM.
        """
        return vba.load_object_from_text(
            self._dispatcher.access_app, object_type, object_name, text_data
        )

    def _trusted_locations_wrap(self, func, *args, **kwargs):
        """Execute func(*args, **kwargs) with Trusted Locations preservation if enabled.

        Captures Trusted Locations before the call and restores after,
        controlled by ACCESS_MCP_PRESERVE_TRUSTED_LOCATIONS env var (default False).
        """
        return trusted_locations.trusted_locations_wrap(func, *args, **kwargs)

    # ------------------------------------------------------------------ #
    # Form operations
    # ------------------------------------------------------------------ #

    def _list_objects(self, kind: str):
        """List all objects of a given kind (form/report)."""
        return trusted_locations.list_objects(
            self._dispatcher, self._OBJECT_KINDS, kind
        )

    def get_forms(self):
        """Get all forms in the database."""
        return self._list_objects("form")

    def get_reports(self):
        """Get all reports in the database."""
        return self._list_objects("report")

    def _object_exists(self, kind: str, name: str) -> bool:
        """Check if an object of the given kind (form/report) exists."""
        return object_kinds._object_exists(self._dispatcher, kind, name)

    def form_exists(self, form_name: str) -> bool:
        """Check if a form exists."""
        return self._object_exists("form", form_name)

    def report_exists(self, report_name: str) -> bool:
        """Check if a report exists."""
        return self._object_exists("report", report_name)

    def open_form(self, form_name: str) -> bool:
        """Open a form in Access (appears on the server desktop)."""
        return forms.open_form(self._dispatcher, form_name)

    def close_form(self, form_name: str) -> bool:
        """Close an open form without saving."""
        return forms.close_form(self._dispatcher, form_name)

    def _create_object(self, kind: str, object_name: str, record_source: str = "", template_name: str = "", properties: dict[str, Any] | None = None) -> bool:
        """Create a form or report via DoCmd.CreateForm/CreateReport."""
        return object_kinds._create_object(
            self._dispatcher,
            kind,
            object_name,
            record_source,
            template_name,
            properties,
            log_object_failure=self._log_object_failure,
        )

    def _classify_object_failure(self, kind: str, operation: str, object_name: str, exc: BaseException) -> AccessObjectNotFoundError | AccessBackendError:
        """Classify a COM failure: object_name/0x8004 in message → not-found, else backend."""
        return object_kinds._classify_object_failure(kind, operation, object_name, exc)

    def _log_object_failure(self, kind: str, operation: str, object_name: str, exc: BaseException) -> None:
        """Classify a COM failure and log it as a warning (for bool-collapsing operations)."""
        object_kinds._log_object_failure(kind, operation, object_name, exc)

    def _rename_object(self, kind: str, old_name: str, new_name: str) -> bool:
        """Rename a form or report via DoCmd.Rename."""
        return object_kinds._rename_object(self._dispatcher, kind, old_name, new_name)

    def _delete_object(self, kind: str, object_name: str) -> bool:
        """Delete a form or report from the database."""
        return object_kinds._delete_object(self._dispatcher, kind, object_name)

    def create_form(self, form_name: str, record_source: str = "", template_name: str = "", properties: dict[str, Any] | None = None) -> bool:
        """Create a new form via DoCmd.CreateForm, optionally setting RecordSource and properties."""
        return forms.create_form(self._dispatcher, form_name, record_source, template_name, properties, log_object_failure=self._log_object_failure)

    def rename_form(self, old_name: str, new_name: str) -> bool:
        """Rename a form via DoCmd.Rename with acForm=2."""
        return forms.rename_form(self._dispatcher, old_name, new_name)

    def delete_form(self, form_name: str) -> bool:
        """Delete a form from the database."""
        return forms.delete_form(self._dispatcher, form_name)

    def _get_object_properties(self, kind: str, object_name: str) -> dict[str, str]:
        """Get all properties of a form/report by opening it in design view."""
        return self._with_design_view(
            kind, object_name,
            save_flag=2,
            work=lambda obj: _props_to_dict(obj.Properties) if obj is not None else {},
            on_error=lambda exc: {},
            default={},
        )

    def get_form_properties(self, form_name: str) -> dict[str, str]:
        """Get all properties of a form by opening it in design view."""
        return forms.get_form_properties(self._dispatcher, form_name)

    def get_report_properties(self, report_name: str) -> dict[str, str]:
        """Get all properties of a report by opening it in design view."""
        return reports.get_report_properties(self._dispatcher, report_name)

    def _set_object_property(self, kind: str, object_name: str, property_name: str, value: str) -> bool:
        """Set a single property of a form/report by opening it in design view."""
        def work(obj):
            if obj is None:
                return False
            obj.Properties(property_name).Value = value
            return True

        return self._with_design_view(
            kind, object_name,
            save_flag=1,
            work=work,
            on_error=lambda exc: self._log_object_failure(kind, "set_property", object_name, exc) or False,
            default=False,
        )

    def set_form_property(self, form_name: str, property_name: str, value: str) -> bool:
        """Set a single property of a form by opening it in design view."""
        return forms.set_form_property(self._dispatcher, form_name, property_name, value, log_object_failure=self._log_object_failure)

    def _set_object_properties(self, kind: str, object_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a form/report. Returns dict of {property_name: success}."""
        if kind == "form":
            return forms.set_form_properties(self._dispatcher, object_name, properties)
        return reports.set_report_properties(self._dispatcher, object_name, properties)

    def set_form_properties(self, form_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a form. Returns dict of {property_name: success}."""
        return forms.set_form_properties(self._dispatcher, form_name, properties)

    def _get_object_controls(self, kind: str, object_name: str):
        """Get all controls in a form/report by opening it in design view."""
        if kind == "form":
            return forms.get_form_controls(self._dispatcher, object_name)
        return reports.get_report_controls(self._dispatcher, object_name)

    def get_form_controls(self, form_name: str):
        """Get all controls in a form by opening it in design view."""
        return forms.get_form_controls(self._dispatcher, form_name)

    def get_report_controls(self, report_name: str):
        """Get all controls in a report by opening it in design view."""
        return reports.get_report_controls(self._dispatcher, report_name)

    def _get_object_control_properties(self, kind: str, object_name: str, control_name: str) -> dict[str, str]:
        """Get all properties of a specific control by opening the form/report in design view."""
        if kind == "form":
            return forms.get_control_properties(self._dispatcher, object_name, control_name)
        return reports.get_report_control_properties(self._dispatcher, object_name, control_name)

    def get_control_properties(self, form_name: str, control_name: str) -> dict[str, str]:
        """Get all properties of a specific control by opening the form in design view."""
        return forms.get_control_properties(self._dispatcher, form_name, control_name)

    def _set_object_control_property(self, kind: str, object_name: str, control_name: str, property_name: str, value: str) -> bool:
        """Set a property of a control by opening the form/report in design view."""
        if kind == "form":
            return forms.set_control_property(self._dispatcher, object_name, control_name, property_name, value)
        return reports.set_report_control_property(self._dispatcher, object_name, control_name, property_name, value)

    def set_control_property(self, form_name: str, control_name: str, property_name: str, value: str) -> bool:
        """Set a property of a control by opening the form in design view."""
        return forms.set_control_property(self._dispatcher, form_name, control_name, property_name, value)

    def _set_object_control_properties(self, kind: str, object_name: str, control_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a form/report control. Returns dict of {property_name: success}."""
        if kind == "form":
            return forms.set_control_properties(self._dispatcher, object_name, control_name, properties)
        return reports.set_report_control_properties(self._dispatcher, object_name, control_name, properties)

    def set_control_properties(self, form_name: str, control_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties at once. Returns dict of {property_name: success}."""
        return forms.set_control_properties(self._dispatcher, form_name, control_name, properties)

    def _add_object_control(self, kind: str, object_name: str, control_type: str, control_name: str, section: int = 0, properties: dict[str, Any] | None = None) -> bool:
        """Add a control to a form/report by opening it in design view."""
        if kind == "form":
            return forms.add_control(self._dispatcher, object_name, control_type, control_name, section, properties)
        return reports.add_report_control(self._dispatcher, object_name, control_type, control_name, section, properties)

    def add_control(self, form_name: str, control_type: str, control_name: str, section: int = 0, properties: dict[str, Any] | None = None) -> bool:
        """Add a control to a form by opening it in design view."""
        return forms.add_control(self._dispatcher, form_name, control_type, control_name, section, properties)

    def _remove_object_control(self, kind: str, object_name: str, control_name: str) -> bool:
        """Remove a control from a form/report by opening it in design view."""
        if kind == "form":
            return forms.remove_control(self._dispatcher, object_name, control_name)
        return reports.remove_report_control(self._dispatcher, object_name, control_name)

    def remove_control(self, form_name: str, control_name: str) -> bool:
        """Remove a control from a form by opening it in design view."""
        return forms.remove_control(self._dispatcher, form_name, control_name)

    def get_control_event_procedures(self, form_name: str, control_name: str = "") -> list[dict]:
        """List event procedures for a specific control in a form."""
        if not self._dispatcher._started:
            return []

        def _do() -> list[dict]:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return []
            try:
                form_module_name = f"Form_{form_name}"
                target_module = None
                for comp in vb_project.VBComponents:
                    if comp.Name == form_module_name:
                        target_module = comp.CodeModule
                        break
                if target_module is None:
                    return []

                total_lines = target_module.CountOfLines
                if total_lines == 0:
                    return []

                all_procedures: list[dict] = []
                seen_procs: set[str] = set()

                for line in range(1, total_lines + 1):
                    try:
                        proc_name = target_module.ProcOfLine(line, 0)
                        if proc_name and proc_name not in seen_procs:
                            seen_procs.add(proc_name)
                            start_line = target_module.ProcStartLine(proc_name, 0)
                            line_count = target_module.ProcCountLines(proc_name, 0)
                            code = target_module.Lines(start_line, line_count)
                            all_procedures.append({
                                "procedure_name": proc_name,
                                "start_line": start_line,
                                "line_count": line_count,
                                "code": code,
                            })
                    except Exception as exc:
                        logger.debug("vba_list_procedures ProcOfLine line %s failed: %s", line, exc)
                        pass

                if control_name:
                    prefix = f"{control_name}_"
                    filtered = []
                    for proc in all_procedures:
                        if proc["procedure_name"].startswith(prefix):
                            event_name = proc["procedure_name"][len(prefix):]
                            filtered.append({
                                "procedure_name": proc["procedure_name"],
                                "event_name": event_name,
                                "code": proc["code"],
                                "start_line": proc["start_line"],
                            })
                    return filtered
                else:
                    result = []
                    for proc in all_procedures:
                        proc_name = proc["procedure_name"]
                        if "_" in proc_name:
                            parts = proc_name.split("_", 1)
                            result.append({
                                "procedure_name": proc_name,
                                "event_name": parts[1] if len(parts) > 1 else "",
                                "code": proc["code"],
                                "start_line": proc["start_line"],
                            })
                        else:
                            result.append({
                                "procedure_name": proc_name,
                                "event_name": "",
                                "code": proc["code"],
                                "start_line": proc["start_line"],
                            })
                    return result
            except Exception:
                return []

        return self._dispatcher.call(_do)

    def set_control_event_procedure(self, form_name: str, control_name: str, event_name: str, code: str) -> bool:
        """Set a control's event procedure by opening the form in design view."""
        def work(form):
            if form is None:
                return False

            target_ctrl = None
            for i in range(form.Controls.Count):
                try:
                    ctrl = form.Controls(i)
                    if ctrl.Name == control_name:
                        target_ctrl = ctrl
                        break
                except Exception as exc:
                    logger.debug("set_control_event_procedure %s[%s] control lookup failed: %s", form_name, control_name, exc)
                    pass

            if target_ctrl is None:
                return False

            event_prop_name = f"On{event_name}"
            target_ctrl.Properties(event_prop_name).Value = "[Event Procedure]"

            module_name = f"Form_{form_name}"
            proc_name = f"{control_name}_{event_name}"
            return self.vba_replace_procedure(module_name, proc_name, code)

        return self._with_design_view(
            "form", form_name,
            save_flag=1,
            work=work,
            on_error=lambda exc: self._log_object_failure("form", "set_control_event_procedure", form_name, exc) or False,
            default=False,
        )

    def _get_object_sections(self, kind: str, object_name: str) -> list[dict]:
        """Get all sections of a form/report by opening it in design view."""
        if kind == "form":
            return forms.get_form_sections(self._dispatcher, object_name)
        return reports.get_report_sections(self._dispatcher, object_name)

    def get_form_sections(self, form_name: str) -> list[dict]:
        """Get all sections of a form by opening it in design view."""
        return forms.get_form_sections(self._dispatcher, form_name)

    def get_report_sections(self, report_name: str) -> list[dict]:
        """Get all sections of a report by opening it in design view."""
        return reports.get_report_sections(self._dispatcher, report_name)

    def _get_object_section_properties(self, kind: str, object_name: str, section_id: int) -> dict[str, str]:
        """Get all properties of a specific section by opening the form/report in design view."""
        if kind == "form":
            return forms.get_form_section_properties(self._dispatcher, object_name, section_id)
        return reports.get_report_section_properties(self._dispatcher, object_name, section_id)

    def get_form_section_properties(self, form_name: str, section_id: int) -> dict[str, str]:
        """Get all properties of a specific section by opening the form in design view."""
        return forms.get_form_section_properties(self._dispatcher, form_name, section_id)

    def get_report_section_properties(self, report_name: str, section_id: int) -> dict[str, str]:
        """Get all properties of a specific section by opening the report in design view."""
        return reports.get_report_section_properties(self._dispatcher, report_name, section_id)

    def _set_object_section_property(self, kind: str, object_name: str, section_id: int, property_name: str, value: str) -> bool:
        """Set a single property of a form/report section by opening it in design view."""
        if kind == "form":
            return forms.set_form_section_property(self._dispatcher, object_name, section_id, property_name, value)
        return reports.set_report_section_property(self._dispatcher, object_name, section_id, property_name, value)

    def set_form_section_property(self, form_name: str, section_id: int, property_name: str, value: str) -> bool:
        """Set a single property of a form section by opening the form in design view."""
        return forms.set_form_section_property(self._dispatcher, form_name, section_id, property_name, value)

    def _set_object_section_properties(self, kind: str, object_name: str, section_id: int, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a form/report section. Returns dict of {property_name: success}."""
        if kind == "form":
            return forms.set_form_section_properties(self._dispatcher, object_name, section_id, properties)
        return reports.set_report_section_properties(self._dispatcher, object_name, section_id, properties)

    def set_form_section_properties(self, form_name: str, section_id: int, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a form section. Returns dict of {property_name: success}."""
        return forms.set_form_section_properties(self._dispatcher, form_name, section_id, properties)

    def export_table_to_xlsx(self, table_or_query: str, target_path: str) -> int:
        """Export a table or query to an xlsx file via DoCmd.TransferSpreadsheet.

        Uses Access's built-in export engine (acSpreadsheetTypeExcel12Xml = 14).
        This path does NOT require the ACE Excel IISAM provider — it uses the
        Access.Application COM object directly.

        Args:
            table_or_query: Name of the table or query to export.
            target_path: Absolute path to the output .xlsx file.

        Returns:
            Estimated row count (0 if RecordsAffected is not exposed post-call).

        """
        if not self._dispatcher._started:
            return 0

        def _do() -> int:
            try:
                self._dispatcher.access_app.DoCmd.TransferSpreadsheet(
                    1,  # acExport
                    14,  # acSpreadsheetTypeExcel12Xml
                    table_or_query,
                    target_path,
                    True,  # hasfieldnames
                )
                # Try to read records affected; if not exposed, return 0
                try:
                    return int(self._dispatcher.access_app.RecordsAffected)
                except Exception:
                    return 0
            except Exception as exc:
                logger.debug("export_table_to_xlsx %s failed: %s", table_or_query, exc)
                return 0

        return self._dispatcher.call(_do)

    def export_form_to_text(self, form_name: str) -> str:
        """Export a form to text representation via SaveAsText."""
        return forms.export_form_to_text(self._dispatcher, form_name, save_object_to_text=self._save_object_to_text)

    def import_form_from_text(self, form_name: str, form_data: str) -> bool:
        """Import a form from text data via LoadFromText."""
        return forms.import_form_from_text(self._dispatcher, form_name, form_data, load_object_from_text=self._load_object_from_text)

    # ------------------------------------------------------------------ #
    # Report operations
    # ------------------------------------------------------------------ #

    def create_report(self, report_name: str, record_source: str = "", template_name: str = "", properties: dict[str, Any] | None = None) -> bool:
        """Create a new report via DoCmd.CreateReport, optionally setting RecordSource and properties."""
        return reports.create_report(self._dispatcher, report_name, record_source, template_name, properties, log_object_failure=self._log_object_failure)

    def rename_report(self, old_name: str, new_name: str) -> bool:
        """Rename a report via DoCmd.Rename with acReport=4."""
        return reports.rename_report(self._dispatcher, old_name, new_name)

    def delete_report(self, report_name: str) -> bool:
        """Delete a report from the database."""
        return reports.delete_report(self._dispatcher, report_name)

    def set_report_property(self, report_name: str, property_name: str, value: str) -> bool:
        """Set a single property of a report by opening it in design view."""
        return reports.set_report_property(self._dispatcher, report_name, property_name, value, log_object_failure=self._log_object_failure)

    def set_report_properties(self, report_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a report. Returns dict of {property_name: success}."""
        return reports.set_report_properties(self._dispatcher, report_name, properties)

    def get_report_control_properties(self, report_name: str, control_name: str) -> dict[str, str]:
        """Get all properties of a specific control by opening the report in design view."""
        return reports.get_report_control_properties(self._dispatcher, report_name, control_name)

    def set_report_control_property(self, report_name: str, control_name: str, property_name: str, value: str) -> bool:
        """Set a property of a control by opening the report in design view."""
        return reports.set_report_control_property(self._dispatcher, report_name, control_name, property_name, value)

    def set_report_control_properties(self, report_name: str, control_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a report control. Returns dict of {property_name: success}."""
        return reports.set_report_control_properties(self._dispatcher, report_name, control_name, properties)

    def add_report_control(self, report_name: str, control_type: str, control_name: str, section: int = 0, properties: dict[str, Any] | None = None) -> bool:
        """Add a control to a report by opening it in design view."""
        return reports.add_report_control(self._dispatcher, report_name, control_type, control_name, section, properties)

    def remove_report_control(self, report_name: str, control_name: str) -> bool:
        """Remove a control from a report by opening it in design view."""
        return reports.remove_report_control(self._dispatcher, report_name, control_name)

    def set_report_section_property(self, report_name: str, section_id: int, property_name: str, value: str) -> bool:
        """Set a single property of a report section by opening the report in design view."""
        return reports.set_report_section_property(self._dispatcher, report_name, section_id, property_name, value)

    def set_report_section_properties(self, report_name: str, section_id: int, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a report section. Returns dict of {property_name: success}."""
        return reports.set_report_section_properties(self._dispatcher, report_name, section_id, properties)

    def export_report_to_text(self, report_name: str) -> str:
        """Export a report to text representation via SaveAsText."""
        return reports.export_report_to_text(self._dispatcher, report_name, save_object_to_text=self._save_object_to_text)

    def import_report_from_text(self, report_name: str, report_data: str) -> bool:
        """Import a report from text data via LoadFromText."""
        return reports.import_report_from_text(self._dispatcher, report_name, report_data, load_object_from_text=self._load_object_from_text)

    def export_macro_to_text(self, macro_name: str) -> str:
        """Export a macro using the shared versioning backend."""
        return self._versioning.export_macro_to_text(macro_name)

    def import_macro_from_text(self, macro_name: str, text_data: str) -> bool:
        """Import a macro using the shared versioning backend."""
        return self._versioning.import_macro_from_text(macro_name, text_data)

    def export_query_to_text(self, query_name: str) -> str:
        """Export a query using the shared versioning backend."""
        return self._versioning.export_query_to_text(query_name)

    def import_query_from_text(self, query_name: str, text_data: str) -> bool:
        """Import a query using the shared versioning backend."""
        return self._versioning.import_query_from_text(query_name, text_data)

    def export_module_to_text(self, module_name: str) -> str:
        """Export a VBA module using the shared versioning backend."""
        return self._versioning.export_module_to_text(module_name)

    def import_module_from_text(self, module_name: str, code: str) -> bool:
        """Import a VBA module using the shared versioning backend."""
        return self._versioning.import_module_from_text(module_name, code)

    def export_all_versioning(self, out_dir):
        """Export all versioned objects through the shared backend."""
        return self._versioning.export_all_versioning(out_dir)

    def import_all_versioning(self, input_dir):
        """Import all versioned objects through the shared backend."""
        return self._versioning.import_all_versioning(input_dir)

    def compare_versioning(self, export_dir):
        """Compare database objects with versioned files through the backend."""
        return self._versioning.compare_versioning(export_dir)

    def export_schema_ddl(self, out_dir):
        """Export schema DDL through the shared backend."""
        return self._versioning.export_schema_ddl(out_dir)

    # ------------------------------------------------------------------ #
    # Macro operations
    # ------------------------------------------------------------------ #

    def get_macros(self):
        """Get all macros in the database."""
        return macros.get_macros(self._dispatcher)

    def macro_exists(self, macro_name: str) -> bool:
        """Check if a macro exists."""
        return macros.macro_exists(self._dispatcher, macro_name)

    def create_macro(self, macro_name: str) -> bool:
        """Create an empty macro via LoadFromText using a minimal XML template."""
        return macros.create_macro(self._dispatcher, macro_name, load_object_from_text=self._load_object_from_text)

    def rename_macro(self, old_name: str, new_name: str) -> bool:
        """Rename a macro via DoCmd.Rename with acMacro=8."""
        return macros.rename_macro(self._dispatcher, old_name, new_name)

    def delete_macro(self, macro_name: str) -> bool:
        """Delete a macro via DoCmd.DeleteObject with acMacro=8."""
        return macros.delete_macro(self._dispatcher, macro_name)

    def run_macro(self, macro_name: str) -> bool:
        """Execute a macro via DoCmd.RunMacro."""
        return macros.run_macro(self._dispatcher, macro_name)

    def get_macro_properties(self, macro_name: str) -> dict[str, str]:
        """Get all properties of a macro by reading its AccessObject Properties collection."""
        return macros.get_macro_properties(self._dispatcher, macro_name)

    # ------------------------------------------------------------------ #
    # VBA operations
    # ------------------------------------------------------------------ #

    def get_vba_project_name(self) -> str:
        """Get the VBA project name from COM."""
        if not self._dispatcher._started:
            return ""
        try:
            return self._dispatcher.call(lambda: vba.get_vba_project_name(self._dispatcher.access_app))
        except Exception as exc:
            logger.debug("get_vba_project_name dispatcher failed: %s", exc)
            return ""

    def get_modules(self):
        """Get all VBA modules in the database."""
        if not self._dispatcher._started:
            return []
        try:
            return self._dispatcher.call(lambda: vba.get_modules(self._dispatcher.access_app))
        except Exception as exc:
            logger.debug("get_modules dispatcher failed: %s", exc)
            return []

    def get_vba_code(self, module_name: str) -> str:
        """Get VBA code from a module."""
        if not self._dispatcher._started:
            return ""
        return self._dispatcher.call(lambda: vba.get_vba_code(self._dispatcher.access_app, module_name))

    def set_vba_code(self, module_name: str, code: str) -> bool:
        """Set VBA code in a module.

        For non-existent modules: uses LoadFromText.
        For existing modules: uses DeleteLines + AddFromString.
        """
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            return vba.set_vba_code(
                self._dispatcher.access_app,
                module_name,
                code,
                load_object_from_text_fn=self._load_object_from_text,
            )

        return self._dispatcher.call(self._trusted_locations_wrap, _do)

    def add_vba_procedure(self, module_name: str, procedure_name: str, code: str) -> bool:
        """Add a VBA procedure to a module.

        For non-existent modules: uses LoadFromText.
        For existing modules: appends via AddFromString.
        """
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            return vba.add_vba_procedure(
                self._dispatcher.access_app,
                module_name,
                procedure_name,
                code,
                load_object_from_text_fn=self._load_object_from_text,
            )

        return self._dispatcher.call(self._trusted_locations_wrap, _do)

    def delete_module(self, module_name: str) -> bool:
        """Delete a VBA module from the database."""
        if not self._dispatcher._started:
            return False
        return self._dispatcher.call(lambda: vba.delete_module(self._dispatcher.access_app, module_name))

    def create_module(self, module_name: str, module_type: int = 1) -> bool:
        """Create a new VBA module in the database."""
        if not self._dispatcher._started:
            return False
        return self._dispatcher.call(lambda: vba.create_module(self._dispatcher.access_app, module_name, module_type))

    def rename_module(self, old_name: str, new_name: str) -> bool:
        """Rename an existing VBA module."""
        if not self._dispatcher._started:
            return False
        return self._dispatcher.call(lambda: vba.rename_module(self._dispatcher.access_app, old_name, new_name))

    def module_exists(self, module_name: str) -> bool:
        """Check if a VBA module exists in the database."""
        if not self._dispatcher._started:
            return False
        return self._dispatcher.call(lambda: vba.module_exists(self._dispatcher.access_app, module_name))

    def save_database(self) -> dict:
        """Save all VBA modules and database changes."""
        if not self._dispatcher._started:
            return {"success": False, "error": "Not connected"}
        return self._dispatcher.call(lambda: vba.save_database(self._dispatcher.access_app))

    def compile_vba(self) -> dict:
        """Compile VBA code.

        Tries known DoCmd.RunCommand constants for VBA compilation.
        Starts the PID-scoped dialog-killer daemon thread and stops it before returning.
        """
        if not self._dispatcher._started:
            return {"success": False, "error": "Not connected"}

        vb_project = self._get_vb_project()

        def _start_dialog_killer(stop_event: threading.Event) -> None:
            dialog_killer.start_dialog_killer(stop_event, self._dispatcher._access_pid)

        def _do() -> dict:
            return vba.compile_vba(
                self._dispatcher.access_app,
                _COMPILE_CMD_IDS,
                _start_dialog_killer,
                vb_project,
            )

        try:
            return self._dispatcher.call(
                lambda: trusted_locations.trusted_locations_wrap(_do)
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    def vba_list_procedures(self, module_name: str) -> list[dict]:
        """List all procedures in a module with name, type, line info."""
        if not self._dispatcher._started:
            return []
        try:
            vb_project = self._get_vb_project()
            return self._dispatcher.call(lambda: vba.list_procedures(self._dispatcher.access_app, vb_project, module_name))
        except Exception as exc:
            logger.debug("list procedures %s failed: %s", module_name, exc)
            return []

    def vba_get_procedure(self, module_name: str, procedure_name: str) -> dict:
        """Get full source code of a specific procedure."""
        if not self._dispatcher._started:
            return {}
        return self._dispatcher.call(lambda: vba.vba_get_procedure(self._dispatcher.access_app, module_name, procedure_name))

    def vba_replace_procedure(self, module_name: str, procedure_name: str, new_code: str) -> bool:
        """Replace a procedure's body with new code (preserves signature)."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            return vba.vba_replace_procedure(self._dispatcher.access_app, module_name, procedure_name, new_code)

        return self._dispatcher.call(self._trusted_locations_wrap, _do)


# -------------------------------------------------------------------------- #
# Module-level helpers
# -------------------------------------------------------------------------- #

def connect_hidden(db_path: str) -> AccessAdapter:
    """Create and connect an AccessAdapter in hidden mode.

    Returns the connected adapter. Caller must call disconnect() when done.
    """
    adapter = AccessAdapter(db_path)
    adapter.connect()
    return adapter
