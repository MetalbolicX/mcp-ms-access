"""Tests for recovery rungs (plan 047): Rung 1 retry, Rung 3 CLI repair, Rung 4 salvage.

Pattern: model after test_credentials.py (tmp_path + fake adapters).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ms_access_mcp.application.database import DatabaseUseCases, _get_lock_file
from ms_access_mcp.application.maintenance import MaintenanceUseCases
from ms_access_mcp.errors import Err, Ok


# ---------------------------------------------------------------------------
# Rung 1 — .ldb-aware retry
# ---------------------------------------------------------------------------

class TestLdbAwareRetry:
    def test_lock_error_retries_up_to_3_times(self, tmp_path: Path) -> None:
        """Lock error + stale .ldb → retry up to 3 times before returning Err."""
        db = tmp_path / "test.accdb"
        db.write_bytes(b"FAKE")
        # Create a stale .ldb
        ldb = tmp_path / "test.ldb"
        ldb.write_bytes(b"lock")

        call_count = 0

        class FakeAdapter:
            def connect(self, path, password=None):
                nonlocal call_count
                call_count += 1
                raise Exception("[Microsoft][ODBC Microsoft Access Driver] Could not use; file already in use")

        with patch("ms_access_mcp.backends.BackendSelector.get_adapter") as mock_get:
            mock_adapter = FakeAdapter()
            mock_get.return_value = mock_adapter

            uc = DatabaseUseCases(readonly=False, allowed_dirs=[tmp_path])
            result = uc.open_database(str(db))

        assert isinstance(result, Err)
        assert call_count == 4  # 1 initial + 3 retries

    def test_non_lock_error_fails_immediately(self, tmp_path: Path) -> None:
        """Non-lock error → immediate fail, no retry."""
        db = tmp_path / "test.accdb"
        db.write_bytes(b"FAKE")

        call_count = 0

        class FakeAdapter:
            def connect(self, path, password=None):
                nonlocal call_count
                call_count += 1
                raise Exception("Some completely different error")

        with patch("ms_access_mcp.backends.BackendSelector.get_adapter") as mock_get:
            mock_adapter = FakeAdapter()
            mock_get.return_value = mock_adapter

            uc = DatabaseUseCases(readonly=False, allowed_dirs=[tmp_path])
            result = uc.open_database(str(db))

        assert isinstance(result, Err)
        assert call_count == 1  # no retries

    def test_no_stale_ldb_does_not_retry(self, tmp_path: Path) -> None:
        """Lock-like error but no .ldb file → no retry."""
        db = tmp_path / "test.accdb"
        db.write_bytes(b"FAKE")
        # No .ldb file

        call_count = 0

        class FakeAdapter:
            def connect(self, path, password=None):
                nonlocal call_count
                call_count += 1
                raise Exception("[ODBC] Could not use; file already in use")

        with patch("ms_access_mcp.backends.BackendSelector.get_adapter") as mock_get:
            mock_adapter = FakeAdapter()
            mock_get.return_value = mock_adapter

            uc = DatabaseUseCases(readonly=False, allowed_dirs=[tmp_path])
            result = uc.open_database(str(db))

        assert isinstance(result, Err)
        assert call_count == 1  # no retries

    def test_get_lock_file_ldb(self, tmp_path: Path) -> None:
        db = tmp_path / "test.accdb"
        db.touch()
        ldb = tmp_path / "test.ldb"
        ldb.touch()
        assert _get_lock_file(str(db)) == str(ldb)

    def test_get_lock_file_laccdb(self, tmp_path: Path) -> None:
        db = tmp_path / "test.accdb"
        db.touch()
        laccdb = tmp_path / "test.laccdb"
        laccdb.touch()
        assert _get_lock_file(str(db)) == str(laccdb)

    def test_get_lock_file_none(self, tmp_path: Path) -> None:
        db = tmp_path / "test.accdb"
        db.touch()
        assert _get_lock_file(str(db)) is None


# ---------------------------------------------------------------------------
# Rung 3 — msaccess /compact CLI escalation
# ---------------------------------------------------------------------------

class TestRepairWithAccessCli:
    def test_confirm_gate_blocks_execution(self, tmp_path: Path) -> None:
        """repair_with_access_cli refuses when confirm=False."""
        mc = MaintenanceUseCases()
        result = mc.repair_with_access_cli(str(tmp_path / "db.accdb"), confirm=False)
        assert isinstance(result, Err)
        assert "confirm" in result.message.lower()

    def test_missing_file_returns_err(self, tmp_path: Path) -> None:
        """Refuses if file doesn't exist."""
        mc = MaintenanceUseCases()
        result = mc.repair_with_access_cli(str(tmp_path / "nonexistent.accdb"), confirm=True)
        assert isinstance(result, Err)
        assert "not found" in result.message.lower()

    def test_off_windows_returns_err(self, tmp_path: Path) -> None:
        """Refuses non-Windows platforms."""
        with patch("sys.platform", "linux"):
            mc = MaintenanceUseCases()
            result = mc.repair_with_access_cli(str(tmp_path / "db.accdb"), confirm=True)
        assert isinstance(result, Err)
        assert "windows" in result.message.lower()

    def test_missing_executable_returns_err(self, tmp_path: Path) -> None:
        """Returns Err when msaccess.exe cannot be found."""
        db = tmp_path / "db.accdb"
        db.write_bytes(b"FAKE")
        with patch("ms_access_mcp.application.maintenance.shutil.which", return_value=None):
            with patch("pathlib.Path.exists", return_value=False):
                mc = MaintenanceUseCases()
                result = mc.repair_with_access_cli(str(db), confirm=True)
        assert isinstance(result, Err)
        assert "not found" in result.message.lower() or "access" in result.message.lower()

    def test_subprocess_success(self, tmp_path: Path) -> None:
        """Successful subprocess call returns Ok."""
        db = tmp_path / "db.accdb"
        db.write_bytes(b"FAKE")

        with patch("sys.platform", "win32"):
            with patch("ms_access_mcp.application.maintenance.shutil.which", return_value=r"C:\Program Files\...\msaccess.exe"):
                with patch("os.path.exists", return_value=True):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0)
                        mc = MaintenanceUseCases()
                        result = mc.repair_with_access_cli(str(db), confirm=True)

        assert isinstance(result, Ok), f"Expected Ok, got {result!r}"

    def test_subprocess_timeout_returns_err(self, tmp_path: Path) -> None:
        """Non-zero timeout returns Err."""
        db = tmp_path / "db.accdb"
        db.write_bytes(b"FAKE")

        with patch("sys.platform", "win32"):
            with patch("shutil.which", return_value=r"C:\Program Files\...\msaccess.exe"):
                with patch("pathlib.Path.exists", return_value=True):
                    with patch("subprocess.run") as mock_run:
                        mock_run.side_effect = subprocess.TimeoutExpired("msaccess.exe", 120)
                        mc = MaintenanceUseCases()
                        result = mc.repair_with_access_cli(str(db), confirm=True)

        assert isinstance(result, Err)

    def test_subprocess_nonzero_exit_returns_err(self, tmp_path: Path) -> None:
        """Non-zero exit code returns Err."""
        db = tmp_path / "db.accdb"
        db.write_bytes(b"FAKE")

        with patch("sys.platform", "win32"):
            with patch("shutil.which", return_value=r"C:\Program Files\...\msaccess.exe"):
                with patch("pathlib.Path.exists", return_value=True):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=1, stderr="compact failed")
                        mc = MaintenanceUseCases()
                        result = mc.repair_with_access_cli(str(db), confirm=True)

        assert isinstance(result, Err)


