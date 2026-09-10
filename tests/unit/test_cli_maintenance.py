"""Tests for maintenance CLI subcommands — TDD."""

from __future__ import annotations

from argparse import Namespace
from typing import Literal

from ms_access_mcp.application.database import Err, Ok


class FakeMaintenanceUseCases:
    """Fake MaintenanceUseCases for testing CLI in isolation."""

    def __init__(self, readonly: bool = True, db_path: str | None = None):
        self.readonly = readonly
        self.db_path = db_path

    def backup(self, path: str, *, retention: int | None = None):
        return Ok(data={"backup_path": f"/tmp/{path}.bak", "manifest_path": f"/tmp/{path}.bak.manifest"}, rowcount=1)

    def create_dev_copy(self, source: str | None = None):
        return Ok(data={"dev_path": "/tmp/dev.accdb", "manifest_path": "/tmp/dev.json", "production_path": source or "/tmp/prod.accdb"}, rowcount=1)

    def deploy_dev_copy(self, dev_path: str, target: str, *, confirm: bool, preflight_backup: bool = True):
        if not confirm:
            return Err("deploy_dev_copy requires explicit confirmation")
        return Ok(data={"target": target, "bak_path": "/tmp/bak.accdb", "deployed_at": "2025-01-01T00:00:00Z"}, rowcount=1)

    def dev_copy_status(self):
        return Ok(data={"active": True, "dev_path": "/tmp/dev.accdb", "manifest_path": "/tmp/dev.json"}, rowcount=1)

    def compact_repair(self, path: str, mode: Literal["compact", "repair", "compact_and_repair"], *, confirm: bool):
        if not confirm:
            return Err("compact_repair requires explicit confirmation")
        return Ok(data={"path": path, "mode": mode, "output_path": None, "stats": {}}, rowcount=1)

    def diagnose_environment(self):
        return Ok(data=[{"name": "access", "passed": True, "message": "ok", "details": {}}], rowcount=1)

    def recover_access(self, *, confirm: bool):
        if not confirm:
            return Err("recover_access requires explicit confirmation")
        return Ok(data={"killed_pids": [], "reconnected": False, "errors": None}, rowcount=1)


class TestMaintenanceCliHandlers:
    """Test maintenance CLI handlers dispatch correctly."""

    def test_cmd_backup_happy(self, tmp_path):
        from ms_access_mcp.cli import _cmd_maintenance_backup

        uc = FakeMaintenanceUseCases()
        args = Namespace(path=str(tmp_path / "db.accdb"), retention=None, json=False)
        rc = _cmd_maintenance_backup(args, uc)
        assert rc == 0

    def test_cmd_dev_copy_status_happy(self):
        from ms_access_mcp.cli import _cmd_maintenance_dev_copy_status

        uc = FakeMaintenanceUseCases()
        args = Namespace(json=False)
        rc = _cmd_maintenance_dev_copy_status(args, uc)
        assert rc == 0

    def test_cmd_dev_copy_create_happy(self, tmp_path):
        from ms_access_mcp.cli import _cmd_maintenance_dev_copy_create

        uc = FakeMaintenanceUseCases()
        args = Namespace(source=None, json=False)
        rc = _cmd_maintenance_dev_copy_create(args, uc)
        assert rc == 0

    def test_cmd_dev_copy_deploy_requires_yes(self, tmp_path):
        from ms_access_mcp.cli import _cmd_maintenance_dev_copy_deploy

        uc = FakeMaintenanceUseCases()
        args = Namespace(dev_path=str(tmp_path / "dev.accdb"), target=str(tmp_path / "target.accdb"), yes=False, json=False)
        rc = _cmd_maintenance_dev_copy_deploy(args, uc)
        assert rc == 2

    def test_cmd_dev_copy_deploy_happy(self, tmp_path):
        from ms_access_mcp.cli import _cmd_maintenance_dev_copy_deploy

        uc = FakeMaintenanceUseCases()
        args = Namespace(dev_path=str(tmp_path / "dev.accdb"), target=str(tmp_path / "target.accdb"), yes=True, json=False)
        rc = _cmd_maintenance_dev_copy_deploy(args, uc)
        assert rc == 0

    def test_cmd_compact_repair_requires_yes(self, tmp_path):
        from ms_access_mcp.cli import _cmd_maintenance_compact_repair

        uc = FakeMaintenanceUseCases()
        args = Namespace(path=str(tmp_path / "db.accdb"), mode="compact", yes=False, json=False)
        rc = _cmd_maintenance_compact_repair(args, uc)
        assert rc == 2

    def test_cmd_compact_repair_happy(self, tmp_path):
        from ms_access_mcp.cli import _cmd_maintenance_compact_repair

        uc = FakeMaintenanceUseCases()
        args = Namespace(path=str(tmp_path / "db.accdb"), mode="compact", yes=True, json=False)
        rc = _cmd_maintenance_compact_repair(args, uc)
        assert rc == 0

    def test_cmd_diagnose_happy(self):
        from ms_access_mcp.cli import _cmd_maintenance_diagnose

        uc = FakeMaintenanceUseCases()
        args = Namespace(json=False)
        rc = _cmd_maintenance_diagnose(args, uc)
        assert rc == 0

    def test_cmd_recover_requires_yes(self):
        from ms_access_mcp.cli import _cmd_maintenance_recover

        uc = FakeMaintenanceUseCases()
        args = Namespace(yes=False, json=False)
        rc = _cmd_maintenance_recover(args, uc)
        assert rc == 2

    def test_cmd_recover_happy(self):
        from ms_access_mcp.cli import _cmd_maintenance_recover

        uc = FakeMaintenanceUseCases()
        args = Namespace(yes=True, json=False)
        rc = _cmd_maintenance_recover(args, uc)
        assert rc == 0


