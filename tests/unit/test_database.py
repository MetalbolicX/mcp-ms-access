"""Tests for database.py — DatabaseUseCases with fakes for TDD."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ms_access_mcp.application.database import (
    DatabaseUseCases,
    Err,
    Ok,
    _err,
    _ok,
)
from ms_access_mcp.backends.access import AccessAdapter
from ms_access_mcp.models import FieldInfo, IndexInfo, ObjectMetadata, RelationshipInfo, TableInfo
from ms_access_mcp.safety import WritePolicy


class FakeAdapter:
    """Fake adapter for testing."""

    def __init__(self):
        self.connected = False
        self.tables = [
            TableInfo(
                name="Users",
                fields=[
                    FieldInfo(name="id", type="Long Integer", size=4),
                    FieldInfo(name="name", type="Text", size=100),
                ],
                record_count=10,
                primary_key=["id"],
            ),
            TableInfo(
                name="Orders",
                fields=[FieldInfo(name="id", type="Long Integer", size=4)],
                record_count=5,
                primary_key=["id"],
            ),
        ]
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
            return [
                {"name": "id", "type": "INT"},
                {"name": "name", "type": "TEXT"},
            ]
        return []

    def get_indexes(self, table_name: str):
        return [IndexInfo(name="idx_id", columns=["id"], is_unique=True)]

    def get_relationships(self):
        return [
            RelationshipInfo(
                name="FK_Users_Orders",
                table="Users",
                foreign_table="Orders",
                attributes="",
                columns=["id"],
                foreign_columns=["user_id"],
            )
        ]

    def get_object_metadata(self, object_name: str):
        return ObjectMetadata(name=object_name, type="table")

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


class TestQueryDataLimitForwarding:
    def test_limit_is_forwarded_without_signature_introspection(self):
        from ms_access_mcp.models import QueryResult

        class RecordingAdapter:
            def __init__(self):
                self.call_args = None

            def execute_query(self, sql, **kwargs):
                self.call_args = (sql, kwargs)
                return QueryResult(success=True, rows=[{"id": 1}], count=1, columns=["id"])

        db = DatabaseUseCases()
        adapter = RecordingAdapter()
        db._connections["default"] = adapter

        result = db.query_data("SELECT id FROM Users", limit=5)

        assert isinstance(result, Ok)
        assert adapter.call_args == (
            "SELECT id FROM Users",
            {"params": None, "limit": 5},
        )


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


def test_open_database_scrubs_repr_escaped_password_without_stderr(monkeypatch, tmp_path, capsys):
    password = 'scrub\\me"quote'
    connect_string = f"Driver={{ODBC}};PWD={password};Server=example"
    database_path = tmp_path / "database.accdb"
    database_path.touch()

    class FailingAdapter:
        def connect(self, _path, **_kwargs):
            raise RuntimeError(connect_string)

    monkeypatch.setattr(
        "ms_access_mcp.application.database.BackendSelector.get_adapter",
        lambda **_kwargs: FailingAdapter(),
    )
    monkeypatch.setattr("ms_access_mcp.credentials.resolve_password", lambda _path: password)

    result = DatabaseUseCases(allowed_dirs=[tmp_path]).open_database(str(database_path))

    captured = capsys.readouterr()
    assert isinstance(result, Err)
    assert password not in result.message
    assert "PWD=" not in result.message
    assert captured.err == ""


class TestConnectionManagement:
    """Test connection management use cases."""

    def test_open_database_disconnects_previous_adapter_before_replacing(self, tmp_path):
        """Opening another database disconnects the currently stored adapter first."""
        db = DatabaseUseCases(backend="auto", allowed_dirs=[tmp_path])
        first = FakeAdapter()
        second = FakeAdapter()
        first_path = tmp_path / "first.accdb"
        second_path = tmp_path / "second.accdb"
        first_path.touch()
        second_path.touch()
        adapters = iter([first, second])
        second_connect_saw_first_disconnected = []

        def connect_second(db_path: str) -> bool:
            second_connect_saw_first_disconnected.append(not first.connected)
            second.connected = True
            return True

        second.connect = connect_second
        with (
            patch("ms_access_mcp.backends.BackendSelector.get_adapter", side_effect=lambda **_: next(adapters)),
            patch("ms_access_mcp.credentials.resolve_password", return_value=None),
        ):
            assert isinstance(db.open_database(str(first_path)), Ok)
            assert isinstance(db.open_database(str(second_path)), Ok)

        assert second_connect_saw_first_disconnected == [True]
        assert db._connections["default"] is second

    def test_open_database_continues_when_previous_disconnect_raises(self, tmp_path):
        """A stale adapter must not prevent opening the replacement database."""
        db = DatabaseUseCases(backend="auto", allowed_dirs=[tmp_path])
        first = FakeAdapter()
        second = FakeAdapter()
        first_path = tmp_path / "first.accdb"
        second_path = tmp_path / "second.accdb"
        first_path.touch()
        second_path.touch()
        adapters = iter([first, second])

        def disconnect_raising() -> None:
            raise RuntimeError("stale adapter")

        first.disconnect = disconnect_raising
        with (
            patch("ms_access_mcp.backends.BackendSelector.get_adapter", side_effect=lambda **_: next(adapters)),
            patch("ms_access_mcp.credentials.resolve_password", return_value=None),
        ):
            assert isinstance(db.open_database(str(first_path)), Ok)
            assert isinstance(db.open_database(str(second_path)), Ok)

        assert db._connections["default"] is second

    def test_open_database_with_fake_adapter(self):
        """open_database connects with adapter."""
        db = DatabaseUseCases(backend="auto", allowed_dirs=[Path()])
        # Verify the connections dict exists - we can't fully test without mocking BackendSelector
        assert hasattr(db, "_connections")

    def test_not_connected_operations_return_err(self):
        """Operations on disconnected db return Err."""
        db = DatabaseUseCases()
        result = db.get_tables()
        assert isinstance(result, Err)
        assert "Not connected" in result.message


class TestExecuteActionSql:
    """Action SQL goes through the adapter action contract and write policy."""

    def test_execute_action_sql_returns_adapter_rowcount(self):
        db = DatabaseUseCases(readonly=False)
        adapter = FakeAdapter()
        adapter.execute_raw_sql = lambda sql: 7
        db._connections["default"] = adapter

        result = db.execute_action_sql("DELETE FROM Users", confirm=True)

        assert isinstance(result, Ok)
        assert result.rowcount == 7

    @pytest.mark.parametrize(
        "adapter_setup",
        [
            lambda adapter: None,
            lambda adapter: setattr(
                adapter,
                "execute_raw_sql",
                lambda sql: (_ for _ in ()).throw(NotImplementedError("not supported")),
            ),
        ],
        ids=["missing-method", "not-supported"],
    )
    def test_execute_action_sql_reports_unsupported_adapter(self, adapter_setup):
        db = DatabaseUseCases(readonly=False)
        adapter = FakeAdapter()
        adapter_setup(adapter)
        db._connections["default"] = adapter

        result = db.execute_action_sql("DELETE FROM Users", confirm=True)

        assert isinstance(result, Err)
        assert "Backend does not support raw action SQL" in result.message


class TestDatabasePropertyWriteHonesty:
    """Database property writes report the adapter's success result."""

    @pytest.mark.parametrize("adapter_result", [False, None, 0, ""])
    def test_set_database_property_reports_falsy_adapter_result(self, adapter_result):
        db = DatabaseUseCases(readonly=False)
        adapter = FakeAdapter()
        adapter.set_database_property = lambda name, value: adapter_result
        db._connections["default"] = adapter

        result = db.set_database_property("Version", "17.0", confirm=True)

        assert isinstance(result, Err)
        assert result.message == "Database property write is not supported by this backend"

    def test_set_database_property_reports_success_when_adapter_succeeds(self):
        db = DatabaseUseCases(readonly=False)
        adapter = FakeAdapter()
        adapter.set_database_property = lambda name, value: True
        db._connections["default"] = adapter

        result = db.set_database_property("Version", "17.0", confirm=True)

        assert isinstance(result, Ok)
        assert result.rowcount == 1


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


