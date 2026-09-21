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
  - compile_vba starts _dialog_killer daemon and stops it before returning
"""

from __future__ import annotations

import locale
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

from ...errors import AccessBackendError, AccessObjectNotFoundError
from ..dispatcher import ComDispatcher
from ..protocols import AccessDispatcherProtocol
from ..trusted_locations import capture_trusted_locations, restore_trusted_locations
from . import control_types

logger = logging.getLogger(__name__)

# Compile command IDs by Access version (most likely first)
_COMPILE_CMD_IDS = [301, 206, 317, 232]

# Module-level cache for preserve_trusted_locations
_TRUSTED_LOCATIONS_PRESERVE = False

# XML template for creating empty macros
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
            app.OpenCurrentDatabase(target, False, password)
            self._dispatcher._access_app = app
            self._dispatcher.capture_access_pid()  # plan 030: record PID for post-shutdown kill
            self._dispatcher._current_db = app.CurrentDb()
            self._dispatcher._ado_conn = app.CurrentProject.Connection
            self._dispatcher._db_path = target
            ComDispatcher._dismiss_access_dialogs()

        self._dispatcher.call(_do)
        self._connected = True

    def disconnect(self) -> None:
        """Disconnect via dispatcher teardown."""
        if not self._connected:
            return
        self._dispatcher.shutdown()
        self._connected = False

    def is_connected(self) -> bool:
        """Return whether the Access application is connected."""
        return self._connected

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
    _OBJECT_KINDS = {
        "form": {"collection": "AllForms", "ac_type": 2, "open_verb": "OpenForm", "live_collection": "Forms", "close_ac": 2},
        "report": {"collection": "AllReports", "ac_type": 4, "open_verb": "OpenReport", "live_collection": "Reports", "close_ac": 4},
    }

    def _open_object_in_design(self, kind: str, object_name: str) -> None:
        """Open a form/report in design view via the kind's DoCmd verb."""
        open_verb = self._OBJECT_KINDS[kind]["open_verb"]
        getattr(self._dispatcher.access_app.DoCmd, open_verb)(object_name, 1)  # acDesign=1

    def _live_object(self, kind: str, object_name: str) -> Any:
        """Resolve the live form/report in design view (None when unavailable).

        Form side prefers Screen.ActiveForm and falls back to Forms(name);
        report side reads Reports(name) directly. A raising Forms(name)
        fallback propagates to the caller's handler, matching legacy behavior.
        """
        app = self._dispatcher.access_app
        if kind == "form":
            try:
                return app.Screen.ActiveForm
            except Exception:
                return app.Forms(object_name)
        try:
            collection = self._OBJECT_KINDS[kind]["live_collection"]
            return getattr(app, collection)(object_name)
        except Exception:
            return None

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
        if not self._dispatcher._started:
            return default

        def _do() -> _T:
            try:
                self._open_object_in_design(kind, object_name)
                obj = self._live_object(kind, object_name)
                return work(obj)
            except Exception as exc:
                return on_error(exc)
            finally:
                try:
                    close_ac = self._OBJECT_KINDS[kind]["close_ac"]
                    self._dispatcher.access_app.DoCmd.Close(close_ac, object_name, save_flag)
                except Exception as exc:
                    logger.debug("design-view close %s/%s failed: %s", kind, object_name, exc)
                    pass

        return self._dispatcher.call(_do)

    def _get_vb_project(self) -> Any:
        """Get the first VBA project via VBProjects enumeration.

        COM VBProjects collection uses 1-based indexing.
        """
        try:
            vbe = self._dispatcher.access_app.VBE
            for i in range(1, vbe.VBProjects.Count + 1):
                return vbe.VBProjects(i)
        except Exception as exc:
            logger.debug("vb project unavailable: %s", exc)
            pass
        return None

    def _save_object_to_text(self, object_type: int, object_name: str) -> str:
        """Export an Access object to text using SaveAsText.

        Returns the text content or empty string on failure.
        object_type: acForm=2, acReport=4, acModule=5, acMacro=8
        """
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".txt", prefix="mcp_exp_")
            os.close(fd)
            self._dispatcher.access_app.SaveAsText(object_type, object_name, temp_path)
            with open(temp_path, "rb") as f:
                raw = f.read()
            # SaveAsText outputs UTF-16-LE with BOM; decode accordingly
            content = raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
            return content
        except Exception:
            return ""
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except Exception as exc:
                    logger.debug("temp file cleanup %s failed: %s", temp_path, exc)
                    pass

    def _load_object_from_text(self, object_type: int, object_name: str, text_data: str) -> bool:
        """Import an Access object from text data using LoadFromText.

        object_type: acForm=2, acReport=4, acModule=5, acMacro=8

        Encoding: VBA modules (acModule=5) expect the system ANSI codepage
        without BOM. Forms, reports, queries, and macros expect UTF-16-LE
        with BOM.
        """
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".txt", prefix="mcp_imp_")
            os.close(fd)
            if object_type == 5:  # acModule
                enc = locale.getpreferredencoding(False) or "cp1252"
                with open(temp_path, "w", encoding=enc, errors="replace") as f:
                    f.write(text_data)
            else:
                with open(temp_path, "wb") as f:
                    f.write(b"\xff\xfe")
                    f.write(text_data.encode("utf-16-le"))
            self._dispatcher.access_app.LoadFromText(object_type, object_name, temp_path)
            return True
        except Exception:
            return False
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except Exception as exc:
                    logger.debug("temp file cleanup %s failed: %s", temp_path, exc)
                    pass

    @staticmethod
    def _dialog_killer(stop_event: threading.Event) -> None:
        """Background thread that clicks accept button on Access 'Save As' dialogs.

        Args:
            stop_event: threading.Event — set when the dialog killer should stop.

        """
        try:
            import win32con
            import win32gui

            while not stop_event.is_set():
                def find_and_click(hwnd: int, _: object) -> bool:
                    if stop_event.is_set():
                        return False
                    cls = win32gui.GetClassName(hwnd)
                    title = win32gui.GetWindowText(hwnd)
                    if cls != "#32770" or "save" not in title.lower():
                        return True
                    btn = win32gui.FindWindowEx(hwnd, None, "Button", None)
                    while btn:
                        btn_title = win32gui.GetWindowText(btn).strip()
                        if btn_title in ("OK", "Save", "&OK", "&Save"):
                            win32gui.PostMessage(hwnd, win32con.WM_COMMAND,
                                                 win32gui.GetWindowLong(btn, win32con.GWL_ID), 0)
                            return True
                        btn = win32gui.FindWindowEx(hwnd, btn, "Button", None)
                    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, 0x0D, 0)
                    return True

                win32gui.EnumWindows(find_and_click, None)
                if not stop_event.is_set():
                    time.sleep(0.1)

        except ImportError:
            pass
        except Exception as exc:
            logger.debug("dialog killer thread error: %s", exc)
            pass

    def _trusted_locations_wrap(self, func, *args, **kwargs):
        """Execute func(*args, **kwargs) with Trusted Locations preservation if enabled.

        Captures Trusted Locations before the call and restores after,
        controlled by ACCESS_MCP_PRESERVE_TRUSTED_LOCATIONS env var (default False).
        """
        if not _TRUSTED_LOCATIONS_PRESERVE:
            return func(*args, **kwargs)

        captured = capture_trusted_locations()
        try:
            return func(*args, **kwargs)
        finally:
            if captured:
                restore_trusted_locations(captured)

    # ------------------------------------------------------------------ #
    # Form operations
    # ------------------------------------------------------------------ #

    def _list_objects(self, kind: str):
        """List all objects of a given kind (form/report)."""
        if not self._dispatcher._started:
            return []

        from ms_access_mcp.models import FormInfo, ReportInfo

        def _do():
            objects = []
            skipped_props = 0
            skipped_objs = 0
            collection_name = self._OBJECT_KINDS[kind]["collection"]
            try:
                all_objects = getattr(self._dispatcher.access_app.CurrentProject, collection_name)
                for i in range(all_objects.Count):
                    obj = all_objects(i)
                    try:
                        record_source = ""
                        try:
                            record_source = str(obj.Properties("RecordSource")) if obj.Properties.Exists("RecordSource") else ""
                        except Exception:
                            skipped_props += 1
                        model = FormInfo if kind == "form" else ReportInfo
                        objects.append(model(name=obj.Name, record_source=record_source))
                    except Exception:
                        skipped_objs += 1
            except Exception:
                logger.debug("list_objects %s collection access failed", kind)
                pass
            if skipped_props or skipped_objs:
                logger.debug("list_objects %s: %d objects, %d property failures skipped", kind, skipped_objs, skipped_props)
            return objects

        try:
            return self._dispatcher.call(_do)
        except Exception as exc:
            logger.debug("list_objects %s dispatcher failed: %s", kind, exc)
            return []

    def get_forms(self):
        """Get all forms in the database."""
        return self._list_objects("form")

    def get_reports(self):
        """Get all reports in the database."""
        return self._list_objects("report")

    def _object_exists(self, kind: str, name: str) -> bool:
        """Check if an object of the given kind (form/report) exists."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                collection_name = self._OBJECT_KINDS[kind]["collection"]
                all_objects = getattr(self._dispatcher.access_app.CurrentProject, collection_name)
                for i in range(all_objects.Count):
                    if all_objects(i).Name == name:
                        return True
            except Exception as exc:
                logger.debug("object_exists %s/%s collection access failed: %s", kind, name, exc)
                pass
            return False

        try:
            return self._dispatcher.call(_do)
        except Exception as exc:
            logger.debug("object_exists %s/%s dispatcher failed: %s", kind, name, exc)
            return False

    def form_exists(self, form_name: str) -> bool:
        """Check if a form exists."""
        return self._object_exists("form", form_name)

    def report_exists(self, report_name: str) -> bool:
        """Check if a report exists."""
        return self._object_exists("report", report_name)

    def open_form(self, form_name: str) -> bool:
        """Open a form in Access (appears on the server desktop)."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                self._dispatcher.access_app.DoCmd.OpenForm(form_name)
                return True
            except Exception as exc:
                self._log_object_failure("form", "open", form_name, exc)
                return False

        try:
            return self._dispatcher.call(_do)
        except Exception:
            return False

    def close_form(self, form_name: str) -> bool:
        """Close an open form without saving."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                self._dispatcher.access_app.DoCmd.Close(2, form_name, 2)  # acForm=2, acSaveNo=2
                return True
            except Exception as exc:
                self._log_object_failure("form", "close", form_name, exc)
                return False

        try:
            return self._dispatcher.call(_do)
        except Exception:
            return False

    def _create_object(self, kind: str, object_name: str, record_source: str = "", template_name: str = "", properties: dict[str, Any] | None = None) -> bool:
        """Create a form or report via DoCmd.CreateForm/CreateReport."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                ac_type = self._OBJECT_KINDS[kind]["ac_type"]
                create_method = "CreateForm" if kind == "form" else "CreateReport"
                obj = getattr(self._dispatcher.access_app.DoCmd, create_method)()
                if record_source:
                    try:
                        obj.RecordSource = record_source
                    except Exception as exc:
                        logger.debug("create_%s: RecordSource %r rejected: %s", kind, record_source, exc)
                if properties:
                    for prop_name, value in properties.items():
                        try:
                            obj.Properties(prop_name).Value = value
                        except Exception as exc:
                            logger.warning("create_%s: property %s rejected: %s", kind, prop_name, exc)
                self._dispatcher.access_app.DoCmd.Close(ac_type, obj.Name, 1)  # acSaveYes=1
                return True
            except Exception as exc:
                self._log_object_failure(kind, "create", object_name, exc)
                return False

        return self._dispatcher.call(_do)

    def _classify_object_failure(self, kind: str, operation: str, object_name: str, exc: BaseException) -> AccessObjectNotFoundError | AccessBackendError:
        """Classify a COM failure: object_name/0x8004 in message → not-found, else backend."""
        if object_name in str(exc) or "0x8004" in str(exc):
            return AccessObjectNotFoundError(kind, object_name)
        return AccessBackendError(operation, object_name, str(exc))

    def _log_object_failure(self, kind: str, operation: str, object_name: str, exc: BaseException) -> None:
        """Classify a COM failure and log it as a warning (for bool-collapsing operations)."""
        error = self._classify_object_failure(kind, operation, object_name, exc)
        logger.warning(
            "%s %s '%s' failed: %s: %s",
            operation,
            kind,
            object_name,
            type(error).__name__,
            error,
        )

    def _rename_object(self, kind: str, old_name: str, new_name: str) -> bool:
        """Rename a form or report via DoCmd.Rename."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                ac_type = self._OBJECT_KINDS[kind]["ac_type"]
                self._dispatcher.access_app.DoCmd.Rename(new_name, ac_type, old_name)
                return True
            except Exception as exc:
                raise self._classify_object_failure(kind, "rename", old_name, exc) from exc

        try:
            return self._dispatcher.call(_do)
        except (AccessObjectNotFoundError, AccessBackendError) as exc:
            logger.warning(
                "rename %s '%s' failed: %s: %s",
                kind,
                old_name,
                type(exc).__name__,
                exc,
            )
            return False

    def _delete_object(self, kind: str, object_name: str) -> bool:
        """Delete a form or report from the database."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                ac_type = self._OBJECT_KINDS[kind]["ac_type"]
                self._dispatcher.access_app.DoCmd.DeleteObject(ac_type, object_name)
                return True
            except Exception as exc:
                raise self._classify_object_failure(kind, "delete", object_name, exc) from exc

        try:
            return self._dispatcher.call(_do)
        except (AccessObjectNotFoundError, AccessBackendError) as exc:
            logger.warning(
                "delete %s '%s' failed: %s: %s",
                kind,
                object_name,
                type(exc).__name__,
                exc,
            )
            return False

    def create_form(self, form_name: str, record_source: str = "", template_name: str = "", properties: dict[str, Any] | None = None) -> bool:
        """Create a new form via DoCmd.CreateForm, optionally setting RecordSource and properties."""
        return self._create_object("form", form_name, record_source, template_name, properties)

    def rename_form(self, old_name: str, new_name: str) -> bool:
        """Rename a form via DoCmd.Rename with acForm=2."""
        return self._rename_object("form", old_name, new_name)

    def delete_form(self, form_name: str) -> bool:
        """Delete a form from the database."""
        return self._delete_object("form", form_name)

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
        return self._get_object_properties("form", form_name)

    def get_report_properties(self, report_name: str) -> dict[str, str]:
        """Get all properties of a report by opening it in design view."""
        return self._get_object_properties("report", report_name)

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
        return self._set_object_property("form", form_name, property_name, value)

    def _set_object_properties(self, kind: str, object_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a form/report. Returns dict of {property_name: success}."""
        def work_for_prop(prop_name: str, value: Any) -> bool:
            def inner(obj):
                if obj is None:
                    return False
                try:
                    obj.Properties(prop_name).Value = value
                    return True
                except Exception:
                    return False
            return self._with_design_view(
                kind, object_name,
                save_flag=1,
                work=inner,
                on_error=lambda exc: self._log_object_failure(kind, "set_properties", object_name, exc) or False,
                default=False,
            )

        results: dict[str, bool] = {}
        for prop_name, value in properties.items():
            results[prop_name] = work_for_prop(prop_name, value)
        return results

    def set_form_properties(self, form_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a form. Returns dict of {property_name: success}."""
        return self._set_object_properties("form", form_name, properties)

    def _get_object_controls(self, kind: str, object_name: str):
        """Get all controls in a form/report by opening it in design view."""
        def work(obj):
            if obj is None:
                return []
            controls = []
            skipped_ctrls = 0
            skipped_props = 0
            for i in range(obj.Controls.Count):
                try:
                    ctrl = obj.Controls(i)
                    ctrl_name = ctrl.Name
                    ctrl_type_code = ctrl.ControlType
                    ctrl_type = self._control_type_name(ctrl_type_code)

                    props: dict[str, str] = {}
                    for prop_name in ("Visible", "Enabled", "Left", "Top",
                                      "Width", "Height", "Caption",
                                      "ControlSource", "TabIndex"):
                        try:
                            val = ctrl.Properties(prop_name).Value
                            if val is not None:
                                props[prop_name] = str(val)
                        except Exception:
                            skipped_props += 1

                    from ms_access_mcp.models import ControlInfo
                    controls.append(ControlInfo(
                        name=ctrl_name, type=ctrl_type, properties=props,
                    ))
                except Exception:
                    skipped_ctrls += 1
            if skipped_ctrls or skipped_props:
                logger.debug("get_object_controls %s/%s: %d controls, %d properties skipped", kind, object_name, skipped_ctrls, skipped_props)
            return controls

        return self._with_design_view(
            kind, object_name,
            save_flag=2,
            work=work,
            on_error=lambda exc: [],
            default=[],
        )

    def get_form_controls(self, form_name: str):
        """Get all controls in a form by opening it in design view."""
        return self._get_object_controls("form", form_name)

    def get_report_controls(self, report_name: str):
        """Get all controls in a report by opening it in design view."""
        return self._get_object_controls("report", report_name)

    def _get_object_control_properties(self, kind: str, object_name: str, control_name: str) -> dict[str, str]:
        """Get all properties of a specific control by opening the form/report in design view."""
        def work(obj):
            if obj is None:
                return {}
            for i in range(obj.Controls.Count):
                try:
                    ctrl = obj.Controls(i)
                    if ctrl.Name == control_name:
                        return _props_to_dict(ctrl.Properties)
                except Exception as exc:
                    logger.debug("get_object_control_properties %s/%s control lookup failed: %s", kind, object_name, exc)
                    pass
            return {}

        return self._with_design_view(
            kind, object_name,
            save_flag=2,
            work=work,
            on_error=lambda exc: {},
            default={},
        )

    def get_control_properties(self, form_name: str, control_name: str) -> dict[str, str]:
        """Get all properties of a specific control by opening the form in design view."""
        return self._get_object_control_properties("form", form_name, control_name)

    def _set_object_control_property(self, kind: str, object_name: str, control_name: str, property_name: str, value: str) -> bool:
        """Set a property of a control by opening the form/report in design view."""
        def work(obj):
            if obj is None:
                return False
            for i in range(obj.Controls.Count):
                try:
                    ctrl = obj.Controls(i)
                    if ctrl.Name == control_name:
                        ctrl.Properties(property_name).Value = value
                        return True
                except Exception as exc:
                    logger.debug("set_control_property %s[%s] %s failed: %s", object_name, control_name, property_name, exc)
                    pass
            return False

        return self._with_design_view(
            kind, object_name,
            save_flag=1,
            work=work,
            on_error=lambda exc: self._log_object_failure(kind, "set_control_property", object_name, exc) or False,
            default=False,
        )

    def set_control_property(self, form_name: str, control_name: str, property_name: str, value: str) -> bool:
        """Set a property of a control by opening the form in design view."""
        return self._set_object_control_property("form", form_name, control_name, property_name, value)

    def _set_object_control_properties(self, kind: str, object_name: str, control_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a form/report control. Returns dict of {property_name: success}."""
        def work_for_prop(prop_name: str, value: Any) -> bool:
            def inner(obj):
                if obj is None:
                    return False
                skipped = 0
                for i in range(obj.Controls.Count):
                    try:
                        ctrl = obj.Controls(i)
                        if ctrl.Name == control_name:
                            ctrl.Properties(prop_name).Value = value
                            return True
                    except Exception:
                        skipped += 1
                if skipped:
                    logger.debug("set_control_properties %s/%s[%s]: %d controls skipped", kind, object_name, control_name, skipped)
                return False

            return self._with_design_view(
                kind, object_name,
                save_flag=1,
                work=inner,
                on_error=lambda exc: self._log_object_failure(kind, "set_control_properties", object_name, exc) or False,
                default=False,
            )

        results: dict[str, bool] = {}
        for prop_name, value in properties.items():
            results[prop_name] = work_for_prop(prop_name, value)
        return results

    def set_control_properties(self, form_name: str, control_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties at once. Returns dict of {property_name: success}."""
        return self._set_object_control_properties("form", form_name, control_name, properties)

    def _add_object_control(self, kind: str, object_name: str, control_type: str, control_name: str, section: int = 0, properties: dict[str, Any] | None = None) -> bool:
        """Add a control to a form/report by opening it in design view."""
        def work(obj):
            type_int = self._control_type_id(control_type)
            if type_int == 0:
                return False

            ctrl = self._dispatcher.access_app.DoCmd.CreateControl(object_name, type_int, section)
            ctrl.Name = control_name

            if properties:
                for prop_name, value in properties.items():
                    try:
                        ctrl.Properties(prop_name).Value = value
                    except Exception as exc:
                        logger.debug("add_object_control set property %s/%s[%s].%s failed: %s", kind, object_name, control_name, prop_name, exc)
                        pass
            return True

        return self._with_design_view(
            kind, object_name,
            save_flag=1,
            work=work,
            on_error=lambda exc: self._log_object_failure(kind, "add_object_control", object_name, exc) or False,
            default=False,
        )

    def add_control(self, form_name: str, control_type: str, control_name: str, section: int = 0, properties: dict[str, Any] | None = None) -> bool:
        """Add a control to a form by opening it in design view."""
        return self._add_object_control("form", form_name, control_type, control_name, section, properties)

    def _remove_object_control(self, kind: str, object_name: str, control_name: str) -> bool:
        """Remove a control from a form/report by opening it in design view."""
        def work(obj):
            if obj is None:
                return False
            for i in range(obj.Controls.Count):
                try:
                    ctrl = obj.Controls(i)
                    if ctrl.Name == control_name:
                        ctrl.SetFocus()
                        self._dispatcher.access_app.DoCmd.RunCommand(365)  # acCmdDelete
                        return True
                except Exception as exc:
                    logger.debug("remove_object_control %s/%s[%s] failed: %s", kind, object_name, control_name, exc)
                    pass
            return False

        return self._with_design_view(
            kind, object_name,
            save_flag=2,
            work=work,
            on_error=lambda exc: self._log_object_failure(kind, "remove_object_control", object_name, exc) or False,
            default=False,
        )

    def remove_control(self, form_name: str, control_name: str) -> bool:
        """Remove a control from a form by opening it in design view."""
        return self._remove_object_control("form", form_name, control_name)

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
        def work(obj):
            if obj is None:
                return []
            sections: list[dict] = []
            for i in range(5):
                try:
                    section = obj.Section(i)
                    sections.append({
                        "index": i,
                        "name": str(section.Name),
                        "section_type": self._section_name(i),
                        "visible": bool(section.Visible),
                        "height": int(section.Height),
                    })
                except Exception as exc:
                    logger.debug("get_%s_sections %s section %s failed: %s", kind, object_name, i, exc)
                    pass
            return sections

        return self._with_design_view(
            kind, object_name,
            save_flag=2,
            work=work,
            on_error=lambda exc: [],
            default=[],
        )

    def get_form_sections(self, form_name: str) -> list[dict]:
        """Get all sections of a form by opening it in design view."""
        return self._get_object_sections("form", form_name)

    def get_report_sections(self, report_name: str) -> list[dict]:
        """Get all sections of a report by opening it in design view."""
        return self._get_object_sections("report", report_name)

    def _get_object_section_properties(self, kind: str, object_name: str, section_id: int) -> dict[str, str]:
        """Get all properties of a specific section by opening the form/report in design view."""
        def work(obj):
            if obj is not None:
                section = obj.Section(section_id)
                return _props_to_dict(section.Properties)
            return {}

        return self._with_design_view(
            kind, object_name,
            save_flag=2,
            work=work,
            on_error=lambda exc: {},
            default={},
        )

    def get_form_section_properties(self, form_name: str, section_id: int) -> dict[str, str]:
        """Get all properties of a specific section by opening the form in design view."""
        return self._get_object_section_properties("form", form_name, section_id)

    def get_report_section_properties(self, report_name: str, section_id: int) -> dict[str, str]:
        """Get all properties of a specific section by opening the report in design view."""
        return self._get_object_section_properties("report", report_name, section_id)

    def _set_object_section_property(self, kind: str, object_name: str, section_id: int, property_name: str, value: str) -> bool:
        """Set a single property of a form/report section by opening it in design view."""
        def work(obj):
            if obj is None:
                return False
            section = obj.Section(section_id)
            section.Properties(property_name).Value = value
            return True

        return self._with_design_view(
            kind, object_name,
            save_flag=1,
            work=work,
            on_error=lambda exc: self._log_object_failure(kind, "set_section_property", object_name, exc) or False,
            default=False,
        )

    def set_form_section_property(self, form_name: str, section_id: int, property_name: str, value: str) -> bool:
        """Set a single property of a form section by opening the form in design view."""
        return self._set_object_section_property("form", form_name, section_id, property_name, value)

    def _set_object_section_properties(self, kind: str, object_name: str, section_id: int, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a form/report section. Returns dict of {property_name: success}."""
        def work_for_prop(prop_name: str, value: Any) -> bool:
            def inner(obj):
                if obj is None:
                    return False
                try:
                    section = obj.Section(section_id)
                    section.Properties(prop_name).Value = value
                    return True
                except Exception as exc:
                    logger.debug("set_section_property %s/%s[%s].%s failed: %s", kind, object_name, section_id, prop_name, exc)
                    return False

            return self._with_design_view(
                kind, object_name,
                save_flag=1,
                work=inner,
                on_error=lambda exc: self._log_object_failure(kind, "set_section_properties", object_name, exc) or False,
                default=False,
            )

        results: dict[str, bool] = {}
        for prop_name, value in properties.items():
            results[prop_name] = work_for_prop(prop_name, value)
        return results

    def set_form_section_properties(self, form_name: str, section_id: int, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a form section. Returns dict of {property_name: success}."""
        return self._set_object_section_properties("form", form_name, section_id, properties)

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
        if not self._dispatcher._started:
            return ""

        def _do() -> str:
            return self._save_object_to_text(2, form_name)

        return self._dispatcher.call(_do)

    def import_form_from_text(self, form_name: str, form_data: str) -> bool:
        """Import a form from text data via LoadFromText."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            return self._load_object_from_text(2, form_name, form_data)

        return self._dispatcher.call(_do)

    # ------------------------------------------------------------------ #
    # Report operations
    # ------------------------------------------------------------------ #

    def create_report(self, report_name: str, record_source: str = "", template_name: str = "", properties: dict[str, Any] | None = None) -> bool:
        """Create a new report via DoCmd.CreateReport, optionally setting RecordSource and properties."""
        return self._create_object("report", report_name, record_source, template_name, properties)

    def rename_report(self, old_name: str, new_name: str) -> bool:
        """Rename a report via DoCmd.Rename with acReport=4."""
        return self._rename_object("report", old_name, new_name)

    def delete_report(self, report_name: str) -> bool:
        """Delete a report from the database."""
        return self._delete_object("report", report_name)

    def set_report_property(self, report_name: str, property_name: str, value: str) -> bool:
        """Set a single property of a report by opening it in design view."""
        return self._set_object_property("report", report_name, property_name, value)

    def set_report_properties(self, report_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a report. Returns dict of {property_name: success}."""
        return self._set_object_properties("report", report_name, properties)

    def get_report_control_properties(self, report_name: str, control_name: str) -> dict[str, str]:
        """Get all properties of a specific control by opening the report in design view."""
        return self._get_object_control_properties("report", report_name, control_name)

    def set_report_control_property(self, report_name: str, control_name: str, property_name: str, value: str) -> bool:
        """Set a property of a control by opening the report in design view."""
        return self._set_object_control_property("report", report_name, control_name, property_name, value)

    def set_report_control_properties(self, report_name: str, control_name: str, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a report control. Returns dict of {property_name: success}."""
        return self._set_object_control_properties("report", report_name, control_name, properties)

    def add_report_control(self, report_name: str, control_type: str, control_name: str, section: int = 0, properties: dict[str, Any] | None = None) -> bool:
        """Add a control to a report by opening it in design view."""
        return self._add_object_control("report", report_name, control_type, control_name, section, properties)

    def remove_report_control(self, report_name: str, control_name: str) -> bool:
        """Remove a control from a report by opening it in design view."""
        return self._remove_object_control("report", report_name, control_name)

    def set_report_section_property(self, report_name: str, section_id: int, property_name: str, value: str) -> bool:
        """Set a single property of a report section by opening the report in design view."""
        return self._set_object_section_property("report", report_name, section_id, property_name, value)

    def set_report_section_properties(self, report_name: str, section_id: int, properties: dict[str, Any]) -> dict[str, bool]:
        """Set multiple properties of a report section. Returns dict of {property_name: success}."""
        return self._set_object_section_properties("report", report_name, section_id, properties)

    def export_report_to_text(self, report_name: str) -> str:
        """Export a report to text representation via SaveAsText."""
        if not self._dispatcher._started:
            return ""

        def _do() -> str:
            return self._save_object_to_text(4, report_name)

        return self._dispatcher.call(_do)

    def import_report_from_text(self, report_name: str, report_data: str) -> bool:
        """Import a report from text data via LoadFromText."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            return self._load_object_from_text(4, report_name, report_data)

        return self._dispatcher.call(_do)

    # ------------------------------------------------------------------ #
    # Macro operations
    # ------------------------------------------------------------------ #

    def get_macros(self):
        """Get all macros in the database."""
        if not self._dispatcher._started:
            return []

        def _do():
            macros = []
            try:
                all_macros = self._dispatcher.access_app.CurrentProject.AllMacros
                for i in range(all_macros.Count):
                    macro_obj = all_macros(i)
                    try:
                        date_created = ""
                        date_modified = ""
                        try:
                            if macro_obj.Properties.Exists("DateCreated"):
                                date_created = str(macro_obj.Properties("DateCreated"))
                        except Exception as exc:
                            logger.debug("get_macros %s DateCreated failed: %s", macro_obj.Name, exc)
                            pass
                        try:
                            if macro_obj.Properties.Exists("DateModified"):
                                date_modified = str(macro_obj.Properties("DateModified"))
                        except Exception as exc:
                            logger.debug("get_macros %s DateModified failed: %s", macro_obj.Name, exc)
                            pass
                        from ms_access_mcp.models import MacroInfo
                        macros.append(MacroInfo(
                            name=macro_obj.Name,
                            type="Macro",
                            date_created=date_created,
                            date_modified=date_modified,
                        ))
                    except Exception as exc:
                        logger.debug("get_macros %s processing failed: %s", macro_obj.Name, exc)
                        pass
            except Exception as exc:
                logger.debug("get_macros AllMacros collection access failed: %s", exc)
                pass
            return macros

        try:
            return self._dispatcher.call(_do)
        except Exception:
            return []

    def macro_exists(self, macro_name: str) -> bool:
        """Check if a macro exists."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                all_macros = self._dispatcher.access_app.CurrentProject.AllMacros
                for i in range(all_macros.Count):
                    try:
                        if all_macros(i).Name == macro_name:
                            return True
                    except Exception as exc:
                        logger.debug("macro_exists %s AllMacros(%s) failed: %s", macro_name, i, exc)
                        pass
            except Exception as exc:
                logger.debug("macro_exists %s collection access failed: %s", macro_name, exc)
                pass
            return False

        return self._dispatcher.call(_do)

    def create_macro(self, macro_name: str) -> bool:
        """Create an empty macro via LoadFromText using a minimal XML template."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                xml = _MACRO_TEMPLATE.replace("TemplateMacro", macro_name)
                return self._load_object_from_text(8, macro_name, xml)
            except Exception:
                return False

        return self._dispatcher.call(_do)

    def rename_macro(self, old_name: str, new_name: str) -> bool:
        """Rename a macro via DoCmd.Rename with acMacro=8."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                self._dispatcher.access_app.DoCmd.Rename(new_name, 8, old_name)
                return True
            except Exception as exc:
                logger.debug("rename_macro %s->%s failed: %s", old_name, new_name, exc)
                return False

        return self._dispatcher.call(_do)

    def delete_macro(self, macro_name: str) -> bool:
        """Delete a macro via DoCmd.DeleteObject with acMacro=8."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                self._dispatcher.access_app.DoCmd.DeleteObject(8, macro_name)
                return True
            except Exception as exc:
                logger.debug("delete_macro %s failed: %s", macro_name, exc)
                return False

        return self._dispatcher.call(_do)

    def run_macro(self, macro_name: str) -> bool:
        """Execute a macro via DoCmd.RunMacro."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                self._dispatcher.access_app.DoCmd.RunMacro(macro_name)
                return True
            except Exception as exc:
                logger.debug("run_macro %s failed: %s", macro_name, exc)
                return False

        return self._dispatcher.call(_do)

    def get_macro_properties(self, macro_name: str) -> dict[str, str]:
        """Get all properties of a macro by reading its AccessObject Properties collection."""
        if not self._dispatcher._started:
            return {}

        def _do() -> dict[str, str]:
            try:
                all_macros = self._dispatcher.access_app.CurrentProject.AllMacros
                for i in range(all_macros.Count):
                    macro_obj = all_macros(i)
                    if macro_obj.Name == macro_name:
                        props: dict[str, str] = {}
                        skipped_props = 0
                        for prop in macro_obj.Properties:
                            try:
                                props[prop.Name] = str(prop.Value)
                            except Exception:
                                skipped_props += 1
                        if skipped_props:
                            logger.debug("get_macro_properties %s: %d property failures skipped", macro_name, skipped_props)
                        return props
            except Exception as exc:
                logger.debug("get_macro_properties %s failed: %s", macro_name, exc)
                pass
            return {}

        return self._dispatcher.call(_do)

    # ------------------------------------------------------------------ #
    # VBA operations
    # ------------------------------------------------------------------ #

    def get_vba_project_name(self) -> str:
        """Get the VBA project name from COM."""
        if not self._dispatcher._started:
            return ""

        def _do() -> str:
            vb_project = self._get_vb_project()
            return vb_project.Name if vb_project else ""

        try:
            return self._dispatcher.call(_do)
        except Exception as exc:
            logger.debug("get_vba_project_name dispatcher failed: %s", exc)
            return ""

    def get_modules(self):
        """Get all VBA modules in the database."""
        if not self._dispatcher._started:
            return []

        def _do():
            modules = []
            vb_project = self._get_vb_project()
            if vb_project is None:
                return []
            skipped = 0
            try:
                for comp in vb_project.VBComponents:
                    try:
                        code = ""
                        if comp.Type == 1:
                            code = comp.CodeModule.Lines(1, comp.CodeModule.CountOfLines)
                        from ms_access_mcp.models import ModuleInfo
                        modules.append(ModuleInfo(
                            name=comp.Name,
                            type="Standard Module" if comp.Type == 1 else "Class Module",
                            code=code,
                        ))
                    except Exception:
                        skipped += 1
            except Exception as exc:
                logger.debug("get_modules VBComponents collection failed: %s", exc)
                pass
            if skipped:
                logger.debug("get_modules: %d components skipped", skipped)
            return modules

        try:
            return self._dispatcher.call(_do)
        except Exception as exc:
            logger.debug("get_modules dispatcher failed: %s", exc)
            return []

    def get_vba_code(self, module_name: str) -> str:
        """Get VBA code from a module."""
        if not self._dispatcher._started:
            return ""

        def _do() -> str:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return ""
            try:
                for comp in vb_project.VBComponents:
                    if comp.Name == module_name:
                        lines = comp.CodeModule.CountOfLines
                        if lines > 0:
                            return comp.CodeModule.Lines(1, lines)
                        return ""
            except Exception as exc:
                logger.debug("get_vba_code %s failed: %s", module_name, exc)
                pass
            return ""

        return self._dispatcher.call(_do)

    def set_vba_code(self, module_name: str, code: str) -> bool:
        """Set VBA code in a module.

        For non-existent modules: uses LoadFromText.
        For existing modules: uses DeleteLines + AddFromString.
        """
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return False
            try:
                target_module = None
                for mod in vb_project.VBComponents:
                    if mod.Name == module_name:
                        target_module = mod
                        break

                if target_module is None:
                    # New module: use LoadFromText
                    text_data = f"Attribute VB_Name = \"{module_name}\"\r\n{code}"
                    return self._load_object_from_text(5, module_name, text_data)
                else:
                    # Existing module: clear and repopulate in-memory
                    try:
                        target_module.CodeModule.DeleteLines(
                            1, target_module.CodeModule.CountOfLines
                        )
                        target_module.CodeModule.AddFromString(code)
                        return True
                    except Exception as exc:
                        logger.debug("set_vba_code %s repopulate failed: %s", module_name, exc)
                        return False
            except Exception as exc:
                logger.debug("set_vba_code %s failed: %s", module_name, exc)
                return False

        return self._dispatcher.call(self._trusted_locations_wrap, _do)

    def add_vba_procedure(self, module_name: str, procedure_name: str, code: str) -> bool:
        """Add a VBA procedure to a module.

        For non-existent modules: uses LoadFromText.
        For existing modules: appends via AddFromString.
        """
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return False
            try:
                target_module = None
                for comp in vb_project.VBComponents:
                    if comp.Name == module_name:
                        target_module = comp
                        break
                if target_module is None:
                    # New module: use LoadFromText
                    text_data = f"Attribute VB_Name = \"{module_name}\"\r\n{code}"
                    return self._load_object_from_text(5, module_name, text_data)
                else:
                    target_module.CodeModule.AddFromString(code)
                    return True
            except Exception as exc:
                logger.debug("add_vba_procedure %s.%s failed: %s", module_name, procedure_name, exc)
                return False

        return self._dispatcher.call(self._trusted_locations_wrap, _do)

    def delete_module(self, module_name: str) -> bool:
        """Delete a VBA module from the database."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return False
            try:
                for comp in vb_project.VBComponents:
                    if comp.Name == module_name:
                        vb_project.VBComponents.Remove(comp)
                        return True
                return False
            except Exception as exc:
                logger.debug("delete_module %s failed: %s", module_name, exc)
                return False

        return self._dispatcher.call(_do)

    def create_module(self, module_name: str, module_type: int = 1) -> bool:
        """Create a new VBA module in the database.

        Args:
            module_name: Name for the new module
            module_type: vbext_ComponentType — 1 = standard module, 2 = class module

        """
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return False
            try:
                comp = vb_project.VBComponents.Add(module_type)
                comp.Name = module_name
                return True
            except Exception as exc:
                logger.debug("create_module %s failed: %s", module_name, exc)
                return False

        return self._dispatcher.call(_do)

    def rename_module(self, old_name: str, new_name: str) -> bool:
        """Rename an existing VBA module."""
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return False
            try:
                for comp in vb_project.VBComponents:
                    if comp.Name == old_name:
                        comp.Name = new_name
                        return True
                return False
            except Exception as exc:
                logger.debug("rename_module %s->%s failed: %s", old_name, new_name, exc)
                return False

        return self._dispatcher.call(_do)

    def module_exists(self, module_name: str) -> bool:
        """Check if a VBA module exists in the database.

        Uses CurrentProject.AllModules (0-based DAO collection).
        """
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            try:
                for i in range(self._dispatcher.access_app.CurrentProject.AllModules.Count):
                    mod = self._dispatcher.access_app.CurrentProject.AllModules(i)
                    if mod.Name == module_name:
                        return True
            except Exception as exc:
                logger.debug("module_exists %s failed: %s", module_name, exc)
                return False
            return False

        return self._dispatcher.call(_do)

    def save_database(self) -> dict:
        """Save all VBA modules and database changes.

        Returns:
            dict with success=True on success, error message on failure

        """
        if not self._dispatcher._started:
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return {"success": False, "error": "No VBA project"}
            app = self._dispatcher.access_app
            saved = 0
            errors = []
            try:
                for comp in vb_project.VBComponents:
                    if comp.Type == 1:  # vbext_ct_StdModule
                        try:
                            app.DoCmd.Save(5, comp.Name)  # 5 = acModule
                            saved += 1
                        except Exception as e:
                            errors.append(f"{comp.Name}: {e}")
            except Exception as e:
                return {"success": False, "error": str(e)}
            return {
                "success": True,
                "saved_modules": saved,
                "errors": errors,
            }

        return self._dispatcher.call(_do)

    def compile_vba(self) -> dict:
        """Compile VBA code.

        Tries known DoCmd.RunCommand constants for VBA compilation.
        Starts _dialog_killer daemon thread and stops it before returning.

        Returns:
            dict with success=True on success
            dict with success=False and error message on failure

        """
        if not self._dispatcher._started:
            return {"success": False, "error": "Not connected"}

        def _do() -> dict:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return {"success": False, "error": "No VBA project"}
            app = self._dispatcher.access_app

            # Save all modules first so compilation doesn't trigger "Save As" dialog
            for comp in vb_project.VBComponents:
                if comp.Type == 1:  # vbext_ct_StdModule
                    try:
                        app.DoCmd.Save(5, comp.Name)
                    except Exception as exc:
                        logger.debug("DoCmd.Save module %s failed: %s", comp.Name, exc)
                        pass

            # Start dialog killer as safety net
            dismissed = threading.Event()
            killer_thread = threading.Thread(
                target=self._dialog_killer,
                args=(dismissed,),
                daemon=True,
            )
            killer_thread.start()

            try:
                for cmd_id in _COMPILE_CMD_IDS:
                    try:
                        app.DoCmd.RunCommand(cmd_id)
                        return {"success": True}
                    except Exception as exc:
                        logger.debug("compile_vba RunCommand %s failed: %s", cmd_id, exc)
                        continue
                return {
                    "success": False,
                    "error": "Could not find working compile command for this Access version",
                }
            finally:
                dismissed.set()
                killer_thread.join(timeout=2.0)

        try:
            return self._dispatcher.call(self._trusted_locations_wrap, _do)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def vba_list_procedures(self, module_name: str) -> list[dict]:
        """List all procedures in a module with name, type, line info.

        Returns:
            List of dicts with keys: name, type ("Sub"|"Function"|"Property"),
            start_line, line_count

        """
        if not self._dispatcher._started:
            return []

        def _do() -> list[dict]:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return []
            try:
                target_module = None
                for comp in vb_project.VBComponents:
                    if comp.Name == module_name:
                        target_module = comp.CodeModule
                        break
                if target_module is None:
                    return []

                total_lines = target_module.CountOfLines
                if total_lines == 0:
                    return []

                procedures: list[dict] = []
                seen_procs: set[str] = set()

                for line in range(1, total_lines + 1):
                    try:
                        proc_name = target_module.ProcOfLine(line, 0)
                        if proc_name and proc_name not in seen_procs:
                            seen_procs.add(proc_name)
                            proc_kind = target_module.ProcKind(line, 0)
                            proc_type = {0: "Sub", 1: "Function", 2: "Property"}.get(proc_kind, "Sub")
                            start_line = target_module.ProcStartLine(proc_name, 0)
                            line_count = target_module.ProcCountLines(proc_name, 0)
                            procedures.append({
                                "name": proc_name,
                                "type": proc_type,
                                "start_line": start_line,
                                "line_count": line_count,
                            })
                    except Exception as exc:
                        logger.debug("ProcOfLine failed for line %s in %s: %s", line, module_name, exc)
                        pass
                return procedures
            except Exception as exc:
                logger.debug("list procedures %s failed: %s", module_name, exc)
                return []

        return self._dispatcher.call(_do)

    def vba_get_procedure(self, module_name: str, procedure_name: str) -> dict:
        """Get full source code of a specific procedure.

        Returns:
            dict with keys: name, type, code, signature

        """
        if not self._dispatcher._started:
            return {}

        def _do() -> dict:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return {}
            try:
                target_module = None
                for comp in vb_project.VBComponents:
                    if comp.Name == module_name:
                        target_module = comp.CodeModule
                        break
                if target_module is None:
                    return {}

                start_line = target_module.ProcStartLine(procedure_name, 0)
                line_count = target_module.ProcCountLines(procedure_name, 0)
                code = target_module.Lines(start_line, line_count)

                lines = code.split("\n")
                signature = lines[0] if lines else ""

                proc_kind = target_module.ProcKind(start_line, 0)
                proc_type = {0: "Sub", 1: "Function", 2: "Property"}.get(proc_kind, "Sub")

                return {
                    "name": procedure_name,
                    "type": proc_type,
                    "code": code,
                    "signature": signature,
                }
            except Exception as exc:
                logger.debug("vba_get_procedure %s.%s failed: %s", module_name, procedure_name, exc)
                return {}

        return self._dispatcher.call(_do)

    def vba_replace_procedure(self, module_name: str, procedure_name: str, new_code: str) -> bool:
        """Replace a procedure's body with new code (preserves signature).

        Deletes the old procedure lines and inserts new code at the same position.
        """
        if not self._dispatcher._started:
            return False

        def _do() -> bool:
            vb_project = self._get_vb_project()
            if vb_project is None:
                return False
            try:
                target_module = None
                for comp in vb_project.VBComponents:
                    if comp.Name == module_name:
                        target_module = comp.CodeModule
                        break
                if target_module is None:
                    return False

                start_line = target_module.ProcStartLine(procedure_name, 0)
                line_count = target_module.ProcCountLines(procedure_name, 0)

                target_module.DeleteLines(start_line, line_count)
                target_module.InsertLines(start_line, new_code)
                return True
            except Exception as exc:
                logger.debug("vba_replace_procedure %s.%s failed: %s", module_name, procedure_name, exc)
                return False

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
