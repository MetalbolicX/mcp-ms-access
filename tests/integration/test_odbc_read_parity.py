"""Live DAO/ODBC read-envelope parity pins.

ODBC can derive table/query counts, file metadata, column metadata, nullability,
size, and primary keys. It cannot inspect forms, reports, macros, Access
modules, zero-length-string policy, or the Access application version; the
model's existing zero/empty defaults represent those gaps without inventing
metadata. ODBC table record counts also retain the model's default of zero.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pyodbc
import pytest

pytestmark = pytest.mark.com_integration
FIXTURE_DB = Path(__file__).parent.parent / "fixtures" / "test_db.accdb"


def _run_cli(db_path: Path, backend: str, *args: str) -> dict[str, Any]:
    env = {
        **os.environ,
        "ACCESS_MCP_ALLOWED_DIRS": str(db_path.parent),
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "ms_access_mcp.cli",
            *args,
            "--db",
            str(db_path),
            "--backend",
            backend,
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    output = completed.stdout.strip()
    try:
        result = json.loads(output.splitlines()[-1]) if output else {}
    except (IndexError, json.JSONDecodeError):
        result = {"success": False, "error": completed.stderr.strip() or output}
    if completed.returncode and result.get("success") is not False:
        result = {"success": False, "error": completed.stderr.strip() or output}
    return result


def test_odbc_stats_tables_and_schema_match_dao_envelope_keys() -> None:
    if not FIXTURE_DB.exists():
        pytest.skip("No Access fixture database is available")
    if not any("Microsoft Access Driver (*.mdb, *.accdb)" in driver for driver in pyodbc.drivers()):
        pytest.skip("Microsoft Access ODBC driver is unavailable")

    with tempfile.TemporaryDirectory(prefix="odbc_read_parity_") as directory:
        scratch_dir = Path(directory)
        db_path = scratch_dir / FIXTURE_DB.name
        shutil.copy2(FIXTURE_DB, db_path)

        dao_stats = _run_cli(db_path, "dao", "stats")
        odbc_stats = _run_cli(db_path, "odbc", "stats")
        dao_tables = _run_cli(db_path, "dao", "tables")
        odbc_tables = _run_cli(db_path, "odbc", "tables")

        assert dao_stats.get("success") is True, dao_stats
        assert odbc_stats.get("success") is True, odbc_stats
        assert set(odbc_stats["data"]) == set(dao_stats["data"])
        for section in ("objects", "file", "system"):
            assert set(odbc_stats["data"][section]) == set(dao_stats["data"][section])

        assert dao_tables.get("success") is True, dao_tables
        assert odbc_tables.get("success") is True, odbc_tables
        dao_by_name = {table["name"]: table for table in dao_tables["data"]}
        odbc_by_name = {table["name"]: table for table in odbc_tables["data"]}
        assert odbc_by_name.keys() == dao_by_name.keys()
        for table_name, odbc_table in odbc_by_name.items():
            dao_table = dao_by_name[table_name]
            assert set(odbc_table) == set(dao_table)
            assert len(odbc_table["fields"]) == len(dao_table["fields"])
            for odbc_field, dao_field in zip(odbc_table["fields"], dao_table["fields"], strict=True):
                assert set(odbc_field) == set(dao_field)

        table_name = next(iter(dao_by_name))
        dao_schema = _run_cli(db_path, "dao", "schema", table_name)
        odbc_schema = _run_cli(db_path, "odbc", "schema", table_name)
        assert dao_schema.get("success") is True, dao_schema
        assert odbc_schema.get("success") is True, odbc_schema
        assert set(odbc_schema["data"]) == set(dao_schema["data"])