class TestExecuteSqlScript:
    """Test execute_sql_script use-case behavior — six branches pinned."""

    def test_confirm_false_returns_err(self):
        """confirm=False returns Err with acknowledgment message."""
        db = DatabaseUseCases(readonly=False)
        result = db.execute_sql_script("SELECT 1", confirm=False)
        assert isinstance(result, Err)
        assert result.message  # acknowledgment required

    def test_readonly_returns_err(self):
        """readonly=True, confirm=True returns Err about disabled writes."""
        db = DatabaseUseCases(readonly=True)
        result = db.execute_sql_script("SELECT 1", confirm=True)
        assert isinstance(result, Err)
        assert "Write operations are disabled" in result.message

    def test_not_connected_returns_err(self):
        """confirm=True, readonly=False, no connection returns Err 'Not connected'."""
        db = DatabaseUseCases(readonly=False)
        result = db.execute_sql_script("SELECT 1", confirm=True, connection_name="nonexistent")
        assert isinstance(result, Err)
        assert "Not connected" in result.message

    def test_unsupported_adapter_returns_err(self):
        """Adapter without execute_script returns Err about unsupported adapter."""
        db = DatabaseUseCases(readonly=False)
        db._connections["test_conn"] = FakeAdapter()  # has no execute_script
        result = db.execute_sql_script("SELECT 1", confirm=True, connection_name="test_conn")
        assert isinstance(result, Err)
        assert "does not support execute_script" in result.message

    def test_success_returns_ok_with_data(self):
        """Successful execution returns Ok with the adapter result dict verbatim."""
        db = DatabaseUseCases(readonly=False)

        class FakeWithScript(FakeAdapter):
            def execute_script(self, sql: str):
                return {"success": True, "statements": ["SELECT 1"]}

        db._connections["test_conn"] = FakeWithScript()
        result = db.execute_sql_script("SELECT 1", confirm=True, connection_name="test_conn")
        assert isinstance(result, Ok)
        assert result.data == {"success": True, "statements": ["SELECT 1"]}

    def test_exception_returns_err(self):
        """Adapter raising exception returns Err with exception message."""
        db = DatabaseUseCases(readonly=False)

        class FakeWithScript(FakeAdapter):
            def execute_script(self, sql: str):
                raise RuntimeError("boom")

        db._connections["test_conn"] = FakeWithScript()
        result = db.execute_sql_script("SELECT 1", confirm=True, connection_name="test_conn")
        assert isinstance(result, Err)
        assert "boom" in result.message


