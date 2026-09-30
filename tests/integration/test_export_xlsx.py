"""Integration tests for xlsx export via DoCmd.TransferSpreadsheet and IISAM SQL.

PROBE VERDICT (2026-09-13):
HKLM\\SOFTWARE\\Microsoft\\Office\\16.0\\Access Connectivity Engine\\Engines\\Excel
is ABSENT — ACE Excel IISAM provider NOT registered.
Decision OVERTURNED (plan 028): DoCmd path is NOT viable on this host;
DoCmd writes 0-byte files without the engine (confirmed at plan 027 execution).
Both DoCmd tests are now probe-gated and auto-activate when provider is registered.
SQL fallback integration test is skipif(probe=False).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(
        __import__("os").name != "nt",
        reason="Windows-only test",
    ),
    pytest.mark.com_integration,
]


def _excel_provider_available() -> bool:
    """Return True if the ACE Excel IISAM provider is registered (Windows only)."""
    if os.name != "nt":
        return False
    from ms_access_mcp.application.xlsx_capability import IisamProviderProbe
    return IisamProviderProbe.is_excel_provider_registered()


class TestExportXlsx:
    """Test xlsx export end-to-end."""

    @pytest.mark.skipif(
        not _excel_provider_available(),
        reason="ACE Excel IISAM provider not registered (DoCmd xlsx export writes 0 bytes without it)",
    )
    def test_docmd_export_roundtrip(self, temp_db_copy: str) -> None:
        """DoCmd.TransferSpreadsheet creates a valid xlsx; row count verified."""
        pytest.importorskip("win32com")
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends.access import AccessAdapter
        from ms_access_mcp.errors import Ok

        # Kill any stale Access processes first
        subprocess.run(
            ["taskkill", "/F", "/IM", "MSACCESS.EXE"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        time.sleep(1)

        adapter = AccessAdapter(temp_db_copy)
        adapter.connect(temp_db_copy)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                db = DatabaseUseCases(backend="access", readonly=False, allowed_dirs=[Path(tmpdir)])
                db._connections["test_conn"] = adapter

                xlsx_path = str(Path(tmpdir) / "exported.xlsx")

                # Create the output file so is_path_allowed's p.exists() check passes
                with Path(xlsx_path).open("wb"):
                    pass

                result = db.export_data(
                    "Users",
                    xlsx_path,
                    format="xlsx",
                    sheet_name="Data",
                    connection_name="test_conn",
                )

                assert isinstance(result, Ok), f"export_data failed: {result.message}"
                assert result.rowcount >= 0
                assert Path(xlsx_path).exists(), "xlsx file was not created"
                assert Path(xlsx_path).stat().st_size > 0, "xlsx file is empty"

        finally:
            adapter.disconnect()

        # Verify no orphan processes
        result_proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "MSACCESS.EXE" not in result_proc.stdout, "Access orphan detected after test"

    @pytest.mark.skipif(
        not _excel_provider_available(),
        reason="ACE Excel IISAM provider not registered (DoCmd xlsx export writes 0 bytes without it)",
    )
    def test_docmd_export_custom_sheet_name(self, temp_db_copy: str) -> None:
        """Custom sheet name is accepted; file is created successfully."""
        pytest.importorskip("win32com")
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends.access import AccessAdapter
        from ms_access_mcp.errors import Ok

        subprocess.run(
            ["taskkill", "/F", "/IM", "MSACCESS.EXE"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        time.sleep(1)

        adapter = AccessAdapter(temp_db_copy)
        adapter.connect(temp_db_copy)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                db = DatabaseUseCases(backend="access", readonly=False, allowed_dirs=[Path(tmpdir)])
                db._connections["test_conn"] = adapter

                xlsx_path = str(Path(tmpdir) / "exported_custom.xlsx")
                with Path(xlsx_path).open("wb"):
                    pass

                result = db.export_data(
                    "Users",
                    xlsx_path,
                    format="xlsx",
                    sheet_name="MySheet",
                    connection_name="test_conn",
                )

                assert isinstance(result, Ok), f"export_data failed: {result.message}"
                assert Path(xlsx_path).exists()

        finally:
            adapter.disconnect()

    def test_sql_fallback_skipif_no_provider(self) -> None:
        """SQL fallback integration: skipped on hosts without ACE Excel provider."""
        pytest.skip(
            reason=f"ACE Excel IISAM provider not registered on this host "
            f"(probe={_excel_provider_available()})",
        )
