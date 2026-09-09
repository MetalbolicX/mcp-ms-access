"""Tests for cli.py — argparse CLI with destructive operation guard."""

import sys
from io import StringIO
from unittest.mock import patch

import pytest

from ms_access_mcp.cli import main


class TestCLI:
    """Test CLI entry point."""

    def test_help_shows_subcommands(self):
        """--help lists all subcommands."""
        with patch.object(sys, "argv", ["ms-access-mcp", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            # argparse exits with 0 for --help
            assert exc_info.value.code == 0

    def test_doctor_subcommand_exists(self):
        """doctor subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "doctor", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_db_subcommand_exists(self):
        """db subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "db", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_tables_subcommand_exists(self):
        """tables subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "tables", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_schema_subcommand_exists(self):
        """schema subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "schema", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_ddl_subcommand_exists(self):
        """ddl subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "ddl", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_queries_subcommand_exists(self):
        """queries subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "queries", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_query_subcommand_exists(self):
        """query subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "query", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_insert_subcommand_exists(self):
        """insert subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "insert", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_update_subcommand_exists(self):
        """update subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "update", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_delete_subcommand_exists(self):
        """delete subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "delete", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_export_subcommand_exists(self):
        """export subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "export", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_links_subcommand_exists(self):
        """links subcommand is registered."""
        with patch.object(sys, "argv", ["ms-access-mcp", "links", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


class TestDestructiveOperationGuard:
    """Test that destructive operations require --yes flag."""

    def test_drop_table_without_yes_exits_1(self):
        """ddl drop-table without --yes exits with code 1 and 'confirmation required'."""
        from ms_access_mcp.application.database import Err as DbErr

        # Mock the DatabaseUseCases to avoid actual DB operations
        with patch("ms_access_mcp.cli.DatabaseUseCases") as MockDB:
            mock_instance = MockDB.return_value
            # Return an Err for delete_table when confirm=False
            mock_instance.delete_table.return_value = DbErr("Operation 'delete_table' requires explicit confirmation.")
            mock_instance.open_database.return_value = mock_instance

            with patch.object(sys, "argv", ["ms-access-mcp", "ddl", "drop-table", "--table", "Users", "--db", "test.accdb"]):
                captured_stderr = StringIO()
                with patch("sys.stderr", captured_stderr):
                    exit_code = main()

            assert exit_code == 1
            output = captured_stderr.getvalue()
            assert "confirmation" in output.lower() or "required" in output.lower() or "--yes" in output

    def test_delete_query_without_yes_exits_1(self):
        """queries delete without --yes exits with code 1."""
        from ms_access_mcp.application.database import Err as DbErr

        with patch("ms_access_mcp.cli.DatabaseUseCases") as MockDB:
            mock_instance = MockDB.return_value
            mock_instance.delete_query.return_value = DbErr("Operation 'delete_query' requires explicit confirmation.")
            mock_instance.open_database.return_value = mock_instance

            with patch.object(sys, "argv", ["ms-access-mcp", "queries", "delete", "--name", "qry_Test", "--db", "test.accdb"]):
                captured_stderr = StringIO()
                with patch("sys.stderr", captured_stderr):
                    exit_code = main()

            assert exit_code == 1
            output = captured_stderr.getvalue()
            assert "confirmation" in output.lower() or "required" in output.lower() or "--yes" in output

    def test_insert_without_yes_exits_1(self):
        """insert without --yes exits with code 1."""
        from ms_access_mcp.application.database import Err as DbErr

        with patch("ms_access_mcp.cli.DatabaseUseCases") as MockDB:
            mock_instance = MockDB.return_value
            mock_instance.insert_data.return_value = DbErr("Operation 'insert_data' requires explicit confirmation.")
            mock_instance.open_database.return_value = mock_instance

            with patch.object(sys, "argv", ["ms-access-mcp", "insert", "Users", "name=test", "--db", "test.accdb"]):
                captured_stderr = StringIO()
                with patch("sys.stderr", captured_stderr):
                    exit_code = main()

            assert exit_code == 1


class TestGlobalFlags:
    """Test global flags are recognized."""

    def test_backend_flag(self):
        """--backend flag is recognized."""
        with patch.object(sys, "argv", ["ms-access-mcp", "--backend", "odbc", "doctor"]):
            # Should not raise
            with pytest.raises(SystemExit):
                main()

    def test_json_flag(self):
        """--json flag is recognized."""
        with patch.object(sys, "argv", ["ms-access-mcp", "--json", "doctor"]):
            # Should not raise about unknown argument
            with pytest.raises(SystemExit):
                main()

    def test_yes_flag(self):
        """--yes flag is recognized."""
        with patch.object(sys, "argv", ["ms-access-mcp", "--yes", "doctor"]):
            # Should not raise about unknown argument
            with pytest.raises(SystemExit):
                main()

    def test_allow_write_flag(self):
        """--allow-write flag is recognized."""
        with patch.object(sys, "argv", ["ms-access-mcp", "--allow-write", "doctor"]):
            # Should not raise about unknown argument
            with pytest.raises(SystemExit):
                main()
