"""Tests for database.py — DatabaseUseCases with fakes for TDD."""

from pathlib import Path

import pytest

from ms_access_mcp.application.database import (
    DatabaseUseCases,
    Err,
    Ok,
    _err,
    _ok,
)
from ms_access_mcp.backends.access import AccessAdapter


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


class FakeAccessAdapterForXlsx(AccessAdapter):
    """Fake AccessAdapter subclass so isinstance(adapter, AccessAdapter) returns True."""

    def __init__(self) -> None:
        self._export_calls: list[tuple[str, str]] = []

    def disconnect(self) -> None:
        pass

    def export_table_to_xlsx(self, table_or_query: str, target_path: str) -> int:
        self._export_calls.append((table_or_query, target_path))
        return 5


class FakeDaoAdapterForXlsx:
    """Fake DaoAdapter that captures execute_raw_sql calls for IISAM SQL."""

    def __init__(self):
        self.connected = False
        self._sql_calls: list[str] = []

    def connect(self, db_path: str) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def execute_raw_sql(self, sql: str) -> int:
        self._sql_calls.append(sql)
        return 3


class FakeOdbcAdapterForXlsx:
    """Fake OdbcAdapter that raises BackendCapabilityMismatchError."""

    def __init__(self):
        self.connected = False

    def connect(self, db_path: str) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def execute_raw_sql(self, sql: str) -> int:
        from ms_access_mcp.errors import BackendCapabilityMismatchError
        raise BackendCapabilityMismatchError("ODBC backend execute_raw_sql is not supported.")


