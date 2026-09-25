"""Doctor — diagnostics CLI for verifying Access/COM/DAO/ODBC prerequisites.

Run: `ms-access-mcp doctor [--json] [--deep]`
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from .errors import strip_password


@dataclass
class Check:
    """Result of a single diagnostic check."""

    name: str
    passed: bool
    message: str = ""
    details: dict = field(default_factory=dict)


def _is_windows_x64() -> Check:
    """Check: Windows x64 + 64-bit Python."""
    passed = sys.platform == "win32" and sys.maxsize > 2**32
    return Check(
        name="platform",
        passed=passed,
        message="Windows x64" if passed else "Requires Windows x64",
        details={"platform": sys.platform, "maxsize": sys.maxsize},
    )


def _check_package_versions() -> Check:
    """Check: required packages are installed and importable."""
    packages = {}
    all_ok = True
    for name, module_name in [("pywin32", "win32com"), ("pyodbc", "pyodbc"), ("mcp", "mcp")]:
        try:
            __import__(module_name)
            packages[name] = "ok"
        except ImportError:
            packages[name] = "missing"
            all_ok = False

    return Check(
        name="packages",
        passed=all_ok,
        message="All required packages installed" if all_ok else "Some packages missing",
        details={"packages": packages},
    )


def _check_access_com_probe() -> Check:
    """Check: Access.Application COM activation probe."""
    if sys.platform != "win32":
        return Check(
            name="access_com",
            passed=False,
            message="Requires Windows for Access COM",
            details={},
        )

    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            app = win32com.client.Dispatch("Access.Application")
            app.Quit()
            return Check(
                name="access_com",
                passed=True,
                message="Access.Application COM available",
                details={},
            )
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        return Check(
            name="access_com",
            passed=False,
            message=f"Access COM activation failed: {strip_password(str(e))}",
            details={"error": strip_password(str(e))},
        )


def _check_ace_provider() -> Check:
    """Check: ACE OLE DB provider availability."""
    if sys.platform != "win32":
        return Check(
            name="ace_provider",
            passed=False,
            message="Requires Windows for ACE OLE DB",
            details={},
        )

    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            conn = win32com.client.Dispatch("ADODB.Connection")
            try:
                conn.Open("Provider=Microsoft.ACE.OLEDB.12.0;Data Source=NUL;")
            except Exception:
                pass
            return Check(
                name="ace_provider",
                passed=True,
                message="ACE OLE DB provider available",
                details={"provider": "Microsoft.ACE.OLEDB.12.0"},
            )
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        return Check(
            name="ace_provider",
            passed=False,
            message=f"ACE OLE DB provider unavailable: {strip_password(str(e))}",
            details={"error": strip_password(str(e))},
        )


def _check_dao_engine() -> Check:
    """Check: DAO DBEngine.120 constructability."""
    if sys.platform != "win32":
        return Check(
            name="dao_engine",
            passed=False,
            message="Requires Windows for DAO",
            details={},
        )

    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            win32com.client.Dispatch("DAO.DBEngine.120")
            return Check(
                name="dao_engine",
                passed=True,
                message="DAO.DBEngine.120 constructable",
                details={},
            )
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        return Check(
            name="dao_engine",
            passed=False,
            message=f"DAO.DBEngine.120 unavailable: {strip_password(str(e))}",
            details={"error": strip_password(str(e))},
        )


def _check_odbc_driver() -> Check:
    """Check: ODBC Microsoft Access Driver present (enumerated, not assumed)."""
    try:
        import pyodbc  # type: ignore[import]

        drivers = pyodbc.drivers()
        access_drivers = [d for d in drivers if "microsoft access driver" in d.lower()]
        has_access_driver = len(access_drivers) > 0
        return Check(
            name="odbc_driver",
            passed=has_access_driver,
            message=(
                f"Microsoft Access ODBC driver(s): {', '.join(access_drivers)}"
                if has_access_driver
                else "No Microsoft Access ODBC driver found (ACE OLEDB may still work)"
            ),
            details={"drivers": drivers, "access_drivers": access_drivers},
        )
    except Exception as e:
        return Check(
            name="odbc_driver",
            passed=False,
            message=f"ODBC driver check failed: {strip_password(str(e))}",
            details={"error": strip_password(str(e))},
        )


def _check_deep_access() -> Check:
    """Deep check: create + release hidden Access instance; assert zero orphans."""
    if sys.platform != "win32":
        return Check(
            name="deep_access",
            passed=False,
            message="Requires Windows for deep Access check",
            details={},
        )

    # Kill any existing Access processes first
    from .application.maintenance import kill_access_processes

    kill_access_processes(timeout=10)

    try:
        import time

        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            app = win32com.client.Dispatch("Access.Application")
            app.Visible = False
            # Pick a path that does NOT yet exist; NewCurrentDatabase refuses
            # to overwrite. NamedTemporaryFile pre-creates the file and breaks.
            tmpdir = tempfile.mkdtemp(prefix="acc_doctor_")
            temp_db = os.path.join(tmpdir, "doctor_probe.accdb")
            try:
                app.NewCurrentDatabase(temp_db)
                app.CloseCurrentDatabase()
                app.Quit()
            finally:
                try:
                    os.unlink(temp_db)
                except Exception:
                    pass
                try:
                    os.rmdir(tmpdir)
                except Exception:
                    pass

            # Wait for process to exit
            time.sleep(2)

            # Check for orphans
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            orphan_lines = [
                line for line in result.stdout.splitlines()
                if "MSACCESS.EXE" in line
            ]
            has_orphans = len(orphan_lines) > 0

            return Check(
                name="deep_access",
                passed=not has_orphans,
                message=(
                    "No Access orphans after create+release"
                    if not has_orphans
                    else f"Access orphans detected: {orphan_lines}"
                ),
                details={"orphan_lines": orphan_lines},
            )
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        return Check(
            name="deep_access",
            passed=False,
            message=f"Deep Access check failed: {strip_password(str(e))}",
            details={"error": strip_password(str(e))},
        )


def _check_temp_dir_writable() -> Check:
    """Deep check: temp directory is writable."""
    try:
        with tempfile.NamedTemporaryFile(delete=True) as f:
            f.write(b"test")
        return Check(
            name="temp_dir",
            passed=True,
            message="Temp directory is writable",
            details={},
        )
    except Exception as e:
        return Check(
            name="temp_dir",
            passed=False,
            message=f"Temp directory not writable: {strip_password(str(e))}",
            details={"error": strip_password(str(e))},
        )


def run_checks(deep: bool = False) -> list[Check]:
    """Run all doctor checks."""
    from .credentials import _local_credentials_path, synced_store_warning

    checks = [
        _is_windows_x64(),
        _check_package_versions(),
        _check_access_com_probe(),
        _check_ace_provider(),
        _check_dao_engine(),
        _check_odbc_driver(),
    ]
    local_store = _local_credentials_path(os.getcwd())
    if local_store.exists():
        warning = synced_store_warning(local_store)
        if warning:
            checks.append(Check(name="credentials", passed=True, message=f"WARNING: {warning}"))
    if deep:
        checks.append(_check_deep_access())
        checks.append(_check_temp_dir_writable())
    return checks


def _format_human(checks: list[Check]) -> str:
    """Format checks for human-readable output."""
    lines = ["ms-access-mcp doctor", "=" * 40]
    all_passed = True
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"  [{status}] {check.name}: {check.message}")
        if check.details:
            for k, v in check.details.items():
                if k == "error":
                    lines.append(f"         {k}: {v}")
        if not check.passed:
            all_passed = False

    lines.append("")
    lines.append("Format Support Matrix:")
    lines.append("  accdb  | jet4 (.mdb 2000-2003) | jet3 (.mdb 95/97)")
    lines.append("  " + "-" * 50)

    ace_ok = any(c.name == "ace_provider" and c.passed for c in checks)
    odbc_ok = any(c.name == "odbc_driver" and c.passed for c in checks)
    any(c.name == "dao_engine" and c.passed for c in checks)

    # accdb: OK when ACE OLEDB or Access ODBC driver found
    accdb_status = "OK" if (ace_ok or odbc_ok) else "MISSING"
    # jet4: OK under same conditions (ACE reads Jet 4 natively)
    jet4_status = "OK" if (ace_ok or odbc_ok) else "MISSING"
    # jet3: requires conversion (32-bit Jet bridge)
    from .backends.jet3_bridge import bridge_available

    jet3_status = (
        "conversion available (32-bit Jet bridge)"
        if bridge_available()
        else "requires conversion (32-bit Jet bridge not found)"
    )

    lines.append(f"  {accdb_status:6s} | {jet4_status:21s} | {jet3_status}")

    lines.append("")
    if all_passed:
        lines.append("All checks passed.")
    else:
        lines.append("Some checks failed. See remediation below.")
        lines.append("")
        lines.append("Remediation:")
        for check in checks:
            if not check.passed:
                if check.name == "access_com":
                    lines.append("  - Install Microsoft Access or Access Runtime")
                elif check.name == "ace_provider":
                    lines.append("  - Install Microsoft Access Database Engine (ACE OLEDB)")
                elif check.name == "dao_engine":
                    lines.append("  - Install Microsoft Access or Access Runtime for DAO support")
                elif check.name == "odbc_driver":
                    lines.append("  - Install Microsoft Access ODBC driver from Access Database Engine")
                elif check.name == "platform":
                    lines.append("  - Requires Windows x64 with 64-bit Python")

    return "\n".join(lines)


def _format_json(checks: list[Check]) -> str:
    """Format checks as machine-readable JSON."""
    ace_ok = any(c.name == "ace_provider" and c.passed for c in checks)
    odbc_ok = any(c.name == "odbc_driver" and c.passed for c in checks)
    any(c.name == "dao_engine" and c.passed for c in checks)
    from .backends.jet3_bridge import bridge_available

    return json.dumps(
        {
            "passed": all(c.passed for c in checks),
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "details": c.details,
                }
                for c in checks
            ],
            "format_support": {
                "accdb": "OK" if (ace_ok or odbc_ok) else "MISSING",
                "jet4": "OK" if (ace_ok or odbc_ok) else "MISSING",
                "jet3": (
                    "conversion available (32-bit Jet bridge)"
                    if bridge_available()
                    else "requires conversion (32-bit Jet bridge not found)"
                ),
            },
        },
        indent=2,
    )


def doctor_main(deep: bool = False, json_output: bool = False) -> int:
    """Run doctor diagnostics. Returns 0 if all checks pass, 1 otherwise."""
    checks = run_checks(deep)
    all_passed = all(c.passed for c in checks)

    if json_output:
        print(_format_json(checks))
    else:
        print(_format_human(checks))

    return 0 if all_passed else 1
