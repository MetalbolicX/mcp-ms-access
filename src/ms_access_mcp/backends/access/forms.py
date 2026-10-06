"""Form operations for AccessAdapter.

Plan 060: extracted from AccessAdapter to reduce facade size.
Mirrors stay mirrors per plan 016 phase 4.
"""

import logging
from typing import Any

from . import control_types, object_kinds, vba

logger = logging.getLogger(__name__)


def _props_to_dict(props: Any) -> dict[str, str]:
    """Scrape a COM Properties collection into a plain dict."""
    result: dict[str, str] = {}
    for prop in props:
        try:
            result[prop.Name] = str(prop.Value)
        except Exception:
            logger.debug("property %s raised during value extraction, skipping", prop.Name)
    return result


# --------------------------------------------------------------------------- #
# Form CRUD
# --------------------------------------------------------------------------- #

def open_form(dispatcher, form_name: str) -> bool:
    """Open a form in Access (appears on the server desktop)."""
    if not dispatcher._started:
        return False

    def _do() -> bool:
        try:
            dispatcher.access_app.DoCmd.OpenForm(form_name)
            return True
        except Exception as exc:
            object_kinds._log_object_failure("form", "open", form_name, exc)
            return False

    try:
        return dispatcher.call(_do)
    except Exception:
        return False


def close_form(dispatcher, form_name: str) -> bool:
    """Close an open form without saving."""
    if not dispatcher._started:
        return False

    def _do() -> bool:
        try:
            dispatcher.access_app.DoCmd.Close(2, form_name, 2)  # acForm=2, acSaveNo=2
            return True
        except Exception as exc:
            object_kinds._log_object_failure("form", "close", form_name, exc)
            return False

    try:
        return dispatcher.call(_do)
    except Exception:
        return False


def create_form(
    dispatcher,
    form_name: str,
    record_source: str = "",
    template_name: str = "",
    properties: dict[str, Any] | None = None,
    log_object_failure: Any = None,
) -> bool:
    """Create a new form via DoCmd.CreateForm, optionally setting RecordSource and properties."""
    return object_kinds._create_object(
        dispatcher,
        "form",
        form_name,
        record_source,
        template_name,
        properties,
        log_object_failure=log_object_failure,
    )


def rename_form(dispatcher, old_name: str, new_name: str) -> bool:
    """Rename a form via DoCmd.Rename with acForm=2."""
    return object_kinds._rename_object(dispatcher, "form", old_name, new_name)


def delete_form(dispatcher, form_name: str) -> bool:
    """Delete a form from the database."""
    return object_kinds._delete_object(dispatcher, "form", form_name)


# --------------------------------------------------------------------------- #
# Form properties
# --------------------------------------------------------------------------- #

def _get_object_properties(dispatcher, kind: str, object_name: str) -> dict[str, str]:
    """Get all properties of a form/report by opening it in design view."""
    return object_kinds._with_design_view(
        dispatcher,
        kind,
        object_name,
        save_flag=2,
        work=lambda obj: _props_to_dict(obj.Properties) if obj is not None else {},
        on_error=lambda exc: {},
        default={},
    )


def get_form_properties(dispatcher, form_name: str) -> dict[str, str]:
    """Get all properties of a form by opening it in design view."""
    return _get_object_properties(dispatcher, "form", form_name)


def _set_object_property(
    dispatcher,
    kind: str,
    object_name: str,
    property_name: str,
    value: str,
    log_object_failure: Any = None,
) -> bool:
    """Set a single property of a form/report by opening it in design view."""
    def work(obj):
        if obj is None:
            return False
        obj.Properties(property_name).Value = value
        return True

    on_error = (lambda exc: (log_object_failure(kind, "set_property", object_name, exc) or False)) if log_object_failure else (lambda exc: False)

    return object_kinds._with_design_view(
        dispatcher,
        kind,
        object_name,
        save_flag=1,
        work=work,
        on_error=on_error,
        default=False,
    )


def set_form_property(dispatcher, form_name: str, property_name: str, value: str, log_object_failure: Any = None) -> bool:
    """Set a single property of a form by opening it in design view."""
    return _set_object_property(dispatcher, "form", form_name, property_name, value, log_object_failure=log_object_failure)


