"""Integration tests for Access.Application hidden adapter."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest


def _count_msaccess() -> int:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return len([line for line in result.stdout.splitlines() if "MSACCESS.EXE" in line])


def _kill_owned_access(adapter) -> None:
    """Kill only this adapter's captured PID; never terminate all Access processes."""
    pid = adapter._dispatcher._access_pid
    if pid is None:
        return
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if str(pid) in result.stdout:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
        )


pytestmark = [
    pytest.mark.skipif(
        __import__("os").name != "nt",
        reason="Windows-only test",
    ),
    pytest.mark.com_integration,
]


class TestDaoTopLimitProbe:
    def test_query_data_limit_returns_five_rows_from_twenty(self, temp_db_copy):
        import uuid

        from ms_access_mcp.application.database import DatabaseUseCases, Ok
        from ms_access_mcp.backends.dao import DaoAdapter

        table_name = f"LimitProbe_{uuid.uuid4().hex[:8]}"
        adapter = DaoAdapter(temp_db_copy)
        adapter.connect()
        try:
            adapter.execute_raw_sql(f"CREATE TABLE [{table_name}] ([id] INTEGER)")
            for row_id in range(20):
                adapter.execute_raw_sql(
                    f"INSERT INTO [{table_name}] ([id]) VALUES ({row_id})"
                )

            database = DatabaseUseCases()
            database._connections["default"] = adapter
            result = database.query_data(f"SELECT [id] FROM [{table_name}]", limit=5)

            assert isinstance(result, Ok), result
            assert len(result.data) == 5
        finally:
            try:
                adapter.execute_raw_sql(f"DROP TABLE [{table_name}]")
            except Exception:
                pass
            adapter.disconnect()


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
        """Connect/disconnect returns process count to its pre-test baseline within 10s."""
        pytest.importorskip("win32com")
        from ms_access_mcp.backends.access import connect_hidden

        baseline = _count_msaccess()
        adapter = connect_hidden(temp_db_copy)
        try:
            db_name = adapter.get_current_db_name()
            assert db_name
            assert db_name.lower().endswith(".accdb")
        finally:
            adapter.disconnect()

        # Wait up to 10 seconds for the process count to return to baseline.
        for _ in range(20):
            time.sleep(0.5)
            count = _count_msaccess()
            if count == baseline:
                break
        else:
            pytest.fail(
                f"MSACCESS.EXE count did not return to baseline {baseline} within 10s: {count}"
            )

    def test_single_open_invariant_two_consecutive_connects(self, temp_db_copy):
        """Plan-065 Step 6 operator gate: two consecutive COM connects
        reuse one process relative to the pre-test baseline.

        Note: the use-case layer is backend-agnostic since plan 071 — the
        default DAO backend opens databases without spawning MSACCESS.EXE at
        all. The one-open invariant lives in the Access.Application COM path
        exercised here via ``connect_hidden``.
        """
        pytest.importorskip("win32com")
        from ms_access_mcp.backends.access import connect_hidden

        baseline = _count_msaccess()

        # The architecture serializes COM opens (plan 066's session registry
        # keeps one adapter per session; a second concurrent open on the same
        # app raises "You already have the database open"). The one-open
        # invariant under regression is therefore SEQUENTIAL: repeated
        # connect/disconnect cycles must reuse ONE MSACCESS.EXE process and
        # never accumulate orphans — the historical reconnect-every-call bug
        # that plan 065 fixed.
        db2 = str(Path(temp_db_copy).with_name(Path(temp_db_copy).stem + "_2.accdb"))
        shutil.copy2(temp_db_copy, db2)

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
            assert _count_msaccess() == baseline + 1, (
                f"expected baseline + 1 ({baseline + 1}) MSACCESS.EXE during the second "
                f"connect cycle, got {_count_msaccess()}"
            )
        finally:
            adapter2.disconnect()

        # Teardown returns to the pre-test baseline within 10s.
        for _ in range(20):
            time.sleep(0.5)
            count = _count_msaccess()
            if count == baseline:
                break
        else:
            pytest.fail(
                f"MSACCESS.EXE count did not return to baseline {baseline} within 10s: {count}"
            )
        # Fallback cleanup is scoped to this adapter's captured process only.
        _kill_owned_access(adapter2)
