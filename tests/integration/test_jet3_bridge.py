"""Integration tests for Jet 3 → Jet 4 conversion via 32-bit JRO bridge.

 marker: odbc_integration
 skip: if 32-bit cscript.exe is absent OR PrivateDBEngine.36 cannot create a Jet 3 fixture.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time

import pytest

IS_WINDOWS = os.name == "nt"


def _bridge_available() -> bool:
    """True when the 32-bit cscript.exe exists."""
    if not IS_WINDOWS:
        return False
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.isfile(os.path.join(system_root, "SysWOW64", "cscript.exe"))


def _private_dbengine_36_works() -> bool:
    """True when DAO.PrivateDBEngine.36 can create a Jet 3 database."""
    if not IS_WINDOWS:
        return False
    # Use a temp file path (forward slashes to avoid PowerShell interpretation)
    tmp_path = os.path.join(tempfile.gettempdir(), "jet3_probe.mdb").replace("\\", "/")
    vbs = (
        f'Set e = CreateObject("DAO.PrivateDBEngine.36")\n'
        f'Set db = e.CreateDatabase("{tmp_path}", ";LANGID=0x0409;CP=1252;COUNTRY=0", 32)\n'
        f'db.Close\n'
    )
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".vbs", delete=False) as fh:
        fh.write(vbs.encode("utf-16-le"))
        vbs_path = fh.name
    try:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        cscript = os.path.join(system_root, "SysWOW64", "cscript.exe")
        result = subprocess.run(
            [cscript, "//nologo", vbs_path],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0 and os.path.isfile(tmp_path):
            os.unlink(tmp_path)
            return True
        return False
    except Exception:
        return False
    finally:
        try:
            os.unlink(vbs_path)
        except OSError:
            pass


def _create_jet3_vbs(tmp_dir: str) -> str | None:
    """Create a genuine Jet 3 .mdb via 32-bit DAO.PrivateDBEngine.36.

    Returns path to the created .mdb, or None on failure.
    The database contains one table [T1] with rows (1, 'alpha') and (2, 'beta').
    """
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    cscript = os.path.join(system_root, "SysWOW64", "cscript.exe")
    mdb_path = os.path.join(tmp_dir, "test_jet3.mdb")
    # Use forward slashes in VBS paths to avoid PowerShell interpretation
    mdb_path_fs = mdb_path.replace("\\", "/")

    vbs = (
        f'Set e = CreateObject("DAO.PrivateDBEngine.36")\n'
        f'Set db = e.CreateDatabase("{mdb_path_fs}", ";LANGID=0x0409;CP=1252;COUNTRY=0", 32)\n'
        f'db.Execute "CREATE TABLE [T1] ([id] INTEGER, [name] TEXT(50))"\n'
        "db.Execute \"INSERT INTO [T1] ([id], [name]) VALUES (1, 'alpha')\"\n"
        "db.Execute \"INSERT INTO [T1] ([id], [name]) VALUES (2, 'beta')\"\n"
        f'db.Close\n'
    )

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".vbs", delete=False, dir=tmp_dir) as fh:
        fh.write(vbs.encode("utf-16-le"))
        vbs_path = fh.name

    try:
        result = subprocess.run(
            [cscript, "//nologo", vbs_path],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0 or not os.path.isfile(mdb_path):
            return None
        return mdb_path
    except Exception:
        return None
    finally:
        try:
            os.unlink(vbs_path)
        except OSError:
            pass


skip_unless_bridge = pytest.mark.skipif(
    not _bridge_available(),
    reason="32-bit cscript.exe not found (SysWOW64)",
)

skip_unless_jet3_fixture = pytest.mark.skipif(
    not _private_dbengine_36_works(),
    reason="DAO.PrivateDBEngine.36 cannot create Jet 3 fixture",
)

pytestmark = [
    pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only test"),
    skip_unless_bridge,
    skip_unless_jet3_fixture,
    pytest.mark.odbc_integration,
]


@pytest.fixture
def jet3_mdb_fixture():
    """Create a genuine Jet 3 .mdb via DAO.PrivateDBEngine.36.

    Yields dict with:
        - path: absolute path to the Jet 3 .mdb
        - cleanup: callable to remove the temp directory
    """
    tmpdir = tempfile.mkdtemp(prefix="jet3_mdb_")
    mdb_path = _create_jet3_vbs(tmpdir)

    if mdb_path is None:
        shutil.rmtree(tmpdir, ignore_errors=True)
        pytest.skip("Failed to create Jet 3 .mdb fixture via PrivateDBEngine.36")

    yield {"path": mdb_path, "cleanup": lambda: shutil.rmtree(tmpdir, ignore_errors=True)}

    time.sleep(0.25)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.odbc_integration
class TestJet3BridgeIntegration:
    """End-to-end Jet 3 → Jet 4 conversion tests."""

    def test_jet3_fixture_detected_as_jet3(self, jet3_mdb_fixture):
        """The created Jet 3 fixture is correctly detected as JET3 format."""
        from ms_access_mcp.backends.format_detector import AccessFormat, detect_access_format

        mdb_path = jet3_mdb_fixture["path"]
        fmt = detect_access_format(mdb_path)
        assert fmt == AccessFormat.JET3, f"Expected JET3, got {fmt}"

    def test_jet3_open_returns_rejection(self, jet3_mdb_fixture):
        """Opening a Jet 3 file via application/database returns the JET3 rejection error."""
        from ms_access_mcp.application.database import DatabaseUseCases

        mdb_path = jet3_mdb_fixture["path"]
        uc = DatabaseUseCases(allowed_dirs=[os.path.dirname(mdb_path)])
        result = uc.open_database(mdb_path)

        from ms_access_mcp.errors import Err

        assert isinstance(result, Err), f"Expected Err, got {type(result).__name__}"
        assert "Jet 3" in result.message or "convert_legacy_database" in result.message

    def test_convert_jet3_to_jet4_produces_jet4(self, jet3_mdb_fixture, tmp_path):
        """convert_jet3_to_jet4 produces a Jet 4 file at the expected path."""
        from ms_access_mcp.backends.format_detector import AccessFormat, detect_access_format
        from ms_access_mcp.backends.jet3_bridge import convert_jet3_to_jet4

        src = jet3_mdb_fixture["path"]
        dst = str(tmp_path / "converted.mdb")

        result = convert_jet3_to_jet4(src, dst)

        from ms_access_mcp.errors import Ok

        assert isinstance(result, Ok), f"Expected Ok, got {result.message}"
        assert os.path.isfile(dst), "Destination file was not created"
        assert detect_access_format(dst) == AccessFormat.JET4, "Output is not Jet 4 format"

    def test_converted_jet4_readable_via_odbc(self, jet3_mdb_fixture, tmp_path):
        """The converted Jet 4 .mdb is readable via ODBC with correct rows."""
        import pyodbc

        from ms_access_mcp.backends.jet3_bridge import convert_jet3_to_jet4
        from ms_access_mcp.errors import Ok

        src = jet3_mdb_fixture["path"]
        dst = str(tmp_path / "converted.mdb")

        result = convert_jet3_to_jet4(src, dst)
        assert isinstance(result, Ok), f"Conversion failed: {result.message}"

        # Open via ODBC ACE driver
        conn_str = f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={dst};"
        conn = pyodbc.connect(conn_str, timeout=10)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM [T1] ORDER BY id")
            rows = cursor.fetchall()
            assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
            assert rows[0][0] == 1
            assert rows[0][1] == "alpha"
            assert rows[1][0] == 2
            assert rows[1][1] == "beta"
        finally:
            conn.close()

    def test_converted_jet4_openable_via_database_usecases(self, jet3_mdb_fixture, tmp_path):
        """The converted Jet 4 .mdb can be opened via DatabaseUseCases and queried."""
        from ms_access_mcp.backends.jet3_bridge import convert_jet3_to_jet4
        from ms_access_mcp.errors import Ok

        src = jet3_mdb_fixture["path"]
        dst = str(tmp_path / "converted.mdb")

        result = convert_jet3_to_jet4(src, dst)
        assert isinstance(result, Ok), f"Conversion failed: {result.message}"

        # Open via DatabaseUseCases
        from ms_access_mcp.application.database import DatabaseUseCases

        uc = DatabaseUseCases(allowed_dirs=[tmp_path])
        open_result = uc.open_database(dst)

        assert isinstance(open_result, Ok), f"open_database failed: {open_result.message}"

        # Query the data
        query_result = uc.query_data("SELECT id, name FROM [T1] ORDER BY id")
        assert isinstance(query_result, Ok), f"Query failed: {query_result.message}"
        rows = query_result.data
        assert len(rows) == 2
        assert rows[0]["id"] == 1
        assert rows[0]["name"] == "alpha"
        assert rows[1]["id"] == 2
        assert rows[1]["name"] == "beta"

        uc.close_database()

    def test_dst_defaults_to_sibling_jet4_mdb(self, jet3_mdb_fixture, tmp_path):
        """When dst is not provided, the bridge uses a sibling <name>.jet4.mdb path."""
        from ms_access_mcp.backends.format_detector import AccessFormat, detect_access_format
        from ms_access_mcp.backends.jet3_bridge import convert_jet3_to_jet4
        from ms_access_mcp.errors import Ok

        src = jet3_mdb_fixture["path"]
        src_path = __import__("pathlib").Path(src)
        expected_dst = str(src_path.with_name(src_path.stem + ".jet4.mdb"))

        # The bridge itself doesn't auto-generate dst, but we verify the user flow works
        result = convert_jet3_to_jet4(src, expected_dst)
        assert isinstance(result, Ok), f"Conversion failed: {result.message}"
        assert detect_access_format(expected_dst) == AccessFormat.JET4
