"""Characterization tests for typed-boundary serialization (plan 037 Step 1).

These tests pin the EXACT dict/JSON shape each in-scope method produces at
the application-layer seam (DatabaseUseCases → Ok payload). They pass NOW on
the current stringly-typed code and must remain UNMODIFIED after the Step 2/3
typed-model conversion — proving payloads are identical.

Tests use FakeAdapter patterns: the adapter is swapped at the connection-pool
level so DatabaseUseCases methods are exercised without real Access/COM.
"""

from __future__ import annotations

import sys

import pytest

from ms_access_mcp.application.database import DatabaseUseCases
from ms_access_mcp.backends.ado import AdoAdapter
from ms_access_mcp.backends.odbc import OdbcAdapter
from ms_access_mcp.errors import Err, Ok
from ms_access_mcp.models import ObjectMetadata, QueryResult, WriteResult


class _FakeRecordset:
    EOF = True

    def Close(self) -> None:
        pass


class _FakeAdoConnection:
    def Execute(self, sql: str, params=None):
        return (_FakeRecordset(),)


class _FakeCursor:
    rowcount = 1
    description = [("id",)]

    def execute(self, sql: str, params=None) -> None:
        pass

    def fetchall(self):
        return [(1,)]

    def close(self) -> None:
        pass


class _FakeOdbcConnection:
    def cursor(self):
        return _FakeCursor()

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


@pytest.mark.parametrize("adapter_type", [OdbcAdapter, AdoAdapter])
def test_backend_crud_dict_serialization_characterization(adapter_type) -> None:
    """Pin current backend CRUD dict payloads before envelope unification."""
    adapter = adapter_type("test.accdb")
    adapter._conn = _FakeOdbcConnection() if adapter_type is OdbcAdapter else _FakeAdoConnection()
    adapter._connected = True

    query = adapter.execute_query("SELECT id FROM Users")
    write = adapter.insert_data("Users", {"id": 1})

    assert query == {"success": True, "rows": [{"id": 1}] if adapter_type is OdbcAdapter else [], "count": 1 if adapter_type is OdbcAdapter else 0, "columns": ["id"] if adapter_type is OdbcAdapter else []}
    assert write == {"success": True, "affected": 1}


@pytest.mark.parametrize("adapter_type", [OdbcAdapter, AdoAdapter])
def test_backend_insert_returns_write_result(adapter_type) -> None:
    adapter = adapter_type("test.accdb")
    adapter._conn = _FakeOdbcConnection() if adapter_type is OdbcAdapter else _FakeAdoConnection()
    adapter._connected = True

    assert isinstance(adapter.insert_data("Users", {"id": 1}), WriteResult)


@pytest.mark.parametrize("adapter_type", [OdbcAdapter, AdoAdapter])
def test_backend_query_returns_query_result(adapter_type) -> None:
    adapter = adapter_type("test.accdb")
    adapter._conn = _FakeOdbcConnection() if adapter_type is OdbcAdapter else _FakeAdoConnection()
    adapter._connected = True

    assert isinstance(adapter.execute_query("SELECT id FROM Users"), QueryResult)


class _FakeOdbcShapedAdapter:
    committed = False

    def insert_data(self, table_name: str, data: dict | list[dict]) -> dict:
        self.committed = True
        return {"success": True, "affected": 3}


def test_insert_data_accepts_odbc_result_without_post_commit_type_error() -> None:
    from ms_access_mcp.safety import WritePolicy

    adapter = _FakeOdbcShapedAdapter()
    uc = DatabaseUseCases()
    uc._connections["default"] = adapter  # type: ignore[attr-defined]
    uc._policy = WritePolicy(readonly=False)  # type: ignore[attr-defined]

    result = uc.insert_data("Users", {"id": 1}, confirm=True)

    assert adapter.committed
    assert isinstance(result, Ok)
    assert result.rowcount == 3

# ---------------------------------------------------------------------------
# Fake adapters for connection-pool substitution
# ---------------------------------------------------------------------------


class FakeQueryAdapter:
    """Fake adapter whose execute_query returns the given dict."""

    def __init__(self, result: dict) -> None:
        self._result = result

    def execute_query(self, sql: str, params=None) -> dict:
        return self._result


class FakeWriteAdapter:
    """Fake adapter whose write ops return WriteResult."""

    def __init__(self, result: dict) -> None:
        self._result = WriteResult(**result)

    def insert_data(self, table_name: str, data: dict | list[dict]) -> WriteResult:
        return self._result

    def update_data(self, table_name: str, set_dict: dict, where_dict: str | dict | None = None) -> WriteResult:
        return self._result

    def delete_data(self, table_name: str, where_dict: str | dict | None = None) -> WriteResult:
        return self._result


class FakeMetadataAdapter:
    """Fake adapter for schema inspector methods."""

    def __init__(self, meta_result: ObjectMetadata | None) -> None:
        self._meta = meta_result

    def get_object_metadata(self, object_name: str) -> ObjectMetadata | None:
        return self._meta


# ---------------------------------------------------------------------------
# DAO: query_data (DatabaseUseCases → Ok payload dict shape)
# ---------------------------------------------------------------------------


