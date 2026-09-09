"""Integration test conftest — temp_db_copy fixture and platform skips."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
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