def _set_object_properties(
    dispatcher,
    kind: str,
    object_name: str,
    properties: dict[str, Any],
    log_object_failure: Any = None,
) -> dict[str, bool]:
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

        on_error = (lambda exc: (log_object_failure(kind, "set_properties", object_name, exc) or False)) if log_object_failure else (lambda exc: False)

        return object_kinds._with_design_view(
            dispatcher,
            kind,
            object_name,
            save_flag=1,
            work=inner,
            on_error=on_error,
            default=False,
        )

    results: dict[str, bool] = {}
    for prop_name, value in properties.items():
        results[prop_name] = work_for_prop(prop_name, value)
    return results


def set_form_properties(dispatcher, form_name: str, properties: dict[str, Any]) -> dict[str, bool]:
    """Set multiple properties of a form. Returns dict of {property_name: success}."""
    return _set_object_properties(dispatcher, "form", form_name, properties)


# --------------------------------------------------------------------------- #
# Form controls
# --------------------------------------------------------------------------- #

def _get_object_controls(dispatcher, kind: str, object_name: str, control_type_name_func: Any = None):
    """Get all controls in a form/report by opening it in design view."""
    if control_type_name_func is None:
        def _default_control_type_name(ct: int) -> str:
            return control_types.control_type_name(ct)
        control_type_name_func = _default_control_type_name

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
                ctrl_type = control_type_name_func(ctrl_type_code)

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

    return object_kinds._with_design_view(
        dispatcher,
        kind,
        object_name,
        save_flag=2,
        work=work,
        on_error=lambda exc: [],
        default=[],
    )


def get_form_controls(dispatcher, form_name: str):
    """Get all controls in a form by opening it in design view."""
    return _get_object_controls(dispatcher, "form", form_name)


def _get_object_control_properties(dispatcher, kind: str, object_name: str, control_name: str) -> dict[str, str]:
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

    return object_kinds._with_design_view(
        dispatcher,
        kind,
        object_name,
        save_flag=2,
        work=work,
        on_error=lambda exc: {},
        default={},
    )


def get_control_properties(dispatcher, form_name: str, control_name: str) -> dict[str, str]:
    """Get all properties of a specific control by opening the form in design view."""
    return _get_object_control_properties(dispatcher, "form", form_name, control_name)


def _set_object_control_property(
    dispatcher,
    kind: str,
    object_name: str,
    control_name: str,
    property_name: str,
    value: str,
    log_object_failure: Any = None,
) -> bool:
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

    on_error = (lambda exc: (log_object_failure(kind, "set_control_property", object_name, exc) or False)) if log_object_failure else (lambda exc: False)

    return object_kinds._with_design_view(
        dispatcher,
        kind,
        object_name,
        save_flag=1,
        work=work,
        on_error=on_error,
        default=False,
    )


def set_control_property(dispatcher, form_name: str, control_name: str, property_name: str, value: str) -> bool:
    """Set a property of a control by opening the form in design view."""
    return _set_object_control_property(dispatcher, "form", form_name, control_name, property_name, value)


def _set_object_control_properties(
    dispatcher,
    kind: str,
    object_name: str,
    control_name: str,
    properties: dict[str, Any],
    log_object_failure: Any = None,
) -> dict[str, bool]:
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

        on_error = (lambda exc: (log_object_failure(kind, "set_control_properties", object_name, exc) or False)) if log_object_failure else (lambda exc: False)

        return object_kinds._with_design_view(
            dispatcher,
            kind,
            object_name,
            save_flag=1,
            work=inner,
            on_error=on_error,
            default=False,
        )

    results: dict[str, bool] = {}
    for prop_name, value in properties.items():
        results[prop_name] = work_for_prop(prop_name, value)
    return results


def set_control_properties(dispatcher, form_name: str, control_name: str, properties: dict[str, Any]) -> dict[str, bool]:
    """Set multiple properties at once. Returns dict of {property_name: success}."""
    return _set_object_control_properties(dispatcher, "form", form_name, control_name, properties)