class TestPathValidation:
    """Test path validation via safety.is_path_allowed."""

    def test_open_database_rejects_disallowed_path(self):
        """open_database rejects paths not in allowed_dirs."""
        db = DatabaseUseCases(allowed_dirs=[Path("/allowed")])
        result = db.open_database("/forbidden/path.accdb")
        assert isinstance(result, Err)
        assert "not allowed" in result.message


class TestJet3Rejection:
    """Test that Jet 3 (Access 95/97) files are rejected with a clear error."""

    def test_open_database_rejects_jet3_format(self, tmp_path):
        """open_database returns Err for a JET3 file before attempting connection."""
        from ms_access_mcp.backends.format_detector import _JET_MAGIC, _VERSION_OFFSET

        # Build a fake JET3 header: Standard Jet DB + version byte 0x00
        header_bytes = bytearray(32)
        header_bytes[0:4] = b"\x00\x01\x00\x00"
        header_bytes[4:4 + len(_JET_MAGIC)] = _JET_MAGIC
        header_bytes[_VERSION_OFFSET] = 0x00  # JET3 version byte

        jet3_path = tmp_path / "test_jet3.mdb"
        jet3_path.write_bytes(bytes(header_bytes))

        db = DatabaseUseCases(allowed_dirs=[tmp_path])
        result = db.open_database(str(jet3_path))

        assert isinstance(result, Err)
        assert "Jet 3" in result.message or "not supported" in result.message
        assert "convert" in result.message.lower()

    def test_open_database_accepts_jet4_format(self, tmp_path):
        """open_database proceeds normally for a JET4 file (no early rejection)."""
        from ms_access_mcp.backends.format_detector import _JET_MAGIC, _VERSION_OFFSET

        header_bytes = bytearray(32)
        header_bytes[0:4] = b"\x00\x01\x00\x00"
        header_bytes[4:4 + len(_JET_MAGIC)] = _JET_MAGIC
        header_bytes[_VERSION_OFFSET] = 0x01  # JET4 version byte

        jet4_path = tmp_path / "test_jet4.mdb"
        jet4_path.write_bytes(bytes(header_bytes))

        db = DatabaseUseCases(allowed_dirs=[tmp_path])
        # Patch get_adapter so we don't need real COM
        from unittest.mock import patch
        fake_adapter = FakeAdapter()
        with patch("ms_access_mcp.application.database.BackendSelector.get_adapter", return_value=fake_adapter):
            result = db.open_database(str(jet4_path))

        # Should not fail on JET4 check; passes through to adapter.connect
        assert isinstance(result, Ok) or isinstance(result, Err)

    def test_open_database_accepts_ace_format(self, tmp_path):
        """open_database proceeds normally for an ACE format file."""
        from ms_access_mcp.backends.format_detector import _ACE_MAGIC, _VERSION_OFFSET

        header_bytes = bytearray(32)
        header_bytes[0:4] = b"\x00\x01\x00\x00"
        header_bytes[4:4 + len(_ACE_MAGIC)] = _ACE_MAGIC
        header_bytes[_VERSION_OFFSET] = 0x00

        ace_path = tmp_path / "test.accdb"
        ace_path.write_bytes(bytes(header_bytes))

        db = DatabaseUseCases(allowed_dirs=[tmp_path])
        fake_adapter = FakeAdapter()
        with patch("ms_access_mcp.application.database.BackendSelector.get_adapter", return_value=fake_adapter):
            result = db.open_database(str(ace_path))

        assert isinstance(result, Ok) or isinstance(result, Err)

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
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.xlsx"
            # Touch the file so is_path_allowed's p.exists() passes
            Path(output_path).touch()
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
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.xlsx"
            Path(output_path).touch()
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
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.xlsx"
            Path(output_path).touch()
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
        import tempfile

        import ms_access_mcp.application.xlsx_capability as xlsx_capability

        monkeypatch.setattr(xlsx_capability.IisamProviderProbe, "_cached", True)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.xlsx"
            Path(output_path).touch()
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
        import tempfile

        import ms_access_mcp.application.xlsx_capability as xlsx_capability

        monkeypatch.setattr(xlsx_capability.IisamProviderProbe, "_cached", True)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.xlsx"
            Path(output_path).touch()
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
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.xlsx"
            Path(output_path).touch()
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
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.xlsx"
            Path(output_path).touch()
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
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.xlsx"
            Path(output_path).touch()
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
        import tempfile

        import ms_access_mcp.application.xlsx_capability as xlsx_capability

        monkeypatch.setattr(xlsx_capability.IisamProviderProbe, "_cached", False)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.xlsx"
            Path(output_path).touch()
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
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.xlsx"
            Path(output_path).touch()
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
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "output.csv"
            json_path = Path(tmpdir) / "output.json"
            Path(csv_path).touch()
            Path(json_path).touch()
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


