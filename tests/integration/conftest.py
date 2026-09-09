"""Integration test conftest — temp_db_copy fixture and platform skips."""

from __future__ import annotations

import os
import shutil
import tempfile
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


def _has_db_file() -> bool:
    fixture_dir = Path(__file__).parent / "fixtures"
    test_db = fixture_dir / "test_db.accdb"
    return test_db.exists()


# Fixture database path
FIXTURE_DB = Path(__file__).parent / "fixtures" / "test_db.accdb"
TEST_DB = str(FIXTURE_DB) if FIXTURE_DB.exists() else ""

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
        import time
        time.sleep(0.25)
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
