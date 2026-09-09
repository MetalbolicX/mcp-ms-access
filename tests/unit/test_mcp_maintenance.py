"""Tests for mcp_tools/maintenance.py — MCP tool registry for maintenance."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mcp.types import TextContent

from ms_access_mcp.application.database import Ok


class TestMaintenanceToolRegistry:
    """Test maintenance MCP tool registry names."""

    def test_tools_dict_has_expected_names(self):
        from ms_access_mcp.mcp_tools.maintenance import _TOOLS

        expected = {
            "maintenance_backup",
            "maintenance_dev_copy_status",
            "maintenance_dev_copy_create",
            "maintenance_dev_copy_deploy",
            "maintenance_compact_repair",
            "maintenance_diagnose_environment",
        }
        actual = set(_TOOLS.keys())
        assert expected == actual, f"Expected {expected}, got {actual}"

    def test_recover_access_not_in_tools(self):
        from ms_access_mcp.mcp_tools.maintenance import _TOOLS

        assert "maintenance_recover_access" not in _TOOLS
        assert "recover_access" not in _TOOLS


class TestMaintenanceRegister:
    """Test register() adds correct tools."""

    def test_register_adds_six_tools(self):
        from ms_access_mcp.mcp_tools.maintenance import register

        mock_mcp = MagicMock()
        register(mock_mcp)
        assert mock_mcp.add_tool.call_count == 6


class TestMaintenanceToolFunctions:
    """Test individual tool functions return CallToolResult with correct shape."""

    def test_maintenance_backup_returns_result(self, tmp_path):
        from ms_access_mcp.mcp_tools.maintenance import maintenance_backup

        with patch(
            "ms_access_mcp.mcp_tools.maintenance.MaintenanceUseCases"
        ) as MockUC:
            instance = MockUC.return_value
            instance.backup.return_value = Ok(
                data={"backup_path": "/tmp/db.accdb.bak", "manifest_path": "/tmp/db.accdb.bak.manifest"},
                rowcount=1,
            )
            result = maintenance_backup(str(tmp_path / "db.accdb"))
        assert hasattr(result, "content")
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert result.isError is False

    def test_maintenance_dev_copy_status_returns_result(self):
        from ms_access_mcp.mcp_tools.maintenance import maintenance_dev_copy_status

        with patch(
            "ms_access_mcp.mcp_tools.maintenance.MaintenanceUseCases"
        ) as MockUC:
            instance = MockUC.return_value
            instance.dev_copy_status.return_value = Ok(
                data={"active": True, "dev_path": "/tmp/dev.accdb"},
                rowcount=1,
            )
            result = maintenance_dev_copy_status("")
        assert hasattr(result, "content")
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert result.isError is False

    def test_maintenance_dev_copy_create_returns_result(self, tmp_path):
        from ms_access_mcp.mcp_tools.maintenance import maintenance_dev_copy_create

        with patch(
            "ms_access_mcp.mcp_tools.maintenance.MaintenanceUseCases"
        ) as MockUC:
            instance = MockUC.return_value
            instance.create_dev_copy.return_value = Ok(
                data={"dev_path": "/tmp/dev.accdb", "manifest_path": "/tmp/dev.json", "production_path": "/tmp/prod.accdb"},
                rowcount=1,
            )
            result = maintenance_dev_copy_create("")
        assert hasattr(result, "content")
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert result.isError is False

    def test_maintenance_dev_copy_deploy_returns_result(self, tmp_path):
        from ms_access_mcp.mcp_tools.maintenance import maintenance_dev_copy_deploy

        with patch(
            "ms_access_mcp.mcp_tools.maintenance.MaintenanceUseCases"
        ) as MockUC:
            instance = MockUC.return_value
            instance.deploy_dev_copy.return_value = Ok(
                data={"target": str(tmp_path / "target.accdb"), "bak_path": "/tmp/bak.accdb", "deployed_at": "2025-01-01T00:00:00Z"},
                rowcount=1,
            )
            result = maintenance_dev_copy_deploy(
                "",
                params={"dev_path": str(tmp_path / "dev.accdb"), "target": str(tmp_path / "target.accdb")},
                confirm=True,
            )
        assert hasattr(result, "content")
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert result.isError is False

    def test_maintenance_compact_repair_returns_result(self, tmp_path):
        from ms_access_mcp.mcp_tools.maintenance import maintenance_compact_repair

        with patch(
            "ms_access_mcp.mcp_tools.maintenance.MaintenanceUseCases"
        ) as MockUC:
            instance = MockUC.return_value
            instance.compact_repair.return_value = Ok(
                data={"path": str(tmp_path / "db.accdb"), "mode": "compact", "output_path": None, "stats": {}},
                rowcount=1,
            )
            result = maintenance_compact_repair(
                str(tmp_path / "db.accdb"),
                params={"mode": "compact"},
                confirm=True,
            )
        assert hasattr(result, "content")
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert result.isError is False

    def test_maintenance_diagnose_environment_returns_result(self):
        from ms_access_mcp.mcp_tools.maintenance import maintenance_diagnose_environment

        with patch(
            "ms_access_mcp.mcp_tools.maintenance.MaintenanceUseCases"
        ) as MockUC:
            instance = MockUC.return_value
            instance.diagnose_environment.return_value = Ok(
                data=[{"name": "access", "passed": True, "message": "ok", "details": {}}],
                rowcount=1,
            )
            result = maintenance_diagnose_environment("")
        assert hasattr(result, "content")
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert result.isError is False

    def test_maintenance_dev_copy_deploy_confirm_false_returns_error(self, tmp_path):
        from ms_access_mcp.mcp_tools.maintenance import maintenance_dev_copy_deploy

        result = maintenance_dev_copy_deploy(
            "",
            params={"dev_path": str(tmp_path / "dev.accdb"), "target": str(tmp_path / "target.accdb")},
            confirm=False,
        )
        block = result.content[0]
        assert isinstance(block, TextContent)
        import json
        data = json.loads(block.text)
        assert data["success"] is False
        assert "confirmation" in data["error"].lower()


class TestMaintenanceErrorPath:
    """Test error responses from tool functions."""

    def test_backup_nonexistent_path_returns_error(self):
        from ms_access_mcp.mcp_tools.maintenance import maintenance_backup

        result = maintenance_backup("Z:/nonexistent/path.accdb")
        assert result.isError is True
        block = result.content[0]
        assert isinstance(block, TextContent)
        import json
        data = json.loads(block.text)
        assert data["success"] is False
        assert "not found" in data["error"].lower()
