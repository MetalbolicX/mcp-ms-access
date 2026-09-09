"""Integration tests for Access.Application hidden adapter."""

from __future__ import annotations

import subprocess
import time

import pytest

pytestmark = [
    pytest.mark.skipif(
        __import__("os").name != "nt",
        reason="Windows-only test",
    ),
]


class TestAccessAdapterHidden:
    """Test hidden Access.Application connect and teardown."""

    def test_access_adapter_not_connected_initially(self):
        pytest.importorskip("win32com")
        from ms_access_mcp.backends.access import AccessAdapter
        adapter = AccessAdapter("test.accdb")
        assert adapter.is_connected() is False

    def test_disconnect_when_not_connected(self):
        pytest.importorskip("win32com")
        from ms_access_mcp.backends.access import AccessAdapter
        adapter = AccessAdapter("test.accdb")
        adapter.disconnect()  # should not raise

    def test_no_msaccess_orphans_after_disconnect(self, temp_db_copy):
        """Integration: connect → read CurrentDb().Name → disconnect; zero orphans within 10s."""
        pytest.importorskip("win32com")
        from ms_access_mcp.backends.access import connect_hidden

        # Ensure no orphans before starting
        subprocess.run(
            ["taskkill", "/F", "/IM", "MSACCESS.EXE"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        time.sleep(1)

        adapter = connect_hidden(temp_db_copy)
        try:
            db_name = adapter.get_current_db().Name
            assert db_name is not None
        finally:
            adapter.disconnect()

        # Wait up to 10 seconds for MSACCESS.EXE to disappear
        orphan_lines: list[str] = []
        for _ in range(20):
            time.sleep(0.5)
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            orphan_lines = [ln for ln in result.stdout.splitlines() if "MSACCESS.EXE" in ln]
            if not orphan_lines:
                break
        else:
            pytest.fail(f"MSACCESS.EXE orphans still present after 10s: {orphan_lines}")
