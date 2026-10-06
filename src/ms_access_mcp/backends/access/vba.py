"""VBA project operations: compilation, procedure listing, text export/import.

Extracted from AccessAdapter (plan 060). Functions take COM app/dispatcher
as first arg; stateless w.r.t AccessAdapter beyond connection access.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Compile command IDs by Access version (most likely first)
_COMPILE_CMD_IDS = [301, 206, 317, 232]


def _proc_type_from_signature(signature: str) -> str:
    """Derive a procedure type from its declaration line.

    VBE CodeModule has no ProcKind method (plan-098 live finding: the
    call raises and silently empties the result); the declaration line
    carries the same information.
    """
    head = signature.strip().lower()
    for prefix in ("public ", "private ", "static "):
        while head.startswith(prefix):
            head = head[len(prefix):]
    if head.startswith("function"):
        return "Function"
    if head.startswith("property"):
        return "Property"
    return "Sub"


def get_vb_project(access_app: Any) -> Any | None:
    """Get the first VBA project via VBProjects enumeration.

    COM VBProjects collection uses 1-based indexing.
    Returns None if VBE is unavailable (caller handles via try/except).
    """
    try:
        vbe = access_app.VBE
    except Exception as exc:
        logger.debug("vb project unavailable: %s", exc)
        return None
    for i in range(1, vbe.VBProjects.Count + 1):
        return vbe.VBProjects(i)
    return None


def save_object_to_text(access_app: Any, object_type: int, object_name: str) -> str:
    """Export an Access object to text using SaveAsText.

    Returns the text content or empty string on failure.
    object_type: acForm=2, acReport=4, acModule=5, acMacro=8
    """
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=".txt", prefix="mcp_exp_")
        os.close(fd)
        access_app.SaveAsText(object_type, object_name, temp_path)
        with Path(temp_path).open("rb") as f:
            raw = f.read()
        # SaveAsText outputs UTF-16-LE with BOM; decode accordingly
        content = raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
        return content
    except Exception:
        return ""
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink()
            except Exception as exc:
                logger.debug("temp file cleanup %s failed: %s", temp_path, exc)
                pass


def load_object_from_text(
    access_app: Any, object_type: int, object_name: str, text_data: str
) -> bool:
    """Import an Access object from text data using LoadFromText.

    object_type: acForm=2, acReport=4, acModule=5, acMacro=8

    Encoding: VBA modules (acModule=5) expect the system ANSI codepage
    without BOM. Forms, reports, queries, and macros expect UTF-16-LE
    with BOM.
    """
    import locale

    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=".txt", prefix="mcp_imp_")
        os.close(fd)
        if object_type == 5:  # acModule
            # Byte-exact system-ANSI write: Access reads module import files as
            # ANSI without BOM; text mode injects \r\r\n drift (plan-085 live
            # ground truth 2026-10-05: UTF-16 truncates to "A").
            enc = locale.getpreferredencoding(False) or "cp1252"
            with Path(temp_path).open("wb") as f:
                f.write(text_data.encode(enc, errors="replace"))
        else:
            with Path(temp_path).open("wb") as f:
                f.write(b"\xff\xfe")
                f.write(text_data.encode("utf-16-le"))
        access_app.LoadFromText(object_type, object_name, temp_path)
        return True
    except Exception:
        return False
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink()
            except Exception as exc:
                logger.debug("temp file cleanup %s failed: %s", temp_path, exc)
                pass


def list_procedures(access_app: Any, vb_project: Any, module_name: str) -> list[dict]:
    """List all procedures in a module with name, type, line info.

    Returns:
        List of dicts with keys: name, type ("Sub"|"Function"|"Property"),
        start_line, line_count

    """
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
                    start_line = target_module.ProcStartLine(proc_name, 0)
                    line_count = target_module.ProcCountLines(proc_name, 0)
                    signature = target_module.Lines(start_line, 1)
                    procedures.append({
                        "name": proc_name,
                        "type": _proc_type_from_signature(signature),
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


def compile_vba(
    access_app: Any,
    compile_cmd_ids: list[int],
    dialog_killer_fn: Any,
    vb_project: Any | None = None,
) -> dict:
    """Compile VBA code.

    Tries known DoCmd.RunCommand constants for VBA compilation.
    Starts dialog_killer_fn as daemon thread and stops it before returning.

    Args:
        access_app: The Access COM application object.
        compile_cmd_ids: List of DoCmd.RunCommand command IDs to try.
        dialog_killer_fn: Function to call for dialog killer thread.
        vb_project: Optional pre-fetched VB project. If None, fetches via get_vb_project.

    Returns:
        dict with success=True on success
        dict with success=False and error message on failure

    """
    if vb_project is None:
        vb_project = get_vb_project(access_app)
    if vb_project is None:
        return {"success": False, "error": "No VBA project"}

    # Save all modules first so compilation doesn't trigger "Save As" dialog
    for comp in vb_project.VBComponents:
        if comp.Type == 1:  # vbext_ct_StdModule
            try:
                access_app.DoCmd.Save(5, comp.Name)
            except Exception as exc:
                logger.debug("DoCmd.Save module %s failed: %s", comp.Name, exc)
                pass

    # Start dialog killer as safety net
    dismissed = threading.Event()
    killer_thread = threading.Thread(
        target=dialog_killer_fn,
        args=(dismissed,),
        daemon=True,
    )
    killer_thread.start()

    try:
        for cmd_id in compile_cmd_ids:
            try:
                access_app.DoCmd.RunCommand(cmd_id)
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


def get_vba_project_name(access_app: Any) -> str:
    """Get the VBA project name from COM."""
    vb_project = get_vb_project(access_app)
    return vb_project.Name if vb_project else ""


def get_modules(access_app: Any) -> list:
    """Get all VBA modules in the database."""
    vb_project = get_vb_project(access_app)
    if vb_project is None:
        return []
    modules = []
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


def get_vba_code(access_app: Any, module_name: str) -> str:
    """Get VBA code from a module."""
    vb_project = get_vb_project(access_app)
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


def set_vba_code(
    access_app: Any,
    module_name: str,
    code: str,
    load_object_from_text_fn: Any = None,
) -> bool:
    """Set VBA code in a module.

    For non-existent modules: uses LoadFromText.
    For existing modules: uses DeleteLines + AddFromString.
    """
    vb_project = get_vb_project(access_app)
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
            if load_object_from_text_fn:
                return load_object_from_text_fn(5, module_name, text_data)
            return False
        else:
            # Existing module: snapshot, then clear and repopulate in-memory.
            code_module = target_module.CodeModule
            line_count = 0
            original = ""
            try:
                line_count = code_module.CountOfLines
                original = code_module.Lines(1, line_count) if line_count else ""
                code_module.DeleteLines(1, line_count)
                code_module.AddFromString(code)
                return True
            except Exception as exc:
                logger.debug("set_vba_code %s repopulate failed: %s", module_name, exc)
                try:
                    current_line_count = code_module.CountOfLines
                    if current_line_count:
                        code_module.DeleteLines(1, current_line_count)
                    if line_count:
                        code_module.AddFromString(original)
                except Exception as restore_exc:
                    logger.debug("set_vba_code %s restore failed: %s", module_name, restore_exc)
                return False
    except Exception as exc:
        logger.debug("set_vba_code %s failed: %s", module_name, exc)
        return False


def add_vba_procedure(
    access_app: Any,
    module_name: str,
    procedure_name: str,
    code: str,
    load_object_from_text_fn: Any = None,
) -> bool:
    """Add a VBA procedure to a module.

    For non-existent modules: uses LoadFromText.
    For existing modules: appends via AddFromString.
    """
    vb_project = get_vb_project(access_app)
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
            if load_object_from_text_fn:
                return load_object_from_text_fn(5, module_name, text_data)
            return False
        else:
            target_module.CodeModule.AddFromString(code)
            return True
    except Exception as exc:
        logger.debug("add_vba_procedure %s.%s failed: %s", module_name, procedure_name, exc)
        return False


def delete_module(access_app: Any, module_name: str) -> bool:
    """Delete a VBA module from the database."""
    vb_project = get_vb_project(access_app)
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


def create_module(access_app: Any, module_name: str, module_type: int = 1) -> bool:
    """Create a new VBA module in the database.

    Args:
        access_app: The Access COM application object.
        module_name: Name for the new module.
        module_type: vbext_ComponentType — 1 = standard module, 2 = class module.

    """
    vb_project = get_vb_project(access_app)
    if vb_project is None:
        return False
    try:
        comp = vb_project.VBComponents.Add(module_type)
        comp.Name = module_name
        return True
    except Exception as exc:
        logger.debug("create_module %s failed: %s", module_name, exc)
        return False


def rename_module(access_app: Any, old_name: str, new_name: str) -> bool:
    """Rename an existing VBA module."""
    vb_project = get_vb_project(access_app)
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


def module_exists(access_app: Any, module_name: str) -> bool:
    """Check if a VBA module exists in the database.

    Uses CurrentProject.AllModules (0-based DAO collection).
    """
    try:
        for i in range(access_app.CurrentProject.AllModules.Count):
            mod = access_app.CurrentProject.AllModules(i)
            if mod.Name == module_name:
                return True
    except Exception as exc:
        logger.debug("module_exists %s failed: %s", module_name, exc)
        return False
    return False


def save_database(access_app: Any) -> dict:
    """Save all VBA modules and database changes.

    Returns:
        dict with success=True on success, error message on failure.

    """
    vb_project = get_vb_project(access_app)
    if vb_project is None:
        return {"success": False, "error": "No VBA project"}
    saved = 0
    errors = []
    try:
        for comp in vb_project.VBComponents:
            if comp.Type == 1:  # vbext_ct_StdModule
                try:
                    access_app.DoCmd.Save(5, comp.Name)  # 5 = acModule
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


def vba_get_procedure(access_app: Any, module_name: str, procedure_name: str) -> dict:
    """Get full source code of a specific procedure.

    Returns:
        dict with keys: name, type, code, signature.

    """
    vb_project = get_vb_project(access_app)
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

        proc_type = _proc_type_from_signature(signature)

        return {
            "name": procedure_name,
            "type": proc_type,
            "code": code,
            "signature": signature,
        }
    except Exception as exc:
        logger.debug("vba_get_procedure %s.%s failed: %s", module_name, procedure_name, exc)
        return {}


def vba_replace_procedure(access_app: Any, module_name: str, procedure_name: str, new_code: str) -> bool:
    """Replace a procedure's body with new code (preserves signature).

    Deletes the old procedure lines and inserts new code at the same position.
    """
    vb_project = get_vb_project(access_app)
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
        original = target_module.Lines(start_line, line_count)

        try:
            target_module.DeleteLines(start_line, line_count)
            target_module.InsertLines(start_line, new_code)
            return True
        except Exception as exc:
            logger.debug("vba_replace_procedure %s.%s failed: %s", module_name, procedure_name, exc)
            try:
                target_module.InsertLines(start_line, original)
            except Exception as restore_exc:
                logger.debug(
                    "vba_replace_procedure %s.%s restore failed: %s",
                    module_name,
                    procedure_name,
                    restore_exc,
                )
            return False
    except Exception as exc:
        logger.debug("vba_replace_procedure %s.%s failed: %s", module_name, procedure_name, exc)
        return False