def _add_object_control(
    dispatcher,
    kind: str,
    object_name: str,
    control_type: str,
    control_name: str,
    section: int = 0,
    properties: dict[str, Any] | None = None,
    log_object_failure: Any = None,
) -> bool:
    """Add a control to a form/report by opening it in design view."""
    def work(obj):
        type_int = control_types.control_type_id(control_type)
        if type_int == 0:
            return False

        ctrl = dispatcher.access_app.CreateControl(object_name, type_int, section)
        ctrl.Name = control_name

        if properties:
            for prop_name, value in properties.items():
                try:
                    ctrl.Properties(prop_name).Value = value
                except Exception as exc:
                    logger.debug("add_object_control set property %s/%s[%s].%s failed: %s", kind, object_name, control_name, prop_name, exc)
                    pass
        return True

    on_error = (lambda exc: (log_object_failure(kind, "add_object_control", object_name, exc) or False)) if log_object_failure else (lambda exc: False)

    return object_kinds._with_design_view(
        dispatcher,
        kind,
        object_name,
        save_flag=1,
        work=work,
        on_error=on_error,
        default=False,
    )


def add_control(
    dispatcher,
    form_name: str,
    control_type: str,
    control_name: str,
    section: int = 0,
    properties: dict[str, Any] | None = None,
) -> bool:
    """Add a control to a form by opening it in design view."""
    return _add_object_control(dispatcher, "form", form_name, control_type, control_name, section, properties)


def _remove_object_control(
    dispatcher,
    kind: str,
    object_name: str,
    control_name: str,
    log_object_failure: Any = None,
) -> bool:
    """Remove a control from a form/report by opening it in design view."""
    def work(obj):
        if obj is None:
            return False
        for i in range(obj.Controls.Count):
            try:
                ctrl = obj.Controls(i)
                if ctrl.Name == control_name:
                    # Design-view deletion recipe (plan-098, live-verified
                    # 2026-10-05): select the design window, select the
                    # control, then acCmdDelete=337 (typelib-verified; 365 is
                    # HidePane). SetFocus does not exist on design-view
                    # controls.
                    ac_type = object_kinds.OBJECT_KINDS[kind]["ac_type"]
                    dispatcher.access_app.DoCmd.SelectObject(ac_type, object_name)
                    ctrl.InSelection = True
                    dispatcher.access_app.DoCmd.RunCommand(337)  # acCmdDelete
                    return True
            except Exception as exc:
                logger.debug("remove_object_control %s/%s[%s] failed: %s", kind, object_name, control_name, exc)
                pass
        return False

    on_error = (lambda exc: (log_object_failure(kind, "remove_object_control", object_name, exc) or False)) if log_object_failure else (lambda exc: False)

    return object_kinds._with_design_view(
        dispatcher,
        kind,
        object_name,
        save_flag=1,
        work=work,
        on_error=on_error,
        default=False,
    )


def remove_control(dispatcher, form_name: str, control_name: str) -> bool:
    """Remove a control from a form by opening it in design view."""
    return _remove_object_control(dispatcher, "form", form_name, control_name)


# --------------------------------------------------------------------------- #
# Form control events
# --------------------------------------------------------------------------- #

def get_control_event_procedures(dispatcher, form_name: str, control_name: str = "") -> list[dict]:
    """List event procedures for a specific control in a form."""
    if not dispatcher._started:
        return []

    def _do() -> list[dict]:
        vb_project = _get_vb_project(dispatcher)
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

    return dispatcher.call(_do)


def _get_vb_project(dispatcher) -> Any:
    """Get the first VBA project via VBProjects enumeration."""
    try:
        return vba.get_vb_project(dispatcher.access_app)
    except Exception:
        return None


def set_control_event_procedure(
    dispatcher,
    form_name: str,
    control_name: str,
    event_name: str,
    code: str,
    vba_replace_procedure: Any = None,
    log_object_failure: Any = None,
) -> bool:
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
        if vba_replace_procedure:
            return vba_replace_procedure(module_name, proc_name, code)
        return False

    on_error = (lambda exc: (log_object_failure("form", "set_control_event_procedure", form_name, exc) or False)) if log_object_failure else (lambda exc: False)

    return object_kinds._with_design_view(
        dispatcher,
        "form",
        form_name,
        save_flag=1,
        work=work,
        on_error=on_error,
        default=False,
    )


# --------------------------------------------------------------------------- #
# Form sections
# --------------------------------------------------------------------------- #

