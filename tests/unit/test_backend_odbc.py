"""Backend-level parameter contract for ``OdbcAdapter.execute_query``.

``None`` and empty sequences must call ``cursor.execute(sql)`` (no bind args);
a non-empty sequence must call ``cursor.execute(sql, params)`` and bind every
value, including ``None`` elements inside the list.
"""

from __future__ import annotations

from typing import Any

import pytest

from ms_access_mcp.backends.odbc import OdbcAdapter


class _RecordingCursor:
    description = [("id",)]

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.executed: list[tuple[Any, ...]] = []
        self._rows = list(rows or [])

    def execute(self, query: str, *params: Any) -> None:
        self.executed.append((query, *params))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def close(self) -> None:
        pass


class _RecordingConnection:
    def __init__(self, cursor: _RecordingCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _RecordingCursor:
        return self._cursor


def _adapter(rows: list[tuple[Any, ...]] | None = None) -> tuple[OdbcAdapter, _RecordingCursor]:
    adapter = OdbcAdapter("test.accdb")
    cursor = _RecordingCursor(rows)
    adapter._connected = True
    adapter._conn = _RecordingConnection(cursor)
    return adapter, cursor


class TestExecuteQueryParamsContract:
    @pytest.mark.parametrize("params", [None, []], ids=["none", "empty"])
    def test_absent_params_execute_plain_sql(self, params: Any):
        adapter, cursor = _adapter([(1,)])

        result = adapter.execute_query("SELECT id FROM t", params=params)

        assert result.success is True
        assert cursor.executed == [("SELECT id FROM t",)]

    def test_empty_tuple_params_execute_plain_sql(self):
        """An empty tuple is a no-bind call: ``cursor.execute(sql)``, no arguments."""
        adapter, cursor = _adapter([(1,)])

        result = adapter.execute_query("SELECT id FROM t", params=())

        assert result.success is True
        assert cursor.executed == [("SELECT id FROM t",)]

    def test_nonempty_params_bind_sequence(self):
        adapter, cursor = _adapter([(1,)])

        result = adapter.execute_query("SELECT id FROM t WHERE id = ?", params=[1])

        assert result.success is True
        assert cursor.executed == [("SELECT id FROM t WHERE id = ?", [1])]

    def test_none_element_inside_list_binds(self):
        adapter, cursor = _adapter([(0,)])

        result = adapter.execute_query("SELECT id FROM t WHERE x = ?", params=[None])

        assert result.success is True
        assert cursor.executed == [("SELECT id FROM t WHERE x = ?", [None])]

    def test_disconnected_behavior_preserved(self):
        adapter = OdbcAdapter("test.accdb")

        result = adapter.execute_query("SELECT 1")

        assert result.success is False
        assert result.error == "Not connected"

    def test_limit_rewrite_and_slice_preserved_without_params(self):
        adapter, cursor = _adapter([(row_id,) for row_id in range(20)])

        result = adapter.execute_query("select id from t", limit=5)

        assert result.success is True
        assert len(result.rows) == 5
        assert result.count == 5
        assert cursor.executed[0][0].upper().startswith("SELECT TOP 5")
        assert len(cursor.executed[0]) == 1  # still no bind arguments
