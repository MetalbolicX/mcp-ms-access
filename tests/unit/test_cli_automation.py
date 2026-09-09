"""Tests for automation CLI subcommands — TDD."""

import json
import subprocess
import sys
from argparse import Namespace
from unittest.mock import MagicMock

from ms_access_mcp.application.automation import Err, Ok
from ms_access_mcp.cli import (
    _cmd_automation_forms_add_control,
    _cmd_automation_forms_controls,
    _cmd_automation_forms_create,
    _cmd_automation_forms_delete,
    _cmd_automation_forms_exists,
    _cmd_automation_forms_export,
    _cmd_automation_forms_import,
    _cmd_automation_forms_list,
    _cmd_automation_forms_properties,
    _cmd_automation_forms_remove_control,
    _cmd_automation_forms_rename,
    _cmd_automation_forms_sections,
    _cmd_automation_macros_create,
    _cmd_automation_macros_delete,
    _cmd_automation_macros_exists,
    _cmd_automation_macros_list,
    _cmd_automation_macros_properties,
    _cmd_automation_macros_rename,
    _cmd_automation_macros_run,
    _cmd_automation_reports_add_control,
    _cmd_automation_reports_controls,
    _cmd_automation_reports_create,
    _cmd_automation_reports_delete,
    _cmd_automation_reports_exists,
    _cmd_automation_reports_export,
    _cmd_automation_reports_import,
    _cmd_automation_reports_list,
    _cmd_automation_reports_properties,
    _cmd_automation_reports_remove_control,
    _cmd_automation_reports_rename,
    _cmd_automation_reports_sections,
    _cmd_automation_vba_add_procedure,
    _cmd_automation_vba_compile,
    _cmd_automation_vba_create_module,
    _cmd_automation_vba_delete_module,
    _cmd_automation_vba_get_code,
    _cmd_automation_vba_get_procedure,
    _cmd_automation_vba_list_procedures,
    _cmd_automation_vba_module_exists,
    _cmd_automation_vba_modules,
    _cmd_automation_vba_rename_module,
    _cmd_automation_vba_replace_procedure,
    _cmd_automation_vba_save,
    _cmd_automation_vba_set_code,
)

# =============================================================================
# Fake AutomationUseCases
# =============================================================================


