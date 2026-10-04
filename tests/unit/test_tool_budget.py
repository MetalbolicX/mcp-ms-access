from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def tool_budget_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "tool_budget.py"
    spec = importlib.util.spec_from_file_location("tool_budget", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_failure_exits_nonzero(monkeypatch):
    from ms_access_mcp import server

    tool_budget = tool_budget_module()

    def raise_build_error(profile):
        raise RuntimeError("build failed")

    monkeypatch.setattr(server, "build_server", raise_build_error)
    with pytest.raises(SystemExit) as exc_info:
        sys.exit(tool_budget.main())
    assert exc_info.value.code == 1


def test_zero_tool_core_profile_exits_nonzero(monkeypatch):
    from ms_access_mcp import server

    class EmptyServer:
        _tool_manager = type("ToolManager", (), {"_tools": []})()

    tool_budget = tool_budget_module()
    monkeypatch.setattr(server, "build_server", lambda profile: EmptyServer())

    with pytest.raises(SystemExit) as exc_info:
        sys.exit(tool_budget.main())
    assert exc_info.value.code == 1


def test_profile_list_includes_all_eight_profiles():
    tool_budget = tool_budget_module()
    assert set(tool_budget.PROFILES) == {
        "core",
        "automation",
        "maintenance",
        "versioning",
        "ddl",
        "analysis",
        "migration",
        "full",
    }
