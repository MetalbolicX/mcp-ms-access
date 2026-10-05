"""Tests for recovery rungs (plan 047): Rung 1 retry, Rung 3 CLI repair, Rung 4 salvage.

Pattern: model after test_credentials.py (tmp_path + fake adapters).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import FakeDispatcher

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
# Rung 2 — recover_access
# ---------------------------------------------------------------------------

class TestRecoverAccess:
    def test_fresh_use_case_returns_err_not_assertion(self, tmp_path: Path) -> None:
        """recover_access on a fresh use case (no db_path) → Err envelope, not AssertionError."""
        mc = MaintenanceUseCases()

        with patch("sys.platform", "win32"):
            with patch(
                "ms_access_mcp.application.maintenance.kill_access_processes",
                return_value=([], []),
            ):
                result = mc.recover_access(confirm=True)

        assert isinstance(result, Err), f"Expected Err, got {result!r}"
        assert "db_path" in result.message or "adapter" in result.message.lower()


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
            mc = MaintenanceUseCases(allowed_dirs=[tmp_path])
            result = mc.repair_with_access_cli(str(db), confirm=True)
        assert isinstance(result, Err)
        assert "not found" in result.message.lower() or "access" in result.message.lower()

    def test_subprocess_success(self, tmp_path: Path) -> None:
        """Successful subprocess call returns Ok."""
        db = tmp_path / "db.accdb"
        db.write_bytes(b"FAKE")

        with patch("sys.platform", "win32"):
            with patch("ms_access_mcp.application.maintenance.shutil.which", return_value=r"C:\Program Files\...\msaccess.exe"):
                with patch("pathlib.Path.exists", return_value=True):
                    with patch("pathlib.Path.is_file", return_value=True):
                        with patch("subprocess.run") as mock_run:
                            mock_run.return_value = MagicMock(returncode=0)
                            mc = MaintenanceUseCases(allowed_dirs=[tmp_path])
                            result = mc.repair_with_access_cli(str(db), confirm=True)

        assert isinstance(result, Ok), f"Expected Ok, got {result!r}"

    def test_exit_zero_without_output_returns_err(self, tmp_path: Path) -> None:
        """Exit 0 but no output file at target → Err (output verification failed)."""
        db = tmp_path / "db.accdb"
        db.write_bytes(b"FAKE")

        with patch("sys.platform", "win32"):
            with patch("ms_access_mcp.application.maintenance.shutil.which", return_value=r"C:\Program Files\...\msaccess.exe"):
                with patch("pathlib.Path.exists", return_value=True):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0)
                        mc = MaintenanceUseCases(allowed_dirs=[tmp_path])
                        result = mc.repair_with_access_cli(str(db), confirm=True)

        assert isinstance(result, Err), f"Expected Err, got {result!r}"
        assert "escalating" in result.message

    def test_argv_passed_unquoted(self, tmp_path: Path) -> None:
        """subprocess.run receives raw paths — list-form quoting handles spaces."""
        db = tmp_path / "db.accdb"
        db.write_bytes(b"FAKE")

        with patch("sys.platform", "win32"):
            with patch("ms_access_mcp.application.maintenance.shutil.which", return_value=r"C:\Program Files\...\msaccess.exe"):
                with patch("pathlib.Path.exists", return_value=True):
                    with patch("pathlib.Path.is_file", return_value=True):
                        with patch("subprocess.run") as mock_run:
                            mock_run.return_value = MagicMock(returncode=0)
                            mc = MaintenanceUseCases(allowed_dirs=[tmp_path])
                            result = mc.repair_with_access_cli(str(db), confirm=True)

        assert isinstance(result, Ok), f"Expected Ok, got {result!r}"
        argv = mock_run.call_args[0][0]
        assert argv[0] == r"C:\Program Files\Microsoft Office\Office16\msaccess.exe"
        assert argv[1] == str(db)
        assert argv[2] == "/compact"
        assert argv[3].endswith(f"repair_{db.name}")
        assert not any('"' in arg for arg in argv), f"quoted arg in argv: {argv!r}"

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

        class _FakeDispatcher(FakeDispatcher):
            def start(self):
                pass

            def shutdown(self):
                pass

        mc = MaintenanceUseCases(allowed_dirs=[tmp_path])
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

        class _FakeField:
            def __init__(self, name, value, ftype=10, fsize=255):
                self.Name = name
                self.Type = ftype
                self.Size = fsize
                self.Value = value

        class _FakeFields:
            def __init__(self, rows, offset=0):
                self._rows = rows
                self._offset = offset

            @property
            def Count(self):
                return len(self._rows[0]) if self._rows else 0

            def __call__(self, i):
                val = self._rows[0][i] if self._rows else None
                # Infer DAO type from Python type
                if isinstance(val, bool):
                    t, s = 1, 1
                elif isinstance(val, int):
                    t, s = 4, 4
                elif isinstance(val, float):
                    t, s = 7, 8
                elif isinstance(val, str):
                    t, s = 10, min(len(val), 255) if val else 255
                else:
                    t, s = 12, 0  # MEMO
                return _FakeField(f"field_{i}", val, t, s)

        class _FakeRS:
            def __init__(self, rows):
                self._rows = rows
                self._i = 0

            @property
            def EOF(self):
                return self._i >= len(self._rows)

            def MoveNext(self):
                self._i += 1

            @property
            def Fields(self):
                return _FakeFields(self._rows)

            def AddNew(self):
                pass

            def Update(self):
                pass

            def CancelUpdate(self):
                pass

            def Close(self):
                pass

        class _FakeDB:
            TableDefs = [_FakeTableDef("good_table"), _FakeTableDef("bad_table")]

            def OpenRecordset(self, sql, type_=2):
                if "bad_table" in sql:
                    raise Exception("Cannot read table")
                return _FakeRS([[1, "row1"]])

            def Execute(self, sql, options=None):
                # Silently succeed for test purposes
                pass

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
            def __init__(self):
                self._fake_engine = _FakeDBEngine()

            def start(self):
                pass

            def call(self, fn, engine=None):
                return fn(self._fake_engine)

            def shutdown(self):
                pass

        mc = MaintenanceUseCases(allowed_dirs=[tmp_path])
        with patch("ms_access_mcp.backends.dispatcher.ComDispatcher", return_value=_FakeDispatcher()):
            with patch("ms_access_mcp.backends.BackendSelector.get_adapter") as mock_get:
                mock_adapter = MagicMock()
                mock_adapter.salvage_tables.return_value = [
                    {"table_name": "good_table", "salvaged_rows": 1, "error": None},
                    {"table_name": "bad_table", "salvaged_rows": 0, "error": "Cannot read table"},
                ]
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

    def test_salvage_failure_returns_error_with_corrupt_path(self, tmp_path: Path) -> None:
        db = tmp_path / "db.accdb"
        db.write_bytes(b"CORRUPT CONTENT")

        class _FakeDispatcher:
            def start(self):
                pass

            def call(self, fn, engine=None):
                return fn(engine)

            def shutdown(self):
                pass

        adapter = MagicMock()
        adapter.salvage_tables.side_effect = RuntimeError("COM failure")
        mc = MaintenanceUseCases(allowed_dirs=[tmp_path])
        with patch("ms_access_mcp.backends.dispatcher.ComDispatcher", return_value=_FakeDispatcher()):
            with patch("ms_access_mcp.backends.BackendSelector.get_adapter", return_value=adapter):
                result = mc.salvage_database(str(db), confirm=True)

        assert isinstance(result, Err)
        corrupt_path = next(tmp_path.glob("db.corrupt-*.accdb"))
        assert str(corrupt_path).replace("\\", "\\\\") in result.message
        assert "do not re-run salvage" in result.message
        adapter.disconnect.assert_called_once_with()

    def test_salvage_disconnects_adapter_after_success(self, tmp_path: Path) -> None:
        db = tmp_path / "db.accdb"
        db.write_bytes(b"CORRUPT CONTENT")

        class _FakeDispatcher:
            def start(self):
                pass

            def call(self, fn, engine=None):
                return fn(engine)

            def shutdown(self):
                pass

        adapter = MagicMock()
        adapter.salvage_tables.return_value = []
        mc = MaintenanceUseCases(allowed_dirs=[tmp_path])
        with patch("ms_access_mcp.backends.dispatcher.ComDispatcher", return_value=_FakeDispatcher()):
            with patch("ms_access_mcp.backends.BackendSelector.get_adapter", return_value=adapter):
                result = mc.salvage_database(str(db), confirm=True)

        assert isinstance(result, Ok)
        adapter.disconnect.assert_called_once_with()

    def test_salvage_disconnects_adapter_after_failure(self, tmp_path: Path) -> None:
        db = tmp_path / "db.accdb"
        db.write_bytes(b"CORRUPT CONTENT")

        class _FakeDispatcher:
            def start(self):
                pass

            def call(self, fn, engine=None):
                return fn(engine)

            def shutdown(self):
                pass

        adapter = MagicMock()
        adapter.salvage_tables.side_effect = RuntimeError("COM failure")
        mc = MaintenanceUseCases(allowed_dirs=[tmp_path])
        with patch("ms_access_mcp.backends.dispatcher.ComDispatcher", return_value=_FakeDispatcher()):
            with patch("ms_access_mcp.backends.BackendSelector.get_adapter", return_value=adapter):
                result = mc.salvage_database(str(db), confirm=True)

        assert isinstance(result, Err)
        adapter.disconnect.assert_called_once_with()

    def test_salvage_disconnects_adapter_when_create_fresh_fails(self, tmp_path: Path) -> None:
        """The fresh adapter is disconnected even if create_database itself fails."""
        db = tmp_path / "db.accdb"
        db.write_bytes(b"CORRUPT CONTENT")

        class _FakeDispatcher:
            def start(self):
                pass

            def call(self, fn, engine=None):
                return fn(engine)

            def shutdown(self):
                pass

        adapter = MagicMock()
        adapter.create_database.side_effect = RuntimeError("disk full")
        mc = MaintenanceUseCases(allowed_dirs=[tmp_path])
        with patch("ms_access_mcp.backends.dispatcher.ComDispatcher", return_value=_FakeDispatcher()):
            with patch("ms_access_mcp.backends.BackendSelector.get_adapter", return_value=adapter):
                result = mc.salvage_database(str(db), confirm=True)

        assert isinstance(result, Err)
        assert "Cannot create fresh database" in result.message
        adapter.disconnect.assert_called_once_with()