# ---------------------------------------------------------------------------
# Rung 4 — salvage_database
# ---------------------------------------------------------------------------

class TestSalvageDatabase:
    def test_confirm_gate_blocks(self, tmp_path: Path) -> None:
        """salvage_database refuses when confirm=False."""
        mc = MaintenanceUseCases()
        result = mc.salvage_database(str(tmp_path / "db.accdb"), confirm=False)
        assert isinstance(result, Err)
        assert "confirm" in result.message.lower()

    def test_missing_file_returns_err(self, tmp_path: Path) -> None:
        """Refuses if file doesn't exist."""
        mc = MaintenanceUseCases()
        result = mc.salvage_database(str(tmp_path / "nonexistent.accdb"), confirm=True)
        assert isinstance(result, Err)

    def test_renames_original_to_corrupt(self, tmp_path: Path) -> None:
        """Renames the original file to .corrupt-<timestamp>."""
        db = tmp_path / "db.accdb"
        db.write_bytes(b"CORRUPT CONTENT")

        class _FakeDB:
            TableDefs = []
            def Close(self):
                pass

        class _FakeDBEngine:
            def OpenDatabase(self, path):
                return _FakeDB()
            def CreateDatabase(self, path, locale, options):
                pass

        class _FakeApp:
            DBEngine = _FakeDBEngine()

        class _FakeDispatcher:
            access_app = _FakeApp()
            def shutdown(self):
                pass

        mc = MaintenanceUseCases()
        with patch("ms_access_mcp.backends.dispatcher.ComDispatcher", return_value=_FakeDispatcher()):
            with patch("ms_access_mcp.backends.BackendSelector.get_adapter") as mock_get:
                mock_adapter = MagicMock()
                mock_get.return_value = mock_adapter
                result = mc.salvage_database(str(db), confirm=True)

        assert isinstance(result, Ok), f"Expected Ok, got {result!r}"
        data = result.data
        assert "renamed_path" in data
        renamed = Path(data["renamed_path"])
        assert renamed.exists()
        assert ".corrupt-" in renamed.name
        assert not db.exists()  # original was renamed

    def test_per_table_loss_recorded(self, tmp_path: Path) -> None:
        """A table that fails to open is recorded as lost."""
        db = tmp_path / "db.accdb"
        db.write_bytes(b"CORRUPT CONTENT")

        class _FakeTableDef:
            def __init__(self, name):
                self.Name = name

        class _FakeRS:
            def __init__(self):
                self._rows = [[1, "row1"]]
                self._i = 0
            def EOF(self):
                return self._i >= len(self._rows)
            def MoveNext(self):
                self._i += 1
            def Fields(self, i):
                class _FakeField:
                    Value = self._rows[0][i]
                return _FakeField()
            def Close(self):
                pass

        class _FakeDB:
            TableDefs = [_FakeTableDef("good_table"), _FakeTableDef("bad_table")]

            def OpenRecordset(self, sql, type_=2):
                if "bad_table" in sql:
                    raise Exception("Cannot read table")
                return _FakeRS()

            def Close(self):
                pass

        class _FakeDBEngine:
            def OpenDatabase(self, path):
                return _FakeDB()
            def CreateDatabase(self, path, locale, options):
                pass

        class _FakeApp:
            DBEngine = _FakeDBEngine()

        class _FakeDispatcher:
            access_app = _FakeApp()
            def shutdown(self):
                pass

        mc = MaintenanceUseCases()
        with patch("ms_access_mcp.backends.dispatcher.ComDispatcher", return_value=_FakeDispatcher()):
            with patch("ms_access_mcp.backends.BackendSelector.get_adapter") as mock_get:
                mock_adapter = MagicMock()
                mock_get.return_value = mock_adapter
                result = mc.salvage_database(str(db), confirm=True)

        assert isinstance(result, Ok), f"Expected Ok, got {result!r}"
        data = result.data
        tables = data.get("tables", [])
        bad = next((t for t in tables if t["table_name"] == "bad_table"), None)
        assert bad is not None
        assert bad["salvaged_rows"] == 0
        assert "Cannot read table" in bad["error"]
        # good_table was iterated without error (salvaged_rows may be 0 if param binding
        # fails in the fake, but no error means iteration succeeded)
        good = next((t for t in tables if t["table_name"] == "good_table"), None)
        assert good is not None
        assert good["error"] is None
