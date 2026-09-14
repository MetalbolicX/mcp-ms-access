"""Tests for application/maintenance.py — MaintenanceUseCases with fakes for TDD."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from ms_access_mcp.application.database import Err, Ok


class FakeMaintenanceAdapter:
    """Fake adapter for testing MaintenanceUseCases."""

    def __init__(self, connected: bool = True, compact_error: str | None = None):
        self.connected = connected
        self.calls: list[tuple[str, tuple, dict]] = []
        self._compact_error = compact_error
        self._recover_called = False

    def connect(self, db_path: str | None = None, /) -> bool:
        self.connected = True
        self.calls.append(("connect", (db_path,), {}))
        return True

    def disconnect(self) -> None:
        self.connected = False

    def compact_repair(self, path: str, mode: str) -> dict[str, Any]:
        self.calls.append(("compact_repair", (path, mode), {}))
        if self._compact_error:
            raise RuntimeError(self._compact_error)
        return {
            "original_size": 1024,
            "compacted_size": 512,
        }

    def recover(self) -> bool:
        self.calls.append(("recover", (), {}))
        self._recover_called = True
        return True


# =============================================================================
# TestBackup
# =============================================================================


class TestBackup:
    def test_backup_happy(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        source = tmp_path / "test.accdb"
        source.write_bytes(b"fake db content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)
        result = uc.backup(str(source))

        assert isinstance(result, Ok)
        assert "backup_path" in result.data
        assert "manifest" in result.data
        assert result.data["manifest"]["kind"] == "backup"
        assert result.data["manifest"]["source_path"] == str(source)
        assert result.data["manifest"]["sha256"] == hashlib.sha256(b"fake db content").hexdigest()

    def test_backup_creates_manifest(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        source = tmp_path / "test.accdb"
        source.write_bytes(b"fake db content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)
        result = uc.backup(str(source))

        assert isinstance(result, Ok)
        manifest_path = result.data["manifest_path"]
        assert os.path.exists(manifest_path)
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest["kind"] == "backup"
        assert manifest["source_path"] == str(source)

    def test_backup_retention_prunes_old(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        source = tmp_path / "test.accdb"
        source.write_bytes(b"fake db content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        for i in range(7):
            uc.backup(str(source), retention=5)
            time.sleep(0.01)

        result = uc.backup(str(source), retention=5)
        assert isinstance(result, Ok)
        assert result.data["kept"] == 5
        assert result.data["pruned"] >= 1

    def test_backup_source_not_found(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)
        result = uc.backup(str(tmp_path / "nonexistent.accdb"))

        assert isinstance(result, Err)
        assert "not found" in result.message


# =============================================================================
# TestCreateDevCopy
# =============================================================================


class TestCreateDevCopy:
    def test_create_dev_copy_happy(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        source = tmp_path / "prod.accdb"
        source.write_bytes(b"prod db content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)
        result = uc.create_dev_copy(source=str(source))

        assert isinstance(result, Ok)
        assert "dev_path" in result.data
        assert "manifest_path" in result.data
        assert result.data["production_path"] == str(source)
        assert os.path.exists(result.data["dev_path"])

    def test_create_dev_copy_manifest_written(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        source = tmp_path / "prod.accdb"
        source.write_bytes(b"prod db content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)
        result = uc.create_dev_copy(source=str(source))

        assert isinstance(result, Ok)
        manifest_path = result.data["manifest_path"]
        assert os.path.exists(manifest_path)
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest["production_path"] == str(source)
        assert manifest["deployed_at"] is None

    def test_create_dev_copy_already_active(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        source = tmp_path / "prod.accdb"
        source.write_bytes(b"prod db content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        result1 = uc.create_dev_copy(source=str(source))
        assert isinstance(result1, Ok)

        result2 = uc.create_dev_copy(source=str(source))
        assert isinstance(result2, Err)
        assert "already active" in result2.message

    def test_create_dev_copy_no_source_no_db_path(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)
        result = uc.create_dev_copy(source=None)

        assert isinstance(result, Err)

    def test_create_dev_copy_uses_disposable_dir(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        source = tmp_path / "prod.accdb"
        source.write_bytes(b"prod db content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)
        result = uc.create_dev_copy(source=str(source))

        assert isinstance(result, Ok)
        dev_path = result.data["dev_path"]
        assert "ms_access_dev" in dev_path


# =============================================================================
# TestDeployDevCopy
# =============================================================================


class TestDeployDevCopy:
    def test_deploy_dev_copy_happy(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        dev_source = tmp_path / "dev.accdb"
        dev_source.write_bytes(b"dev content")
        target = tmp_path / "target.accdb"
        target.write_bytes(b"original content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        result = uc.deploy_dev_copy(
            dev_path=str(dev_source),
            target=str(target),
            confirm=True,
            preflight_backup=False,
        )

        assert isinstance(result, Ok)
        assert result.data["target"] == str(target)
        assert target.read_bytes() == b"dev content"

    def test_deploy_dev_copy_deletes_manifest(self, tmp_path):
        import shutil

        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        dev_dir = os.path.join(tempfile.gettempdir(), "ms_access_dev")
        if os.path.exists(dev_dir):
            shutil.rmtree(dev_dir)

        source = tmp_path / "prod.accdb"
        source.write_bytes(b"prod content")
        target = tmp_path / "target.accdb"
        target.write_bytes(b"target content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        create_result = uc.create_dev_copy(source=str(source))
        assert isinstance(create_result, Ok)
        manifest_path = create_result.data["manifest_path"]
        actual_dev_path = create_result.data["dev_path"]

        deploy_result = uc.deploy_dev_copy(
            dev_path=actual_dev_path,
            target=str(target),
            confirm=True,
            preflight_backup=False,
        )
        assert isinstance(deploy_result, Ok)

        assert not os.path.exists(manifest_path)

    def test_deploy_dev_copy_no_confirm(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        dev_source = tmp_path / "dev.accdb"
        dev_source.write_bytes(b"dev content")
        target = tmp_path / "target.accdb"
        target.write_bytes(b"original content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        result = uc.deploy_dev_copy(
            dev_path=str(dev_source),
            target=str(target),
            confirm=False,
            preflight_backup=False,
        )

        assert isinstance(result, Err)
        assert "requires explicit confirmation" in result.message

    def test_deploy_dev_copy_preflight_backup_blocks_on_failure(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        dev_source = tmp_path / "dev.accdb"
        dev_source.write_bytes(b"dev content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        bad_target = str(tmp_path / "nonexistent" / "target.accdb")
        result = uc.deploy_dev_copy(
            dev_path=str(dev_source),
            target=bad_target,
            confirm=True,
            preflight_backup=True,
        )

        assert isinstance(result, Err)


# =============================================================================
# TestDevCopyStatus
# =============================================================================


class TestDevCopyStatus:
    def test_dev_copy_status_no_manifest(self, tmp_path):
        import shutil

        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        dev_dir = os.path.join(tempfile.gettempdir(), "ms_access_dev")
        if os.path.exists(dev_dir):
            shutil.rmtree(dev_dir)

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)
        result = uc.dev_copy_status()

        assert isinstance(result, Ok)
        assert result.data["active"] is False

    def test_dev_copy_status_with_manifest(self, tmp_path):
        import shutil

        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        dev_dir = os.path.join(tempfile.gettempdir(), "ms_access_dev")
        if os.path.exists(dev_dir):
            shutil.rmtree(dev_dir)

        source = tmp_path / "prod.accdb"
        source.write_bytes(b"prod content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        create_result = uc.create_dev_copy(source=str(source))
        assert isinstance(create_result, Ok)

        result = uc.dev_copy_status()

        assert isinstance(result, Ok)
        assert result.data["active"] is True
        assert result.data["production_path"] == str(source)


# =============================================================================
# TestCompactRepair
# =============================================================================


class TestCompactRepair:
    def test_compact_repair_invalid_mode(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        target = tmp_path / "test.accdb"
        target.write_bytes(b"db content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        mode: str = "invalid_mode"
        result = uc.compact_repair(
            path=str(target),
            mode=mode,  # type: ignore[arg-type]
            confirm=True,
        )

        assert isinstance(result, Err)
        assert "invalid_mode" in result.message

    def test_compact_repair_no_confirm(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        target = tmp_path / "test.accdb"
        target.write_bytes(b"db content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        result = uc.compact_repair(
            path=str(target),
            mode="compact",
            confirm=False,
        )

        assert isinstance(result, Err)
        assert "requires explicit confirmation" in result.message

    def test_compact_repair_adapter_called(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        target = tmp_path / "test.accdb"
        target.write_bytes(b"db content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        result = uc.compact_repair(
            path=str(target),
            mode="compact",
            confirm=True,
        )

        assert isinstance(result, Ok)
        assert any(
            call[0] == "compact_repair"
            for call in adapter.calls
        )

    def test_compact_repair_engine_error(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        target = tmp_path / "test.accdb"
        target.write_bytes(b"db content")

        adapter = FakeMaintenanceAdapter(compact_error="DAO engine error")
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        result = uc.compact_repair(
            path=str(target),
            mode="compact",
            confirm=True,
        )

        assert isinstance(result, Err)
        assert "DAO engine error" in result.message

    def test_compact_repair_disposable_path_no_preflight(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        disposable_dir = tempfile.mkdtemp(prefix="ms_access_")
        target = os.path.join(disposable_dir, "test.accdb")
        with open(target, "wb") as f:
            f.write(b"db content")

        try:
            adapter = FakeMaintenanceAdapter()
            uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

            result = uc.compact_repair(
                path=target,
                mode="compact",
                confirm=True,
            )

            assert isinstance(result, Ok)
        finally:
            shutil.rmtree(disposable_dir, ignore_errors=True)


# =============================================================================
# TestDiagnoseEnvironment
# =============================================================================


class TestDiagnoseEnvironment:
    def test_diagnose_environment_returns_list_shape(self, tmp_path, monkeypatch):
        from ms_access_mcp import doctor
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        @dataclass
        class FakeCheck:
            name: str
            passed: bool
            message: str = ""
            details: dict = field(default_factory=dict)

        fake_checks = [
            FakeCheck(name="platform", passed=True, message="Windows x64", details={}),
            FakeCheck(name="packages", passed=True, message="All required packages installed", details={}),
        ]

        monkeypatch.setattr(doctor, "run_checks", lambda deep: fake_checks)

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=True, db_path=None)

        result = uc.diagnose_environment()

        assert isinstance(result, Ok)
        assert isinstance(result.data, list)
        assert len(result.data) == 2
        assert result.data[0]["name"] == "platform"

    def test_diagnose_environment_calls_run_checks(self, tmp_path, monkeypatch):
        from ms_access_mcp import doctor
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        @dataclass
        class FakeCheck:
            name: str
            passed: bool
            message: str = ""
            details: dict = field(default_factory=dict)

        fake_checks = [FakeCheck(name="test", passed=True)]
        called = False

        def fake_run_checks(deep=False):
            nonlocal called
            called = True
            return fake_checks

        monkeypatch.setattr(doctor, "run_checks", fake_run_checks)

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=True, db_path=None)

        uc.diagnose_environment()

        assert called


# =============================================================================
# TestRecoverAccess
# =============================================================================


class TestRecoverAccess:
    def test_recover_access_no_confirm(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        result = uc.recover_access(confirm=False)

        assert isinstance(result, Err)
        assert "requires explicit confirmation" in result.message

    def test_recover_access_non_windows(self, tmp_path, monkeypatch):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        monkeypatch.setattr(sys, "platform", "linux")
        result = uc.recover_access(confirm=True)

        assert isinstance(result, Err)
        assert "only available on Windows" in result.message

    def test_recover_access_windows_with_confirm(self, tmp_path, monkeypatch):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        called_commands = []

        def fake_run(cmd, **kwargs):
            called_commands.append(cmd)
            if cmd[0] == "tasklist":
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout='"MSACCESS.EXE","111","Console","1","12,000 K"\n"MSACCESS.EXE","222","Console","1","8,000 K"',
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(sys, "platform", "win32")

        result = uc.recover_access(confirm=True)

        assert isinstance(result, Ok)
        taskkill_commands = [cmd for cmd in called_commands if cmd[0] == "taskkill"]
        assert len(taskkill_commands) == 1
        assert "/PID" in taskkill_commands[0]
        assert "111" in taskkill_commands[0]
        assert "222" in taskkill_commands[0]
        for cmd in called_commands:
            assert "/IM" not in cmd, f"/IM must never appear in recover kill: {cmd}"
        assert result.data["killed_pids"] == ["111", "222"]

    def test_recover_access_skips_kill_when_no_access_running(self, monkeypatch):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)
        called_commands = []

        def fake_run(cmd, **kwargs):
            called_commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(sys, "platform", "win32")

        result = uc.recover_access(confirm=True)

        assert isinstance(result, Ok)
        assert not any(cmd[0] == "taskkill" for cmd in called_commands)
        assert result.data["killed_pids"] == []


# =============================================================================
# TestOdbcAdapterCompactRepairStub
# =============================================================================


class TestOdbcAdapterCompactRepairStub:
    def test_odbc_adapter_compact_repair_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter

        adapter = OdbcAdapter("dummy_path")
        with pytest.raises(NotImplementedError) as exc_info:
            adapter.compact_repair("/tmp/test.accdb", "compact")
        assert "ODBC" in str(exc_info.value) or "COM" in str(exc_info.value)


# =============================================================================
# TestPreflightBackup
# =============================================================================


class TestPreflightBackup:
    def test_preflight_backup_succeeds(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        source = tmp_path / "test.accdb"
        source.write_bytes(b"test content")

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        result = uc._preflight_backup(str(source))

        assert isinstance(result, Ok)
        bak_path = result.data["backup_path"]
        assert os.path.exists(bak_path)

    def test_preflight_backup_missing_source(self, tmp_path):
        from ms_access_mcp.application.maintenance import MaintenanceUseCases

        adapter = FakeMaintenanceAdapter()
        uc = MaintenanceUseCases(adapter=adapter, readonly=False, db_path=None)

        result = uc._preflight_backup(str(tmp_path / "nonexistent.accdb"))

        assert isinstance(result, Err)
        assert "not found" in result.message
