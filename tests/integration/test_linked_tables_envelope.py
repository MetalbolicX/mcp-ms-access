"""Live CLI pin for linked-table result shape and row count."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.com_integration
FIXTURE_DB = Path(__file__).parent.parent / "fixtures" / "test_db.accdb"


def test_links_list_json_has_direct_list_and_matching_rowcount() -> None:
    if not FIXTURE_DB.exists():
        pytest.skip("No Access fixture database is available")

    with tempfile.TemporaryDirectory(prefix="linked_tables_envelope_") as directory:
        scratch_dir = Path(directory)
        db_path = scratch_dir / FIXTURE_DB.name
        shutil.copy2(FIXTURE_DB, db_path)
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "ms_access_mcp.cli",
                "links",
                "list",
                "--db",
                str(db_path),
                "--backend",
                "dao",
                "--json",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "ACCESS_MCP_ALLOWED_DIRS": str(scratch_dir)},
            timeout=90,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr
        result: dict[str, Any] = json.loads(completed.stdout.strip().splitlines()[-1])

        assert result.get("success") is True, result
        assert isinstance(result.get("data"), list), result
        assert result["rowcount"] == len(result["data"])
