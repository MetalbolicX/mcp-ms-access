"""Live Access coverage for the VBA write opt-in on an isolated database copy."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from ms_access_mcp import cli


def _vba_command(action: str, module: str, db_path: Path) -> list[str]:
    return [
        "automation",
        "vba",
        action,
        "--module",
        module,
        "--db",
        str(db_path),
        "--allow-write",
        "--yes",
        "--json",
    ]


@pytest.mark.com_integration
def test_create_module_requires_opt_in_and_allows_envelope_when_enabled(
    temp_db_copy: str, monkeypatch, capsys
):
    """The opt-in blocks writes by default and lets an enabled create report success.

    Envelope-level only: cross-session persistence is pinned separately.
    """
    pytest.importorskip("win32com")

    db_path = Path(temp_db_copy)
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(db_path.parent))
    module_name = f"McpOptIn{uuid.uuid4().hex[:8]}"
    command = _vba_command("create-module", module_name, db_path)

    monkeypatch.delenv("ACCESS_MCP_ENABLE_VBA_WRITES", raising=False)
    disabled_exit = cli.main(command)
    disabled = json.loads(capsys.readouterr().out)
    assert disabled_exit == 1
    assert disabled["success"] is False
    assert "ACCESS_MCP_ENABLE_VBA_WRITES=1" in disabled["error"]

    monkeypatch.setenv("ACCESS_MCP_ENABLE_VBA_WRITES", "1")
    enabled_exit = cli.main(command)
    enabled = json.loads(capsys.readouterr().out)
    assert enabled_exit == 0, enabled
    assert enabled["success"] is True


@pytest.mark.com_integration
def test_created_module_persists_across_sessions(temp_db_copy: str, monkeypatch, capsys):
    """A created module must still be listed by a fresh session (persistence)."""
    pytest.importorskip("win32com")

    db_path = Path(temp_db_copy)
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(db_path.parent))
    monkeypatch.setenv("ACCESS_MCP_ENABLE_VBA_WRITES", "1")
    module_name = f"McpPersist{uuid.uuid4().hex[:8]}"

    create_exit = cli.main(_vba_command("create-module", module_name, db_path))
    created = json.loads(capsys.readouterr().out)
    assert create_exit == 0, created

    list_exit = cli.main(["automation", "vba", "modules", "--db", str(db_path), "--json"])
    listed = json.loads(capsys.readouterr().out)
    assert list_exit == 0, listed
    names = [entry.get("name") for entry in listed.get("data", [])]
    assert module_name in names


@pytest.mark.com_integration
def test_set_vba_code_persists_across_sessions(temp_db_copy: str, monkeypatch, capsys):
    """Code set in one session must be readable from a fresh session."""
    pytest.importorskip("win32com")

    db_path = Path(temp_db_copy)
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(db_path.parent))
    monkeypatch.setenv("ACCESS_MCP_ENABLE_VBA_WRITES", "1")
    module_name = f"McpCode{uuid.uuid4().hex[:8]}"
    marker = f"PersistMarker{uuid.uuid4().hex}"
    code = f"Public Sub {marker}()\nEnd Sub"

    # Seed an already-persisted component so this pin exercises rewriting an
    # existing CodeModule rather than LoadFromText creating a new component.
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        app = win32com.client.Dispatch("Access.Application")
        app.OpenCurrentDatabase(str(db_path))
        component = app.VBE.VBProjects(1).VBComponents.Add(1)
        component.Name = module_name
        app.DoCmd.Save(5, module_name)
        app.Quit(2)
    finally:
        pythoncom.CoUninitialize()

    set_exit = cli.main([
        "automation", "vba", "set-code", "--module", module_name,
        "--code", code, "--db", str(db_path), "--allow-write", "--yes", "--json",
    ])
    result = json.loads(capsys.readouterr().out)
    assert set_exit == 0, result

    get_exit = cli.main([
        "automation", "vba", "get-code", "--module", module_name,
        "--db", str(db_path), "--json",
    ])
    fetched = json.loads(capsys.readouterr().out)
    assert get_exit == 0, fetched
    assert marker in str(fetched.get("data", fetched))