class TestMaintenanceNonDestructiveNoYes:
    """Non-destructive maintenance commands should not require --yes."""

    def test_backup_no_yes_ok(self, tmp_path):
        from ms_access_mcp.cli import _cmd_maintenance_backup

        uc = FakeMaintenanceUseCases()
        args = Namespace(path=str(tmp_path / "db.accdb"), retention=None, yes=False, json=False)
        rc = _cmd_maintenance_backup(args, uc)
        assert rc == 0

    def test_dev_copy_create_no_yes_ok(self, tmp_path):
        from ms_access_mcp.cli import _cmd_maintenance_dev_copy_create

        uc = FakeMaintenanceUseCases()
        args = Namespace(source=str(tmp_path / "db.accdb"), yes=False, json=False)
        rc = _cmd_maintenance_dev_copy_create(args, uc)
        assert rc == 0

    def test_dev_copy_status_no_yes_ok(self):
        from ms_access_mcp.cli import _cmd_maintenance_dev_copy_status

        uc = FakeMaintenanceUseCases()
        args = Namespace(yes=False, json=False)
        rc = _cmd_maintenance_dev_copy_status(args, uc)
        assert rc == 0

    def test_diagnose_no_yes_ok(self):
        from ms_access_mcp.cli import _cmd_maintenance_diagnose

        uc = FakeMaintenanceUseCases()
        args = Namespace(yes=False, json=False)
        rc = _cmd_maintenance_diagnose(args, uc)
        assert rc == 0


class TestMaintenanceErrorPath:
    """Test error responses from CLI handlers."""

    def test_backup_error_from_uc(self, tmp_path):
        from ms_access_mcp.cli import _cmd_maintenance_backup

        class ErrUc:
            def backup(self, path, *, retention=None):
                return Err("source not found")

            def dev_copy_status(self):
                return Ok(data={}, rowcount=0)

            def create_dev_copy(self, source=None):
                return Ok(data={}, rowcount=0)

            def deploy_dev_copy(self, dev_path, target, *, confirm, preflight_backup=True):
                return Ok(data={}, rowcount=0)

            def compact_repair(self, path, mode, *, confirm):
                return Ok(data={}, rowcount=0)

            def diagnose_environment(self):
                return Ok(data=[], rowcount=0)

            def recover_access(self, *, confirm):
                return Ok(data={}, rowcount=0)

        args = Namespace(path=str(tmp_path / "db.accdb"), retention=None, json=False)
        rc = _cmd_maintenance_backup(args, ErrUc())
        assert rc == 1

    def test_dev_copy_deploy_error_from_uc(self, tmp_path):
        from ms_access_mcp.cli import _cmd_maintenance_dev_copy_deploy

        class ErrUc:
            def backup(self, path, *, retention=None):
                return Ok(data={}, rowcount=0)

            def dev_copy_status(self):
                return Ok(data={}, rowcount=0)

            def create_dev_copy(self, source=None):
                return Ok(data={}, rowcount=0)

            def deploy_dev_copy(self, dev_path, target, *, confirm, preflight_backup=True):
                return Err("dev copy not found")

            def compact_repair(self, path, mode, *, confirm):
                return Ok(data={}, rowcount=0)

            def diagnose_environment(self):
                return Ok(data=[], rowcount=0)

            def recover_access(self, *, confirm):
                return Ok(data={}, rowcount=0)

        args = Namespace(dev_path=str(tmp_path / "dev.accdb"), target=str(tmp_path / "t.accdb"), yes=True, json=False)
        rc = _cmd_maintenance_dev_copy_deploy(args, ErrUc())
        assert rc == 1
