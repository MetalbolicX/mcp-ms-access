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

# The Office trust-center refusal ("Programmatic access to Visual Basic Project is
# denied", HRESULT 0x800A17B4 / Office error 6068) is the only positively recognized trust
# refusal. It is matched by status code only: localized message text is never classified.
TRUST_ACCESS_REFUSED_HRESULT = 0x800A17B4

VBA_TRUST_PREREQUISITE = (
    "VBProject access requires the operator to enable 'Trust access to the VBA project "
    "object model' in the Access Trust Center; the VBA module catalogue is unavailable"
)


class VbaTrustRefusedError(RuntimeError):
    """The host refuses programmatic VBProject access (operator trust setting)."""


class VbaProjectUnavailableError(RuntimeError):
    """The VBA project could not be read; a real failure, never a trust diagnosis."""


def failing_hresults(error: BaseException) -> tuple[int, ...]:
    """Return every failing HRESULT an operational COM error exposes (duck-typed).

    pywin32 exposes the status as ``hresult`` and as the first positional argument. A
    nested COM failure carries the real status as ``excepinfo[5]`` (``scode``) of
    ``args[2]`` while ``args[0]`` may hold only the generic ``DISP_E_EXCEPTION``. Codes
    are normalized to the unsigned 32-bit form so signed and unsigned spellings compare
    equal; ``bool`` is never an HRESULT, and a success (severity-0) code is not a failure.

    A plain ``int`` that only ``args`` carries is deliberately accepted, exactly like an
    ``hresult`` attribute: the classification is duck-typed and is never tightened to
    require ``hresult`` (a high-bit, severity-1 plain int is a failing HRESULT).

    Args:
        error: Any raised error; only its exposed integer statuses are inspected.

    Returns:
        Every normalized failing HRESULT, in discovery order (possibly empty).

    """
    candidates: list[object] = [getattr(error, "hresult", None)]
    if error.args:
        candidates.append(error.args[0])
        if len(error.args) > 2 and isinstance(error.args[2], tuple) and len(error.args[2]) > 5:
            candidates.append(error.args[2][5])
    codes: list[int] = []
    for candidate in candidates:
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            continue
        unsigned = candidate & 0xFFFFFFFF
        if unsigned & 0x80000000:
            codes.append(unsigned)
    return tuple(codes)


def is_vba_trust_refusal(error: BaseException) -> bool:
    """Report whether ``error`` positively carries the VBProject trust refusal status.

    Args:
        error: The COM-shaped failure raised while reaching the VBE project.

    Returns:
        ``True`` only when a failing HRESULT equals ``0x800A17B4``.

    """
    return TRUST_ACCESS_REFUSED_HRESULT in failing_hresults(error)


def _vb_project_failure(error: BaseException) -> Exception:
    """Classify a strict VBE/VBProjects access failure; never invent a trust diagnosis."""
    if is_vba_trust_refusal(error):
        return VbaTrustRefusedError(VBA_TRUST_PREREQUISITE)
    codes = failing_hresults(error)
    if codes:
        msg = (
            f"the VBA project could not be read (unknown COM failure 0x{codes[-1]:08X}); "
            "the catalogue is failed, not a trust refusal"
        )
        return VbaProjectUnavailableError(msg)
    msg = f"the VBA project could not be read ({error}); the catalogue is failed, not a trust refusal"
    return VbaProjectUnavailableError(msg)


def _save_vba_module(access_app: Any, module_name: str, operation: str) -> bool:
    """Persist a successful VBE mutation before reporting success."""
    try:
        access_app.DoCmd.Save(5, module_name)  # 5 = acModule; saves the VBA project
        return True
    except Exception as exc:
        logger.debug("%s save module %s failed: %s", operation, module_name, exc)
        return False


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


def get_vb_project(access_app: Any, *, strict: bool = False) -> Any | None:
    """Get the first VBA project via VBProjects enumeration.

    COM VBProjects collection uses 1-based indexing. By default a VBE access failure is
    swallowed to ``None`` after a DEBUG log (the documented behaviour of the existing
    CRUD/form callers). With ``strict=True`` -- the documentation reader's mode -- it is
    raised instead: a positively recognized trust refusal becomes
    :class:`VbaTrustRefusedError`, and every other failure (corruption, an unknown COM
    status, an empty project list) becomes :class:`VbaProjectUnavailableError`, so an
    unknown failure is never described as a trust refusal.

    Args:
        access_app: The Access COM application object.
        strict: Raise a classified failure instead of returning ``None``.

    Returns:
        The first VBProject, or ``None`` when nothing is readable and ``strict`` is off.

    Raises:
        VbaTrustRefusedError: The host refuses programmatic VBProject access.
        VbaProjectUnavailableError: The VBE project is unreadable for any other reason.

    """
    try:
        vbe = access_app.VBE
    except Exception as exc:
        if strict:
            # Strict callers raise a classified failure, so the default swallow-and-DEBUG
            # wording would describe an outcome that never happens here.
            raise _vb_project_failure(exc) from exc
        logger.debug("vb project unavailable: %s", exc)
        return None
    try:
        count = vbe.VBProjects.Count
        for i in range(1, count + 1):
            return vbe.VBProjects(i)
    except Exception as exc:
        if strict:
            raise _vb_project_failure(exc) from exc
        raise
    if strict:
        msg = (
            "the VBE reported no VBA project for the open database; the catalogue is "
            "failed, not a trust refusal"
        )
        raise VbaProjectUnavailableError(msg)
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
                if not load_object_from_text_fn(5, module_name, text_data):
                    return False
                return _save_vba_module(access_app, module_name, "set_vba_code")
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
                return _save_vba_module(access_app, module_name, "set_vba_code")
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
                if not load_object_from_text_fn(5, module_name, text_data):
                    return False
                return _save_vba_module(access_app, module_name, "add_vba_procedure")
            return False
        else:
            target_module.CodeModule.AddFromString(code)
            return _save_vba_module(access_app, module_name, "add_vba_procedure")
    except Exception as exc:
        logger.debug("add_vba_procedure %s.%s failed: %s", module_name, procedure_name, exc)
        return False


def delete_module(access_app: Any, module_name: str) -> bool:
    """Delete a VBA module and persist removal when a module can be saved.

    Access cannot save an empty VBA project with DoCmd.Save(5, module), so a
    last-module removal is reported as unsuccessful and may be discarded when
    the database closes.
    """
    vb_project = get_vb_project(access_app)
    if vb_project is None:
        return False
    try:
        for comp in vb_project.VBComponents:
            if comp.Name == module_name:
                vb_project.VBComponents.Remove(comp)
                for remaining in vb_project.VBComponents:
                    if _save_vba_module(access_app, remaining.Name, "delete_module"):
                        return True
                    return False
                logger.debug(
                    "delete_module %s removed the last component; no module remains "
                    "to persist the removal",
                    module_name,
                )
                return False
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
        return _save_vba_module(access_app, module_name, "create_module")
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
                return _save_vba_module(access_app, new_name, "rename_module")
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
            return _save_vba_module(access_app, module_name, "vba_replace_procedure")
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