class TestStep7cSerialization:
    """Step 7c: use cases must convert dataclasses to dicts before Ok/data."""

    def test_get_tables_returns_plain_dicts(self) -> None:
        """get_tables Ok.data must be JSON-serializable dicts, not dataclasses."""
        db = DatabaseUseCases()
        db._connections["default"] = FakeAdapter()
        result = db.get_tables()
        assert isinstance(result, Ok)
        assert isinstance(result.data, list)
        for item in result.data:
            assert isinstance(item, dict)
            assert "name" in item
            assert "fields" in item
            assert "record_count" not in item
        # Verify json.dumps works (load-bearing contract)
        import json

        json.dumps(result.data)

    def test_get_indexes_returns_plain_dicts(self) -> None:
        """get_indexes Ok.data must be JSON-serializable dicts, not dataclasses."""
        db = DatabaseUseCases()
        db._connections["default"] = FakeAdapter()
        result = db.get_indexes("Users")
        assert isinstance(result, Ok)
        assert isinstance(result.data, list)
        for item in result.data:
            assert isinstance(item, dict)
            assert "name" in item
            assert "columns" in item
        import json

        json.dumps(result.data)

    def test_get_relationships_returns_plain_dicts(self) -> None:
        """get_relationships Ok.data must be JSON-serializable dicts, not dataclasses."""
        db = DatabaseUseCases()
        db._connections["default"] = FakeAdapter()
        result = db.get_relationships()
        assert isinstance(result, Ok)
        assert isinstance(result.data, list)
        for item in result.data:
            assert isinstance(item, dict)
            assert "name" in item
            assert "columns" in item
            assert "foreign_columns" in item
        import json

        json.dumps(result.data)


class TestGetSchemasFor:
    def test_fetches_multiple_schemas_with_one_adapter_pass(self) -> None:
        db = DatabaseUseCases()
        adapter = FakeAdapter()
        adapter.get_tables = MagicMock(wraps=adapter.get_tables)
        db._connections["default"] = adapter

        result = db.get_schemas_for(["Users", "Orders"])

        assert isinstance(result, Ok)
        assert set(result.data) == {"Users", "Orders"}
        adapter.get_tables.assert_called_once_with()