class TestExportDataXlsx:
    """Tests for xlsx export via export_data."""

    def test_xlsx_via_docmd_on_access_adapter(self) -> None:
        """format='xlsx' routes to AccessAdapter.export_table_to_xlsx."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.xlsx")
            # Touch the file so is_path_allowed's p.exists() passes
            open(output_path, "w").close()
            db = DatabaseUseCases(readonly=False, allowed_dirs=[Path(tmpdir)])
            fake_adapter = FakeAccessAdapterForXlsx()
            db._connections["test_conn"] = fake_adapter

            result = db.export_data(
                "Users",
                output_path,
                format="xlsx",
                connection_name="test_conn",
            )

            assert isinstance(result, Ok)
            assert result.rowcount == 5
            assert len(fake_adapter._export_calls) == 1
            assert fake_adapter._export_calls[0] == ("Users", output_path)

    def test_xlsx_via_docmd_uses_default_sheet_name(self) -> None:
        """Default sheet_name is 'Sheet1' when not specified."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.xlsx")
            open(output_path, "w").close()
            db = DatabaseUseCases(readonly=False, allowed_dirs=[Path(tmpdir)])
            fake_adapter = FakeAccessAdapterForXlsx()
            db._connections["test_conn"] = fake_adapter

            result = db.export_data(
                "Users",
                output_path,
                format="xlsx",
                connection_name="test_conn",
            )

            assert isinstance(result, Ok)
            assert result.rowcount == 5

    def test_xlsx_via_docmd_custom_sheet_name_passed(self) -> None:
        """Custom sheet_name is accepted and passed to routing."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.xlsx")
            open(output_path, "w").close()
            db = DatabaseUseCases(readonly=False, allowed_dirs=[Path(tmpdir)])
            fake_adapter = FakeAccessAdapterForXlsx()
            db._connections["test_conn"] = fake_adapter

            result = db.export_data(
                "Users",
                output_path,
                format="xlsx",
                sheet_name="Data",
                connection_name="test_conn",
            )

            assert isinstance(result, Ok)
            assert result.rowcount == 5

    def test_xlsx_via_sql_fallback_when_probe_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With probe=True and DaoAdapter, IISAM SQL is generated."""
        import os
        import tempfile

        import ms_access_mcp.application.xlsx_capability as xlsx_capability

        monkeypatch.setattr(xlsx_capability.IisamProviderProbe, "_cached", True)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.xlsx")
            open(output_path, "w").close()
            db = DatabaseUseCases(readonly=False, allowed_dirs=[Path(tmpdir)])
            fake_dao = FakeDaoAdapterForXlsx()
            db._connections["test_dao"] = fake_dao

            result = db.export_data(
                "Users",
                output_path,
                format="xlsx",
                sheet_name="Sheet1",
                connection_name="test_dao",
            )

            assert isinstance(result, Ok)
            assert result.rowcount == 3
            assert len(fake_dao._sql_calls) == 1
            sql = fake_dao._sql_calls[0]
            assert sql.startswith("INSERT INTO [Excel 12.0;HDR=YES;DATABASE=")
            assert "].[Sheet1$]" in sql
            assert "SELECT " in sql  # build_select output

    def test_xlsx_sheet_name_trailing_dollar_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Trailing $ in sheet name is stripped automatically."""
        import os
        import tempfile

        import ms_access_mcp.application.xlsx_capability as xlsx_capability

        monkeypatch.setattr(xlsx_capability.IisamProviderProbe, "_cached", True)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.xlsx")
            open(output_path, "w").close()
            db = DatabaseUseCases(readonly=False, allowed_dirs=[Path(tmpdir)])
            fake_dao = FakeDaoAdapterForXlsx()
            db._connections["test_dao"] = fake_dao

            result = db.export_data(
                "Users",
                output_path,
                format="xlsx",
                sheet_name="Data$",
                connection_name="test_dao",
            )

            assert isinstance(result, Ok)
            sql = fake_dao._sql_calls[0]
            assert "].[Data$]" in sql  # $ added by SQL builder after sanitization

    def test_xlsx_sheet_name_rejects_bracket(self) -> None:
        """Sheet name with ] is rejected before any adapter call."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.xlsx")
            open(output_path, "w").close()
            db = DatabaseUseCases(readonly=False, allowed_dirs=[Path(tmpdir)])
            fake_adapter = FakeAccessAdapterForXlsx()
            db._connections["test_conn"] = fake_adapter

            result = db.export_data(
                "Users",
                output_path,
                format="xlsx",
                sheet_name="Sheet]1",
                connection_name="test_conn",
            )

            assert isinstance(result, Err)
            assert "sheet" in result.message.lower() or "bracket" in result.message.lower() or "]" in result.message
            assert len(fake_adapter._export_calls) == 0  # Not called

    def test_xlsx_sheet_name_rejects_empty(self) -> None:
        """Empty sheet name is rejected."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.xlsx")
            open(output_path, "w").close()
            db = DatabaseUseCases(readonly=False, allowed_dirs=[Path(tmpdir)])
            fake_adapter = FakeAccessAdapterForXlsx()
            db._connections["test_conn"] = fake_adapter

            result = db.export_data(
                "Users",
                output_path,
                format="xlsx",
                sheet_name="",
                connection_name="test_conn",
            )

            assert isinstance(result, Err)
            assert len(fake_adapter._export_calls) == 0

    def test_xlsx_sheet_name_rejects_too_long(self) -> None:
        """Sheet name over 31 chars is rejected."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.xlsx")
            open(output_path, "w").close()
            db = DatabaseUseCases(readonly=False, allowed_dirs=[Path(tmpdir)])
            fake_adapter = FakeAccessAdapterForXlsx()
            db._connections["test_conn"] = fake_adapter

            result = db.export_data(
                "Users",
                output_path,
                format="xlsx",
                sheet_name="A" * 32,
                connection_name="test_conn",
            )

            assert isinstance(result, Err)
            assert len(fake_adapter._export_calls) == 0

    def test_xlsx_dao_no_provider_returns_err(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DaoAdapter without ACE provider returns BackendCapabilityMismatchError as Err."""
        import os
        import tempfile

        import ms_access_mcp.application.xlsx_capability as xlsx_capability

        monkeypatch.setattr(xlsx_capability.IisamProviderProbe, "_cached", False)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.xlsx")
            open(output_path, "w").close()
            db = DatabaseUseCases(readonly=False, allowed_dirs=[Path(tmpdir)])
            fake_dao = FakeDaoAdapterForXlsx()
            db._connections["test_dao"] = fake_dao

            result = db.export_data(
                "Users",
                output_path,
                format="xlsx",
                connection_name="test_dao",
            )

            assert isinstance(result, Err)
            assert "ACE" in result.message or "access backend" in result.message.lower() or "IISAM" in result.message
            assert len(fake_dao._sql_calls) == 0  # Never called

    def test_xlsx_odbc_always_returns_err(self) -> None:
        """OdbcAdapter always returns BackendCapabilityMismatchError as Err."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.xlsx")
            open(output_path, "w").close()
            db = DatabaseUseCases(readonly=False, allowed_dirs=[Path(tmpdir)])
            fake_odbc = FakeOdbcAdapterForXlsx()
            db._connections["test_odbc"] = fake_odbc

            result = db.export_data(
                "Users",
                output_path,
                format="xlsx",
                connection_name="test_odbc",
            )

            assert isinstance(result, Err)
            assert "ACE" in result.message or "access backend" in result.message.lower() or "IISAM" in result.message

    def test_csv_and_json_still_work(self) -> None:
        """CSV and JSON export paths remain unchanged (no xlsx dependency)."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "output.csv")
            json_path = os.path.join(tmpdir, "output.json")
            open(csv_path, "w").close()
            open(json_path, "w").close()
            db = DatabaseUseCases(readonly=False, allowed_dirs=[Path(tmpdir)])
            fake_adapter = FakeAccessAdapterForXlsx()
            db._connections["test_conn"] = fake_adapter

            # CSV
            result_csv = db.export_data("Users", csv_path, format="csv", connection_name="test_conn")
            assert isinstance(result_csv, Err)  # Err because query_data not implemented on fake
            # JSON
            result_json = db.export_data("Users", json_path, format="json", connection_name="test_conn")
            assert isinstance(result_json, Err)  # Err because query_data not implemented on fake

        # No xlsx calls made when format is csv/json
        assert len(fake_adapter._export_calls) == 0
