"""Integration test conftest — temp_db_copy fixture and platform skips."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import pytest

# Skip conditions (defined inline to avoid import path issues)
IS_WINDOWS = os.name == "nt"


def _has_pywin32() -> bool:
    try:
        import pythoncom  # noqa: F401
        import win32com  # noqa: F401
        return True
    except ImportError:
        return False


def _resolve_test_db() -> str:
    """Resolve the canonical .accdb fixture path.

    Resolution order:
    1. ``ACCESS_TEST_DB`` environment variable (absolute path to .accdb)
    2. ``tests/fixtures/test_db.accdb`` (project root)
    3. ``tests/integration/fixtures/test_db.accdb`` (legacy)
    """
    raw = os.environ.get("ACCESS_TEST_DB")
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.exists():
            return str(candidate)
    for rel in ("tests/fixtures/test_db.accdb", "fixtures/test_db.accdb"):
        p = Path(__file__).resolve().parents[1] / rel
        if p.exists():
            return str(p)
    return ""


def _has_db_file() -> bool:
    return bool(_resolve_test_db())


TEST_DB = _resolve_test_db()

skip_unless_windows = pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only test")
skip_unless_pywin32 = pytest.mark.skipif(not _has_pywin32(), reason="Requires pywin32")
skip_unless_db = pytest.mark.skipif(not _has_db_file(), reason="No test fixture found")

pytestmark = [skip_unless_windows, skip_unless_pywin32, skip_unless_db]


@pytest.fixture
def temp_db_copy():
    """Create a temporary copy of the test database for destructive tests.

    Yields the path to the cloned .accdb file. The clone is a file-level
    copy (shutil.copy2) so every test gets a pristine isolated database.
    The master fixture at TEST_DB is never modified.
    """
    if not TEST_DB:
        pytest.skip("ACCESS_TEST_DB not set and no fixture found")

    src_path = Path(TEST_DB)
    if not src_path.exists():
        pytest.skip(f"Fixture database not found: {src_path}")

    tmpdir = tempfile.mkdtemp(prefix="acc_test_")
    clone_path = Path(tmpdir) / src_path.name

    try:
        shutil.copy2(src_path, clone_path)
        yield str(clone_path)
    finally:
        time.sleep(0.25)
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def _has_access() -> bool:
    """Check if Microsoft Access is installed (msaccess.exe exists)."""
    if not IS_WINDOWS:
        return False
    import shutil
    return shutil.which("msaccess.exe") is not None


skip_unless_access = pytest.mark.skipif(not _has_access(), reason="Requires Microsoft Access (msaccess.exe)")


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


@pytest.fixture
def jet4_mdb_fixture():
    """Create a real Jet 4 .mdb via DAO CreateDatabase(dbVersion40).

    Yields a dict with:
        - path: absolute path to the created .mdb file
        - cleanup: callable to delete the file

    The database is created with one table "T1" containing two text rows
    to support read-back verification.

    Skips if Access is not installed or COM is unavailable.
    """
    if not IS_WINDOWS or not _has_ace_dao():
        pytest.skip("Requires ACE DAO (DAO.DBEngine.120)")

    try:
        import pythoncom
        import win32com.client  # noqa: F401
    except ImportError:
        pytest.skip("Requires pywin32")

    tmpdir = tempfile.mkdtemp(prefix="jet4_mdb_")
    mdb_path = str(Path(tmpdir) / "test_jet4.mdb")

    try:
        pythoncom.CoInitialize()
        try:
            engine = win32com.client.Dispatch("DAO.DBEngine.120")
            # DAO.CreateDatabase(path, locale, version)
            # dbVersion40 = 64 → Jet 4 format
            new_db = engine.CreateDatabase(mdb_path, ";LANGID=0x0409;CP=1252;COUNTRY=0", 64)
            new_db.Close()

            # Populate with a simple table via DAO
            dao_db = engine.OpenDatabase(mdb_path)
            try:
                dao_db.Execute(
                    "CREATE TABLE [T1] ([id] INTEGER NOT NULL, [name] TEXT(100) NOT NULL)",
                    128,  # dbFailOnError
                )
                dao_db.Execute(
                    "INSERT INTO [T1] ([id], [name]) VALUES (1, 'alpha')",
                    128,
                )
                dao_db.Execute(
                    "INSERT INTO [T1] ([id], [name]) VALUES (2, 'beta')",
                    128,
                )
            finally:
                dao_db.Close()
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        pytest.skip(f"Failed to create Jet 4 .mdb via DAO: {e}")

    yield {"path": mdb_path, "cleanup": lambda: shutil.rmtree(tmpdir, ignore_errors=True)}

    # Cleanup
    time.sleep(0.25)
    shutil.rmtree(tmpdir, ignore_errors=True)