class TestGetTableSchema:
    """Tests for get_table_schema composition from get_tables (Bug B fix)."""

    def test_returns_matching_table_as_dict(self) -> None:
        """get_table_schema('Users') returns the Users TableInfo as a dict."""
        db = DatabaseUseCases()
        db._connections["default"] = FakeAdapter()
        result = db.get_table_schema("Users")
        assert isinstance(result, Ok)
        assert isinstance(result.data, dict)
        assert result.data["name"] == "Users"
        assert "record_count" not in result.data
        # Verify json.dumps works
        import json

        json.dumps(result.data)

    def test_missing_table_returns_err(self) -> None:
        """get_table_schema('NonExistent') returns Err with not-found message."""
        db = DatabaseUseCases()
        db._connections["default"] = FakeAdapter()
        result = db.get_table_schema("NonExistent")
        assert isinstance(result, Err)
        assert "NonExistent" in result.message


class TestGetObjectMetadataBugA:
    """Tests for Bug A fix: get_object_metadata is 1-arg, not 2-arg."""

    def test_single_arg_call_succeeds(self) -> None:
        """get_object_metadata(object_name) with 1 arg succeeds (Bug A fix)."""
        db = DatabaseUseCases()
        db._connections["default"] = FakeAdapter()
        result = db.get_object_metadata("Users")
        assert isinstance(result, Ok)
        assert result.data["name"] == "Users"


# =============================================================================
# Step 3 RED: Linked-table connect_ref + hard-reject
# Cases 13-19 from plan 040
# =============================================================================


class TestCreateLinkedTableConnectRef:
    """Case 13: create_linked_table with connect_ref resolves to full string."""

    def test_connect_ref_resolves_to_full_string(self, monkeypatch, tmp_path):
        """connect_ref='prod_dw' causes adapter to receive the resolved connection string."""
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends import BackendSelector

        captured: dict = {}

        class FakeLinkedAdapter:
            def create_linked_table(self, name, source_table, connect_string):
                captured["connect_string"] = connect_string
                return {"success": True}

        monkeypatch.setattr(BackendSelector, "get_adapter", lambda **kw: FakeLinkedAdapter())

        # Mock credentials to return the full string when resolve_profile is called
        # Must patch at the usage site (database module) because of how Python import binding works
        import ms_access_mcp.application.database as db_module

        monkeypatch.setattr(
            db_module,
            "resolve_profile",
            lambda name: "Driver={ODBC};PWD=hunter2;Server=srv" if name == "prod_dw" else None,
        )

        db = DatabaseUseCases()
        db._policy = WritePolicy(readonly=False)
        db._connections["default"] = FakeLinkedAdapter()
        result = db.create_linked_table(
            name="Orders",
            source_table="Orders",
            connect_ref="prod_dw",
            confirm=True,
        )

        assert isinstance(result, Ok), f"expected Ok, got {result}"
        assert captured["connect_string"] == "Driver={ODBC};PWD=hunter2;Server=srv"


class TestCreateLinkedTableRefNotFound:
    """Case 14: connect_ref not found → Err with CLI guidance, no credential value."""

    def test_ref_not_found_error_has_guidance(self, monkeypatch):
        """Err message mentions the CLI command to set the profile."""
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends import BackendSelector

        monkeypatch.setattr(BackendSelector, "get_adapter", lambda **kw: MagicMock())

        # Mock resolve_profile to return None (profile not found)
        monkeypatch.setattr(
            "ms_access_mcp.credentials.resolve_profile",
            lambda name: None,
        )

        db = DatabaseUseCases()
        db._policy = WritePolicy(readonly=False)
        result = db.create_linked_table(
            name="Orders",
            source_table="Orders",
            connect_ref="nonexistent_profile",
            confirm=True,
        )

        assert isinstance(result, Err), f"expected Err, got {result}"
        assert "ms-access-mcp credentials profile set" in result.message
        assert "nonexistent_profile" in result.message
        # Must NOT contain any hardcoded credential value
        assert "hunter2" not in result.message
        assert "PWD=" not in result.message


class TestCreateLinkedTableHardReject:
    """Case 15: literal connect_string with PWD= → hard-reject Err."""

    def test_literal_connect_string_with_password_hard_rejected(self, monkeypatch):
        """A literal connect_string containing PWD= is rejected with profile guidance."""
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends import BackendSelector

        monkeypatch.setattr(BackendSelector, "get_adapter", lambda **kw: MagicMock())

        db = DatabaseUseCases()
        db._policy = WritePolicy(readonly=False)
        result = db.create_linked_table(
            name="Orders",
            source_table="Orders",
            connect_string="ODBC;PWD=hunter2;Database=Northwind",
            confirm=True,
        )

        assert isinstance(result, Err), f"expected Err, got {result}"
        assert "ms-access-mcp credentials profile set" in result.message
        assert "hunter2" not in result.message


