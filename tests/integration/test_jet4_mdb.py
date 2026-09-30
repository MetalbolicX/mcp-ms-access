"""Integration tests for Jet 4 .mdb support — DAO creation and ODBC read-back."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import pytest

# Skip conditions: these tests require Windows + Access + ODBC driver
IS_WINDOWS = os.name == "nt"


def _has_pywin32() -> bool:
    try:
        import pythoncom  # noqa: F401
        import win32com  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _has_ace_dao() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            win32com.client.Dispatch("DAO.DBEngine.120")
        finally:
            pythoncom.CoUninitialize()
        return True
    except Exception:
        return False


def _has_ace_odbc_driver() -> bool:
    """Check if the ACE ODBC driver can be loaded."""
    if not IS_WINDOWS:
        return False
    try:
        import pyodbc
        drivers = pyodbc.drivers()
        return any("ACE" in d or "Access" in d for d in drivers)
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only test"),
    pytest.mark.skipif(not _has_pywin32(), reason="Requires pywin32"),
    pytest.mark.skipif(not _has_ace_dao(), reason="Requires ACE DAO (DAO.DBEngine.120)"),
]


@pytest.mark.odbc_integration
class TestJet4MdbIntegration:
    """Verify Jet 4 .mdb round-trip: DAO create → ODBC open → data read-back."""

    def test_jet4_mdb_fixture_read_back(self, jet4_mdb_fixture):
        """Create Jet4 .mdb with DAO, open via ODBC, read rows back."""
        import pyodbc

        mdb_path = jet4_mdb_fixture["path"]
        assert Path(mdb_path).exists(), "Jet 4 .mdb was not created"

        # Connect via ODBC using ACE driver
        conn_str = f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={mdb_path};"
        conn = pyodbc.connect(conn_str, timeout=10)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM [T1] ORDER BY id")
            rows = cursor.fetchall()
            assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
            assert rows[0][0] == 1
            assert rows[0][1] == "alpha"
            assert rows[1][0] == 2
            assert rows[1][1] == "beta"
        finally:
            conn.close()

    def test_jet4_mdb_fixture_creates_correct_format(self, jet4_mdb_fixture):
        """Verify the created .mdb is detected as JET4 format."""
        from ms_access_mcp.backends.format_detector import AccessFormat, detect_access_format

        mdb_path = jet4_mdb_fixture["path"]
        fmt = detect_access_format(mdb_path)
        assert fmt == AccessFormat.JET4, f"Expected JET4, got {fmt}"


@pytest.mark.odbc_integration
class TestJet4MdbRecovery:
    """Verify salvage/recovery accepts .mdb targets."""

    def test_salvage_accepts_mdb_target(self, jet4_mdb_fixture, tmp_path):
        """salvage_database should not reject .mdb files."""
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        mdb_path = jet4_mdb_fixture["path"]

        uc = MaintenanceUseCases(
            allowed_dirs=[tmp_path, Path(mdb_path).parent],
        )

        # Run salvage with a fresh copy to avoid corrupting the fixture
        import shutil

        salvage_target = tmp_path / "salvage_test.mdb"
        shutil.copy2(mdb_path, str(salvage_target))

        # confirm=True to bypass gate
        result = uc.salvage_database(str(salvage_target), confirm=True)

        # Should succeed (not reject .mdb format)
        from ms_access_mcp.errors import Err
        if isinstance(result, Err):
            pytest.fail(f"salvage_database rejected .mdb: {result.message}")
