"""Opt-in isolated deadline/cancellation qualification for documentation inspection (T4b).

This suite proves the supervised documentation runtime with **real** owned children:

* the real production worker succeeds on a real self-built ``.accdb`` fixture through both
  the public CLI subprocess and the public MCP tool;
* a private fixture child (``tests/helpers/documentation_deadline_worker.py``) opens a
  disposable fixture read-only and then deliberately blocks (both during source-open setup
  and after the open), and the supervisor must kill/reap exactly that child, leave the
  source hash unchanged, leave no lock or surviving worker, and leave a parent session
  usable;
* a path outside the allowed directories is refused *inside* the child, never in the
  parent.

Run explicitly on Windows with ACE DAO and pywin32::

    $env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
    .venv\\Scripts\\python.exe -m pytest tests\\integration\\test_documentation_deadline.py -q

Without ``ACCESS_DOCUMENTATION_FIXTURES_TEST=1`` the module skips cleanly. All scratch is
under a self-owned ``db/fixtures/e2e-<uuid>`` tree; the canonical fixtures and Northwind
are never touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ms_access_mcp.application.database import DatabaseUseCases, Err
from ms_access_mcp.application.documentation_runtime import DocumentationRuntime
from ms_access_mcp.errors import AccessMcpError
from tests.integration.conftest import _has_ace_dao

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "db" / "fixtures"
HELPER = ROOT / "tests" / "helpers" / "documentation_deadline_worker.py"
OPT_IN_ENV = "ACCESS_DOCUMENTATION_FIXTURES_TEST"
CLI_TIMEOUT_SECONDS = 180

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.skipif(not _has_ace_dao(), reason="Requires ACE DAO (DAO.DBEngine.120)"),
    pytest.mark.skipif(
        os.environ.get(OPT_IN_ENV) != "1",
        reason=f"Set {OPT_IN_ENV}=1 to run the isolated documentation-deadline suite",
    ),
    pytest.mark.com_integration,
]


def _venv_python() -> str:
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lock_files(directory: Path, stem: str) -> list[str]:
    return sorted(path.name for path in directory.iterdir() if path.name.startswith(f"{stem}."))


def _create_minimal_database(path: Path) -> None:
    """Create a real single-table ACE ``.accdb`` fixture (no queries, no code)."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        engine = win32com.client.Dispatch("DAO.DBEngine.120")
        database = engine.CreateDatabase(str(path), ";LANGID=0x0409")
        try:
            database.Execute("CREATE TABLE Items (Id LONG NOT NULL, Name TEXT(50), PRIMARY KEY (Id))")
            database.Execute("INSERT INTO Items (Id, Name) VALUES (1, 'one')")
        finally:
            database.Close()
    finally:
        pythoncom.CoUninitialize()


def _remove_self_created_tree(output_dir: Path, created_fixture_root: bool) -> None:
    assert output_dir.parent == FIXTURE_ROOT
    assert output_dir.name.startswith("e2e-")
    shutil.rmtree(output_dir, ignore_errors=True)
    if created_fixture_root:
        try:
            FIXTURE_ROOT.rmdir()
        except OSError:
            pass


@pytest.fixture(scope="session")
def deadline_build() -> Iterator[dict[str, Any]]:
    """Build one disposable single-table ``.accdb`` under a fresh self-owned e2e tree."""
    output_dir = FIXTURE_ROOT / f"e2e-{uuid.uuid4().hex}"
    created_fixture_root = not FIXTURE_ROOT.exists()
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "deadline.accdb"
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        _create_minimal_database(db_path)
        yield {"directory": output_dir, "db": db_path, "sha256": _sha256(db_path)}
    finally:
        _remove_self_created_tree(output_dir, created_fixture_root)


def _fixture_launcher(mode: str, records: list[subprocess.Popen[bytes]]):
    def launch() -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [_venv_python(), str(HELPER), "--mode", mode],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        records.append(process)
        return process

    return launch


# ---------------------------------------------------------------------------
# 1. Real production worker success through CLI and MCP
# ---------------------------------------------------------------------------


