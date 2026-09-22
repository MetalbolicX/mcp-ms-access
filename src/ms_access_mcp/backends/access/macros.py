"""Macro operations for AccessAdapter.

Plan 060: extracted from AccessAdapter to reduce facade size.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# XML template for creating empty macros
_MACRO_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-16" standalone="no"?>\n'
    "<Macros>\n"
    '  <Macro Name="TemplateMacro">\n'
    "    <Statements/>\n"
    "  </Macro>\n"
    "</Macros>\n"
)


def get_macros(dispatcher):
    """Get all macros in the database."""
    if not dispatcher._started:
        return []

    def _do():
        macros = []
        try:
            all_macros = dispatcher.access_app.CurrentProject.AllMacros
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
        return dispatcher.call(_do)
    except Exception:
        return []


def macro_exists(dispatcher, macro_name: str) -> bool:
    """Check if a macro exists."""
    if not dispatcher._started:
        return False

    def _do() -> bool:
        try:
            all_macros = dispatcher.access_app.CurrentProject.AllMacros
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

    return dispatcher.call(_do)


def create_macro(dispatcher, macro_name: str, load_object_from_text: Any = None) -> bool:
    """Create an empty macro via LoadFromText using a minimal XML template."""
    if not dispatcher._started:
        return False

    def _do() -> bool:
        try:
            xml = _MACRO_TEMPLATE.replace("TemplateMacro", macro_name)
            if load_object_from_text:
                return load_object_from_text(8, macro_name, xml)
            return False
        except Exception:
            return False

    return dispatcher.call(_do)


def rename_macro(dispatcher, old_name: str, new_name: str) -> bool:
    """Rename a macro via DoCmd.Rename with acMacro=8."""
    if not dispatcher._started:
        return False

    def _do() -> bool:
        try:
            dispatcher.access_app.DoCmd.Rename(new_name, 8, old_name)
            return True
        except Exception as exc:
            logger.debug("rename_macro %s->%s failed: %s", old_name, new_name, exc)
            return False

    return dispatcher.call(_do)


def delete_macro(dispatcher, macro_name: str) -> bool:
    """Delete a macro via DoCmd.DeleteObject with acMacro=8."""
    if not dispatcher._started:
        return False

    def _do() -> bool:
        try:
            dispatcher.access_app.DoCmd.DeleteObject(8, macro_name)
            return True
        except Exception as exc:
            logger.debug("delete_macro %s failed: %s", macro_name, exc)
            return False

    return dispatcher.call(_do)


def run_macro(dispatcher, macro_name: str) -> bool:
    """Execute a macro via DoCmd.RunMacro."""
    if not dispatcher._started:
        return False

    def _do() -> bool:
        try:
            dispatcher.access_app.DoCmd.RunMacro(macro_name)
            return True
        except Exception as exc:
            logger.debug("run_macro %s failed: %s", macro_name, exc)
            return False

    return dispatcher.call(_do)


def get_macro_properties(dispatcher, macro_name: str) -> dict[str, str]:
    """Get all properties of a macro by reading its AccessObject Properties collection."""
    if not dispatcher._started:
        return {}

    def _do() -> dict[str, str]:
        try:
            all_macros = dispatcher.access_app.CurrentProject.AllMacros
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

    return dispatcher.call(_do)