class FakeAutomationUseCases:
    """Fake AutomationUseCases for testing CLI in isolation."""

    def __init__(self, readonly: bool = True, db_path: str | None = None):
        self.readonly = readonly
        self.db_path = db_path

    def list_objects(self, object_kind):
        return Ok(data=[{"name": "TestForm", "type": "Form"}], rowcount=1)

    def object_exists(self, object_kind, name):
        return Ok(data=True, rowcount=1)

    def create_object(self, object_kind, name, record_source="", properties=None, confirm=False):
        return Ok(data={"name": name}, rowcount=1)

    def rename_object(self, object_kind, old_name, new_name, confirm=False):
        return Ok(data={"old": old_name, "new": new_name}, rowcount=1)

    def delete_object(self, object_kind, name, confirm=False):
        return Ok(data={"deleted": name}, rowcount=1)

    def get_properties(self, object_kind, name):
        return Ok(data={"name": name, "caption": "Test"}, rowcount=1)

    def get_controls(self, object_kind, name):
        return Ok(data=[{"name": "Button1", "type": "Button"}], rowcount=1)

    def add_control(self, object_kind, name, control_type, control_name, section=0, properties=None, confirm=False):
        return Ok(data={"added": control_name}, rowcount=1)

    def remove_control(self, object_kind, name, control_name, confirm=False):
        return Ok(data={"removed": control_name}, rowcount=1)

    def get_sections(self, object_kind, name):
        return Ok(data=[{"id": 0, "name": "Detail"}], rowcount=1)

    def export_to_text(self, object_kind, name):
        return Ok(data={"object_kind": object_kind, "name": name, "text": "form text"}, rowcount=1)

    def import_from_text(self, object_kind, name, text, confirm=False):
        return Ok(data={"imported": name}, rowcount=1)

    def get_macros(self):
        return Ok(data=[{"name": "TestMacro"}], rowcount=1)

    def macro_exists(self, name):
        return Ok(data=True, rowcount=1)

    def create_macro(self, name, confirm=False):
        return Ok(data={"name": name}, rowcount=1)

    def rename_macro(self, old_name, new_name, confirm=False):
        return Ok(data={"old": old_name, "new": new_name}, rowcount=1)

    def delete_macro(self, name, confirm=False):
        return Ok(data={"deleted": name}, rowcount=1)

    def run_macro(self, name, confirm=False):
        return Ok(data={"ran": name}, rowcount=1)

    def get_macro_properties(self, name):
        return Ok(data={"name": name}, rowcount=1)

    def get_modules(self):
        return Ok(data=[{"name": "Module1"}], rowcount=1)

    def get_vba_code(self, module_name):
        return Ok(data="Sub Test()\nEnd Sub", rowcount=1)

    def set_vba_code(self, module_name, code, confirm=False):
        return Ok(data={"module": module_name}, rowcount=1)

    def add_vba_procedure(self, module_name, procedure_name, code, confirm=False):
        return Ok(data={"procedure": procedure_name}, rowcount=1)

    def compile_vba(self):
        return Ok(data={"compileErrors": []}, rowcount=1)

    def save_database(self, confirm=False):
        return Ok(data={"saved": True}, rowcount=1)

    def module_exists(self, module_name):
        return Ok(data=True, rowcount=1)

    def create_module(self, module_name, module_type=1, confirm=False):
        return Ok(data={"module": module_name}, rowcount=1)

    def delete_module(self, module_name, confirm=False):
        return Ok(data={"deleted": module_name}, rowcount=1)

    def rename_module(self, old_name, new_name, confirm=False):
        return Ok(data={"old": old_name, "new": new_name}, rowcount=1)

    def vba_list_procedures(self, module_name):
        return Ok(data=["TestProc"], rowcount=1)

    def vba_get_procedure(self, module_name, procedure_name):
        return Ok(data={"name": procedure_name, "code": "Sub Test()\nEnd Sub"}, rowcount=1)

    def vba_replace_procedure(self, module_name, procedure_name, code, confirm=False):
        return Ok(data={"procedure": procedure_name}, rowcount=1)


# =============================================================================
# Helper to build args Namespace
# =============================================================================


def make_args(**kwargs):
    """Build a fake argparse.Namespace with common defaults."""
    base = {
        "json": False,
        "yes": False,
        "allow_write": False,
        "db": None,
    }
    base.update(kwargs)
    return Namespace(**base)


# =============================================================================
# Forms tests
# =============================================================================