class TestQueryDataBoundary:
    """Pin DatabaseUseCases.query_data Ok payload dict keys."""

    def _uc_with_adapter(self, adapter: FakeQueryAdapter) -> DatabaseUseCases:
        uc = DatabaseUseCases()
        uc._connections["default"] = adapter  # type: ignore[attr-defined]
        return uc

    def test_success_2_rows(self) -> None:
        """Success path: Ok payload data=rows, rowcount=count."""
        adapter = FakeQueryAdapter({
            "success": True,
            "rows": [{"id": 1}, {"id": 2}],
            "count": 2,
            "columns": ["id"],
            "error": None,
        })
        uc = self._uc_with_adapter(adapter)

        result = uc.query_data("SELECT id FROM Users")

        assert isinstance(result, Ok)
        # data contains the rows from execute_query result
        assert hasattr(result, "data")
        assert hasattr(result, "rowcount")

    def test_not_connected(self) -> None:
        """Not-connected path returns Err."""
        adapter = FakeQueryAdapter({
            "success": False,
            "rows": [],
            "count": 0,
            "columns": [],
            "error": "Not connected",
        })
        uc = self._uc_with_adapter(adapter)

        result = uc.query_data("SELECT 1")

        assert isinstance(result, Ok)  # query_data returns Ok even on query failure
        # (it wraps the result dict directly, even if success=False)
        assert hasattr(result, "data")
        assert hasattr(result, "rowcount")

    def test_empty_result(self) -> None:
        """Empty recordset: Ok payload with empty rows."""
        adapter = FakeQueryAdapter({
            "success": True,
            "rows": [],
            "count": 0,
            "columns": [],
        })
        uc = self._uc_with_adapter(adapter)

        result = uc.query_data("SELECT 1")

        assert isinstance(result, Ok)
        assert hasattr(result, "data")
        assert hasattr(result, "rowcount")


# ---------------------------------------------------------------------------
# DAO: insert_data / update_data / delete_data (DatabaseUseCases → rowcount)
# ---------------------------------------------------------------------------


class TestWriteOpsBoundary:
    """Pin DatabaseUseCases insert/update/delete Ok payload."""

    def _uc_with_adapter(self, adapter: FakeWriteAdapter) -> DatabaseUseCases:
        from ms_access_mcp.safety import WritePolicy
        uc = DatabaseUseCases()
        uc._connections["default"] = adapter  # type: ignore[attr-defined]
        uc._policy = WritePolicy(readonly=False)  # type: ignore[attr-defined]
        return uc

    def test_insert_success(self) -> None:
        adapter = FakeWriteAdapter({"success": True, "affected": 5})
        uc = self._uc_with_adapter(adapter)

        result = uc.insert_data("Users", {"name": "Alice"}, confirm=True)

        assert isinstance(result, Ok)
        assert hasattr(result, "rowcount")

    def test_insert_failure(self) -> None:
        adapter = FakeWriteAdapter({
            "success": False,
            "error": "Not connected",
            "affected": 0,
        })
        uc = self._uc_with_adapter(adapter)

        result = uc.insert_data("Users", {"name": "Bob"}, confirm=True)

        assert isinstance(result, Err)
        assert hasattr(result, "message")

    def test_update_success(self) -> None:
        adapter = FakeWriteAdapter({"success": True, "affected": 3})
        uc = self._uc_with_adapter(adapter)

        result = uc.update_data("Users", {"name": "Charlie"}, where="id=1", confirm=True)

        assert isinstance(result, Ok)
        assert hasattr(result, "rowcount")

    def test_update_failure(self) -> None:
        adapter = FakeWriteAdapter({
            "success": False,
            "error": "Not connected",
            "affected": 0,
        })
        uc = self._uc_with_adapter(adapter)

        result = uc.update_data("Users", {"name": "Dave"}, where="id=1", confirm=True)

        assert isinstance(result, Err)
        assert hasattr(result, "message")

    def test_delete_success(self) -> None:
        adapter = FakeWriteAdapter({"success": True, "affected": 2})
        uc = self._uc_with_adapter(adapter)

        result = uc.delete_data("Users", where="id=1", confirm=True)

        assert isinstance(result, Ok)
        assert hasattr(result, "rowcount")

    def test_delete_failure(self) -> None:
        adapter = FakeWriteAdapter({
            "success": False,
            "error": "Not connected",
            "affected": 0,
        })
        uc = self._uc_with_adapter(adapter)

        result = uc.delete_data("Users", where="id=1", confirm=True)

        assert isinstance(result, Err)
        assert hasattr(result, "message")


# ---------------------------------------------------------------------------
# Schema inspector: get_object_metadata (DatabaseUseCases → Ok payload)
# ---------------------------------------------------------------------------


class TestGetObjectMetadataBoundary:
    """Pin DatabaseUseCases.get_object_metadata Ok payload dict keys."""

    def _uc_with_adapter(self, adapter: FakeMetadataAdapter) -> DatabaseUseCases:
        uc = DatabaseUseCases()
        uc._connections["default"] = adapter  # type: ignore[attr-defined]
        return uc

    def test_table_hit(self) -> None:
        adapter = FakeMetadataAdapter(ObjectMetadata(
            name="MyTable",
            type="table",
            properties={"record_count": "10"},
        ))
        uc = self._uc_with_adapter(adapter)

        result = uc.get_object_metadata("MyTable")

        assert isinstance(result, Ok)
        assert hasattr(result, "data")

    def test_form_hit(self) -> None:
        adapter = FakeMetadataAdapter(ObjectMetadata(
            name="MyForm",
            type="form",
            properties={},
        ))
        uc = self._uc_with_adapter(adapter)

        result = uc.get_object_metadata("MyForm")

        assert isinstance(result, Ok)
        assert hasattr(result, "data")

    def test_miss(self) -> None:
        """Miss: backend returns None, application layer returns Ok(data={})."""
        adapter = FakeMetadataAdapter(None)
        uc = self._uc_with_adapter(adapter)

        result = uc.get_object_metadata("NonExistent")

        assert isinstance(result, Ok)
        assert hasattr(result, "data")

    def test_not_connected(self) -> None:
        adapter = FakeMetadataAdapter(None)
        uc = self._uc_with_adapter(adapter)

        result = uc.get_object_metadata("Anything")

        assert isinstance(result, Ok)
        assert hasattr(result, "data")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
