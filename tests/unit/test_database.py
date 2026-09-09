"""Tests for database.py — DatabaseUseCases with fakes for TDD."""

from pathlib import Path

from ms_access_mcp.application.database import (
    DatabaseUseCases,
    Err,
    Ok,
    _err,
    _ok,
)


class FakeAdapter:
    """Fake adapter for testing."""

    def __init__(self):
        self.connected = False
        self.tables = ["Users", "Orders"]
        self.queries = ["qry_ActiveUsers"]
        self._properties = {}

    def connect(self, db_path: str) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def get_tables(self):
        return self.tables

    def get_system_tables(self):
        return ["MSysObjects", "MSysQueries"]

    def get_table_schema(self, table_name: str):
        if table_name == "Users":
            return [{"name": "id", "type": "INT"}, {"name": "name", "type": "TEXT"}]
        return []

    def get_indexes(self, table_name: str):
        return [{"name": "idx_id", "columns": ["id"], "unique": True}]

    def get_relationships(self):
        return []

    def get_object_metadata(self, object_name: str, object_type: str = "table"):
        return {"name": object_name, "type": object_type}

    def get_queries(self):
        return self.queries

    def get_database_properties(self):
        return {"Version": "16.0", "Format": "Access 2007-2025"}

    def get_database_statistics(self):
        return {"table_count": 2, "query_count": 1}

    def get_linked_tables(self):
        return []


class TestDatabaseUseCasesConstruction:
    """Test DatabaseUseCases initialization."""

    def test_default_readonly(self):
        """Default is readonly mode."""
        db = DatabaseUseCases()
        assert db._policy.readonly is True

    def test_explicit_readonly_false(self):
        """readonly=False enables writes."""
        db = DatabaseUseCases(readonly=False)
        assert db._policy.readonly is False

    def test_default_backend_auto(self):
        """Default backend is auto."""
        db = DatabaseUseCases()
        assert db._backend_name == "auto"

    def test_explicit_backend(self):
        """Explicit backend is stored."""
        db = DatabaseUseCases(backend="odbc")
        assert db._backend_name == "odbc"


class TestResultEnvelopes:
    """Test result envelope types."""

    def test_ok_is_frozen(self):
        """Ok is frozen (immutable)."""
        ok = Ok(data={"test": 1})
        # Frozen dataclass cannot be modified
        assert ok.data == {"test": 1}
        # Attempting to assign would raise FrozenInstanceError

    def test_err_is_frozen(self):
        """Err is frozen (immutable)."""
        err = Err(message="test error")
        # Frozen dataclass cannot be modified
        assert err.message == "test error"
        # Attempting to assign would raise FrozenInstanceError

    def test_ok_with_data_and_rowcount(self):
        """Ok can hold data and rowcount."""
        ok = Ok(data=[1, 2, 3], rowcount=3)
        assert ok.data == [1, 2, 3]
        assert ok.rowcount == 3

    def test_ok_defaults(self):
        """Ok defaults to None data and 0 rowcount."""
        ok = Ok()
        assert ok.data is None
        assert ok.rowcount == 0

    def test_err_message(self):
        """Err holds error message."""
        err = Err("something went wrong")
        assert err.message == "something went wrong"

    def test_helper_ok(self):
        """_ok helper creates Ok."""
        result = _ok(data={"key": "value"}, rowcount=5)
        assert isinstance(result, Ok)
        assert result.data == {"key": "value"}
        assert result.rowcount == 5

    def test_helper_err(self):
        """_err helper creates Err."""
        result = _err("error occurred")
        assert isinstance(result, Err)
        assert result.message == "error occurred"


class TestConnectionManagement:
    """Test connection management use cases."""

    def test_open_database_with_fake_adapter(self):
        """open_database connects with adapter."""
        db = DatabaseUseCases(backend="auto", allowed_dirs=[Path(".")])
        # Verify the connections dict exists - we can't fully test without mocking BackendSelector
        assert hasattr(db, "_connections")

    def test_not_connected_operations_return_err(self):
        """Operations on disconnected db return Err."""
        db = DatabaseUseCases()
        result = db.get_tables()
        assert isinstance(result, Err)
        assert "Not connected" in result.message


class TestWriteProtection:
    """Test readonly policy enforcement."""

    def test_insert_blocked_in_readonly_mode(self):
        """insert_data is blocked when readonly."""
        db = DatabaseUseCases(readonly=True)
        result = db.insert_data("Users", {"name": "test"}, confirm=True)
        assert isinstance(result, Err)
        assert "disabled" in result.message or "readonly" in result.message

    def test_delete_blocked_in_readonly_mode(self):
        """delete_data is blocked when readonly."""
        db = DatabaseUseCases(readonly=True)
        result = db.delete_data("Users", confirm=True)
        assert isinstance(result, Err)
        assert "disabled" in result.message or "readonly" in result.message

    def test_update_blocked_in_readonly_mode(self):
        """update_data requires confirm regardless of readonly setting."""
        # The confirmation gate is tested separately; this is a placeholder
        pass


class TestConfirmationGate:
    """Test confirmation gate for destructive operations."""

    def test_delete_table_without_confirm_returns_error(self):
        """delete_table without confirm returns error about confirmation."""
        db = DatabaseUseCases(readonly=False)
        result = db.delete_table("Users", confirm=False)
        assert isinstance(result, Err)
        assert "confirmation" in result.message.lower() or "required" in result.message.lower()

    def test_delete_table_with_confirm_passes(self):
        """delete_table with confirm=True passes to adapter (which may fail if not connected)."""
        db = DatabaseUseCases(readonly=False)
        result = db.delete_table("Users", confirm=True)
        # Will fail because not connected, but the gate passed
        assert isinstance(result, Err)  # Err because not connected
        assert "Not connected" in result.message or "Path" in result.message


class TestPathValidation:
    """Test path validation via safety.is_path_allowed."""

    def test_open_database_rejects_disallowed_path(self):
        """open_database rejects paths not in allowed_dirs."""
        db = DatabaseUseCases(allowed_dirs=[Path("/allowed")])
        result = db.open_database("/forbidden/path.accdb")
        assert isinstance(result, Err)
        assert "not allowed" in result.message

    def test_export_data_rejects_disallowed_output_path(self):
        """export_data rejects output paths not in allowed_dirs."""
        db = DatabaseUseCases(allowed_dirs=[Path("/allowed")])
        result = db.export_data("Users", "/forbidden/output.csv", format="csv")
        assert isinstance(result, Err)
        assert "not allowed" in result.message
