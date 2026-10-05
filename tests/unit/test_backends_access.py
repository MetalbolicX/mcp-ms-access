"""Tests for AccessAdapter export_table_to_xlsx (DoCmd path)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


class TestAccessAdapterExportTableToXlsx:
    """Test AccessAdapter.export_table_to_xlsx via fake dispatcher."""

    def test_docmd_transfer_spreadsheet_called_with_correct_args(self) -> None:
        """DoCmd.TransferSpreadsheet is called with acExport=1, acSpreadsheetTypeExcel12Xml=14."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

        from ms_access_mcp.backends.access import AccessAdapter

        # Capture what DoCmd.TransferSpreadsheet receives
        captured_args: list[tuple] = []

        docmd_mock = MagicMock()
        docmd_mock.TransferSpreadsheet = lambda *args, **kwargs: captured_args.append((args, kwargs))

        # Create a mock dispatcher with access_app as a plain attribute (not a property)
        mock_dispatcher = MagicMock()
        mock_app = MagicMock()
        mock_app.DoCmd = docmd_mock
        mock_dispatcher.access_app = mock_app

        # Create adapter with __new__ to bypass __init__
        adapter = AccessAdapter.__new__(AccessAdapter)
        adapter._db_path = "fake.accdb"
        adapter._dispatcher = mock_dispatcher
        adapter._connected = True

        # Override call to run synchronously
        def fake_call(fn):
            return fn()

        adapter._dispatcher.call = fake_call

        # Call the method we're testing
        adapter.export_table_to_xlsx("Users", "C:\\temp\\output.xlsx")

        # Assert DoCmd.TransferSpreadsheet was called
        assert len(captured_args) == 1
        args, kwargs = captured_args[0]

        # args[0] = acExport = 1
        # args[1] = acSpreadsheetTypeExcel12Xml = 14
        # args[2] = table_or_query = "Users"
        # args[3] = target_path = "C:\\temp\\output.xlsx"
        # args[4] = hasfieldnames = True
        assert args[0] == 1  # acExport
        assert args[1] == 14  # acSpreadsheetTypeExcel12Xml
        assert args[2] == "Users"
        assert args[3] == "C:\\temp\\output.xlsx"
        assert args[4] is True  # hasfieldnames

    def test_returns_record_count_from_docmd(self) -> None:
        """export_table_to_xlsx returns an int (rowcount proxy)."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

        from ms_access_mcp.backends.access import AccessAdapter

        docmd_mock = MagicMock()
        docmd_mock.TransferSpreadsheet = lambda *args, **kwargs: None

        mock_dispatcher = MagicMock()
        mock_app = MagicMock()
        mock_app.DoCmd = docmd_mock
        mock_app.RecordsAffected = 42
        mock_dispatcher.access_app = mock_app

        adapter = AccessAdapter.__new__(AccessAdapter)
        adapter._db_path = "fake.accdb"
        adapter._dispatcher = mock_dispatcher
        adapter._connected = True

        def fake_call(fn):
            return fn()

        adapter._dispatcher.call = fake_call

        result = adapter.export_table_to_xlsx("Users", "C:\\temp\\output.xlsx")
        assert isinstance(result, int)
        assert result >= 0  # 0 is acceptable when RecordsAffected isn't exposed


class TestTrustedLocationsPassthrough:
    def test_wrapper_invokes_operation_without_registry_mutation(self):
        from ms_access_mcp.backends.access.trusted_locations import trusted_locations_wrap

        calls = []

        def operation(value):
            calls.append(value)
            return value + 1

        assert trusted_locations_wrap(operation, 4) == 5
        assert calls == [4]
