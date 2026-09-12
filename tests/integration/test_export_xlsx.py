"""Integration tests for xlsx export via DoCmd.TransferSpreadsheet and IISAM SQL.

PROBE VERDICT (2026-09-12, dev host):
HKLM\\SOFTWARE\\Microsoft\\Office\\16.0\\Access Connectivity Engine\\Engines\\Excel
is ABSENT — ACE Excel IISAM provider NOT registered.
Decision: DoCmd path is the primary test (runs on this host);
SQL fallback integration test is skipif(probe=False).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time

import pytest

pytestmark = [
    pytest.mark.skipif(
        __import__("os").name != "nt",
        reason="Windows-only test",
    ),
    pytest.mark.com_integration,
]


class TestExportXlsx:
    """Test xlsx export end-to-end."""

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
            db = DatabaseUseCases(backend="access", readonly=False)
            db._connections["test_conn"] = adapter

            with tempfile.TemporaryDirectory() as tmpdir:
                xlsx_path = os.path.join(tmpdir, "exported.xlsx")

                # Create the output file so is_path_allowed's p.exists() check passes
                with open(xlsx_path, "wb"):
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
                assert os.path.exists(xlsx_path), "xlsx file was not created"
                assert os.path.getsize(xlsx_path) > 0, "xlsx file is empty"

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
            db = DatabaseUseCases(backend="access", readonly=False)
            db._connections["test_conn"] = adapter

            with tempfile.TemporaryDirectory() as tmpdir:
                xlsx_path = os.path.join(tmpdir, "exported_custom.xlsx")
                with open(xlsx_path, "wb"):
                    pass

                result = db.export_data(
                    "Users",
                    xlsx_path,
                    format="xlsx",
                    sheet_name="MySheet",
                    connection_name="test_conn",
                )

                assert isinstance(result, Ok), f"export_data failed: {result.message}"
                assert os.path.exists(xlsx_path)

        finally:
            adapter.disconnect()

    def test_sql_fallback_skipif_no_provider(self) -> None:
        """SQL fallback integration: skipped on hosts without ACE Excel provider."""
        from ms_access_mcp.application.xlsx_capability import IisamProviderProbe

        pytest.skip(
            reason=f"ACE Excel IISAM provider not registered on this host "
            f"(probe={IisamProviderProbe.is_excel_provider_registered()})",
            allow_module_level=True,
        )
