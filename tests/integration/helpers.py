"""Integration test helpers — platform checks and skip conditions."""

from __future__ import annotations

import os
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"
REPO_FIXTURE_DB = FIXTURE_DIR / "test_db.accdb"

_raw = os.environ.get("ACCESS_TEST_DB")
if _raw:
    TEST_DB = _raw.strip('"').strip("'")
elif REPO_FIXTURE_DB.exists():
    TEST_DB = str(REPO_FIXTURE_DB)
else:
    TEST_DB = ""

IS_WINDOWS = os.name == "nt"


def has_pywin32() -> bool:
    try:
        import pythoncom  # noqa: F401
        import win32com  # noqa: F401
        return True
    except ImportError:
        return False


def has_odbc_driver() -> bool:
    try:
        import pyodbc  # type: ignore[import]

        access_keywords = ["access", "mdb", "ace", "mdbtools", "mdbodbc"]
        return any(any(kw in d.lower() for kw in access_keywords) for d in pyodbc.drivers())
    except ImportError:
        return False


def has_db_file() -> bool:
    return bool(TEST_DB) and Path(TEST_DB).exists()