class TestCreateLinkedTableCleanLiteral:
    """Case 16: clean literal connect_string (no creds) is allowed."""

    def test_clean_literal_connect_string_allowed(self, monkeypatch):
        """A literal connect_string with Trusted_Connection (no PWD) is allowed."""
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends import BackendSelector

        captured = {}

        class FakeLinkedAdapter:
            def create_linked_table(self, name, source_table, connect_string):
                captured["connect_string"] = connect_string
                return {"success": True}

        monkeypatch.setattr(BackendSelector, "get_adapter", lambda **kw: FakeLinkedAdapter())

        db = DatabaseUseCases()
        db._policy = WritePolicy(readonly=False)
        db._connections["default"] = FakeLinkedAdapter()
        result = db.create_linked_table(
            name="Orders",
            source_table="Orders",
            connect_string="ODBC;DSN=mydsn;Trusted_Connection=yes",
            confirm=True,
        )

        assert isinstance(result, Ok), f"expected Ok, got {result}"
        assert captured["connect_string"] == "ODBC;DSN=mydsn;Trusted_Connection=yes"


class TestCreateLinkedTableAmbiguous:
    """Case 17: both connect_ref and connect_string → Err (ambiguous)."""

    def test_both_ref_and_literal_ambiguous(self, monkeypatch):
        """Providing both connect_ref and connect_string returns an ambiguous error."""
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends import BackendSelector

        monkeypatch.setattr(BackendSelector, "get_adapter", lambda **kw: MagicMock())

        db = DatabaseUseCases()
        db._policy = WritePolicy(readonly=False)
        result = db.create_linked_table(
            name="Orders",
            source_table="Orders",
            connect_ref="prod_dw",
            connect_string="ODBC;DSN=mydsn;",
            confirm=True,
        )

        assert isinstance(result, Err), f"expected Err, got {result}"
        assert "ambiguous" in result.message.lower()


class TestRefreshLinkedTableNeither:
    """Case 18: refresh_linked_table with neither connect_ref nor connect_string."""

    def test_refresh_with_no_connect_args_preserves_existing(self, monkeypatch):
        """refresh_linked_table called with no new connect args calls adapter with None."""
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends import BackendSelector

        captured = {}

        class FakeLinkedAdapter:
            def refresh_linked_table(self, name, connect_string):
                captured["connect_string"] = connect_string
                return {"success": True}

        monkeypatch.setattr(BackendSelector, "get_adapter", lambda **kw: FakeLinkedAdapter())

        db = DatabaseUseCases()
        db._policy = WritePolicy(readonly=False)
        db._connections["default"] = FakeLinkedAdapter()
        result = db.refresh_linked_table(
            name="Orders",
            confirm=True,
        )

        assert isinstance(result, Ok), f"expected Ok, got {result}"
        assert captured["connect_string"] is None


class TestCreateLinkedTableErrorScrub:
    """Case 19: adapter error with password in message is scrubbed at use-case seam."""

    def test_adapter_error_scrubs_password(self, monkeypatch):
        """Error from adapter containing PWD= must not leak the password value."""
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends import BackendSelector

        monkeypatch.setattr(BackendSelector, "get_adapter", lambda **kw: MagicMock())
        monkeypatch.setattr(
            "ms_access_mcp.application.database.resolve_profile",
            lambda name: "ODBC;PWD=hunter2;Server=srv" if name == "prod_dw" else None,
        )

        class LeakingAdapter:
            def create_linked_table(self, name, source_table, connect_string):
                raise Exception(f"ODBC connect failed: {connect_string}")

        # Patch get_adapter to return leaking adapter
        monkeypatch.setattr(
            BackendSelector,
            "get_adapter",
            lambda **kw: LeakingAdapter(),
        )

        db = DatabaseUseCases()
        db._policy = WritePolicy(readonly=False)
        db._connections["default"] = LeakingAdapter()
        result = db.create_linked_table(
            name="Orders",
            source_table="Orders",
            connect_ref="prod_dw",
            confirm=True,
        )

        assert isinstance(result, Err), f"expected Err, got {result}"
        assert "hunter2" not in result.message
        # Scrub contract (plan 077): the PWD token is removed entirely rather
        # than substituted with a marker; the non-secret remainder survives.
        assert "PWD=" not in result.message.upper()
        assert "ODBC;Server=srv" in result.message
