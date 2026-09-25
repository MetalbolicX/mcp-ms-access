"""Integration tests for Access.Application hidden adapter."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(
        __import__("os").name != "nt",
        reason="Windows-only test",
    ),
    pytest.mark.com_integration,
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
        """Integration: connect → read get_current_db_name() → disconnect; zero orphans within 10s."""
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
            db_name = adapter.get_current_db_name()
            assert db_name
            assert db_name.lower().endswith(".accdb")
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

    def test_single_open_invariant_two_consecutive_connects(self, temp_db_copy):
        """Plan-065 Step 6 operator gate: two consecutive COM adapter connects
        reuse exactly ONE MSACCESS.EXE process (connect-once invariant).

        Note: the use-case layer is backend-agnostic since plan 071 — the
        default DAO backend opens databases without spawning MSACCESS.EXE at
        all. The one-open invariant lives in the Access.Application COM path
        exercised here via ``connect_hidden``.
        """
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

        # The architecture serializes COM opens (plan 066's session registry
        # keeps one adapter per session; a second concurrent open on the same
        # app raises "You already have the database open"). The one-open
        # invariant under regression is therefore SEQUENTIAL: repeated
        # connect/disconnect cycles must reuse ONE MSACCESS.EXE process and
        # never accumulate orphans — the historical reconnect-every-call bug
        # that plan 065 fixed.
        db2 = str(Path(temp_db_copy).with_name(Path(temp_db_copy).stem + "_2.accdb"))
        shutil.copy2(temp_db_copy, db2)

        def _count_msaccess() -> int:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return len([ln for ln in result.stdout.splitlines() if "MSACCESS.EXE" in ln])

        adapter1 = connect_hidden(temp_db_copy)
        try:
            assert adapter1.connected is True
            assert adapter1.get_current_db_name()
        finally:
            adapter1.disconnect()
        time.sleep(2)  # allow process exit

        adapter2 = connect_hidden(db2)
        try:
            assert adapter2.connected is True
            assert adapter2.get_current_db_name()
            time.sleep(2)  # allow COM process state to settle
            assert _count_msaccess() == 1, (
                f"expected exactly 1 MSACCESS.EXE during the second connect cycle, "
                f"got {_count_msaccess()}"
            )
        finally:
            adapter2.disconnect()

        # Teardown returns to zero within 10s (mirrors the orphan test)
        for _ in range(20):
            time.sleep(0.5)
            if _count_msaccess() == 0:
                break
        else:
            pytest.fail(f"MSACCESS.EXE orphans still present after 10s: count={_count_msaccess()}")
        # Fallback cleanup: never leave MSACCESS.EXE behind
        subprocess.run(
            ["taskkill", "/F", "/IM", "MSACCESS.EXE"],
            capture_output=True,
            text=True,
            timeout=10,
        )
