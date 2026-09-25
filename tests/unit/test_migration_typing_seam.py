import pytest

from ms_access_mcp.application.migration import _select_migration_adapter
from ms_access_mcp.backends import BackendSelector
from ms_access_mcp.errors import BackendUnavailableError


def test_select_migration_adapter_accepts_odbc_surface() -> None:
    adapter = _select_migration_adapter(":memory:", backend="odbc")
    assert callable(adapter.execute_query)
    assert callable(adapter.get_tables)


def test_select_migration_adapter_rejects_missing_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    class IncompleteAdapter:
        pass

    monkeypatch.setattr(
        BackendSelector,
        "get_adapter",
        staticmethod(lambda db_path, backend=None: IncompleteAdapter()),
    )

    with pytest.raises(BackendUnavailableError, match="missing: connect"):
        _select_migration_adapter(":memory:", backend="odbc")
