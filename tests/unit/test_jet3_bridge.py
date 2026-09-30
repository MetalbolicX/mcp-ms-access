"""Unit tests for jet3_bridge — happy path and error branches."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from ms_access_mcp.backends.format_detector import AccessFormat
from ms_access_mcp.backends.jet3_bridge import (
    JET3_CONVERT_VBS,
    bridge_available,
    convert_jet3_to_jet4,
)


class TestBridgeAvailable:
    """Tests for bridge_available()."""

    def test_returns_true_when_cscript_exists(self):
        """bridge_available() returns True when SysWOW64 cscript.exe exists."""
        with patch("pathlib.Path.is_file", return_value=True):
            with patch.dict(os.environ, {"SystemRoot": "C:\\Windows"}):
                result = bridge_available()
                assert result is True

    def test_returns_false_when_cscript_missing(self):
        """bridge_available() returns False when SysWOW64 cscript.exe is absent."""
        with patch("pathlib.Path.is_file", return_value=False):
            with patch.dict(os.environ, {"SystemRoot": "C:\\Windows"}):
                result = bridge_available()
                assert result is False

    def test_uses_systemroot_env(self):
        """bridge_available() resolves cscript from SystemRoot env var."""
        captured: list[Path] = []

        def fake_is_file(p: Path) -> bool:
            captured.append(p)
            return True

        with patch.object(Path, "is_file", fake_is_file):
            with patch.dict(os.environ, {"SystemRoot": "D:\\WINDOWS"}):
                assert bridge_available() is True
        assert captured == [Path("D:\\WINDOWS\\SysWOW64\\cscript.exe")]

    def test_falls_back_to_c_windows(self):
        """bridge_available() falls back to C:\\WINDOWS when SystemRoot is unset."""
        captured: list[Path] = []

        def fake_is_file(p: Path) -> bool:
            captured.append(p)
            return True

        env = os.environ.copy()
        env.pop("SystemRoot", None)
        with patch.object(Path, "is_file", fake_is_file):
            with patch.dict(os.environ, env, clear=True):
                assert bridge_available() is True
        assert captured == [Path("C:\\WINDOWS\\SysWOW64\\cscript.exe")]


class TestJet3ConvertVbsConstant:
    """Verify the VBS template contains the correct conversion parameters."""

    def test_contains_engine_type_5(self):
        """VBS must specify 'Engine Type=5' for Jet 4 output."""
        assert "Engine Type=5" in JET3_CONVERT_VBS

    def test_contains_jet_engine(self):
        """VBS must use JRO.JetEngine CompactDatabase."""
        assert "JRO.JetEngine" in JET3_CONVERT_VBS

    def test_passes_paths_via_arguments(self):
        """VBS must receive paths via WScript.Arguments, not string interpolation."""
        # WScript.Arguments(0) and WScript.Arguments(1) must appear
        assert "WScript.Arguments(0)" in JET3_CONVERT_VBS
        assert "WScript.Arguments(1)" in JET3_CONVERT_VBS


class TestConvertJet3HappyPath:
    """Happy-path conversion tests (subprocess mocked)."""

    @pytest.fixture
    def mock_cscript_check(self):
        """Skip real bridge_available check."""
        with patch.object(
            __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["bridge_available"]),
            "bridge_available",
            return_value=True,
        ):
            yield

    def test_calls_cscript_with_src_dst_args(
        self, tmp_path, mock_cscript_check
    ):
        """subprocess.run is called with cscript path, //nologo, vbs, src, dst."""
        src = str(tmp_path / "source.mdb")
        dst = str(tmp_path / "dest.mdb")

        # Touch src and dst so they pass the exists checks
        Path(src).touch()
        Path(dst).touch()

        # detect_access_format is called twice: once for src (JET3), once for dst (JET4)
        detect_calls = []

        def detect_side_effect(path):
            detect_calls.append(str(path))
            if "source" in str(path):
                return AccessFormat.JET3
            return AccessFormat.JET4

        with patch.object(
            __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["detect_access_format"]),
            "detect_access_format",
            side_effect=detect_side_effect,
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0, stderr=b"")
                result = convert_jet3_to_jet4(src, dst)

        from ms_access_mcp.errors import Ok

        assert isinstance(result, Ok)
        assert result.data["src"] == src
        assert result.data["dst"] == dst

        # Verify cscript was called correctly
        call_args = mock_run.call_args
        args_list = call_args[0][0] if call_args else None
        assert args_list is not None
        assert "cscript.exe" in args_list[0]
        assert "//nologo" in args_list[1:]

    def test_vbs_written_contains_engine_type_5(self, tmp_path, mock_cscript_check):
        """The VBS written to temp file must contain 'Engine Type=5'."""
        src = str(tmp_path / "source.mdb")
        dst = str(tmp_path / "dest.mdb")
        Path(src).touch()
        Path(dst).touch()

        vbs_written = []

        orig_named_temporary = tempfile.NamedTemporaryFile

        class FakeTempFile:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self._fh = orig_named_temporary(**kwargs)
                self.name = self._fh.name

            def write(self, data):
                vbs_written.append(data)
                return self._fh.write(data)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self._fh.__exit__(*args)

            def close(self):
                return self._fh.close()

        def detect_side_effect(path):
            if "source" in str(path):
                return AccessFormat.JET3
            return AccessFormat.JET4

        with patch.object(
            __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["detect_access_format"]),
            "detect_access_format",
            side_effect=detect_side_effect,
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0, stderr=b"")
                with patch("tempfile.NamedTemporaryFile", side_effect=FakeTempFile):
                    convert_jet3_to_jet4(src, dst)

        # Verify VBS content — written as UTF-16 LE bytes; decode to check string
        flat = b"".join(vbs_written)
        vbs_text = flat.decode("utf-16-le")
        assert "Engine Type=5" in vbs_text
        assert "JRO.JetEngine" in vbs_text