def test_real_worker_success_through_cli_and_mcp(deadline_build: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    directory = deadline_build["directory"]
    db_path = deadline_build["db"]
    before = _sha256(db_path)

    env = dict(os.environ)
    env["ACCESS_MCP_ALLOWED_DIRS"] = str(directory)
    completed = subprocess.run(
        [
            _venv_python(),
            "-m",
            "ms_access_mcp.cli",
            "documentation",
            "inspect",
            "--db",
            str(db_path),
            "--backend",
            "dao",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
        check=False,
        env=env,
        cwd=ROOT,
    )
    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True
    assert [table["name"] for table in envelope["data"]["data"]["tables"]] == ["Items"]

    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(directory))
    from ms_access_mcp.mcp_tools import analysis

    result = analysis.documentation_inspect(str(db_path))
    payload = json.loads(result.content[0].text)
    assert result.isError is False
    assert payload["success"] is True
    assert payload["data"]["source"]["backend_identity"] == "DAO"
    assert [table["name"] for table in payload["data"]["data"]["tables"]] == ["Items"]

    assert _sha256(db_path) == before == deadline_build["sha256"]
    assert _lock_files(directory, "deadline.accdb") == []


# ---------------------------------------------------------------------------
# 2. Deadline kills and reaps the owned child
# ---------------------------------------------------------------------------


def test_deadline_after_open_kills_reaps_and_preserves_parent_session(deadline_build: dict[str, Any]) -> None:
    directory = deadline_build["directory"]
    db_path = deadline_build["db"]
    before = _sha256(db_path)
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(
        launcher=_fixture_launcher("block_after_open", records),
        deadline_seconds=1.0,
        cleanup_grace_seconds=5.0,
    )
    parent = DatabaseUseCases(backend="dao", readonly=True, allowed_dirs=[directory])
    opened = parent.open_database(str(db_path))
    assert not isinstance(opened, Err), getattr(opened, "message", opened)
    try:
        with pytest.raises(AccessMcpError, match="deadline"):
            runtime.inspect(
                str(db_path),
                backend="dao",
                allowed_dirs=[directory],
                tables=None,
                queries=None,
            )
        assert len(records) == 1
        assert records[0].poll() is not None  # the exact owned child was reaped
        # The parent's own session is untouched and still usable after the kill.
        tables = parent.get_tables()
        assert not isinstance(tables, Err), tables
        assert {table["name"] for table in tables.data} == {"Items"}
        assert _sha256(db_path) == before
    finally:
        parent.close_database()
    assert _lock_files(directory, "deadline.accdb") == []


def test_deadline_during_source_open_setup_kills_and_reaps_owned_child(deadline_build: dict[str, Any]) -> None:
    directory = deadline_build["directory"]
    db_path = deadline_build["db"]
    before = _sha256(db_path)
    records: list[subprocess.Popen[bytes]] = []
    runtime = DocumentationRuntime(
        launcher=_fixture_launcher("block_before_open", records),
        deadline_seconds=1.0,
        cleanup_grace_seconds=5.0,
    )

    with pytest.raises(AccessMcpError, match="deadline"):
        runtime.inspect(str(db_path), backend="dao", allowed_dirs=[directory], tables=None, queries=None)

    assert len(records) == 1
    assert records[0].poll() is not None
    assert _sha256(db_path) == before == deadline_build["sha256"]
    assert _lock_files(directory, "deadline.accdb") == []


# ---------------------------------------------------------------------------
# 3. Child-side policy enforcement
# ---------------------------------------------------------------------------


def test_disallowed_path_is_refused_inside_the_owned_child(deadline_build: dict[str, Any]) -> None:
    db_path = deadline_build["db"]
    before = _sha256(db_path)
    runtime = DocumentationRuntime(deadline_seconds=60.0)

    with pytest.raises(AccessMcpError, match="Path not allowed"):
        runtime.inspect(str(db_path), backend="dao", allowed_dirs=[], tables=None, queries=None)

    assert _sha256(db_path) == before == deadline_build["sha256"]
    assert _lock_files(deadline_build["directory"], "deadline.accdb") == []
