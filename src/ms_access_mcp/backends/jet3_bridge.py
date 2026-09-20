r"""Jet 3 → Jet 4 conversion bridge via 32-bit JRO out-of-proc.

Phase 2 of plans/055-legacy-mdb-support.md.
ACE cannot open Jet 3 (Access 95/97) files.  This module converts them to
Jet 4 format using the 32-bit JRO.JetEngine CompactDatabase via a VBScript
spawned through SysWOW64\\cscript.exe (the 32-bit script host on x64 Windows).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from ..errors import Result
from ..errors import err as _err
from ..errors import ok as _ok
from .format_detector import AccessFormat, detect_access_format

__all__ = ["bridge_available", "convert_jet3_to_jet4"]

# ------------------------------------------------------------------ ----------
# VBScript template — paths passed ONLY via WScript.Arguments (never
# interpolated into the script body) to avoid any quoting hell.
# Engine Type=5 produces Jet 4.0 format (dbVersion40-equivalent).
# ------------------------------------------------------------------ ----------

JET3_CONVERT_VBS = (
    "Set e = CreateObject(\"JRO.JetEngine\")\n"
    "e.CompactDatabase "
    "\"Provider=Microsoft.Jet.OLEDB.4.0;Data Source=\" & WScript.Arguments(0), "
    "\"Provider=Microsoft.Jet.OLEDB.4.0;Data Source=\" & WScript.Arguments(1) & "
    "\";Jet OLEDB:Engine Type=5\"\n"
)


def bridge_available() -> bool:
    r"""Check whether the 32-bit cscript.exe (WOW64 script host) exists.

    Returns
    -------
    bool
        True when C:\\Windows\\SysWOW64\\cscript.exe is present.

    Notes
    -----
    No COM or subprocess call is made — only a cheap filesystem probe.

    """
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    cscript_path = os.path.join(system_root, "SysWOW64", "cscript.exe")
    return os.path.isfile(cscript_path)


def convert_jet3_to_jet4(src: str, dst: str) -> Result:
    """Convert a Jet 3 .mdb file to Jet 4 format via the 32-bit JRO bridge.

    Parameters
    ----------
    src:
        Absolute path to the source Jet 3 .mdb file.
    dst:
        Absolute path where the Jet 4 .mdb will be written.

    Returns
    -------
    Result
        ``Ok(data={"src": ..., "dst": ...})`` on success.
        ``Err(message=...)`` on any failure.

    Errors
    ------
    - Source file does not exist.
    - Source is not a Jet 3 file (checked via format_detector).
    - 32-bit cscript.exe is not available.
    - VBScript write or subprocess failure.
    - Destination file was not created or is not Jet 4 format.

    """
    # ---- validate source exists ------------------------------------------------
    if not os.path.isfile(src):
        return _err(f"Source file not found: {src}")

    # ---- validate it is actually Jet 3 ---------------------------------------
    fmt = detect_access_format(Path(src))
    if fmt != AccessFormat.JET3:
        return _err(
            f"Source file is not Jet 3 format (detected: {fmt.value}). "
            "Only Access 95/97 (Jet 3) files require this conversion."
        )

    # ---- check bridge availability ---------------------------------------------
    if not bridge_available():
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        cscript_path = os.path.join(system_root, "SysWOW64", "cscript.exe")
        return _err(
            f"32-bit script host not found at {cscript_path}. "
            "Cannot convert Jet 3 files on this system."
        )

    # ---- write VBS to temp file ------------------------------------------------
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    cscript_path = os.path.join(system_root, "SysWOW64", "cscript.exe")

    vbs_content = JET3_CONVERT_VBS.encode("utf-16-le")

    tmp_vbs: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".vbs",
            delete=False,
            dir=tempfile.gettempdir(),
        ) as fh:
            fh.write(vbs_content)
            tmp_vbs = fh.name

        # ---- run conversion ----------------------------------------------------
        # Pass paths as individual list args — no shell, no string interpolation.
        # cscript.exe is 32-bit (WOW64) so it loads the 32-bit JRO/Jet engine.
        try:
            proc = subprocess.run(
                [cscript_path, "//nologo", tmp_vbs, src, dst],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _err("Jet 3 conversion timed out after 60s")

        if proc.returncode != 0:
            stderr_text = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
            return _err(
                f"Jet 3 conversion failed (exit {proc.returncode}): {stderr_text.strip()}"
            )

    finally:
        # ---- always clean up the temp VBS ------------------------------------
        if tmp_vbs and os.path.isfile(tmp_vbs):
            try:
                os.unlink(tmp_vbs)
            except OSError:
                pass

    # ---- verify output ---------------------------------------------------------
    if not os.path.isfile(dst):
        return _err(f"Conversion completed but destination file was not created: {dst}")

    detected_fmt = detect_access_format(Path(dst))
    if detected_fmt != AccessFormat.JET4:
        return _err(
            f"Conversion completed but output is not Jet 4 format "
            f"(detected: {detected_fmt.value}): {dst}"
        )

    return _ok(data={"src": src, "dst": dst})