class TestFormsList:
    def test_returns_zero_on_ok(self):
        uc = FakeAutomationUseCases()
        args = make_args(json=False)
        rc = _cmd_automation_forms_list(args, uc)
        assert rc == 0

    def test_returns_one_on_err(self):
        uc = MagicMock()
        uc.list_objects.return_value = Err("failed")
        args = make_args(json=False)
        rc = _cmd_automation_forms_list(args, uc)
        assert rc == 1

    def test_json_output(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(json=True)
        rc = _cmd_automation_forms_list(args, uc)
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is True


class TestFormsExists:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyForm", json=False)
        rc = _cmd_automation_forms_exists(args, uc)
        assert rc == 0

    def test_returns_two_without_name(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(name=None, json=False)
        rc = _cmd_automation_forms_exists(args, uc)
        assert rc == 2
        assert "Error" in capsys.readouterr().err


class TestFormsCreate:
    def test_returns_zero_with_name_and_yes(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="NewForm", yes=True, json=False)
        rc = _cmd_automation_forms_create(args, uc)
        assert rc == 0

    def test_returns_zero_without_yes_but_still_calls_uc(self):
        """create_object itself gates on confirm, CLI passes args.yes."""
        uc = FakeAutomationUseCases()
        args = make_args(name="NewForm", yes=False, json=False)
        rc = _cmd_automation_forms_create(args, uc)
        assert rc == 0  # use case handles the confirm gate


class TestFormsRename:
    def test_returns_zero_with_both_names(self):
        uc = FakeAutomationUseCases()
        args = make_args(old_name="Old", new_name="New", yes=True, json=False)
        rc = _cmd_automation_forms_rename(args, uc)
        assert rc == 0

    def test_returns_two_without_old_name(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(old_name=None, new_name="New", yes=True, json=False)
        rc = _cmd_automation_forms_rename(args, uc)
        assert rc == 2


class TestFormsDelete:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="OldForm", yes=True, json=False)
        rc = _cmd_automation_forms_delete(args, uc)
        assert rc == 0

    def test_returns_two_without_name(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(name=None, json=False)
        rc = _cmd_automation_forms_delete(args, uc)
        assert rc == 2


class TestFormsProperties:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyForm", json=False)
        rc = _cmd_automation_forms_properties(args, uc)
        assert rc == 0


class TestFormsControls:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyForm", json=False)
        rc = _cmd_automation_forms_controls(args, uc)
        assert rc == 0


class TestFormsAddControl:
    def test_returns_zero_with_required_args(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyForm", type="Button", control_name="Btn1", section=0, yes=True, json=False)
        rc = _cmd_automation_forms_add_control(args, uc)
        assert rc == 0

    def test_returns_two_without_name(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(name=None, type="Button", control_name="Btn1", json=False)
        rc = _cmd_automation_forms_add_control(args, uc)
        assert rc == 2


class TestFormsRemoveControl:
    def test_returns_zero_with_required_args(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyForm", control_name="Btn1", yes=True, json=False)
        rc = _cmd_automation_forms_remove_control(args, uc)
        assert rc == 0


class TestFormsSections:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyForm", json=False)
        rc = _cmd_automation_forms_sections(args, uc)
        assert rc == 0


class TestFormsExport:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyForm", json=False)
        rc = _cmd_automation_forms_export(args, uc)
        assert rc == 0


class TestFormsImport:
    def test_returns_zero_with_required_args(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="ImportedForm", text="Form XML", yes=True, json=False)
        rc = _cmd_automation_forms_import(args, uc)
        assert rc == 0

    def test_returns_two_without_name(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(name=None, text="Form XML", json=False)
        rc = _cmd_automation_forms_import(args, uc)
        assert rc == 2


# =============================================================================
# Reports tests — mirror of forms
# =============================================================================


class TestReportsList:
    def test_returns_zero_on_ok(self):
        uc = FakeAutomationUseCases()
        args = make_args(json=False)
        rc = _cmd_automation_reports_list(args, uc)
        assert rc == 0


class TestReportsExists:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyReport", json=False)
        rc = _cmd_automation_reports_exists(args, uc)
        assert rc == 0

    def test_returns_two_without_name(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(name=None, json=False)
        rc = _cmd_automation_reports_exists(args, uc)
        assert rc == 2


class TestReportsCreate:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="NewReport", yes=True, json=False)
        rc = _cmd_automation_reports_create(args, uc)
        assert rc == 0


class TestReportsRename:
    def test_returns_zero_with_both_names(self):
        uc = FakeAutomationUseCases()
        args = make_args(old_name="Old", new_name="New", yes=True, json=False)
        rc = _cmd_automation_reports_rename(args, uc)
        assert rc == 0


class TestReportsDelete:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="OldReport", yes=True, json=False)
        rc = _cmd_automation_reports_delete(args, uc)
        assert rc == 0


class TestReportsProperties:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyReport", json=False)
        rc = _cmd_automation_reports_properties(args, uc)
        assert rc == 0


class TestReportsControls:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyReport", json=False)
        rc = _cmd_automation_reports_controls(args, uc)
        assert rc == 0


class TestReportsAddControl:
    def test_returns_zero_with_required_args(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyReport", type="Label", control_name="Lbl1", section=0, yes=True, json=False)
        rc = _cmd_automation_reports_add_control(args, uc)
        assert rc == 0


class TestReportsRemoveControl:
    def test_returns_zero_with_required_args(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyReport", control_name="Lbl1", yes=True, json=False)
        rc = _cmd_automation_reports_remove_control(args, uc)
        assert rc == 0


class TestReportsSections:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyReport", json=False)
        rc = _cmd_automation_reports_sections(args, uc)
        assert rc == 0


class TestReportsExport:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="MyReport", json=False)
        rc = _cmd_automation_reports_export(args, uc)
        assert rc == 0


class TestReportsImport:
    def test_returns_zero_with_required_args(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="ImportedReport", text="Report XML", yes=True, json=False)
        rc = _cmd_automation_reports_import(args, uc)
        assert rc == 0


# =============================================================================
# Macros tests
# =============================================================================


class TestMacrosList:
    def test_returns_zero_on_ok(self):
        uc = FakeAutomationUseCases()
        args = make_args(json=False)
        rc = _cmd_automation_macros_list(args, uc)
        assert rc == 0

    def test_returns_one_on_err(self):
        uc = MagicMock()
        uc.get_macros.return_value = Err("failed")
        args = make_args(json=False)
        rc = _cmd_automation_macros_list(args, uc)
        assert rc == 1


class TestMacrosExists:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="TestMacro", json=False)
        rc = _cmd_automation_macros_exists(args, uc)
        assert rc == 0

    def test_returns_two_without_name(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(name=None, json=False)
        rc = _cmd_automation_macros_exists(args, uc)
        assert rc == 2


class TestMacrosCreate:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="NewMacro", yes=True, json=False)
        rc = _cmd_automation_macros_create(args, uc)
        assert rc == 0


class TestMacrosRename:
    def test_returns_zero_with_both_names(self):
        uc = FakeAutomationUseCases()
        args = make_args(old_name="Old", new_name="New", yes=True, json=False)
        rc = _cmd_automation_macros_rename(args, uc)
        assert rc == 0

    def test_returns_two_without_old_name(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(old_name=None, new_name="New", yes=True, json=False)
        rc = _cmd_automation_macros_rename(args, uc)
        assert rc == 2


class TestMacrosDelete:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="OldMacro", yes=True, json=False)
        rc = _cmd_automation_macros_delete(args, uc)
        assert rc == 0


class TestMacrosRun:
    def test_returns_two_without_yes(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(name="TestMacro", yes=False, json=False)
        rc = _cmd_automation_macros_run(args, uc)
        assert rc == 2
        assert "--yes" in capsys.readouterr().err

    def test_returns_two_without_name(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(name=None, yes=True, json=False)
        rc = _cmd_automation_macros_run(args, uc)
        assert rc == 2

    def test_returns_zero_with_name_and_yes(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="TestMacro", yes=True, json=False)
        rc = _cmd_automation_macros_run(args, uc)
        assert rc == 0

    def test_returns_one_on_use_case_err(self):
        uc = MagicMock()
        uc.run_macro.return_value = Err("macro not found")
        args = make_args(name="MissingMacro", yes=True, json=False)
        rc = _cmd_automation_macros_run(args, uc)
        assert rc == 1


class TestMacrosProperties:
    def test_returns_zero_with_name(self):
        uc = FakeAutomationUseCases()
        args = make_args(name="TestMacro", json=False)
        rc = _cmd_automation_macros_properties(args, uc)
        assert rc == 0


# =============================================================================
# VBA tests
# =============================================================================


class TestVbaModules:
    def test_returns_zero_on_ok(self):
        uc = FakeAutomationUseCases()
        args = make_args(json=False)
        rc = _cmd_automation_vba_modules(args, uc)
        assert rc == 0


class TestVbaGetCode:
    def test_returns_zero_with_module(self):
        uc = FakeAutomationUseCases()
        args = make_args(module="Module1", json=False)
        rc = _cmd_automation_vba_get_code(args, uc)
        assert rc == 0

    def test_returns_two_without_module(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(module=None, json=False)
        rc = _cmd_automation_vba_get_code(args, uc)
        assert rc == 2


class TestVbaSetCode:
    def test_returns_two_without_yes(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(module="Module1", code="Sub Test()", yes=False, json=False)
        rc = _cmd_automation_vba_set_code(args, uc)
        assert rc == 2
        assert "--yes" in capsys.readouterr().err

    def test_returns_two_without_module(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(module=None, code="Sub Test()", yes=True, json=False)
        rc = _cmd_automation_vba_set_code(args, uc)
        assert rc == 2

    def test_returns_two_without_code(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(module="Module1", code=None, yes=True, json=False)
        rc = _cmd_automation_vba_set_code(args, uc)
        assert rc == 2

    def test_returns_zero_with_all_args(self):
        uc = FakeAutomationUseCases()
        args = make_args(module="Module1", code="Sub Test()", yes=True, json=False)
        rc = _cmd_automation_vba_set_code(args, uc)
        assert rc == 0


class TestVbaAddProcedure:
    def test_returns_zero_with_all_args(self):
        uc = FakeAutomationUseCases()
        args = make_args(module="Module1", procedure="TestProc", code="Sub Test()", yes=True, json=False)
        rc = _cmd_automation_vba_add_procedure(args, uc)
        assert rc == 0


class TestVbaCompile:
    def test_returns_zero_on_ok(self):
        uc = FakeAutomationUseCases()
        args = make_args(json=False)
        rc = _cmd_automation_vba_compile(args, uc)
        assert rc == 0

    def test_returns_one_on_err(self):
        uc = MagicMock()
        uc.compile_vba.return_value = Err("compile failed")
        args = make_args(json=False)
        rc = _cmd_automation_vba_compile(args, uc)
        assert rc == 1


class TestVbaSave:
    def test_returns_zero_with_yes(self):
        uc = FakeAutomationUseCases()
        args = make_args(yes=True, json=False)
        rc = _cmd_automation_vba_save(args, uc)
        assert rc == 0


class TestVbaModuleExists:
    def test_returns_zero_with_module(self):
        uc = FakeAutomationUseCases()
        args = make_args(module="Module1", json=False)
        rc = _cmd_automation_vba_module_exists(args, uc)
        assert rc == 0


class TestVbaCreateModule:
    def test_returns_zero_with_module(self):
        uc = FakeAutomationUseCases()
        args = make_args(module="NewModule", yes=True, json=False)
        rc = _cmd_automation_vba_create_module(args, uc)
        assert rc == 0


class TestVbaDeleteModule:
    def test_returns_zero_with_module(self):
        uc = FakeAutomationUseCases()
        args = make_args(module="OldModule", yes=True, json=False)
        rc = _cmd_automation_vba_delete_module(args, uc)
        assert rc == 0


class TestVbaRenameModule:
    def test_returns_zero_with_both_names(self):
        uc = FakeAutomationUseCases()
        args = make_args(old_name="Old", new_name="New", yes=True, json=False)
        rc = _cmd_automation_vba_rename_module(args, uc)
        assert rc == 0


class TestVbaListProcedures:
    def test_returns_zero_with_module(self):
        uc = FakeAutomationUseCases()
        args = make_args(module="Module1", json=False)
        rc = _cmd_automation_vba_list_procedures(args, uc)
        assert rc == 0


class TestVbaGetProcedure:
    def test_returns_zero_with_args(self):
        uc = FakeAutomationUseCases()
        args = make_args(module="Module1", procedure="TestProc", json=False)
        rc = _cmd_automation_vba_get_procedure(args, uc)
        assert rc == 0

    def test_returns_two_without_module(self, capsys):
        uc = FakeAutomationUseCases()
        args = make_args(module=None, procedure="TestProc", json=False)
        rc = _cmd_automation_vba_get_procedure(args, uc)
        assert rc == 2


class TestVbaReplaceProcedure:
    def test_returns_zero_with_args(self):
        uc = FakeAutomationUseCases()
        args = make_args(module="Module1", procedure="TestProc", code="Sub New()", yes=True, json=False)
        rc = _cmd_automation_vba_replace_procedure(args, uc)
        assert rc == 0


# =============================================================================
# Integration smoke tests via subprocess
# =============================================================================


class TestAutomationHelpSmoke:
    """Smoke tests for --help on automation subcommand groups."""

    def test_automation_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "automation", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert ("forms" in result.stdout or "macros" in result.stdout or "vba" in result.stdout)

    def test_automation_forms_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "automation", "forms", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "list" in result.stdout

    def test_automation_reports_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "automation", "reports", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "list" in result.stdout

    def test_automation_macros_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "automation", "macros", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "list" in result.stdout

    def test_automation_vba_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "ms_access_mcp.cli", "automation", "vba", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "modules" in result.stdout or "get-code" in result.stdout
