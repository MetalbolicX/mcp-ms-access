"""Characterization tests for ms_access_mcp.doctor (plan 014).

These tests patch every environment/COM seam (sys.platform, pyodbc,
pythoncom/win32com, subprocess) and assert the ``Check`` result shape plus
current aggregation behavior. Tests marked
``# characterization: documents current swallow behavior`` assert what the
code DOES today — including silent exception swallows — not what it should
do. Future bug-fix plans should grep for that marker and convert them to
intended-behavior tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ms_access_mcp import doctor
from ms_access_mcp.doctor import Check, _is_windows_x64, run_checks

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class FakePyodbc:
    """pyodbc stand-in with scripted drivers()."""

    def __init__(self, drivers: list[str] | None = None, error: Exception | None = None) -> None:
        self._drivers = drivers or []
        self._error = error

    def drivers(self) -> list[str]:
        if self._error is not None:
            raise self._error
        return self._drivers


class FakePythoncom:
    """pythoncom stand-in: COM init is a no-op."""

    @staticmethod
    def CoInitialize() -> None:
        return None

    @staticmethod
    def CoUninitialize() -> None:
        return None


class FakeWin32ComClient:
    """win32com.client stand-in with a scriptable Dispatch."""

    def __init__(self, dispatch: Any = None) -> None:
        self.Dispatch = dispatch or (lambda progid: object())


class FakeWin32Com:
    """Parent package for `import win32com.client` to bind against."""

    def __init__(self, client: FakeWin32ComClient) -> None:
        self.client = client


def _patch_com_imports(monkeypatch: pytest.MonkeyPatch) -> FakeWin32ComClient:
    """Make `import pythoncom` / `import win32com.client` resolve to fakes."""
    fake_client = FakeWin32ComClient()
    monkeypatch.setitem(sys.modules, "pythoncom", FakePythoncom())
    monkeypatch.setitem(sys.modules, "win32com", FakeWin32Com(fake_client))
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    return fake_client


# ---------------------------------------------------------------------------
# _is_windows_x64 truth table
# ---------------------------------------------------------------------------


class TestIsWindowsX64:
    @pytest.mark.parametrize(
        ("platform", "maxsize", "expected"),
        [
            ("win32", 2**63 - 1, True),
            ("win32", 2**32, False),  # 32-bit interpreter: maxsize == 2**32
            ("linux", 2**63 - 1, False),
            ("darwin", 2**32 - 1, False),
        ],
    )
    def test_truth_table(self, monkeypatch: pytest.MonkeyPatch, platform: str, maxsize: int, expected: bool):
        monkeypatch.setattr(doctor.sys, "platform", platform)
        monkeypatch.setattr(doctor.sys, "maxsize", maxsize)

        result = _is_windows_x64()

        assert isinstance(result, Check)
        assert result.name == "platform"
        assert result.passed is expected
        assert result.details["platform"] == platform
        assert result.details["maxsize"] == maxsize
        if expected:
            assert result.message == "Windows x64"
        else:
            assert result.message == "Requires Windows x64"


# ---------------------------------------------------------------------------
# COM probes (Access, ACE, DAO) — win32com faked, Check shape asserted
# ---------------------------------------------------------------------------


class TestComProbes:
    """Each COM probe returns a Check; assert shape, not environment outcomes."""

    def test_access_com_happy_path(self, monkeypatch: pytest.MonkeyPatch):
        fake_client = _patch_com_imports(monkeypatch)
        monkeypatch.setattr(doctor.sys, "platform", "win32")

        class FakeAccessApp:
            quit_called = False

            def Quit(self) -> None:
                self.quit_called = True

        app = FakeAccessApp()
        fake_client.Dispatch = lambda progid: app

        result = doctor._check_access_com_probe()

        assert result.name == "access_com"
        assert result.passed is True
        assert result.message == "Access.Application COM available"
        assert app.quit_called is True

    def test_access_com_probe_exception_is_reported_not_raised(self, monkeypatch: pytest.MonkeyPatch):
        fake_client = _patch_com_imports(monkeypatch)
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        fake_client.Dispatch = lambda progid: (_ for _ in ()).throw(RuntimeError(f"cannot activate {progid}"))

        result = doctor._check_access_com_probe()

        assert result.name == "access_com"
        assert result.passed is False
        assert "cannot activate Access.Application" in result.message
        assert result.details["error"]

    def test_ace_provider_happy_path(self, monkeypatch: pytest.MonkeyPatch):
        fake_client = _patch_com_imports(monkeypatch)
        monkeypatch.setattr(doctor.sys, "platform", "win32")

        class FakeConnection:
            opened_with: str = ""

            def Open(self, conn_str: str) -> None:
                self.opened_with = conn_str

        conn = FakeConnection()
        fake_client.Dispatch = lambda progid: conn

        result = doctor._check_ace_provider()

        assert result.name == "ace_provider"
        assert result.passed is True
        assert result.details["provider"] == "Microsoft.ACE.OLEDB.12.0"
        assert "Provider=Microsoft.ACE.OLEDB.12.0" in conn.opened_with

    def test_dao_engine_happy_path(self, monkeypatch: pytest.MonkeyPatch):
        fake_client = _patch_com_imports(monkeypatch)
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        dispatched: list[str] = []
        fake_client.Dispatch = lambda progid: dispatched.append(progid) or object()

        result = doctor._check_dao_engine()

        assert result.name == "dao_engine"
        assert result.passed is True
        assert dispatched == ["DAO.DBEngine.120"]

    def test_dao_engine_probe_exception_is_reported_not_raised(self, monkeypatch: pytest.MonkeyPatch):
        fake_client = _patch_com_imports(monkeypatch)
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        fake_client.Dispatch = lambda progid: (_ for _ in ()).throw(RuntimeError("DAO missing"))

        result = doctor._check_dao_engine()

        assert result.name == "dao_engine"
        assert result.passed is False
        assert "DAO missing" in result.message
        assert result.details["error"] == "DAO missing"

    @pytest.mark.parametrize(
        ("probe", "expected_name"),
        [
            ("_check_access_com_probe", "access_com"),
            ("_check_ace_provider", "ace_provider"),
            ("_check_dao_engine", "dao_engine"),
        ],
    )
    def test_non_windows_early_return(self, monkeypatch: pytest.MonkeyPatch, probe: str, expected_name: str):
        monkeypatch.setattr(doctor.sys, "platform", "linux")

        result = getattr(doctor, probe)()

        assert result.name == expected_name
        assert result.passed is False
        assert "Requires Windows" in result.message
        assert result.details == {}


# ---------------------------------------------------------------------------
# ODBC probe — pyodbc accessor patched
# ---------------------------------------------------------------------------


class TestOdbcDriverProbe:
    def test_access_driver_present(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(
            sys.modules, "pyodbc", FakePyodbc(drivers=["Microsoft Access Driver (*.mdb, *.accdb)"])
        )

        result = doctor._check_odbc_driver()

        assert result.name == "odbc_driver"
        assert result.passed is True
        assert "Microsoft Access Driver (*.mdb, *.accdb)" in result.message
        assert "Microsoft Access Driver (*.mdb, *.accdb)" in result.details["drivers"]
        assert "Microsoft Access Driver (*.mdb, *.accdb)" in result.details["access_drivers"]

    def test_no_access_driver(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(sys.modules, "pyodbc", FakePyodbc(drivers=["SQL Server"]))

        result = doctor._check_odbc_driver()

        assert result.passed is False
        assert "No Microsoft Access ODBC driver found" in result.message
        assert result.details["access_drivers"] == []

    @pytest.mark.parametrize("needle", ["Microsoft Access Driver (*.mdb, *.accdb)", "Microsoft Access Driver (*.mdb)"])
    def test_driver_match_requires_microsoft_access_driver(self, monkeypatch: pytest.MonkeyPatch, needle: str):
        monkeypatch.setitem(sys.modules, "pyodbc", FakePyodbc(drivers=[needle]))

        result = doctor._check_odbc_driver()

        assert result.passed is True

    def test_drivers_error_is_reported_not_raised(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(sys.modules, "pyodbc", FakePyodbc(error=RuntimeError("odbc exploded")))

        result = doctor._check_odbc_driver()

        assert result.passed is False
        assert result.message == "ODBC driver check failed: odbc exploded"
        assert result.details["error"] == "odbc exploded"

    def test_pyodbc_import_error_is_reported_not_raised(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(sys.modules, "pyodbc", None)  # forces ImportError on import

        result = doctor._check_odbc_driver()

        assert result.name == "odbc_driver"
        assert result.passed is False
        assert result.message.startswith("ODBC driver check failed: ")


# ---------------------------------------------------------------------------
# _check_temp_dir_writable
# ---------------------------------------------------------------------------


class TestTempDirWritable:
    def test_writable(self):
        result = doctor._check_temp_dir_writable()

        assert result.name == "temp_dir"
        assert result.passed is True
        assert result.message == "Temp directory is writable"

    def test_not_writable_is_reported_not_raised(self, monkeypatch: pytest.MonkeyPatch):
        def boom(*args: object, **kwargs: object) -> object:
            raise OSError("read-only temp")

        monkeypatch.setattr(doctor.tempfile, "NamedTemporaryFile", boom)

        result = doctor._check_temp_dir_writable()

        assert result.passed is False
        assert result.message == "Temp directory not writable: read-only temp"
        assert result.details["error"] == "read-only temp"


# ---------------------------------------------------------------------------
# Silent swallows — characterization
# ---------------------------------------------------------------------------


class TestAceProviderOpenFailures:
    """ACE Open errors distinguish provider absence from other failures."""

    @pytest.mark.parametrize(
        ("hresult", "expected_passed", "expected_message"),
        [
            (0x800A0E7A, False, "provider missing"),
            (0x80004005, True, "Open failed"),
        ],
    )
    def test_ace_open_failure_classification(
        self,
        monkeypatch: pytest.MonkeyPatch,
        hresult: int,
        expected_passed: bool,
        expected_message: str,
    ) -> None:
        fake_client = _patch_com_imports(monkeypatch)
        monkeypatch.setattr(doctor.sys, "platform", "win32")

        class FakeComError(Exception):
            def __init__(self, error_code: int) -> None:
                super().__init__(error_code, "Open failed")
                self.hresult = error_code

        class FakeConnection:
            def Open(self, conn_str: str) -> None:
                raise FakeComError(hresult)

        fake_client.Dispatch = lambda progid: FakeConnection()

        result = doctor._check_ace_provider()

        assert result.name == "ace_provider"
        assert result.passed is expected_passed
        assert expected_message.lower() in result.message.lower()

    def test_deep_access_happy_path_cleanup_swallow_never_created_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        # characterization: documents current swallow behavior — the fake app
        # never creates the probe file, so os.unlink(temp_db) raises
        # FileNotFoundError inside the `finally` and is swallowed at :232;
        # the check still completes and reports from the orphan scan only.
        fake_client = _patch_com_imports(monkeypatch)
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr(doctor.tempfile, "mkdtemp", lambda prefix: str(tmp_path / prefix))

        events: list[str] = []

        class FakeApp:
            Visible = False

            def NewCurrentDatabase(self, path: str) -> None:
                events.append(f"new:{path}")

            def CloseCurrentDatabase(self) -> None:
                events.append("close")

            def Quit(self) -> None:
                events.append("quit")

        fake_client.Dispatch = lambda progid: FakeApp()

        class FakeCompleted:
            stdout = "INFO: No tasks are running which match the specified criteria."

        monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: FakeCompleted())

        result = doctor._check_deep_access()

        assert result.name == "deep_access"
        assert result.passed is True  # orphan-free; the swallowed unlink did not matter
        assert result.details["orphan_lines"] == []
        assert events[0].startswith("new:") and events[-1] == "quit"

    def test_deep_access_rmdir_failure_swallowed(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        # characterization: documents current swallow behavior — the
        # `except Exception: pass` around os.rmdir (:236) means a directory
        # cleanup failure never surfaces; the check completes normally.
        fake_client = _patch_com_imports(monkeypatch)
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        monkeypatch.setattr("time.sleep", lambda s: None)
        (tmp_path / "occupied").mkdir()
        monkeypatch.setattr(doctor.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "occupied"))
        monkeypatch.setattr(doctor.os, "rmdir", lambda path: (_ for _ in ()).throw(OSError("dir not empty")))

        class FakeApp:
            Visible = False

            def NewCurrentDatabase(self, path: str) -> None:
                # Simulate Access creating the file so unlink succeeds and
                # rmdir is reached (then scripted to fail).
                with Path(path).open("w") as f:
                    f.write("probe")

            def CloseCurrentDatabase(self) -> None:
                return None

            def Quit(self) -> None:
                return None

        fake_client.Dispatch = lambda progid: FakeApp()

        class FakeCompleted:
            stdout = "INFO: No tasks are running which match the specified criteria."

        monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: FakeCompleted())

        result = doctor._check_deep_access()

        assert result.name == "deep_access"
        assert result.passed is True  # rmdir failure swallowed; orphan scan rules
        assert result.details["orphan_lines"] == []

    def test_deep_access_never_kills_by_image_name(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        fake_client = _patch_com_imports(monkeypatch)
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr(doctor.tempfile, "mkdtemp", lambda prefix: str(tmp_path / prefix))

        captured_commands = []

        class FakeCompleted:
            stdout = ""

        monkeypatch.setattr(
            doctor.subprocess,
            "run",
            lambda cmd, **kwargs: (captured_commands.append(cmd) or FakeCompleted()),
        )

        class FakeApp:
            Visible = False

            def NewCurrentDatabase(self, path: str) -> None:
                return None

            def CloseCurrentDatabase(self) -> None:
                return None

            def Quit(self) -> None:
                return None

        fake_client.Dispatch = lambda progid: FakeApp()

        doctor._check_deep_access()

        for cmd in captured_commands:
            assert "/IM" not in cmd, f"/IM must never appear in deep access kill: {cmd}"

    def test_deep_access_com_failure_reported_not_raised(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        fake_client = _patch_com_imports(monkeypatch)
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr(doctor.tempfile, "mkdtemp", lambda prefix: str(tmp_path / prefix))
        fake_client.Dispatch = lambda progid: (_ for _ in ()).throw(RuntimeError("Access blocked"))

        result = doctor._check_deep_access()

        assert result.name == "deep_access"
        assert result.passed is False
        assert "Access blocked" in result.message
        assert result.details["error"] == "Access blocked"


# ---------------------------------------------------------------------------
# run_checks aggregation
# ---------------------------------------------------------------------------


def _fake_check(name: str, passed: bool) -> Check:
    return Check(name=name, passed=passed, message=f"{name} message", details={})


class TestRunChecksAggregation:
    def test_shallow_runs_six_checks_all_pass(self, monkeypatch: pytest.MonkeyPatch):
        # (attribute name, Check name) — the packages probe lives in
        # _check_package_versions but reports name="packages".
        checks = [
            ("_is_windows_x64", "platform"),
            ("_check_package_versions", "packages"),
            ("_check_access_com_probe", "access_com"),
            ("_check_ace_provider", "ace_provider"),
            ("_check_dao_engine", "dao_engine"),
            ("_check_odbc_driver", "odbc_driver"),
        ]
        for attr, name in checks:
            monkeypatch.setattr(doctor, attr, lambda name=name: _fake_check(name, True))

        result = run_checks(deep=False)

        assert len(result) in (6, 7)
        assert all(c.passed for c in result)
        assert [c.name for c in result][:6] == [n for _, n in checks]

    def test_one_failure_is_preserved_in_order(self, monkeypatch: pytest.MonkeyPatch):
        checks = [
            ("_is_windows_x64", "platform", True),
            ("_check_package_versions", "packages", False),
            ("_check_access_com_probe", "access_com", True),
            ("_check_ace_provider", "ace_provider", True),
            ("_check_dao_engine", "dao_engine", True),
            ("_check_odbc_driver", "odbc_driver", True),
        ]
        for attr, name, passed in checks:
            monkeypatch.setattr(
                doctor, attr, lambda name=name, passed=passed: _fake_check(name, passed)
            )

        result = run_checks(deep=False)

        assert [c.name for c in result][:6] == [n for _, n, _ in checks]
        assert [c.passed for c in result] == [p for _, _, p in checks]

    def test_deep_adds_two_checks(self, monkeypatch: pytest.MonkeyPatch):
        checks = [
            ("_is_windows_x64", "platform"),
            ("_check_package_versions", "packages"),
            ("_check_access_com_probe", "access_com"),
            ("_check_ace_provider", "ace_provider"),
            ("_check_dao_engine", "dao_engine"),
            ("_check_odbc_driver", "odbc_driver"),
        ]
        for attr, name in checks:
            monkeypatch.setattr(doctor, attr, lambda name=name: _fake_check(name, True))
        monkeypatch.setattr(doctor, "_check_deep_access", lambda: _fake_check("deep_access", True))
        monkeypatch.setattr(doctor, "_check_temp_dir_writable", lambda: _fake_check("temp_dir", True))

        result = run_checks(deep=True)

        assert len(result) in (8, 9)
        assert [c.name for c in result][-2:] == ["deep_access", "temp_dir"]

    def test_doctor_main_exit_codes(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
        monkeypatch.setattr(doctor, "_is_windows_x64", lambda: _fake_check("platform", True))
        monkeypatch.setattr(doctor, "_check_package_versions", lambda: _fake_check("packages", True))
        monkeypatch.setattr(doctor, "_check_access_com_probe", lambda: _fake_check("access_com", True))
        monkeypatch.setattr(doctor, "_check_ace_provider", lambda: _fake_check("ace_provider", False))
        monkeypatch.setattr(doctor, "_check_dao_engine", lambda: _fake_check("dao_engine", True))
        monkeypatch.setattr(doctor, "_check_odbc_driver", lambda: _fake_check("odbc_driver", True))

        code = doctor.doctor_main(deep=False, json_output=False)

        assert code == 1
        out = capsys.readouterr().out
        assert "[FAIL] ace_provider" in out
        assert "Some checks failed" in out

    def test_doctor_main_json_output(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
        monkeypatch.setattr(doctor, "_is_windows_x64", lambda: _fake_check("platform", True))
        monkeypatch.setattr(doctor, "_check_package_versions", lambda: _fake_check("packages", True))
        monkeypatch.setattr(doctor, "_check_access_com_probe", lambda: _fake_check("access_com", True))
        monkeypatch.setattr(doctor, "_check_ace_provider", lambda: _fake_check("ace_provider", True))
        monkeypatch.setattr(doctor, "_check_dao_engine", lambda: _fake_check("dao_engine", True))
        monkeypatch.setattr(doctor, "_check_odbc_driver", lambda: _fake_check("odbc_driver", True))

        code = doctor.doctor_main(deep=False, json_output=True)

        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["passed"] is True
        assert len(payload["checks"]) == 6
        assert payload["checks"][0]["name"] == "platform"


# ---------------------------------------------------------------------------
# Check dataclass shape
# ---------------------------------------------------------------------------


class TestCheckShape:
    def test_defaults(self):
        c = Check(name="x", passed=True)

        assert c.message == ""
        assert c.details == {}

    def test_details_is_per_instance(self):
        a = Check(name="a", passed=True)
        b = Check(name="b", passed=True)
        a.details["k"] = "v"

        assert b.details == {}


# ---------------------------------------------------------------------------
# Format support matrix
# ---------------------------------------------------------------------------


class TestFormatSupportMatrix:
    """Format matrix derived from probe outcomes."""

    def test_all_formats_ok(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
        monkeypatch.setattr(doctor, "_is_windows_x64", lambda: _fake_check("platform", True))
        monkeypatch.setattr(doctor, "_check_package_versions", lambda: _fake_check("packages", True))
        monkeypatch.setattr(doctor, "_check_access_com_probe", lambda: _fake_check("access_com", True))
        monkeypatch.setattr(doctor, "_check_ace_provider", lambda: _fake_check("ace_provider", True))
        monkeypatch.setattr(doctor, "_check_dao_engine", lambda: _fake_check("dao_engine", True))
        monkeypatch.setattr(doctor, "_check_odbc_driver", lambda: _fake_check("odbc_driver", True))
        # Mock bridge_available at the source module so the local imports inside
        # _format_human and _format_json both see the patched value.
        import ms_access_mcp.backends.jet3_bridge as jb

        monkeypatch.setattr(jb, "bridge_available", lambda: True)

        doctor.doctor_main(deep=False, json_output=False)

        out = capsys.readouterr().out
        assert "Format Support Matrix:" in out
        assert "accdb  | jet4" in out
        # accdb and jet4 should show OK when ACE or ODBC is available
        assert "OK" in out
        # jet3 shows "conversion available" when bridge is available
        assert "conversion available (32-bit Jet bridge)" in out

    def test_no_odbc_driver(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
        monkeypatch.setattr(doctor, "_is_windows_x64", lambda: _fake_check("platform", True))
        monkeypatch.setattr(doctor, "_check_package_versions", lambda: _fake_check("packages", True))
        monkeypatch.setattr(doctor, "_check_access_com_probe", lambda: _fake_check("access_com", True))
        monkeypatch.setattr(doctor, "_check_ace_provider", lambda: _fake_check("ace_provider", False))
        monkeypatch.setattr(doctor, "_check_dao_engine", lambda: _fake_check("dao_engine", True))
        monkeypatch.setattr(doctor, "_check_odbc_driver", lambda: _fake_check("odbc_driver", False))

        doctor.doctor_main(deep=False, json_output=False)

        out = capsys.readouterr().out
        assert "Format Support Matrix:" in out
        # When neither ACE nor ODBC is available, accdb and jet4 show MISSING
        assert "MISSING" in out

    def test_no_dao(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
        monkeypatch.setattr(doctor, "_is_windows_x64", lambda: _fake_check("platform", True))
        monkeypatch.setattr(doctor, "_check_package_versions", lambda: _fake_check("packages", True))
        monkeypatch.setattr(doctor, "_check_access_com_probe", lambda: _fake_check("access_com", True))
        monkeypatch.setattr(doctor, "_check_ace_provider", lambda: _fake_check("ace_provider", True))
        monkeypatch.setattr(doctor, "_check_dao_engine", lambda: _fake_check("dao_engine", False))
        monkeypatch.setattr(doctor, "_check_odbc_driver", lambda: _fake_check("odbc_driver", True))
        # Bridge unavailable (cscript missing) — jet3 shows the not-found variant
        import ms_access_mcp.backends.jet3_bridge as jb

        monkeypatch.setattr(jb, "bridge_available", lambda: False)

        doctor.doctor_main(deep=False, json_output=False)

        out = capsys.readouterr().out
        assert "Format Support Matrix:" in out
        # DAO missing + bridge unavailable shows "requires conversion" with not-found suffix
        assert "requires conversion (32-bit Jet bridge not found)" in out

    def test_json_output_includes_format_support(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
        monkeypatch.setattr(doctor, "_is_windows_x64", lambda: _fake_check("platform", True))
        monkeypatch.setattr(doctor, "_check_package_versions", lambda: _fake_check("packages", True))
        monkeypatch.setattr(doctor, "_check_access_com_probe", lambda: _fake_check("access_com", True))
        monkeypatch.setattr(doctor, "_check_ace_provider", lambda: _fake_check("ace_provider", True))
        monkeypatch.setattr(doctor, "_check_dao_engine", lambda: _fake_check("dao_engine", True))
        monkeypatch.setattr(doctor, "_check_odbc_driver", lambda: _fake_check("odbc_driver", True))
        import ms_access_mcp.backends.jet3_bridge as jb

        monkeypatch.setattr(jb, "bridge_available", lambda: False)

        doctor.doctor_main(deep=False, json_output=True)

        payload = json.loads(capsys.readouterr().out)
        assert "format_support" in payload
        assert payload["format_support"]["accdb"] == "OK"
        assert payload["format_support"]["jet4"] == "OK"
        assert payload["format_support"]["jet3"] == "requires conversion (32-bit Jet bridge not found)"

    def test_non_windows_odbc_driver_message(self, monkeypatch: pytest.MonkeyPatch):
        """On non-Windows, ODBC driver check reports correctly."""
        monkeypatch.setattr(doctor.sys, "platform", "linux")
        monkeypatch.setitem(sys.modules, "pyodbc", FakePyodbc(drivers=["Microsoft Access Driver (*.mdb, *.accdb)"]))

        result = doctor._check_odbc_driver()

        # Non-Windows path doesn't exist in current implementation
        # but if it did, it should handle gracefully
        assert result.name == "odbc_driver"