def _get_object_sections(dispatcher, kind: str, object_name: str, section_name_func: Any = None) -> list[dict]:
    """Get all sections of a form/report by opening it in design view."""
    if section_name_func is None:
        def _default_section_name(sid: int) -> str:
            return control_types.section_name(sid)
        section_name_func = _default_section_name

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
                    "section_type": section_name_func(i),
                    "visible": bool(section.Visible),
                    "height": int(section.Height),
                })
            except Exception as exc:
                logger.debug("get_%s_sections %s section %s failed: %s", kind, object_name, i, exc)
                pass
        return sections

    return object_kinds._with_design_view(
        dispatcher,
        kind,
        object_name,
        save_flag=2,
        work=work,
        on_error=lambda exc: [],
        default=[],
    )


def get_form_sections(dispatcher, form_name: str) -> list[dict]:
    """Get all sections of a form by opening it in design view."""
    return _get_object_sections(dispatcher, "form", form_name)


def _get_object_section_properties(dispatcher, kind: str, object_name: str, section_id: int) -> dict[str, str]:
    """Get all properties of a specific section by opening the form/report in design view."""
    def work(obj):
        if obj is not None:
            section = obj.Section(section_id)
            return _props_to_dict(section.Properties)
        return {}

    return object_kinds._with_design_view(
        dispatcher,
        kind,
        object_name,
        save_flag=2,
        work=work,
        on_error=lambda exc: {},
        default={},
    )


def get_form_section_properties(dispatcher, form_name: str, section_id: int) -> dict[str, str]:
    """Get all properties of a specific section by opening the form in design view."""
    return _get_object_section_properties(dispatcher, "form", form_name, section_id)


def _set_object_section_property(
    dispatcher,
    kind: str,
    object_name: str,
    section_id: int,
    property_name: str,
    value: str,
    log_object_failure: Any = None,
) -> bool:
    """Set a single property of a form/report section by opening it in design view."""
    def work(obj):
        if obj is None:
            return False
        section = obj.Section(section_id)
        section.Properties(property_name).Value = value
        return True

    on_error = (lambda exc: (log_object_failure(kind, "set_section_property", object_name, exc) or False)) if log_object_failure else (lambda exc: False)

    return object_kinds._with_design_view(
        dispatcher,
        kind,
        object_name,
        save_flag=1,
        work=work,
        on_error=on_error,
        default=False,
    )


def set_form_section_property(dispatcher, form_name: str, section_id: int, property_name: str, value: str) -> bool:
    """Set a single property of a form section by opening the form in design view."""
    return _set_object_section_property(dispatcher, "form", form_name, section_id, property_name, value)


def _set_object_section_properties(
    dispatcher,
    kind: str,
    object_name: str,
    section_id: int,
    properties: dict[str, Any],
    log_object_failure: Any = None,
) -> dict[str, bool]:
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

        on_error = (lambda exc: (log_object_failure(kind, "set_section_properties", object_name, exc) or False)) if log_object_failure else (lambda exc: False)

        return object_kinds._with_design_view(
            dispatcher,
            kind,
            object_name,
            save_flag=1,
            work=inner,
            on_error=on_error,
            default=False,
        )

    results: dict[str, bool] = {}
    for prop_name, value in properties.items():
        results[prop_name] = work_for_prop(prop_name, value)
    return results


def set_form_section_properties(dispatcher, form_name: str, section_id: int, properties: dict[str, Any]) -> dict[str, bool]:
    """Set multiple properties of a form section. Returns dict of {property_name: success}."""
    return _set_object_section_properties(dispatcher, "form", form_name, section_id, properties)


# --------------------------------------------------------------------------- #
# Form text export/import
# --------------------------------------------------------------------------- #

def export_form_to_text(dispatcher, form_name: str, save_object_to_text: Any = None) -> str:
    """Export a form to text representation via SaveAsText."""
    if not dispatcher._started:
        return ""

    def _do() -> str:
        if save_object_to_text:
            return save_object_to_text(2, form_name)
        return ""

    return dispatcher.call(_do)


def import_form_from_text(dispatcher, form_name: str, form_data: str, load_object_from_text: Any = None) -> bool:
    """Import a form from text data via LoadFromText."""
    if not dispatcher._started:
        return False

    def _do() -> bool:
        if load_object_from_text:
            return load_object_from_text(2, form_name, form_data)
        return False

    return dispatcher.call(_do)
