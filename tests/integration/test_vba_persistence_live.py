"""Live persistence pins for Access macros and VBA objects."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from ms_access_mcp import cli


@pytest.mark.com_integration
def test_created_macro_persists_across_sessions(temp_db_copy: str, monkeypatch, capsys):
    """A macro created through SaveAsText/LoadFromText survives a fresh session."""
    pytest.importorskip("win32com")

    db_path = Path(temp_db_copy)
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(db_path.parent))
    monkeypatch.setenv("ACCESS_MCP_ENABLE_VBA_WRITES", "1")
    macro_name = f"McpPersist{uuid.uuid4().hex[:8]}"

    create_exit = cli.main([
        "automation", "macros", "create", "--name", macro_name,
        "--db", str(db_path), "--allow-write", "--yes", "--json",
    ])
    created = json.loads(capsys.readouterr().out)
    assert create_exit == 0, created

    list_exit = cli.main(["automation", "macros", "list", "--db", str(db_path), "--json"])
    listed = json.loads(capsys.readouterr().out)
    assert list_exit == 0, listed
    names = [entry.get("name") for entry in listed.get("data", [])]
    assert macro_name in names