class TestConvertJet3Errors:
    """Error-branch conversion tests."""

    def test_err_missing_source(self):
        """Returns Err when source file does not exist."""
        result = convert_jet3_to_jet4("C:/nonexistent.mdb", "C:/out.mdb")
        assert isinstance(result.message, str)
        assert "not found" in result.message

    def test_err_wrong_format(self, tmp_path):
        """Returns Err when source is not Jet 3."""
        src = tmp_path / "notjet3.mdb"
        src.touch()

        with patch.object(
            __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["detect_access_format"]),
            "detect_access_format",
            return_value=AccessFormat.JET4,
        ):
            result = convert_jet3_to_jet4(str(src), str(tmp_path / "out.mdb"))

        assert isinstance(result.message, str)
        assert "not Jet 3" in result.message

    def test_err_bridge_unavailable(self, tmp_path):
        """Returns Err when 32-bit cscript is not found."""
        src = tmp_path / "source.mdb"
        src.touch()

        with patch.object(
            __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["detect_access_format"]),
            "detect_access_format",
            return_value=AccessFormat.JET3,
        ):
            with patch.object(
                __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["bridge_available"]),
                "bridge_available",
                return_value=False,
            ):
                result = convert_jet3_to_jet4(str(src), str(tmp_path / "out.mdb"))

        assert isinstance(result.message, str)
        assert "not found" in result.message

    def test_err_nonzero_exit(self, tmp_path):
        """Returns Err on non-zero subprocess exit code."""
        src = tmp_path / "source.mdb"
        dst = tmp_path / "out.mdb"
        src.touch()

        with patch.object(
            __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["detect_access_format"]),
            "detect_access_format",
            return_value=AccessFormat.JET3,
        ):
            with patch.object(
                __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["bridge_available"]),
                "bridge_available",
                return_value=True,
            ):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = Mock(
                        returncode=1,
                        stderr=b"Unspecified error",
                    )
                    result = convert_jet3_to_jet4(str(src), str(dst))

        assert isinstance(result.message, str)
        assert "failed (exit 1)" in result.message

    def test_err_dst_not_created(self, tmp_path):
        """Returns Err when destination file is not created after conversion."""
        src = tmp_path / "source.mdb"
        dst = tmp_path / "out.mdb"
        src.touch()

        with patch.object(
            __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["detect_access_format"]),
            "detect_access_format",
            return_value=AccessFormat.JET3,
        ):
            with patch.object(
                __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["bridge_available"]),
                "bridge_available",
                return_value=True,
            ):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = Mock(returncode=0, stderr=b"")
                    result = convert_jet3_to_jet4(str(src), str(dst))

        assert isinstance(result.message, str)
        assert "not created" in result.message

    def test_err_dst_wrong_format(self, tmp_path):
        """Returns Err when destination is not Jet 4 format after conversion."""
        src = tmp_path / "source.mdb"
        dst = tmp_path / "out.mdb"
        src.touch()

        with patch.object(
            __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["detect_access_format"]),
            "detect_access_format",
            side_effect=[AccessFormat.JET3, AccessFormat.JET3],  # src=Jet3, dst still Jet3
        ):
            with patch.object(
                __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["bridge_available"]),
                "bridge_available",
                return_value=True,
            ):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = Mock(returncode=0, stderr=b"")
                    # Mock os.path.isfile to return True for dst
                    with patch("pathlib.Path.is_file", return_value=True):
                        result = convert_jet3_to_jet4(str(src), str(dst))

        assert isinstance(result.message, str)
        assert "not Jet 4 format" in result.message

    def test_err_timeout(self, tmp_path):
        """Returns Err when conversion times out."""
        src = tmp_path / "source.mdb"
        dst = tmp_path / "out.mdb"
        src.touch()

        with patch.object(
            __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["detect_access_format"]),
            "detect_access_format",
            return_value=AccessFormat.JET3,
        ):
            with patch.object(
                __import__("ms_access_mcp.backends.jet3_bridge", fromlist=["bridge_available"]),
                "bridge_available",
                return_value=True,
            ):
                with patch("subprocess.run") as mock_run:
                    mock_run.side_effect = subprocess.TimeoutExpired("cmd", 60)
                    result = convert_jet3_to_jet4(str(src), str(dst))

        assert isinstance(result.message, str)
        assert "timed out" in result.message
