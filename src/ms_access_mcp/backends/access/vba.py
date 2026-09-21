"""VBA project operations: compilation, procedure listing, text export/import.

Extracted from AccessAdapter (plan 060). Functions take COM app/dispatcher
as first arg; stateless w.r.t AccessAdapter beyond connection access.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Compile command IDs by Access version (most likely first)
_COMPILE_CMD_IDS = [301, 206, 317, 232]


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
            enc = locale.getpreferredencoding(False) or "cp1252"
            with open(temp_path, "w", encoding=enc, errors="replace") as f:
                f.write(text_data)
        else:
            with open(temp_path, "wb") as f:
                f.write(b"\xff\xfe")
                f.write(text_data.encode("utf-16-le"))
        access_app.LoadFromText(object_type, object_name, temp_path)
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
