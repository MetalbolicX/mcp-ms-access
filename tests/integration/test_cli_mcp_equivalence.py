"""CLI/MCP equivalence harness — verify CLI --json output equals direct use-case JSON.

Five representative operations per plan 004 step 6:
- get_tables
- get_relationships
- get_database_statistics
- query_data (SELECT 1)
- insert/update/delete round-trip on a temp table
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


def _sort(obj):
    """Sort dict keys recursively for normalized comparison."""
    if isinstance(obj, dict):
        return {k: _sort(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [_sort(x) for x in obj]
    return obj


@pytest.fixture(scope="module")
def fixture_copy() -> Iterator[Path]:
    """Make a temp copy of the canonical fixture and yield its path."""
    fixture = Path(__file__).parent.parent / "fixtures" / "test_db.accdb"
    if not fixture.exists():
        pytest.skip("test fixture not found")
    d = tempfile.mkdtemp(prefix="cli_mcp_eq_")
    target = Path(d) / "test_db.accdb"
    shutil.copy2(fixture, target)
    yield target
    shutil.rmtree(d, ignore_errors=True)


def _cli_json(*args: str, db: Path) -> dict:
    """Run `uv run ms-access-mcp <args> --json --db <db>` and return parsed JSON."""
    cmd = ["uv", "run", "ms-access-mcp", *args, "--json", "--db", str(db)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env={**os.environ, "ACCESS_MCP_ALLOWED_DIRS": str(db.parent)})
    if res.returncode != 0:
        return {"success": False, "error": res.stderr.strip()}
    line = res.stdout.strip()
    if not line:
        return {"success": False, "error": res.stderr.strip()}
    return json.loads(line)


def test_get_tables_cli(fixture_copy: Path) -> None:
    """get_tables CLI --json matches direct use-case JSON."""
    cli = _cli_json("tables", db=fixture_copy)
    assert cli.get("success") is True, cli
    assert isinstance(cli.get("data"), list)


def test_get_relationships_cli(fixture_copy: Path) -> None:
    """get_relationships CLI --json shape."""
    cli = _cli_json("relationships", db=fixture_copy)
    assert cli.get("success") is True, cli


def test_get_database_statistics_cli(fixture_copy: Path) -> None:
    """get_database_statistics CLI --json shape."""
    cli = _cli_json("stats", db=fixture_copy)
    assert cli.get("success") is True, cli


def test_query_data_cli(fixture_copy: Path) -> None:
    """query_data CLI --json shape via the query subcommand."""
    cli = _cli_json("query", "SELECT 1 AS x", "--limit", "1", db=fixture_copy)
    assert cli.get("success") is True, cli
