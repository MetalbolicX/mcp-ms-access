from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from ms_access_mcp.backends.dao import DaoAdapter
from ms_access_mcp.backends.dispatcher import ComDispatcher


class _Recordset:
    def __init__(self, rows: list[list[object]], *, fail_move: bool = False) -> None:
        self.rows = rows
        self.index = 0
        self.close_calls = 0
        self.fail_move = fail_move

    @property
    def EOF(self) -> bool:
        return self.index >= len(self.rows)

    @property
    def Fields(self):
        values = self.rows[self.index] if not self.EOF else []
        class _Fields:
            Count = len(values)

            def __call__(self, i):
                return SimpleNamespace(Name=f"field_{i}", Value=values[i])

        return _Fields()

    def MoveNext(self) -> None:
        if self.fail_move and self.index == 1:
            raise RuntimeError("COM boom")
        self.index += 1

    def Close(self) -> None:
        self.close_calls += 1
        if self.close_calls > 1:
            raise RuntimeError("already closed")


class _Dispatcher:
    def __init__(self, db) -> None:
        self.current_db = db

    def is_connected(self) -> bool:
        return True

    def call(self, fn, engine=None):
        _ = engine
        return fn()


def test_execute_query_closes_recordset_when_iteration_fails() -> None:
    recordset = _Recordset([[1], [2], [3]], fail_move=True)
    db = SimpleNamespace(OpenRecordset=lambda _sql: recordset)
    adapter = DaoAdapter("unused", dispatcher=cast(ComDispatcher, _Dispatcher(db)))

    result = adapter.execute_query("SELECT value")

    assert result.success is False
    assert result.error == "COM boom"
    assert recordset.close_calls == 1


def test_salvage_tables_closes_recordsets_when_field_read_fails() -> None:
    opened: list[_SalvageRecordset] = []

    class _Field:
        def __init__(self, name: str, value: object, *, fail: bool = False) -> None:
            self.Name = name
            self.Type = 10
            self.Size = 20
            self._value = value
            self.fail = fail

        @property
        def Value(self) -> object:
            if self.fail:
                raise RuntimeError("field read failed")
            return self._value

        @Value.setter
        def Value(self, value: object) -> None:
            self._value = value

    class _SalvageRecordset:
        def __init__(self, *, fail_value: bool = False) -> None:
            self.index = 0
            self.close_calls = 0
            self.fail_value = fail_value
            opened.append(self)

        @property
        def EOF(self) -> bool:
            return self.index >= 1

        @property
        def Fields(self):
            return _Fields(self.fail_value)

        def MoveNext(self) -> None:
            self.index += 1

        def AddNew(self) -> None:
            pass

        def Update(self) -> None:
            pass

        def CancelUpdate(self) -> None:
            pass

        def Close(self) -> None:
            self.close_calls += 1

    class _Fields:
        def __init__(self, fail_value: bool) -> None:
            self.Count = 1
            self.fail_value = fail_value

        def __call__(self, _index: int) -> _Field:
            return _Field("value", "row", fail=self.fail_value)

    class _Database:
        def __init__(self, table_names: list[str], *, is_fresh: bool = False) -> None:
            self.TableDefs = [SimpleNamespace(Name=name) for name in table_names]
            self.is_fresh = is_fresh
            self.open_count = 0

        def OpenRecordset(self, _sql: str, _kind: int = 2) -> _SalvageRecordset:
            self.open_count += 1
            fail_value = not self.is_fresh and self.open_count == 3
            recordset = _SalvageRecordset(fail_value=fail_value)
            return recordset

        def Execute(self, _sql: str) -> None:
            pass

        def Close(self) -> None:
            pass

    corrupt_db = _Database(["first_table", "second_table"])
    fresh_db = _Database([], is_fresh=True)

    class _Engine:
        def OpenDatabase(self, path: str) -> _Database:
            return corrupt_db if path == "corrupt.accdb" else fresh_db

    adapter = DaoAdapter("unused", dispatcher=cast(ComDispatcher, _Dispatcher(None)))
    reports = adapter.salvage_tables("corrupt.accdb", "fresh.accdb", engine=_Engine())

    second_table = next(report for report in reports if report["table_name"] == "second_table")
    assert second_table["error"] == "field read failed"
    assert opened
    assert all(recordset.close_calls == 1 for recordset in opened)
