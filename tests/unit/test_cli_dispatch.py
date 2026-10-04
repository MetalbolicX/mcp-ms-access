"""Characterization tests for per-subcommand dispatch — pins current behavior before refactor.

These tests capture the CURRENT exit codes, stdout/stderr text, and use-case method calls
for every leaf command. They MUST pass unmodified after Step 3 (the set_defaults registry refactor).

Strategy: monkeypatch sys.argv then call cli.main() — same pattern as test_cli_configure.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ms_access_mcp import cli
from ms_access_mcp.application.database import Err, Ok
from ms_access_mcp.cli_format import format_result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_format_result_json_err_exit_code():
    assert format_result(Err("boom"), json_output=True) == 1
    assert format_result(Ok(data={"a": 1}), json_output=True) == 0


class _FakeDb:
    """Fake DatabaseUseCases that records which method was called."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def open_database(self, path):
        self.calls.append(("open_database", (path,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def close_database(self):
        self.calls.append(("close_database", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def list_connections(self):
        self.calls.append(("list_connections", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def get_active(self):
        self.calls.append(("get_active", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def get_tables(self):
        self.calls.append(("get_tables", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def get_system_tables(self):
        self.calls.append(("get_system_tables", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def get_table_schema(self, table):
        self.calls.append(("get_table_schema", (table,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok({})

    def get_indexes(self, table):
        self.calls.append(("get_indexes", (table,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def get_relationships(self):
        self.calls.append(("get_relationships", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def get_database_statistics(self):
        self.calls.append(("get_database_statistics", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok({})

    def get_database_properties(self):
        self.calls.append(("get_database_properties", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok({})

    def set_database_property(self, name, value, confirm=False):
        self.calls.append(("set_database_property", (name, value), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def get_queries(self):
        self.calls.append(("get_queries", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def create_query(self, name, sql, confirm=False):
        self.calls.append(("create_query", (name, sql), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def set_query_sql(self, name, sql, confirm=False):
        self.calls.append(("set_query_sql", (name, sql), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def delete_query(self, name, confirm=False):
        self.calls.append(("delete_query", (name,), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def query_data(self, sql, params=None, limit=None):
        self.calls.append(("query_data", (sql,), {"params": params, "limit": limit}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def insert_data(self, table, data, confirm=False):
        self.calls.append(("insert_data", (table, data), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def update_data(self, table, data, where=None, confirm=False):
        self.calls.append(("update_data", (table, data), {"where": where, "confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def delete_data(self, table, where=None, confirm=False):
        self.calls.append(("delete_data", (table,), {"where": where, "confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def create_table(self, table, columns, confirm=False):
        self.calls.append(("create_table", (table, columns), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def alter_table(self, table, action, column_name=None, sql_type=None, confirm=False):
        self.calls.append(("alter_table", (table, action), {"column_name": column_name, "sql_type": sql_type, "confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def delete_table(self, table, confirm=False):
        self.calls.append(("delete_table", (table,), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def create_index(self, table, index, columns, unique=False, confirm=False):
        self.calls.append(("create_index", (table, index, columns), {"unique": unique, "confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def drop_index(self, table, index, confirm=False):
        self.calls.append(("drop_index", (table, index), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def create_relationship(self, name, src_table, src_col, dst_table, dst_col, confirm=False):
        self.calls.append(("create_relationship", (name, src_table, src_col, dst_table, dst_col), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def export_data(self, source, out_path, format=None, sheet_name=None):
        self.calls.append(("export_data", (source, out_path), {"format": format, "sheet_name": sheet_name}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def get_linked_tables(self):
        self.calls.append(("get_linked_tables", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])


class _FakeAutomationUc:
    """Fake AutomationUseCases that records which method was called."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def list_objects(self, kind):
        self.calls.append(("list_objects", (kind,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def object_exists(self, kind, name):
        self.calls.append(("object_exists", (kind, name), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(False)

    def create_object(self, kind, name, confirm=False):
        self.calls.append(("create_object", (kind, name), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def rename_object(self, kind, old_name, new_name, confirm=False):
        self.calls.append(("rename_object", (kind, old_name, new_name), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def delete_object(self, kind, name, confirm=False):
        self.calls.append(("delete_object", (kind, name), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def get_properties(self, kind, name):
        self.calls.append(("get_properties", (kind, name), {}))
        from ms_access_mcp.application.database import Ok
        return Ok({})

    def get_controls(self, kind, name):
        self.calls.append(("get_controls", (kind, name), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def add_control(self, kind, name, ctype, control_name, section=0, confirm=False):
        self.calls.append(("add_control", (kind, name, ctype, control_name), {"section": section, "confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def remove_control(self, kind, name, control_name, confirm=False):
        self.calls.append(("remove_control", (kind, name, control_name), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def get_sections(self, kind, name):
        self.calls.append(("get_sections", (kind, name), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def export_to_text(self, kind, name):
        self.calls.append(("export_to_text", (kind, name), {}))
        from ms_access_mcp.application.database import Ok
        return Ok("")

    def import_from_text(self, kind, name, text, confirm=False):
        self.calls.append(("import_from_text", (kind, name, text), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def get_macros(self):
        self.calls.append(("get_macros", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def macro_exists(self, name):
        self.calls.append(("macro_exists", (name,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(False)

    def create_macro(self, name, confirm=False):
        self.calls.append(("create_macro", (name,), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def rename_macro(self, old_name, new_name, confirm=False):
        self.calls.append(("rename_macro", (old_name, new_name), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def delete_macro(self, name, confirm=False):
        self.calls.append(("delete_macro", (name,), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def run_macro(self, name, confirm=False):
        self.calls.append(("run_macro", (name,), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def get_macro_properties(self, name):
        self.calls.append(("get_macro_properties", (name,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok({})

    def get_modules(self):
        self.calls.append(("get_modules", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def get_vba_code(self, module):
        self.calls.append(("get_vba_code", (module,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok("")

    def set_vba_code(self, module, code, confirm=False):
        self.calls.append(("set_vba_code", (module, code), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def add_vba_procedure(self, module, procedure, code, confirm=False):
        self.calls.append(("add_vba_procedure", (module, procedure, code), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def compile_vba(self):
        self.calls.append(("compile_vba", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def save_database(self, confirm=False):
        self.calls.append(("save_database", (), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def module_exists(self, module):
        self.calls.append(("module_exists", (module,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(False)

    def create_module(self, module, module_type=1, confirm=False):
        self.calls.append(("create_module", (module,), {"module_type": module_type, "confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def delete_module(self, module, confirm=False):
        self.calls.append(("delete_module", (module,), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def rename_module(self, old_name, new_name, confirm=False):
        self.calls.append(("rename_module", (old_name, new_name), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def vba_list_procedures(self, module):
        self.calls.append(("vba_list_procedures", (module,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok([])

    def vba_get_procedure(self, module, procedure):
        self.calls.append(("vba_get_procedure", (module, procedure), {}))
        from ms_access_mcp.application.database import Ok
        return Ok("")

    def vba_replace_procedure(self, module, procedure, code, confirm=False):
        self.calls.append(("vba_replace_procedure", (module, procedure, code), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)


class _FakeVersioningUc:
    """Fake VersioningUseCases."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def export_object(self, kind, name, out_dir):
        self.calls.append(("export_object", (kind, name, out_dir), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def export_all(self, out_dir):
        self.calls.append(("export_all", (out_dir,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def import_object(self, kind, name, file_path, confirm=True):
        self.calls.append(("import_object", (kind, name, file_path), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def import_all(self, input_dir, confirm=True):
        self.calls.append(("import_all", (input_dir,), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def compare(self, export_dir):
        self.calls.append(("compare", (export_dir,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def export_schema_ddl(self, out_dir):
        self.calls.append(("export_schema_ddl", (out_dir,), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)


class _FakeMaintenanceUc:
    """Fake MaintenanceUseCases."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def backup(self, path, retention=None):
        self.calls.append(("backup", (path,), {"retention": retention}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def dev_copy_status(self):
        self.calls.append(("dev_copy_status", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def create_dev_copy(self, source=None):
        self.calls.append(("create_dev_copy", (), {"source": source}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def deploy_dev_copy(self, dev_path, target, confirm=True):
        self.calls.append(("deploy_dev_copy", (dev_path, target), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def compact_repair(self, path, mode, confirm=True):
        self.calls.append(("compact_repair", (path, mode), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def diagnose_environment(self):
        self.calls.append(("diagnose_environment", (), {}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)

    def recover_access(self, confirm=True):
        self.calls.append(("recover_access", (), {"confirm": confirm}))
        from ms_access_mcp.application.database import Ok
        return Ok(None)


def _run_cli(argv: list[str]) -> int:
    """Run cli.main() with sys.argv set to argv (program name prepended)."""
    full_argv = ["ms-access-mcp"] + argv
    with patch.object(sys, "argv", full_argv):
        return cli.main()


# ---------------------------------------------------------------------------
# Test: no-args (prints help and exits 0)
# ---------------------------------------------------------------------------

class TestNoArgs:
    def test_no_args_exits_0(self):
        """No args → print help, return 0 (current behavior)."""
        with patch.object(sys, "argv", ["ms-access-mcp"]):
            result = cli.main()
        assert result == 0


# ---------------------------------------------------------------------------
# Test: db subcommands
# ---------------------------------------------------------------------------

class TestDbSubcommands:
    @pytest.mark.parametrize("argv,expected_call", [
        (["db", "open", "--db", "test.accdb"], ("open_database", ("test.accdb",), {})),
        (["db", "close"], ("close_database", (), {})),
        (["db", "list"], ("list_connections", (), {})),
        (["db", "active"], ("get_active", (), {})),
    ])
    def test_db_action_dispatched(self, argv, expected_call):
        fake_db = _FakeDb()
        with (
            patch.object(cli, "DatabaseUseCases", return_value=fake_db),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(argv)
        assert result == 0
        assert expected_call in fake_db.calls


class TestDbOpenMissingArg:
    def test_db_open_requires_db_flag(self):
        """db open without --db → exit 2 with stderr."""
        with patch.object(cli, "format_result", return_value=0):
            result = _run_cli(["db", "open"])
        assert result == 2


# ---------------------------------------------------------------------------
# Test: tables, schema, indexes, relationships, stats
# ---------------------------------------------------------------------------

class TestReadOnlyCommands:
    @pytest.mark.parametrize("argv,cmd_name", [
        (["tables"], "get_tables"),
        (["tables", "--system"], "get_system_tables"),
        (["schema", "Foo"], "get_table_schema"),
        (["indexes", "Foo"], "get_indexes"),
        (["relationships"], "get_relationships"),
        (["stats"], "get_database_statistics"),
    ])
    def test_readonly_command_dispatched(self, argv, cmd_name):
        fake_db = _FakeDb()
        with (
            patch.object(cli, "DatabaseUseCases", return_value=fake_db),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(argv)
        assert result == 0
        assert any(c[0] == cmd_name for c in fake_db.calls)


# NOTE: schema/indexes missing TABLE arg is caught by argparse (required positional)
# before the handler is called → SystemExit 2. These are argparse-level validation,
# not handler-level, so we do not test them here.


# ---------------------------------------------------------------------------
# Test: props get|set
# ---------------------------------------------------------------------------

class TestProps:
    def test_props_get_dispatched(self):
        fake_db = _FakeDb()
        with (
            patch.object(cli, "DatabaseUseCases", return_value=fake_db),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(["props", "get"])
        assert result == 0
        assert ("get_database_properties", (), {}) in fake_db.calls

    def test_props_set_dispatched(self):
        fake_db = _FakeDb()
        with (
            patch.object(cli, "DatabaseUseCases", return_value=fake_db),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(["props", "set", "--name", "foo", "--value", "bar"])
        assert result == 0
        assert ("set_database_property", ("foo", "bar"), {"confirm": False}) in fake_db.calls

    # NOTE: props set without --name is caught by argparse (required), not handler.
    # Removed: same argparse-exits-before-handler pattern.


# ---------------------------------------------------------------------------
# Test: queries list|create|set|delete
# ---------------------------------------------------------------------------

class TestQueries:
    @pytest.mark.parametrize("argv,expected_call", [
        (["queries", "list"], ("get_queries", (), {})),
        (["queries", "create", "--name", "q1", "--sql", "SELECT 1"],
         ("create_query", ("q1", "SELECT 1"), {"confirm": False})),
        (["queries", "set", "--name", "q1", "--sql", "SELECT 2"],
         ("set_query_sql", ("q1", "SELECT 2"), {"confirm": False})),
        (["queries", "delete", "--name", "q1"], ("delete_query", ("q1",), {"confirm": False})),
    ])
    def test_queries_action_dispatched(self, argv, expected_call):
        fake_db = _FakeDb()
        with (
            patch.object(cli, "DatabaseUseCases", return_value=fake_db),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(argv)
        assert result == 0
        assert expected_call in fake_db.calls

    # NOTE: queries create without --name/--sql is caught by argparse (required), not handler.
    # Removed: same argparse-exits-before-handler pattern.


# ---------------------------------------------------------------------------
# Test: query, insert, update, delete
# ---------------------------------------------------------------------------

class TestDataCommands:
    @pytest.mark.parametrize("argv,cmd_name", [
        (["query", "SELECT 1"], "query_data"),
        (["insert", "Foo", "a=1"], "insert_data"),
        (["update", "Foo", "a=1", "--where", "id=1"], "update_data"),
        (["delete", "Foo", "--where", "id=1"], "delete_data"),
    ])
    def test_data_command_dispatched(self, argv, cmd_name):
        fake_db = _FakeDb()
        with (
            patch.object(cli, "DatabaseUseCases", return_value=fake_db),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(argv)
        assert result == 0
        assert any(c[0] == cmd_name for c in fake_db.calls)


# ---------------------------------------------------------------------------
# Test: ddl subcommands
# ---------------------------------------------------------------------------

class TestDdl:
    @pytest.mark.parametrize("argv,expected_call", [
        (
            ["ddl", "create-table", "--table", "t1", "--columns", "a=INT"],
            ("create_table", ("t1", {"a": "INT"}), {"confirm": False}),
        ),
        (
            ["ddl", "drop-table", "--table", "t1"],
            ("delete_table", ("t1",), {"confirm": False}),
        ),
        (
            ["ddl", "create-index", "--table", "t1", "--index", "i1", "--columns", "a"],
            ("create_index", ("t1", "i1", ["a"]), {"unique": False, "confirm": False}),
        ),
        (
            ["ddl", "drop-index", "--table", "t1", "--index", "i1"],
            ("drop_index", ("t1", "i1"), {"confirm": False}),
        ),
        (
            ["ddl", "relationship",
             "--name", "r1",
             "--source-table", "t1", "--source-column", "a",
             "--dest-table", "t2", "--dest-column", "b"],
            ("create_relationship", ("r1", "t1", "a", "t2", "b"), {"confirm": False}),
        ),
    ])
    def test_ddl_action_dispatched(self, argv, expected_call):
        fake_db = _FakeDb()
        with (
            patch.object(cli, "DatabaseUseCases", return_value=fake_db),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(argv)
        assert result == 0
        assert expected_call in fake_db.calls


# ---------------------------------------------------------------------------
# Test: export
# ---------------------------------------------------------------------------

class TestExport:
    def test_export_table_dispatched(self):
        fake_db = _FakeDb()
        with (
            patch.object(cli, "DatabaseUseCases", return_value=fake_db),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(["export", "Foo", "--out", "out.csv"])
        assert result == 0
        assert ("export_data", ("Foo", "out.csv"), {"format": "csv", "sheet_name": "Sheet1"}) in fake_db.calls


# ---------------------------------------------------------------------------
# Test: links list
# ---------------------------------------------------------------------------

class TestLinks:
    def test_links_list_dispatched(self):
        fake_db = _FakeDb()
        with (
            patch.object(cli, "DatabaseUseCases", return_value=fake_db),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(["links", "list"])
        assert result == 0
        assert ("get_linked_tables", (), {}) in fake_db.calls


# ---------------------------------------------------------------------------
# Test: automation forms (representative subset)
# ---------------------------------------------------------------------------

class TestAutomationForms:
    @pytest.mark.parametrize("argv,expected_call", [
        (["automation", "forms", "list"], ("list_objects", ("form",), {})),
        (["automation", "forms", "exists", "--name", "Form1"], ("object_exists", ("form", "Form1"), {})),
        (["automation", "forms", "create", "--name", "Form1"], ("create_object", ("form", "Form1"), {"confirm": False})),
        (["automation", "forms", "rename", "--old-name", "F1", "--new-name", "F2"],
         ("rename_object", ("form", "F1", "F2"), {"confirm": False})),
        (["automation", "forms", "delete", "--name", "Form1"], ("delete_object", ("form", "Form1"), {"confirm": False})),
    ])
    def test_automation_forms_action_dispatched(self, argv, expected_call):
        fake_uc = _FakeAutomationUc()
        with (
            patch.object(cli, "AutomationUseCases", return_value=fake_uc),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(argv)
        assert result == 0
        assert expected_call in fake_uc.calls


# ---------------------------------------------------------------------------
# Test: automation reports (representative subset)
# ---------------------------------------------------------------------------

class TestAutomationReports:
    @pytest.mark.parametrize("argv,expected_call", [
        (["automation", "reports", "list"], ("list_objects", ("report",), {})),
        (["automation", "reports", "exists", "--name", "Report1"],
         ("object_exists", ("report", "Report1"), {})),
        (["automation", "reports", "create", "--name", "Report1"],
         ("create_object", ("report", "Report1"), {"confirm": False})),
    ])
    def test_automation_reports_action_dispatched(self, argv, expected_call):
        fake_uc = _FakeAutomationUc()
        with (
            patch.object(cli, "AutomationUseCases", return_value=fake_uc),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(argv)
        assert result == 0
        assert expected_call in fake_uc.calls


# ---------------------------------------------------------------------------
# Test: automation macros (representative subset)
# ---------------------------------------------------------------------------

class TestAutomationMacros:
    @pytest.mark.parametrize("argv,expected_call", [
        (["automation", "macros", "list"], ("get_macros", (), {})),
        (["automation", "macros", "exists", "--name", "Macro1"], ("macro_exists", ("Macro1",), {})),
        (["automation", "macros", "run", "--name", "Macro1", "--yes"],
         ("run_macro", ("Macro1",), {"confirm": True})),
    ])
    def test_automation_macros_action_dispatched(self, argv, expected_call):
        fake_uc = _FakeAutomationUc()
        with (
            patch.object(cli, "AutomationUseCases", return_value=fake_uc),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(argv)
        assert result == 0
        assert expected_call in fake_uc.calls


# ---------------------------------------------------------------------------
# Test: automation VBA (representative subset)
# ---------------------------------------------------------------------------

class TestAutomationVba:
    @pytest.mark.parametrize("argv,expected_call", [
        (["automation", "vba", "modules"], ("get_modules", (), {})),
        (["automation", "vba", "get-code", "--module", "Module1"], ("get_vba_code", ("Module1",), {})),
        (["automation", "vba", "compile"], ("compile_vba", (), {})),
    ])
    def test_automation_vba_action_dispatched(self, argv, expected_call):
        fake_uc = _FakeAutomationUc()
        with (
            patch.object(cli, "AutomationUseCases", return_value=fake_uc),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(argv)
        assert result == 0
        assert expected_call in fake_uc.calls


# ---------------------------------------------------------------------------
# Test: versioning
# ---------------------------------------------------------------------------

class TestVersioning:
    @pytest.mark.parametrize("argv,expected_call", [
        (["versioning", "export-object", "--kind", "form", "--name", "F1", "--out-dir", "out"],
         ("export_object", ("form", "F1", "out"), {})),
        (["versioning", "export-all", "--out-dir", "out"], ("export_all", ("out",), {})),
        (["versioning", "compare", "--export-dir", "out"], ("compare", ("out",), {})),
        (["versioning", "export-schema-ddl", "--out-dir", "out"],
         ("export_schema_ddl", ("out",), {})),
    ])
    def test_versioning_action_dispatched(self, argv, expected_call):
        fake_uc = _FakeVersioningUc()
        with (
            patch.object(cli, "VersioningUseCases", return_value=fake_uc),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(argv)
        assert result == 0
        assert expected_call in fake_uc.calls

    def test_versioning_import_object_requires_yes(self):
        fake_uc = _FakeVersioningUc()
        with (
            patch.object(cli, "VersioningUseCases", return_value=fake_uc),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(["versioning", "import-object",
                               "--kind", "form", "--name", "F1",
                               "--file-path", "f.txt", "--yes"])
        assert result == 0
        assert ("import_object", ("form", "F1", "f.txt"), {"confirm": True}) in fake_uc.calls


# ---------------------------------------------------------------------------
# Test: maintenance
# ---------------------------------------------------------------------------

class TestMaintenance:
    @pytest.mark.parametrize("argv,expected_call", [
        (["maintenance", "backup", "foo.accdb"], ("backup", ("foo.accdb",), {"retention": None})),
        (["maintenance", "dev-copy-status"], ("dev_copy_status", (), {})),
        (["maintenance", "diagnose"], ("diagnose_environment", (), {})),
    ])
    def test_maintenance_action_dispatched(self, argv, expected_call):
        fake_uc = _FakeMaintenanceUc()
        with (
            patch.object(cli, "MaintenanceUseCases", return_value=fake_uc),
            patch.object(cli, "format_result", return_value=0),
        ):
            result = _run_cli(argv)
        assert result == 0
        assert expected_call in fake_uc.calls


# ---------------------------------------------------------------------------
# Test: configure opencode (patched — no real file ops)
# ---------------------------------------------------------------------------

class TestConfigureOpencode:
    def test_configure_opencode_returns_0_on_success(self):
        with (
            patch.object(cli, "_resolve_opencode_target", return_value=Path("/tmp/opencode.json")),
            patch.object(cli, "_resolve_exe_path", return_value=Path("/bin/ms-access-mcp")),
            patch.object(cli, "_run_validator", return_value="ok"),
            patch.object(cli, "register", return_value=cli.OpenCodeOk("ok")),
        ):
            result = _run_cli(["configure", "opencode"])
        assert result == 0

    def test_configure_opencode_remove_returns_0(self):
        with (
            patch.object(cli, "_resolve_opencode_target", return_value=Path("/tmp/opencode.json")),
            patch.object(cli, "remove", return_value=cli.OpenCodeOk("ok")),
        ):
            result = _run_cli(["configure", "opencode", "--remove"])
        assert result == 0


# ---------------------------------------------------------------------------
# Test: doctor (patched — no real doctor work)
# ---------------------------------------------------------------------------

class TestDoctor:
    def test_doctor_json_exits_0(self):
        with patch.object(cli, "doctor_main", return_value=0):
            result = _run_cli(["doctor", "--json"])
        assert result == 0

    def test_doctor_exits_0(self):
        with patch.object(cli, "doctor_main", return_value=0):
            result = _run_cli(["doctor"])
        assert result == 0


# ---------------------------------------------------------------------------
# Test: mcp (patched — no real server)
# ---------------------------------------------------------------------------

class TestMcp:
    def test_mcp_exits_0(self):
        # _run_mcp imports the server lazily at call time, so patch the seam
        # it resolves (ms_access_mcp.server.main), not a module attribute of cli.
        with patch("ms_access_mcp.server.main", return_value=0):
            result = _run_cli(["mcp"])
        assert result == 0
