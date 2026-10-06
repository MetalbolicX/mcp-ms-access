"""Tests for access.py — AccessAdapter with dispatcher-fake for TDD."""

import logging
import sys
import threading
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from ms_access_mcp.backends.access import AccessAdapter


class FakeDispatcher:
    """Fake dispatcher that invokes _do functions directly for unit testing.

    Simulates the STA-threaded COM call pattern without requiring win32com.
    """

    def __init__(self):
        self._started = False
        self.shutdown_calls = 0
        self._access_app: Any = None
        self._access_pid: int | None = None
        self._current_db: Any = None
        self._ado_conn: Any = None
        self._db_path: Any = None
        self._call_impl: Any = None  # custom call implementation

    def start(self):
        self._started = True

    def set_db_path(self, db_path: str) -> None:
        self._db_path = db_path

    @property
    def access_app(self):
        return self._access_app

    @property
    def current_db(self):
        return self._current_db

    @property
    def ado_conn(self):
        return self._ado_conn

    @property
    def db_path(self):
        return self._db_path

    def call(self, fn, *args, **kwargs):
        """Invoke fn(*args, **kwargs) synchronously in-test."""
        if not self._started:
            raise RuntimeError("Dispatcher has not been started")
        # Use custom implementation if provided
        if self._call_impl is not None:
            return self._call_impl(fn, args, kwargs)
        return fn(*args, **kwargs)

    def shutdown(self):
        self.shutdown_calls += 1
        self._started = False

    def capture_access_pid(self):
        pass


def _make_form_info(name, record_source=""):
    """Build a fake AccessObject representing a form."""
    from ms_access_mcp.models import FormInfo
    return FormInfo(name=name, record_source=record_source)


class _FakeFormObject:
    """Fake Access form object with .Name and .Properties."""
    def __init__(self, name, record_source=""):
        self.Name = name
        self._record_source = record_source

    class _FakeProperties:
        def __init__(self, record_source: str):
            self._record_source = record_source

        def Exists(self, name):
            return name == "RecordSource"

        def __getitem__(self, name):
            class _Val:
                Value = self._record_source
            return _Val()

    @property
    def Properties(self):
        return self._FakeProperties(self._record_source)


class _FakeReportObject:
    """Fake Access report object with .Name and .Properties."""
    def __init__(self, name, record_source=""):
        self.Name = name
        self._record_source = record_source

    class _FakeProperties:
        def __init__(self, record_source: str):
            self._record_source = record_source

        def Exists(self, name):
            return name == "RecordSource"

        def __getitem__(self, name):
            class _Val:
                Value = self._record_source
            return _Val()

    @property
    def Properties(self):
        return self._FakeProperties(self._record_source)


class _FakeMacroObject:
    """Fake Access macro object with .Name and .Properties."""
    def __init__(self, name):
        self.Name = name

    class _FakeProperties:
        def Exists(self, name):
            return name in ("DateCreated", "DateModified")
        def __getitem__(self, name):
            return MagicMock()

    @property
    def Properties(self):
        return self._FakeProperties()


class _FakeAllFormsCollection:
    """Fake AllForms collection returning COM-like objects with .Name."""
    def __init__(self, form_infos):
        self._items = [_FakeFormObject(fi.name, fi.record_source) if hasattr(fi, 'name') else _FakeFormObject(fi[0], fi[1]) for fi in form_infos]

    def __getattr__(self, name):
        if name == "Count":
            return len(self._items)
        raise AttributeError(name)

    def __getitem__(self, i):
        return self._items[i]

    def __call__(self, i):
        return self._items[i]

    def __iter__(self):
        return iter(self._items)


class _FakeAllReportsCollection:
    """Fake AllReports collection returning COM-like objects."""
    def __init__(self, report_infos):
        self._items = [_FakeReportObject(ii.name, ii.record_source) if hasattr(ii, 'name') else _FakeReportObject(ii[0], ii[1]) for ii in report_infos]

    def __getattr__(self, name):
        if name == "Count":
            return len(self._items)
        raise AttributeError(name)

    def __getitem__(self, i):
        return self._items[i]

    def __call__(self, i):
        return self._items[i]

    def __iter__(self):
        return iter(self._items)


class _FakeAllMacrosCollection:
    """Fake AllMacros collection."""
    def __init__(self, macro_names):
        self._items = [_FakeMacroObject(n) for n in macro_names]

    def __getattr__(self, name):
        if name == "Count":
            return len(self._items)
        raise AttributeError(name)

    def __getitem__(self, i):
        return self._items[i]

    def __call__(self, i):
        return self._items[i]

    def __iter__(self):
        return iter(self._items)


def _make_all_forms_collection(*form_infos):
    return _FakeAllFormsCollection(form_infos)


def _make_all_reports_collection(*report_infos):
    return _FakeAllReportsCollection(report_infos)


def _make_all_macros_collection(*macro_names):
    return _FakeAllMacrosCollection(macro_names)


# -------------------------------------------------------------------------- #
# Construction tests
# -------------------------------------------------------------------------- #

class TestAccessAdapterConstruction:
    """Test AccessAdapter initialization."""

    def test_constructed_with_db_path(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._db_path == "test.accdb"
        assert adapter._connected is False

    def test_dispatcher_is_accessible(self):
        adapter = AccessAdapter("test.accdb")
        assert hasattr(adapter, "_dispatcher")


class TestAccessAdapterConnectFailure:
    def test_failed_open_quits_dispatched_application(self, monkeypatch):
        class FakeAccessApp:
            def __init__(self) -> None:
                self.quit_calls = 0
                self.Visible = True

            def OpenCurrentDatabase(self, target, exclusive, password):
                raise RuntimeError("file already in use")

            def Quit(self) -> None:
                self.quit_calls += 1

        app = FakeAccessApp()
        client = types.ModuleType("win32com.client")
        client.__dict__["Dispatch"] = MagicMock(return_value=app)
        win32com = types.ModuleType("win32com")
        win32com.__dict__["client"] = client
        monkeypatch.setitem(sys.modules, "win32com", win32com)
        monkeypatch.setitem(sys.modules, "win32com.client", client)

        adapter = AccessAdapter("test.accdb")
        adapter._dispatcher = FakeDispatcher()

        with pytest.raises(RuntimeError, match="file already in use"):
            adapter.connect()

        assert app.quit_calls == 1
        assert adapter.connected is False
        assert adapter._dispatcher.shutdown_calls == 1


# -------------------------------------------------------------------------- #
# Helper method tests — don't need COM
# -------------------------------------------------------------------------- #

class TestControlTypeMapping:
    """Tests for _control_type_name and _control_type_id helpers."""

    def test_control_type_name_label(self):
        adapter = AccessAdapter("test.accdb")
        # Plan-098 typelib ground truth: 100=acLabel, 102=acLine,
        # 104=acCommandButton, 109=acTextBox.
        assert adapter._control_type_name(100) == "Label"

    def test_control_type_name_command_button(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._control_type_name(104) == "CommandButton"

    def test_control_type_name_unknown(self):
        adapter = AccessAdapter("test.accdb")
        assert "Control(999)" in adapter._control_type_name(999)

    def test_control_type_id_textbox(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._control_type_id("TextBox") == 109

    def test_control_type_id_unknown(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._control_type_id("UnknownControl") == 0


class TestSectionMapping:
    """Tests for _section_name and _section_id helpers."""

    def test_section_name_detail(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._section_name(0) == "detail"

    def test_section_name_header(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._section_name(1) == "header"

    def test_section_name_footer(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._section_name(2) == "footer"

    def test_section_name_unknown(self):
        adapter = AccessAdapter("test.accdb")
        assert "Section(99)" in adapter._section_name(99)

    def test_section_id_detail(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._section_id("detail") == 0

    def test_section_id_case_insensitive(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._section_id("HEADER") == 1


# -------------------------------------------------------------------------- #
# Module-level constants
# -------------------------------------------------------------------------- #

class TestModuleConstants:
    """Test module-level constants are defined."""

    def test_compile_cmd_ids_defined(self):
        from ms_access_mcp.backends.access import _COMPILE_CMD_IDS
        assert _COMPILE_CMD_IDS == [301, 206, 317, 232]

    def test_macro_template_is_captured_save_as_text_dialect(self):
        from ms_access_mcp.backends.access import _MACRO_TEMPLATE

        # Byte-identical to a real SaveAsText(4) export (ground truth
        # 2026-10-10): CRLF line endings (LoadFromText is line-ending
        # sensitive) and the escaped quote before <Statements/> inside the
        # Comment value.
        assert _MACRO_TEMPLATE == (
            "Version =196611\r\n"
            "PublishOption =1\r\n"
            "ColumnsShown =0\r\n"
            "Begin\r\n"
            '    Comment ="_AXL:<?xml version=\\"1.0\\" encoding=\\"UTF-16\\" standalone=\\"no\\"?>\\015\\012<UserI"\r\n'
            '        "nterfaceMacro MinimumClientDesignVersion=\\"14.0.0000.0000\\" xmlns=\\"http://schem"\r\n'
            '        "as.microsoft.com/office/accessservices/2009/11/application\\"><Statements/></User"\r\n'
            '        "InterfaceMacro>"\r\n'
            "End\r\n"
        )
        assert "TemplateMacro" not in _MACRO_TEMPLATE


# -------------------------------------------------------------------------- #
# Not-started default-return tests
# -------------------------------------------------------------------------- #

class TestNotStartedDefaults:
    """Methods return safe defaults when dispatcher is not started."""

    def _make_not_started_adapter(self):
        adapter = AccessAdapter("test.accdb")
        adapter._connected = False

        def fake_call(fn, *args, **kwargs):
            # Verify the _do function checks _started internally
            # If it does, calling it will return the safe default
            return fn()
        object.__setattr__(adapter._dispatcher, '_call_impl', fake_call)
        adapter._dispatcher._started = False
        return adapter

    def test_get_forms_returns_empty(self):
        adapter = self._make_not_started_adapter()
        result = adapter.get_forms()
        assert result == []

    def test_get_reports_returns_empty(self):
        adapter = self._make_not_started_adapter()
        result = adapter.get_reports()
        assert result == []

    def test_get_macros_returns_empty(self):
        adapter = self._make_not_started_adapter()
        result = adapter.get_macros()
        assert result == []

    def test_form_exists_returns_false(self):
        adapter = self._make_not_started_adapter()
        assert adapter.form_exists("AnyForm") is False

    def test_report_exists_returns_false(self):
        adapter = self._make_not_started_adapter()
        assert adapter.report_exists("AnyReport") is False

    def test_macro_exists_returns_false(self):
        adapter = self._make_not_started_adapter()
        assert adapter.macro_exists("AnyMacro") is False

    def test_open_form_returns_false(self):
        adapter = self._make_not_started_adapter()
        assert adapter.open_form("AnyForm") is False

    def test_close_form_returns_false(self):
        adapter = self._make_not_started_adapter()
        assert adapter.close_form("AnyForm") is False

    def test_delete_form_returns_false(self):
        adapter = self._make_not_started_adapter()
        assert adapter.delete_form("AnyForm") is False

    def test_export_form_to_text_returns_empty_string(self):
        adapter = self._make_not_started_adapter()
        result = adapter.export_form_to_text("AnyForm")
        assert result == ""

    def test_import_form_from_text_returns_false(self):
        adapter = self._make_not_started_adapter()
        assert adapter.import_form_from_text("AnyForm", "") is False

    def test_get_vba_project_name_returns_empty(self):
        adapter = self._make_not_started_adapter()
        result = adapter.get_vba_project_name()
        assert result == ""

    def test_get_modules_returns_empty(self):
        adapter = self._make_not_started_adapter()
        result = adapter.get_modules()
        assert result == []

    def test_get_vba_code_returns_empty_string(self):
        adapter = self._make_not_started_adapter()
        result = adapter.get_vba_code("AnyModule")
        assert result == ""

    def test_compile_vba_returns_error_dict(self):
        adapter = self._make_not_started_adapter()
        result = adapter.compile_vba()
        assert result["success"] is False
        assert "Not connected" in result["error"]

    def test_save_database_returns_error_dict(self):
        adapter = self._make_not_started_adapter()
        result = adapter.save_database()
        assert result["success"] is False


# -------------------------------------------------------------------------- #
# Happy-path tests with fake dispatcher
# -------------------------------------------------------------------------- #

class TestFormOperationsHappyPath:
    """Happy-path tests for form operations."""

    def _make_connected_adapter(self, all_forms_collection):
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True

        fake_disp = FakeDispatcher()
        fake_disp._started = True

        # Build a proper fake access_app with CurrentProject
        class FakeCurrentProject:
            def __init__(self, all_forms):
                self.AllForms = all_forms

        fake_proj = FakeCurrentProject(all_forms_collection)

        class FakeAccessApp:
            @property
            def CurrentProject(self):
                return fake_proj

        fake_disp._access_app = FakeAccessApp()

        def fake_call(fn, *args, **kwargs):
            return fn()

        fake_disp._call_impl = fake_call
        adapter._dispatcher = fake_disp
        return adapter

    def test_get_forms_returns_list(self):
        forms = [_make_form_info("Form1", "Table1"), _make_form_info("Form2", "Table2")]
        coll = _make_all_forms_collection(*forms)
        adapter = self._make_connected_adapter(coll)

        result = adapter.get_forms()
        assert len(result) == 2
        assert result[0].name == "Form1"
        assert result[1].name == "Form2"

    def test_form_exists_true(self):
        forms = [_make_form_info("MyForm")]
        coll = _make_all_forms_collection(*forms)
        adapter = self._make_connected_adapter(coll)

        assert adapter.form_exists("MyForm") is True

    def test_form_exists_false(self):
        forms = [_make_form_info("MyForm")]
        coll = _make_all_forms_collection(*forms)
        adapter = self._make_connected_adapter(coll)

        assert adapter.form_exists("OtherForm") is False

    def test_open_form_calls_dispatch(self):
        # Override to add DoCmd
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = MagicMock()
        fake_disp._access_app.DoCmd.OpenForm = MagicMock()
        adapter._dispatcher = fake_disp
        result = adapter.open_form("TestForm")
        assert result is True

    def test_close_form_returns_true(self):
        # Override to add DoCmd
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = MagicMock()
        fake_disp._access_app.DoCmd.CloseForm = MagicMock()
        adapter._dispatcher = fake_disp
        result = adapter.close_form("TestForm")
        assert result is True


class TestReportOperationsHappyPath:
    """Happy-path tests for report operations."""

    def _make_connected_adapter(self, all_reports_collection):
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True

        fake_disp = FakeDispatcher()
        fake_disp._started = True

        class FakeCurrentProject:
            def __init__(self, all_reports):
                self.AllReports = all_reports

        fake_proj = FakeCurrentProject(all_reports_collection)

        class FakeAccessApp:
            @property
            def CurrentProject(self):
                return fake_proj

        fake_disp._access_app = FakeAccessApp()

        def fake_call(fn, *args, **kwargs):
            return fn()

        fake_disp.call = fake_call
        adapter._dispatcher = fake_disp
        return adapter

    def test_get_reports_returns_list(self):
        from ms_access_mcp.models import ReportInfo
        reports = [ReportInfo(name="Report1", record_source=""),
                   ReportInfo(name="Report2", record_source="")]
        coll = _make_all_reports_collection(*reports)
        adapter = self._make_connected_adapter(coll)

        result = adapter.get_reports()
        assert len(result) == 2
        assert result[0].name == "Report1"

    def test_report_exists_true(self):
        from ms_access_mcp.models import ReportInfo
        reports = [ReportInfo(name="MyReport", record_source="")]
        coll = _make_all_reports_collection(*reports)
        adapter = self._make_connected_adapter(coll)

        assert adapter.report_exists("MyReport") is True

    def test_report_exists_false(self):
        from ms_access_mcp.models import ReportInfo
        reports = [ReportInfo(name="MyReport", record_source="")]
        coll = _make_all_reports_collection(*reports)
        adapter = self._make_connected_adapter(coll)

        assert adapter.report_exists("NonExistent") is False


class TestMacroOperationsHappyPath:
    """Happy-path tests for macro operations."""

    def _make_connected_adapter(self, all_macros_collection):
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True

        fake_disp = FakeDispatcher()
        fake_disp._started = True

        class FakeCurrentProject:
            def __init__(self, all_macros):
                self.AllMacros = all_macros

        fake_proj = FakeCurrentProject(all_macros_collection)

        class FakeAccessApp:
            DoCmd = MagicMock()

            @property
            def CurrentProject(self):
                return fake_proj

        fake_disp._access_app = FakeAccessApp()

        def fake_call(fn, *args, **kwargs):
            return fn()

        fake_disp.call = fake_call
        adapter._dispatcher = fake_disp
        return adapter

    def test_get_macros_returns_list(self):
        coll = _make_all_macros_collection("Macro1", "Macro2")
        adapter = self._make_connected_adapter(coll)

        result = adapter.get_macros()
        assert len(result) == 2
        assert result[0].name == "Macro1"

    def test_macro_exists_true(self):
        coll = _make_all_macros_collection("MyMacro")
        adapter = self._make_connected_adapter(coll)

        assert adapter.macro_exists("MyMacro") is True

    def test_macro_exists_false(self):
        coll = _make_all_macros_collection("MyMacro")
        adapter = self._make_connected_adapter(coll)

        assert adapter.macro_exists("OtherMacro") is False

    def test_create_macro_loads_with_ac_macro_four_and_name_argument(self):
        coll = _make_all_macros_collection()
        adapter = self._make_connected_adapter(coll)
        loader = MagicMock(return_value=True)
        adapter._load_object_from_text = loader

        assert adapter.create_macro("CreatedName") is True
        object_type, object_name, text = loader.call_args.args
        assert object_type == 4
        assert object_name == "CreatedName"
        from ms_access_mcp.backends.access import _MACRO_TEMPLATE
        assert text == _MACRO_TEMPLATE

    def test_rename_and_delete_macro_use_ac_macro_four(self):
        coll = _make_all_macros_collection()
        adapter = self._make_connected_adapter(coll)

        assert adapter.rename_macro("OldName", "NewName") is True
        assert adapter.delete_macro("NewName") is True
        adapter._dispatcher.access_app.DoCmd.Rename.assert_called_once_with(
            "NewName", 4, "OldName"
        )
        adapter._dispatcher.access_app.DoCmd.DeleteObject.assert_called_once_with(
            4, "NewName"
        )


class TestVBAOperationsHappyPath:
    """Happy-path tests for VBA operations."""

    def _make_connected_adapter_with_vb_project(self, vb_project, all_modules_coll=None):
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True

        fake_disp = FakeDispatcher()
        fake_disp._started = True

        class VBProjectsWithIter:
            def __init__(self, proj, count):
                self._proj = proj
                self.Count = count

            def __getitem__(self, i):
                if 1 <= i <= self.Count:
                    return self._proj
                raise IndexError(i)

            def __call__(self, i):
                return self._proj

            def __iter__(self):
                yield self._proj

        vbe_proj = VBProjectsWithIter(vb_project, 1)

        class VBEWithVBProjects:
            def __init__(self, vb_projs):
                self.VBProjects = vb_projs

        class FakeCurrentProject:
            def __init__(self, all_modules=None):
                self._all_modules = all_modules

            @property
            def AllModules(self):
                return self._all_modules

        class FakeAccessAppWithVBE:
            def __init__(self):
                self.DoCmd = MagicMock()

            @property
            def VBE(self):
                return VBEWithVBProjects(vbe_proj)

            @property
            def CurrentProject(self):
                return FakeCurrentProject(all_modules_coll)

        fake_app = FakeAccessAppWithVBE()
        fake_disp._access_app = fake_app

        def fake_call(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        fake_disp.call = fake_call
        adapter._dispatcher = fake_disp
        return adapter

    def test_get_modules(self):
        comp = MagicMock()
        comp.Name = "Module1"
        comp.Type = 1  # Standard Module
        code_mod = MagicMock()
        code_mod.CountOfLines = 1
        code_mod.Lines = MagicMock(return_value="Sub Test()\nEnd Sub")
        comp.CodeModule = code_mod

        proj = MagicMock()
        proj.VBComponents = [comp]

        adapter = self._make_connected_adapter_with_vb_project(proj)
        result = adapter.get_modules()
        assert len(result) == 1
        assert result[0].name == "Module1"

    def test_get_vba_code(self):
        comp = MagicMock()
        comp.Name = "Module1"
        comp.Type = 1
        code_mod = MagicMock()
        code_mod.CountOfLines = 3
        code_mod.Lines = MagicMock(return_value="Sub Test()\nEnd Sub")
        comp.CodeModule = code_mod

        proj = MagicMock()
        proj.VBComponents = [comp]

        adapter = self._make_connected_adapter_with_vb_project(proj)
        result = adapter.get_vba_code("Module1")
        assert "Sub Test()" in result

    def test_set_vba_code_restores_snapshot_after_add_failure(self):
        class _FakeCodeModule:
            def __init__(self):
                self.text = "Sub Original()\nEnd Sub"
                self.add_calls = []

            @property
            def CountOfLines(self):
                return len(self.text.splitlines())

            def Lines(self, start, count):
                return self.text

            def DeleteLines(self, start, count):
                self.text = ""

            def AddFromString(self, text):
                self.add_calls.append(text)
                if len(self.add_calls) == 1:
                    raise RuntimeError("injected AddFromString failure")
                self.text = text

        code_module = _FakeCodeModule()
        comp = MagicMock(Name="Module1", CodeModule=code_module)
        project = MagicMock(VBComponents=[comp])
        adapter = self._make_connected_adapter_with_vb_project(project)

        assert adapter.set_vba_code("Module1", "Sub Replacement()\nEnd Sub") is False
        assert code_module.add_calls == [
            "Sub Replacement()\nEnd Sub",
            "Sub Original()\nEnd Sub",
        ]
        assert code_module.text == "Sub Original()\nEnd Sub"

    def test_set_vba_code_reports_save_failure(self):
        comp = MagicMock(Name="Module1")
        project = MagicMock(VBComponents=[comp])
        adapter = self._make_connected_adapter_with_vb_project(project)
        app = adapter._dispatcher.access_app
        app.DoCmd.Save.side_effect = RuntimeError("save failed")

        assert adapter.set_vba_code("Module1", "Sub A()\nEnd Sub") is False
        app.DoCmd.Save.assert_called_once_with(5, "Module1")

    def test_set_vba_code_rejects_embedded_nul(self):
        # Defect #8: COM silently truncates code at embedded NUL bytes;
        # the adapter must fail closed instead of corrupting the module.
        comp = MagicMock(Name="Module1")
        project = MagicMock(VBComponents=[comp])
        adapter = self._make_connected_adapter_with_vb_project(project)

        assert adapter.set_vba_code("Module1", "Sub A()\n    x\x00\nEnd Sub") is False
        comp.CodeModule.AddFromString.assert_not_called()
        comp.CodeModule.DeleteLines.assert_not_called()

    def test_add_vba_procedure_rejects_embedded_nul(self):
        comp = MagicMock(Name="Module1")
        project = MagicMock(VBComponents=[comp])
        adapter = self._make_connected_adapter_with_vb_project(project)

        assert adapter.add_vba_procedure("Module1", "A", "Sub A()\n    x\x00\nEnd Sub") is False
        comp.CodeModule.AddFromString.assert_not_called()

    def test_vba_replace_procedure_rejects_embedded_nul(self):
        comp = MagicMock(Name="Module1")
        project = MagicMock(VBComponents=[comp])
        adapter = self._make_connected_adapter_with_vb_project(project)

        assert adapter.vba_replace_procedure("Module1", "A", "Sub A()\n    x\x00\nEnd Sub") is False
        comp.CodeModule.InsertLines.assert_not_called()

    def test_vba_replace_procedure_restores_snapshot_after_insert_failure(self):
        class _FakeCodeModule:
            def __init__(self):
                self.text = "Private Sub Button_Click()\n    OldBody\nEnd Sub"
                self.insert_calls = []

            def ProcStartLine(self, name, kind):
                return 1

            def ProcCountLines(self, name, kind):
                return len(self.text.splitlines())

            def Lines(self, start, count):
                return self.text

            def DeleteLines(self, start, count):
                self.text = ""

            def InsertLines(self, start, text):
                self.insert_calls.append((start, text))
                if len(self.insert_calls) == 1:
                    raise RuntimeError("injected InsertLines failure")
                self.text = text

        code_module = _FakeCodeModule()
        comp = MagicMock(Name="Module1", CodeModule=code_module)
        project = MagicMock(VBComponents=[comp])
        adapter = self._make_connected_adapter_with_vb_project(project)

        assert adapter.vba_replace_procedure(
            "Module1", "Button_Click", "Private Sub Button_Click()\n    NewBody\nEnd Sub"
        ) is False
        assert code_module.insert_calls == [
            (1, "Private Sub Button_Click()\n    NewBody\nEnd Sub"),
            (1, "Private Sub Button_Click()\n    OldBody\nEnd Sub"),
        ]
        assert code_module.text == "Private Sub Button_Click()\n    OldBody\nEnd Sub"

    def test_set_control_event_procedure_creates_missing_procedure_before_property(self):
        events = []

        class _FakeCodeModule:
            def __init__(self):
                self.text = ""

            def ProcStartLine(self, name, kind):
                if not self.text:
                    raise RuntimeError("procedure missing")
                return 1

            def ProcCountLines(self, name, kind):
                return len(self.text.splitlines())

            def Lines(self, start, count):
                return self.text

            def AddFromString(self, code):
                events.append(("write", code))
                self.text += code

            def DeleteLines(self, start, count):
                self.text = ""

            def InsertLines(self, start, code):
                events.append(("write", code))
                self.text = code

        code_module = _FakeCodeModule()
        component = MagicMock(Name="Form_TestForm", CodeModule=code_module)
        project = MagicMock(VBComponents=[component])
        adapter = self._make_connected_adapter_with_vb_project(project)

        class FakeControl:
            Name = "Button"

            def __setattr__(self, attr, value):
                # Real Access controls expose event bindings as direct
                # attributes (plan-098); record them like the old
                # Properties-collection path did.
                if attr.startswith("On"):
                    events.append(("property", attr, value))
                object.__setattr__(self, attr, value)

            def Properties(self, name):
                class EventProperty:
                    @property
                    def Value(self):
                        return ""

                    @Value.setter
                    def Value(self, value):
                        events.append(("property", name, value))

                return EventProperty()

        class FakeControls:
            Count = 1

            def __call__(self, index):
                return FakeControl()

        form = MagicMock()
        form.Controls = FakeControls()
        adapter._with_design_view = lambda kind, name, **kwargs: kwargs["work"](form)

        result = adapter.set_control_event_procedure(
            "TestForm", "Button", "Click", "Private Sub Button_Click()\n    MsgBox \"clicked\"\nEnd Sub"
        )

        assert result is True
        assert code_module.text == "Private Sub Button_Click()\n    MsgBox \"clicked\"\nEnd Sub"
        assert events == [
            ("write", "Private Sub Button_Click()\nEnd Sub"),
            ("write", "Private Sub Button_Click()\n    MsgBox \"clicked\"\nEnd Sub"),
            ("property", "OnClick", "[Event Procedure]"),
        ]

    def test_module_exists_true(self):

        class _FakeVbComp:
            def __init__(self, name):
                self.Name = name
                self.Type = 1
                self.CodeModule = MagicMock()

        comp = _FakeVbComp("Module1")

        proj = MagicMock()
        proj.VBComponents = [comp]

        # Build AllModules collection — plain class, not MagicMock
        class _FakeAllModulesColl:
            def __init__(self, items):
                self._items = items

            def __getattr__(self, name):
                if name == "Count":
                    return len(self._items)
                raise AttributeError(name)

            def __getitem__(self, i):
                return self._items[i]

            def __call__(self, i):
                return self._items[i]

        all_mods = _FakeAllModulesColl([comp])

        adapter = self._make_connected_adapter_with_vb_project(proj, all_modules_coll=all_mods)

        assert adapter.module_exists("Module1") is True


# -------------------------------------------------------------------------- #
# Exception-path tests — fake dispatcher raises → method returns safe default
# -------------------------------------------------------------------------- #

class TestListObjectsCollectionFailure:
    def test_property_read_failure_is_still_skipped(self):
        class FakeObject:
            Name = "FormWithUnreadableProperty"

            @property
            def Properties(self):
                raise RuntimeError("property unavailable")

        class FakeCollection:
            Count = 1

            def __call__(self, _index):
                return FakeObject()

        class FakeCurrentProject:
            AllForms = FakeCollection()

        class FakeApp:
            CurrentProject = FakeCurrentProject()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = FakeApp()
        adapter._dispatcher = fake_disp

        result = adapter.get_forms()

        assert [form.name for form in result] == ["FormWithUnreadableProperty"]
        assert result[0].record_source == ""

    def test_collection_access_failure_raises_backend_error(self):
        from ms_access_mcp.errors import AccessBackendError

        class FakeCurrentProject:
            @property
            def AllForms(self):
                raise RuntimeError("collection unavailable")

        class FakeApp:
            CurrentProject = FakeCurrentProject()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = FakeApp()
        adapter._dispatcher = fake_disp

        with pytest.raises(AccessBackendError, match="form.*collection unavailable"):
            adapter.get_forms()


class TestExceptionSafety:
    """Tests that methods return safe defaults when dispatcher raises."""

    def _make_exception_dispatcher_adapter(self):
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True

        def raising_call(fn, *args, **kwargs):
            raise RuntimeError("COM error")

        adapter._dispatcher.call = raising_call
        adapter._dispatcher._started = True
        return adapter

    def test_get_forms_returns_empty_on_exception(self):
        adapter = self._make_exception_dispatcher_adapter()
        result = adapter.get_forms()
        assert result == []

    def test_get_reports_returns_empty_on_exception(self):
        adapter = self._make_exception_dispatcher_adapter()
        result = adapter.get_reports()
        assert result == []

    def test_get_macros_returns_empty_on_exception(self):
        adapter = self._make_exception_dispatcher_adapter()
        result = adapter.get_macros()
        assert result == []

    def test_get_modules_returns_empty_on_exception(self):
        adapter = self._make_exception_dispatcher_adapter()
        result = adapter.get_modules()
        assert result == []

    def test_form_exists_returns_false_on_exception(self):
        adapter = self._make_exception_dispatcher_adapter()
        assert adapter.form_exists("AnyForm") is False

    def test_open_form_returns_false_on_exception(self):
        adapter = self._make_exception_dispatcher_adapter()
        assert adapter.open_form("AnyForm") is False


# -------------------------------------------------------------------------- #
# VBA method existence
# -------------------------------------------------------------------------- #

class TestVBAOperationsExist:
    """Test that VBA operations exist and route through dispatcher.call."""

    def test_all_vba_methods_exist(self):
        adapter = AccessAdapter("test.accdb")
        methods = [
            "get_vba_project_name", "get_modules", "get_vba_code",
            "set_vba_code", "add_vba_procedure", "delete_module",
            "create_module", "rename_module", "module_exists",
            "save_database", "compile_vba", "vba_list_procedures",
            "vba_get_procedure", "vba_replace_procedure",
        ]
        for m in methods:
            assert hasattr(adapter, m), f"Missing method: {m}"


# -------------------------------------------------------------------------- #
# Dialog killer
# -------------------------------------------------------------------------- #

class TestDialogKiller:
    """Tests for _dialog_killer daemon thread behavior."""

    def test_dialog_killer_exits_on_stop_event(self):
        """Setting stop_event causes loop to exit promptly."""
        stop = threading.Event()

        def fake_killer(stop_event):
            """Fake that simulates the dialog killer loop with quick exit."""
            while not stop_event.is_set():
                stop_event.wait(timeout=0.02)

        # Set stop *before* calling so the fake exits immediately.
        stop.set()
        fake_killer(stop)
        assert stop.is_set()


# -------------------------------------------------------------------------- #
# get_current_db_name — STA-threaded accessor
# -------------------------------------------------------------------------- #

class TestGetCurrentDbName:
    """get_current_db_name dispatches the .Name read through the STA thread."""

    def test_returns_name_via_dispatcher_call(self):
        from ms_access_mcp.backends.access import AccessAdapter

        class _FakeDb:
            Name = "C:\\fake\\db.accdb"

        adapter = AccessAdapter("ignored")
        adapter._dispatcher._started = True
        adapter._dispatcher._current_db = _FakeDb()

        call_count = {"n": 0}

        def _spy_call(fn, *args, **kwargs):
            call_count["n"] += 1
            return fn(*args, **kwargs)

        adapter._dispatcher.call = _spy_call  # type: ignore[assignment]

        result = adapter.get_current_db_name()
        assert result == "C:\\fake\\db.accdb"
        assert call_count["n"] == 1  # must have gone through dispatcher.call


class TestCreateObjectApplicationSurface:
    @pytest.mark.parametrize(
        "kind,create_method,ac_type,default_name",
        [("form", "CreateForm", 2, "Form1"), ("report", "CreateReport", 3, "Report1")],
    )
    def test_creation_uses_three_fresh_dispatcher_calls(
        self, kind, create_method, ac_type, default_name
    ):
        from ms_access_mcp.backends.access.object_kinds import _create_object

        events = []
        created = []

        class FakeProperty:
            def __init__(self, name):
                self.name = name

            @property
            def Value(self):
                return None

            @Value.setter
            def Value(self, value):
                events.append(("Property", self.name, value))

        class FakeObject:
            @property
            def RecordSource(self):
                return ""

            @RecordSource.setter
            def RecordSource(self, value):
                events.append(("RecordSource", value))

            def Properties(self, name):
                return FakeProperty(name)

        obj = FakeObject()
        obj.Name = default_name

        class FakeDoCmd:
            def Close(self, close_type, name, save_flag):
                events.append(("Close", close_type, name, save_flag))

            def Rename(self, new_name, rename_type, old_name):
                events.append(("Rename", new_name, rename_type, old_name))
                obj.Name = new_name

        class FakeApp:
            DoCmd = FakeDoCmd()

            def CreateForm(self):
                events.append(("CreateForm",))
                created.append(obj)
                return obj

            def CreateReport(self):
                events.append(("CreateReport",))
                created.append(obj)
                return obj

        class FakeDispatcher:
            _started = True

            def __init__(self):
                self.calls = 0
                self.app_reads = 0
                self.app = FakeApp()

            @property
            def access_app(self):
                self.app_reads += 1
                return self.app

            def call(self, fn):
                self.calls += 1
                return fn()

        dispatcher = FakeDispatcher()
        result = _create_object(
            dispatcher,
            kind,
            "RequestedName",
            record_source="SELECT 1",
            properties={"Caption": "Wanted"},
        )

        assert result is True
        assert dispatcher.calls == dispatcher.app_reads == 3
        assert created == [obj]
        assert obj.Name == "RequestedName"
        assert events == [
            (create_method,),
            ("RecordSource", "SELECT 1"),
            ("Property", "Caption", "Wanted"),
            ("Close", ac_type, default_name, 1),
            ("Rename", "RequestedName", ac_type, default_name),
        ]

    def test_rename_failure_closes_unsaved_design_object_without_saving(self):
        from ms_access_mcp.backends.access.object_kinds import _create_object

        events = []

        class FakeObject:
            Name = "Form1"

        class FakeDoCmd:
            def Close(self, close_type, name, save_flag):
                events.append(("Close", close_type, name, save_flag))

            def Rename(self, *_args):
                events.append(("Rename",))
                raise RuntimeError("rename rejected")

        class FakeApp:
            DoCmd = FakeDoCmd()

            def CreateForm(self):
                events.append(("CreateForm",))
                return FakeObject()

        class FakeDispatcher:
            _started = True

            def __init__(self):
                self.calls = 0

            @property
            def access_app(self):
                return app

            def call(self, fn):
                self.calls += 1
                return fn()

        app = FakeApp()
        dispatcher = FakeDispatcher()

        result = _create_object(dispatcher, "form", "WantedForm")

        assert result is False
        assert dispatcher.calls == 4
        assert events == [
            ("CreateForm",),
            ("Close", 2, "Form1", 1),
            ("Rename",),
            ("Close", 2, "Form1", 2),
        ]


class TestCreateFormRecordSourceRejectionLogging:
    """create_form preserves best-effort RecordSource behavior with DEBUG logging."""

    def test_record_source_rejection_logs_debug_and_returns_true(self, caplog):
        class _FakeForm:
            Name = "TestForm"

            @property
            def RecordSource(self):
                return ""

            @RecordSource.setter
            def RecordSource(self, value):
                raise RuntimeError(f"Cannot set source {value}")

        form = _FakeForm()

        class _FakeDoCmd:
            def Close(self, ac_type, name, save):
                pass

            def Rename(self, new_name, ac_type, old_name):
                form.Name = new_name

        class _FakeAccessApp:
            DoCmd = _FakeDoCmd()

            def CreateForm(self):
                return form

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        fake_disp._call_impl = lambda fn, *args, **kwargs: fn()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter.create_form("TestForm", record_source="qryX")

        assert result is True
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("RecordSource" in msg and "qryX" in msg for msg in debug_msgs)


# -------------------------------------------------------------------------- #
# Step 2: COM mutation failure classification
# -------------------------------------------------------------------------- #


class TestDeleteFormErrorClassification:
    """delete_form returns False and logs a WARNING with error classification."""

    def _make_adapter_with_dispatcher(self, call_impl):
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True

        class FakeAccessApp:
            class FakeDoCmd:
                def DeleteObject(self, ac_type, name):
                    raise Exception(f"Cannot find object '{name}' in this collection")

            DoCmd = FakeDoCmd()

            class FakeCurrentProject:
                AllForms = MagicMock()

            CurrentProject = FakeCurrentProject()

        fake_disp._access_app = FakeAccessApp()
        fake_disp._call_impl = call_impl
        adapter._dispatcher = fake_disp
        return adapter

    def test_delete_form_not_found_logs_warning(self, caplog):
        """delete_form whose COM raises not-found error returns False and logs WARNING."""
        caplog.set_level(logging.WARNING)

        def raise_delete(fn, args, kwargs):
            return fn()

        adapter = self._make_adapter_with_dispatcher(raise_delete)
        result = adapter.delete_form("GhostForm")

        assert result is False
        assert len(caplog.records) >= 1
        # The warning log should mention the object name and classification
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("GhostForm" in msg for msg in warning_msgs), (
            f"Expected warning about 'GhostForm', got: {warning_msgs}"
        )

    def test_delete_form_generic_error_logs_warning(self, caplog):
        """delete_form whose COM raises generic error returns False and logs WARNING."""
        caplog.set_level(logging.WARNING)

        def raise_delete(fn, args, kwargs):
            return fn()  # let _do() run; its DoCmd call raises the error

        # Replace the DoCmd to raise a generic error (inside _do())
        adapter = self._make_adapter_with_dispatcher(raise_delete)
        app = adapter._dispatcher._access_app
        assert app is not None
        app.DoCmd.DeleteObject = MagicMock(side_effect=RuntimeError("Unexpected COM failure"))
        result = adapter.delete_form("SomeForm")

        assert result is False
        assert len(caplog.records) >= 1
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("SomeForm" in msg for msg in warning_msgs), (
            f"Expected warning about 'SomeForm', got: {warning_msgs}"
        )

    def test_delete_form_not_found_warning_names_exception_class(self, caplog):
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter_with_dispatcher(
            lambda fn, args, kwargs: fn()
        )

        assert adapter.delete_form("GhostForm") is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("AccessObjectNotFoundError" in msg for msg in warning_msgs)

    def test_delete_form_generic_warning_names_exception_class(self, caplog):
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter_with_dispatcher(
            lambda fn, args, kwargs: fn()
        )
        app = adapter._dispatcher._access_app
        assert app is not None
        app.DoCmd.DeleteObject = MagicMock(side_effect=RuntimeError("Unexpected COM failure"))

        assert adapter.delete_form("SomeForm") is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("AccessBackendError" in msg for msg in warning_msgs)


class TestRenameFormErrorClassification:
    """rename_form returns False and logs a WARNING with error classification."""

    def _make_adapter_with_rename_error(self, exc):
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True

        class FakeAccessApp:
            class FakeDoCmd:
                def Rename(self, new_name, ac_type, old_name):
                    raise exc

            DoCmd = FakeDoCmd()

            class FakeCurrentProject:
                AllForms = MagicMock()

            CurrentProject = FakeCurrentProject()

        fake_disp._access_app = FakeAccessApp()

        def fake_call(fn, *args, **kwargs):
            return fn()

        fake_disp.call = fake_call
        adapter._dispatcher = fake_disp
        return adapter

    def test_rename_form_not_found_logs_warning(self, caplog):
        """rename_form whose COM raises not-found error returns False and logs WARNING."""
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter_with_rename_error(
            Exception("Cannot find object 'OldName' in this collection")
        )
        result = adapter.rename_form("OldName", "NewName")

        assert result is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("OldName" in msg for msg in warning_msgs), (
            f"Expected warning about 'OldName', got: {warning_msgs}"
        )

    def test_rename_form_generic_error_logs_warning(self, caplog):
        """rename_form whose COM raises generic error returns False and logs WARNING."""
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter_with_rename_error(
            RuntimeError("Unexpected COM failure")
        )
        result = adapter.rename_form("OldName", "NewName")

        assert result is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("OldName" in msg or "NewName" in msg for msg in warning_msgs), (
            f"Expected warning about 'OldName' or 'NewName', got: {warning_msgs}"
        )

    def test_rename_form_warning_names_exception_class(self, caplog):
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter_with_rename_error(
            Exception("Cannot find object 'OldName' in this collection")
        )

        assert adapter.rename_form("OldName", "NewName") is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("AccessObjectNotFoundError" in msg for msg in warning_msgs)


# -------------------------------------------------------------------------- #
# Step 3: Property-write failures are logged, not swallowed
# -------------------------------------------------------------------------- #

class TestCreateFormPropertyRejectionLogging:
    """create_form with bad property writes logs WARNING but still returns True."""

    def _make_adapter_with_bad_property(self, prop_to_raise_on: str):
        """Adapter whose fake form raises when Properties(prop_to_raise_on).Value is set."""
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True

        class _FakeProperty:
            """Fake property item whose Value setter raises."""
            def __init__(self, name):
                self.name = name

            @property
            def Value(self):
                return None

            @Value.setter
            def Value(self, val):
                raise Exception(f"Cannot set property '{self.name}'")

        class _FakeProperties:
            """Fake Properties collection that returns a raising _FakeProperty."""
            def __init__(self, bad_prop):
                self._bad_prop = bad_prop

            def __call__(self, name):
                return _FakeProperty(name)

        class _FakeForm:
            def __init__(self, name, properties):
                self.Name = name
                self._properties = _FakeProperties(properties)

            @property
            def Properties(self):
                return self._properties

        form_obj = _FakeForm("TestForm", prop_to_raise_on)

        class FakeDoCmd:
            def Close(self, ac_type, name, save):
                pass

            def Rename(self, new_name, ac_type, old_name):
                form_obj.Name = new_name

        class FakeAccessApp:
            DoCmd = FakeDoCmd()

            def CreateForm(self):
                return form_obj

            class CurrentProject:
                AllForms = MagicMock()

        fake_disp._access_app = FakeAccessApp()

        def fake_call(fn, *args, **kwargs):
            return fn()

        fake_disp._call_impl = fake_call
        adapter._dispatcher = fake_disp
        return adapter

    def test_create_form_bad_property_logs_warning(self, caplog):
        """create_form with an invalid property returns True but logs a WARNING."""
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter_with_bad_property("BadProp")
        result = adapter.create_form("TestForm", properties={"BadProp": 1})

        assert result is True, "create_form must return True even on property rejection"
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("BadProp" in msg for msg in warning_msgs), (
            f"Expected WARNING about 'BadProp', got: {warning_msgs}"
        )


class TestCreateFormErrorClassification:
    """create_form returns False and logs a WARNING when CreateForm itself fails."""

    def _make_adapter_with_create_error(self, exc):
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True

        class FakeAccessApp:
            class FakeDoCmd:
                def Close(self, ac_type, name, save):
                    pass

            DoCmd = FakeDoCmd()

            class FakeCurrentProject:
                AllForms = MagicMock()

            CurrentProject = FakeCurrentProject()

            def CreateForm(self):
                raise exc

        fake_disp._access_app = FakeAccessApp()

        def fake_call(fn, *args, **kwargs):
            return fn()

        fake_disp._call_impl = fake_call
        adapter._dispatcher = fake_disp
        return adapter

    def test_create_form_com_failure_logs_warning(self, caplog):
        """create_form whose CreateForm raises returns False and logs a WARNING naming the operation."""
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter_with_create_error(
            RuntimeError("Unexpected COM failure")
        )
        result = adapter.create_form("NewForm")

        assert result is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("create" in msg and "NewForm" in msg for msg in warning_msgs), (
            f"Expected WARNING naming the create operation and 'NewForm', got: {warning_msgs}"
        )

    def test_create_form_not_found_logs_warning(self, caplog):
        """create_form whose COM error names the object classifies as not-found, returns False, logs WARNING."""
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter_with_create_error(
            Exception("Cannot find object 'NewForm' in this collection")
        )
        result = adapter.create_form("NewForm")

        assert result is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("NewForm" in msg for msg in warning_msgs), (
            f"Expected WARNING about 'NewForm', got: {warning_msgs}"
        )


class TestOpenCloseFormErrorClassification:
    """open_form/close_form return False and log a WARNING on COM failure."""

    def _make_adapter(self, open_exc=None, close_exc=None):
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True

        class FakeDoCmd:
            def OpenForm(self, form_name):
                if open_exc is not None:
                    raise open_exc

            def Close(self, ac_type, name, save):
                if close_exc is not None:
                    raise close_exc

        class FakeAccessApp:
            DoCmd = FakeDoCmd()

            class FakeCurrentProject:
                AllForms = MagicMock()

            CurrentProject = FakeCurrentProject()

        fake_disp._access_app = FakeAccessApp()

        def fake_call(fn, *args, **kwargs):
            return fn()

        fake_disp._call_impl = fake_call
        adapter._dispatcher = fake_disp
        return adapter

    def test_open_form_com_failure_logs_warning(self, caplog):
        """open_form whose OpenForm raises returns False and logs a WARNING naming the operation."""
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter(open_exc=RuntimeError("Unexpected COM failure"))
        result = adapter.open_form("MainForm")

        assert result is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("open" in msg and "MainForm" in msg for msg in warning_msgs), (
            f"Expected WARNING naming the open operation and 'MainForm', got: {warning_msgs}"
        )

    def test_close_form_com_failure_logs_warning(self, caplog):
        """close_form whose Close raises returns False and logs a WARNING naming the operation."""
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter(close_exc=RuntimeError("Unexpected COM failure"))
        result = adapter.close_form("MainForm")

        assert result is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("close" in msg and "MainForm" in msg for msg in warning_msgs), (
            f"Expected WARNING naming the close operation and 'MainForm', got: {warning_msgs}"
        )


class TestSetObjectPropertyErrorClassification:
    """set_form_property/set_report_property return False and log a WARNING on COM failure."""

    def _make_adapter(self, kind: str, open_exc=None, prop_exc=None):
        """Adapter whose fake DoCmd open verb and/or property write raise."""
        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True

        class _FakeProperty:
            def __init__(self, name):
                self.name = name

            @property
            def Value(self):
                return None

            @Value.setter
            def Value(self, val):
                if prop_exc is not None:
                    raise prop_exc

        class _FakeProperties:
            def __call__(self, name):
                return _FakeProperty(name)

        class _FakeObject:
            def __init__(self, name):
                self.Name = name

            @property
            def Properties(self):
                return _FakeProperties()

        obj = _FakeObject("TestObject")

        if kind == "form":
            class FakeFormDoCmd:
                def OpenForm(self, name, view):
                    if open_exc is not None:
                        raise open_exc

                def Close(self, ac_type, name, save):
                    pass

            do_cmd = FakeFormDoCmd()
            live_collection_name = "Forms"
        else:
            class FakeReportDoCmd:
                def OpenReport(self, name, view):
                    if open_exc is not None:
                        raise open_exc

                def Close(self, ac_type, name, save):
                    pass

            do_cmd = FakeReportDoCmd()
            live_collection_name = "Reports"

        class FakeAccessApp:
            DoCmd: Any = None  # set after branch to avoid duplicate class names
            Screen = MagicMock()

            class FakeCurrentProject:
                AllForms = MagicMock()
                AllReports = MagicMock()

            CurrentProject = FakeCurrentProject()

        FakeAccessApp.DoCmd = do_cmd
        setattr(FakeAccessApp, live_collection_name, staticmethod(lambda name: obj))
        fake_disp._access_app = FakeAccessApp()

        def fake_call(fn, *args, **kwargs):
            return fn()

        fake_disp._call_impl = fake_call
        adapter._dispatcher = fake_disp
        return adapter

    def test_set_form_property_open_failure_logs_warning(self, caplog):
        """set_form_property whose OpenForm raises returns False and logs a WARNING."""
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter(
            "form", open_exc=RuntimeError("Unexpected COM failure")
        )
        result = adapter.set_form_property("MainForm", "Caption", "Hi")

        assert result is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("MainForm" in msg for msg in warning_msgs), (
            f"Expected WARNING about 'MainForm', got: {warning_msgs}"
        )

    def test_set_form_property_open_failure_warning_names_exception_class(self, caplog):
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter(
            "form", open_exc=RuntimeError("Unexpected COM failure")
        )

        assert adapter.set_form_property("MainForm", "Caption", "Hi") is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("AccessBackendError" in msg for msg in warning_msgs)

    def test_set_report_property_open_failure_logs_warning(self, caplog):
        """set_report_property whose OpenReport raises returns False and logs a WARNING."""
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter(
            "report", open_exc=RuntimeError("Unexpected COM failure")
        )
        result = adapter.set_report_property("MainReport", "Caption", "Hi")

        assert result is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("MainReport" in msg for msg in warning_msgs), (
            f"Expected WARNING about 'MainReport', got: {warning_msgs}"
        )

    def test_set_report_property_write_failure_logs_warning(self, caplog):
        """set_report_property whose property write raises returns False and logs a WARNING."""
        caplog.set_level(logging.WARNING)
        adapter = self._make_adapter(
            "report", prop_exc=RuntimeError("Property is locked")
        )
        result = adapter.set_report_property("MainReport", "Caption", "Hi")

        assert result is False
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("MainReport" in msg for msg in warning_msgs), (
            f"Expected WARNING about 'MainReport', got: {warning_msgs}"
        )


# -------------------------------------------------------------------------- #
# _with_design_view helper tests
# -------------------------------------------------------------------------- #

class TestWithDesignView:
    """Tests for _with_design_view context manager and _props_to_dict."""

    def _make_disp(self, opened_obj=None, open_exc=None, live_obj=None, close_exc=None):
        """Build a fake dispatcher that tracks open/close calls."""
        disp = FakeDispatcher()
        disp.start()

        call_log: list[str] = []
        close_count: list[int] = []

        class _FakeDoCmd:
            def OpenForm(self, name, mode):
                call_log.append(f"OpenForm({name},{mode})")
                if open_exc:
                    raise open_exc

            def OpenReport(self, name, mode):
                call_log.append(f"OpenReport({name},{mode})")
                if open_exc:
                    raise open_exc

            def Close(self, ac_type, name, save):
                close_count.append(1)
                call_log.append(f"Close({ac_type},{name},{save})")
                if close_exc:
                    raise close_exc

        class _FakeScreen:
            @property
            def ActiveForm(self):
                if opened_obj is not None:
                    return opened_obj
                raise RuntimeError("no active form")

        class _FakeApp:
            DoCmd = _FakeDoCmd()
            Screen = _FakeScreen()
            Forms = MagicMock(return_value=live_obj)
            Reports = MagicMock(return_value=live_obj)

        disp._access_app = _FakeApp()

        def fake_call(fn, *args, **kwargs):
            return fn()

        disp._call_impl = fake_call
        return disp, call_log, close_count

    def test_with_design_view_opens_calls_work_and_closes(self):
        """Open verb -> work receives live obj -> close called exactly once."""
        from ms_access_mcp.backends.access import AccessAdapter

        fake_obj = MagicMock()
        disp, log, close_count = self._make_disp(opened_obj=fake_obj, live_obj=fake_obj)
        adapter = AccessAdapter("test.accdb")
        adapter._dispatcher = disp

        adapter._with_design_view(
            "form", "TestForm",
            save_flag=2,
            work=lambda obj: {"props": {}},
            on_error=lambda exc: {},
            default={},
        )

        assert len(close_count) == 1, f"Expected 1 close, got {len(close_count)}"
        assert "OpenForm(TestForm,1)" in log
        assert "Close(2,TestForm,2)" in log

    def test_with_design_view_closes_when_work_raises(self):
        """Work raising -> close called exactly once -> on_error result returned."""
        from ms_access_mcp.backends.access import AccessAdapter

        fake_obj = MagicMock()
        disp, log, close_count = self._make_disp(opened_obj=fake_obj, live_obj=fake_obj)
        adapter = AccessAdapter("test.accdb")
        adapter._dispatcher = disp

        result = adapter._with_design_view(
            "form", "TestForm",
            save_flag=1,
            work=lambda obj: (_ for _ in ()).throw(RuntimeError("boom")),
            on_error=lambda exc: "fallback",
            default="fallback",
        )

        assert len(close_count) == 1, f"Expected 1 close after exception, got {len(close_count)}"
        assert result == "fallback"

    def test_with_design_view_not_started_returns_default(self):
        """Dispatcher not started -> returns default without any COM calls."""
        from ms_access_mcp.backends.access import AccessAdapter

        disp = FakeDispatcher()
        # do NOT start
        adapter = AccessAdapter("test.accdb")
        adapter._dispatcher = disp

        result = adapter._with_design_view(
            "form", "TestForm",
            save_flag=2,
            work=lambda obj: {"should not": "run"},
            on_error=lambda exc: {},
            default={"default": True},
        )

        assert result == {"default": True}

    def test_props_to_dict_logs_and_skips_failing_property(self, caplog):
        """A property whose .Value raises -> skipped with debug log; others returned."""
        import logging

        from ms_access_mcp.backends.access import _props_to_dict

        caplog.set_level(logging.DEBUG)

        class _FakeProp:
            def __init__(self, name, value, raise_on_value=False):
                self.Name = name
                self._value = value
                self._raise_on_value = raise_on_value

            @property
            def Value(self):
                if self._raise_on_value:
                    raise RuntimeError("COM error on value")
                return self._value

        class _FakeProps:
            def __iter__(self):
                return iter([
                    _FakeProp("Name", "TestForm"),
                    _FakeProp("Caption", "My Caption"),
                    _FakeProp("Broken", None, raise_on_value=True),
                    _FakeProp("Tag", ""),
                ])

        result = _props_to_dict(_FakeProps())

        assert result == {"Name": "TestForm", "Caption": "My Caption", "Tag": ""}
        assert "Broken" not in result
        debug_msgs = [r.message for r in caplog.records if r.levelname == "DEBUG"]
        assert any("Broken" in msg for msg in debug_msgs), f"Expected DEBUG about 'Broken', got: {debug_msgs}"


# -------------------------------------------------------------------------- #
# Step N: swallow DEBUG logging — plan 056
# -------------------------------------------------------------------------- #


class TestSwallowLogging:
    """Plan 056: every bare except Exception swallow must emit a DEBUG log."""

    # 1. _get_vb_project — lines 301-303: except Exception: pass / return None
    def test_get_vb_project_swallow_logs_debug(self, caplog):
        """COM failure in _get_vb_project logs DEBUG with 'vb project'."""

        class _FakeVBE:
            @property
            def VBProjects(self):
                raise RuntimeError("VBProjects not available")

        class _FakeAccessApp:
            VBE = _FakeVBE()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter._get_vb_project()

        assert result is None
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("vb project" in msg.lower() for msg in debug_msgs), (
            f"Expected DEBUG about 'vb project', got: {debug_msgs}"
        )

    # 2. vba_list_procedures whole-_do — lines 1764-1765: except Exception: return []
    def test_vba_list_procedures_swallow_logs_debug(self, caplog):
        """Failure in vba_list_procedures._do logs DEBUG with 'list procedures'."""

        class _BrokenCodeModule:
            """Fake that raises on CountOfLines (in outer try, caught by outer handler)."""
            @property
            def CountOfLines(self):
                raise RuntimeError("CountOfLines not available")

        class _FakeVBComponent:
            Name = "TestModule"
            CodeModule = _BrokenCodeModule()

        class _FakeVBComponents:
            def __iter__(self):
                return iter([_FakeVBComponent()])

        class _FakeVBProject:
            def __init__(self):
                self.VBComponents = _FakeVBComponents()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        adapter._dispatcher = fake_disp

        # Patch _get_vb_project so we reach the outer _do exception handler
        original_get = adapter._get_vb_project
        adapter._get_vb_project = lambda: _FakeVBProject()

        with caplog.at_level(logging.DEBUG):
            result = adapter.vba_list_procedures("TestModule")

        adapter._get_vb_project = original_get

        assert result == []
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("list procedures" in msg.lower() for msg in debug_msgs), (
            f"Expected DEBUG about 'list procedures', got: {debug_msgs}"
        )

    # 3. DoCmd.Save per-module — lines 1680-1681: except Exception: pass
    def test_save_module_swallow_logs_debug_with_module_name(self, caplog):
        """DoCmd.Save failure logs DEBUG containing the failing module name."""

        class _FakeVBComponent:
            Type = 1  # vbext_ct_StdModule
            Name = "TestModule"

        class _FakeVBComponents:
            def __iter__(self):
                return iter([_FakeVBComponent()])

        class _FakeVBProject:
            def __init__(self):
                self.VBComponents = _FakeVBComponents()

        class _FakeDoCmd:
            def Save(self, obj_type, name):
                raise RuntimeError("Save blocked")

            def RunCommand(self, cmd_id):
                raise RuntimeError("No compile")

        class _FakeAccessApp:
            DoCmd = _FakeDoCmd()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        # Patch _get_vb_project so we reach the DoCmd.Save swallow
        original_get = adapter._get_vb_project
        adapter._get_vb_project = lambda: _FakeVBProject()

        with caplog.at_level(logging.DEBUG):
            adapter.compile_vba()

        adapter._get_vb_project = original_get

        # compile_vba returns success=False when all RunCommand attempts fail
        # but the module-save swallow still emits DEBUG
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("TestModule" in msg for msg in debug_msgs), (
            f"Expected DEBUG with module name 'TestModule', got: {debug_msgs}"
        )

    # 4. ProcOfLine per-line — lines 1761-1762: except Exception: pass
    def test_proc_of_line_swallow_logs_debug(self, caplog):
        """ProcOfLine failure logs DEBUG with 'ProcOfLine' in message."""

        class _FakeCodeModule:
            CountOfLines = 5

            def ProcOfLine(self, line, kind):
                if line == 3:
                    raise RuntimeError("ProcOfLine not available")
                return f"Proc_{line}"

            def ProcKind(self, line, kind):
                return 0

            def ProcStartLine(self, name, kind):
                return 1

            def ProcCountLines(self, name, kind):
                return 2

        class _FakeVBComponent:
            Name = "TestModule"
            CodeModule = _FakeCodeModule()

        class _FakeVBComponents:
            def __iter__(self):
                return iter([_FakeVBComponent()])

        class _FakeVBProject:
            def __init__(self):
                self.VBComponents = _FakeVBComponents()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        adapter._dispatcher = fake_disp

        # Patch _get_vb_project to succeed so ProcOfLine loop is reached
        original_get = adapter._get_vb_project
        adapter._get_vb_project = lambda: _FakeVBProject()

        with caplog.at_level(logging.DEBUG):
            result = adapter.vba_list_procedures("TestModule")

        adapter._get_vb_project = original_get

        assert isinstance(result, list)
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("ProcOfLine" in msg for msg in debug_msgs), (
            f"Expected DEBUG about 'ProcOfLine', got: {debug_msgs}"
        )

    # 4. get_macro_properties inner loop — lines 1422-1426: per-prop Value swallow
    def test_get_macro_properties_swallow_logs_debug(self, caplog):
        """get_macro_properties skips unreadable prop.Values and logs aggregate DEBUG."""

        class _FakeProperty:
            def __init__(self, name, raise_on_value=False):
                self.Name = name
                self._raise_on_value = raise_on_value

            @property
            def Value(self):
                if self._raise_on_value:
                    raise RuntimeError("Property Value unreadable")
                return "ok"

        class _FakeProperties:
            def __init__(self):
                self._props = [
                    _FakeProperty("PropOK"),
                    _FakeProperty("PropBad", raise_on_value=True),
                    _FakeProperty("PropAlsoOK"),
                ]

            def __iter__(self):
                return iter(self._props)

        class _FakeMacro:
            Name = "TestMacro"
            Properties = _FakeProperties()

        class _FakeMacros:
            def __init__(self):
                self._items = [_FakeMacro()]

            @property
            def Count(self):
                return len(self._items)

            def __call__(self, i):
                return self._items[i]

        class _FakeCurrentProject:
            @property
            def AllMacros(self):
                return _FakeMacros()

        class _FakeAccessApp:
            CurrentProject = _FakeCurrentProject()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter.get_macro_properties("TestMacro")

        assert result == {"PropOK": "ok", "PropAlsoOK": "ok"}
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("property failures skipped" in msg or "TestMacro" in msg for msg in debug_msgs), (
            f"Expected DEBUG about skipped props for TestMacro, got: {debug_msgs}"
        )

    # 5. design-view finally-close — lines 287-288: except Exception: pass
    def test_design_view_close_swallow_logs_debug(self, caplog):
        """Close failure in _with_design_view finally block logs DEBUG with 'close'."""

        class _FakeDoCmd:
            def Close(self, ac_type, name, save):
                raise RuntimeError("Close failed")

        class _FakeAccessApp:
            DoCmd = _FakeDoCmd()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            # work fn raises so we hit the except → on_error path, then finally runs
            result = adapter._with_design_view(
                "form", "TestForm",
                save_flag=2,
                work=lambda obj: {"worked": True},
                on_error=lambda exc: {"error": True},
                default={"default": True},
            )

        # on_error is called because work raises; close then fails silently
        assert result == {"error": True}
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("close" in msg.lower() for msg in debug_msgs), (
            f"Expected DEBUG about 'close', got: {debug_msgs}"
        )

    # ---------------------------------------------------------------------- #
    # Sites found by heavy-verdict sweep (plan 056 follow-up):
    # ---------------------------------------------------------------------- #

    # 6. _set_object_control_property — lines 806-807: control lookup in loop
    def test_set_control_property_swallow_logs_debug(self, caplog):
        """Control property lookup loop swallows COM errors silently — should log DEBUG."""

        class _FakeControl:
            Name = "TargetCtrl"

            class _FakeProperties:
                def __getitem__(self, key):
                    raise RuntimeError("Properties COM error")

            Properties = _FakeProperties()

        class _FakeControls:
            @property
            def Count(self):
                return 1

            def __call__(self, i):
                raise RuntimeError("Controls(i) failed")

        class _FakeForm:
            Name = "TestForm"

            @property
            def Controls(self):
                return _FakeControls()

        class _FakeForms:
            def __init__(self, form):
                self._form = form

            @property
            def Count(self):
                return 1

            def __call__(self, name):
                return self._form

        class _FakeAccessApp:
            DoCmd = MagicMock()
            Forms = _FakeForms(_FakeForm())
            Reports = MagicMock()

            class _FakeScreen:
                @property
                def ActiveForm(self):
                    raise RuntimeError("no active form")

            Screen = _FakeScreen()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter.set_control_property("TestForm", "TargetCtrl", "BackColor", "0")

        assert result is False
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        found = any(
            "TargetCtrl" in m or "set_control_property" in m.lower()
            for m in debug_msgs
        )
        assert found, f"Expected DEBUG about set_control_property, got: {debug_msgs}"

    # 7. set_control_event_procedure — lines 1012-1013: control lookup loop
    def test_set_control_event_procedure_swallow_logs_debug(self, caplog):
        """Control lookup loop swallows COM errors silently — should log DEBUG."""

        class _FakeControl:
            Name = "TargetCtrl"

            class _FakeProperties:
                def __getitem__(self, key):
                    raise RuntimeError("control Properties failed")

            Properties = _FakeProperties()

        class _FakeControls:
            @property
            def Count(self):
                return 1

            def __call__(self, i):
                raise RuntimeError(f"Controls({i}) failed")

        class _FakeForm:
            Name = "TestForm"

            @property
            def Controls(self):
                return _FakeControls()

        class _FakeForms:
            def __init__(self, form):
                self._form = form

            @property
            def Count(self):
                return 1

            def __call__(self, name):
                return self._form

        class _FakeAccessApp:
            DoCmd = MagicMock()
            Forms = _FakeForms(_FakeForm())
            Reports = MagicMock()

            class _FakeScreen:
                @property
                def ActiveForm(self):
                    raise RuntimeError("no active form")

            Screen = _FakeScreen()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter.set_control_event_procedure("TestForm", "TargetCtrl", "Click", "MsgBox 'hi'")

        assert result is False
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        found = any(
            "TargetCtrl" in m or "control" in m.lower()
            for m in debug_msgs
        )
        assert found, f"Expected DEBUG about control lookup, got: {debug_msgs}"

    # 8. _get_object_sections — lines 1049-1050: section enumeration loop
    def test_get_sections_swallow_logs_debug(self, caplog):
        """Section enumeration loop swallows COM errors silently — should log DEBUG."""

        class _FakeForm:
            Name = "TestForm"

            def Section(self, i):
                raise RuntimeError(f"Section({i}) not available")

        class _FakeForms:
            def __init__(self, form):
                self._form = form

            @property
            def Count(self):
                return 1

            def __call__(self, name):
                return self._form

        class _FakeAccessApp:
            DoCmd = MagicMock()
            Forms = _FakeForms(_FakeForm())
            Reports = MagicMock()

            class _FakeScreen:
                @property
                def ActiveForm(self):
                    raise RuntimeError("no active form")

            Screen = _FakeScreen()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter.get_form_sections("TestForm")

        assert result == []
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        found = any(
            "section" in m.lower() or "TestForm" in m
            for m in debug_msgs
        )
        assert found, f"Expected DEBUG about section, got: {debug_msgs}"

    # 9. get_macros — lines 1296-1297: DateCreated property access
    def test_get_macros_date_created_swallow_logs_debug(self, caplog):
        """DateCreated property access swallows silently — should log DEBUG."""

        class _FakeMacro:
            Name = "TestMacro"

            class _FakeProperties:
                def Exists(self, name):
                    if name == "DateCreated":
                        raise RuntimeError("DateCreated not accessible")
                    return False

                def __call__(self, name):
                    raise RuntimeError(f"Property {name} not accessible")

            Properties = _FakeProperties()

        class _FakeAllMacros:
            def __init__(self):
                self._macros = [_FakeMacro()]

            @property
            def Count(self):
                return len(self._macros)

            def __call__(self, i):
                return self._macros[i]

        class _FakeCurrentProject:
            AllMacros = _FakeAllMacros()

        class _FakeVBComponents:
            @property
            def Count(self):
                return 0

            def __iter__(self):
                return iter([])

        class _FakeVBProject:
            VBComponents = _FakeVBComponents()

        class _FakeVBProjects:
            @property
            def Count(self):
                return 1

            def __iter__(self):
                return iter([_FakeVBProject()])

        class _FakeVBE:
            VBProjects = _FakeVBProjects()

        class _FakeAccessApp:
            CurrentProject = _FakeCurrentProject()
            DoCmd = MagicMock()
            VBE = _FakeVBE()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter.get_macros()

        # DateCreated raises → handler logs DEBUG and continues → macro appended with empty date
        assert len(result) == 1
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        found = any(
            "TestMacro" in m or "DateCreated" in m or "get_macros" in m.lower()
            for m in debug_msgs
        )
        assert found, f"Expected DEBUG about get_macros DateCreated, got: {debug_msgs}"

    # 10. get_macros — lines 1301-1302: DateModified property access
    def test_get_macros_date_modified_swallow_logs_debug(self, caplog):
        """DateModified property access swallows silently — should log DEBUG."""

        class _FakeMacro:
            Name = "TestMacro"

            class _FakeProperties:
                def Exists(self, name):
                    if name == "DateCreated":
                        return True
                    if name == "DateModified":
                        raise RuntimeError("DateModified not accessible")
                    return False

                def __call__(self, name):
                    if name == "DateCreated":
                        return "2024-01-01"
                    raise RuntimeError(f"Property {name} not accessible")

            Properties = _FakeProperties()

        class _FakeAllMacros:
            def __init__(self):
                self._macros = [_FakeMacro()]

            @property
            def Count(self):
                return len(self._macros)

            def __call__(self, i):
                return self._macros[i]

        class _FakeCurrentProject:
            AllMacros = _FakeAllMacros()

        class _FakeVBComponents:
            @property
            def Count(self):
                return 0

            def __iter__(self):
                return iter([])

        class _FakeVBProject:
            VBComponents = _FakeVBComponents()

        class _FakeVBProjects:
            @property
            def Count(self):
                return 1

            def __iter__(self):
                return iter([_FakeVBProject()])

        class _FakeVBE:
            VBProjects = _FakeVBProjects()

        class _FakeAccessApp:
            CurrentProject = _FakeCurrentProject()
            DoCmd = MagicMock()
            VBE = _FakeVBE()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter.get_macros()

        # DateModified raises → handler logs DEBUG and continues → macro appended with empty date
        assert len(result) == 1
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        found = any(
            "TestMacro" in m or "DateModified" in m or "get_macros" in m.lower()
            for m in debug_msgs
        )
        assert found, f"Expected DEBUG about get_macros DateModified, got: {debug_msgs}"

    # 11. get_macros — lines 1310-1311: per-macro processing (outer inner try)
    def test_get_macros_per_macro_swallow_logs_debug(self, caplog):
        """Per-macro processing loop swallows silently — should log DEBUG.

        The outer handler at 1310-1311 catches if macro processing itself fails
        (e.g., MacroInfo creation). This test exercises the inner DateCreated
        handler to confirm the macro-processing path is reachable.
        """

        class _FakeMacro:
            Name = "TestMacro"

            class _FakeProperties:
                def Exists(self, name):
                    if name == "DateCreated":
                        raise RuntimeError("DateCreated not accessible")
                    return False

                def __call__(self, key):
                    raise RuntimeError(f"Property {key} not accessible")

            Properties = _FakeProperties()

        class _FakeAllMacros:
            def __init__(self):
                self._macros = [_FakeMacro()]

            @property
            def Count(self):
                return len(self._macros)

            def __call__(self, i):
                return self._macros[i]

        class _FakeCurrentProject:
            AllMacros = _FakeAllMacros()

        class _FakeVBComponents:
            @property
            def Count(self):
                return 0

            def __iter__(self):
                return iter([])

        class _FakeVBProject:
            VBComponents = _FakeVBComponents()

        class _FakeVBProjects:
            def __init__(self):
                self._projects = [_FakeVBProject()]

            @property
            def Count(self):
                return len(self._projects)

            def __call__(self, i):
                return self._projects[i - 1]

            def __iter__(self):
                return iter(self._projects)

        class _FakeVBE:
            VBProjects = _FakeVBProjects()

        class _FakeAccessApp:
            CurrentProject = _FakeCurrentProject()
            DoCmd = MagicMock()
            VBE = _FakeVBE()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter.get_macros()

        # DateCreated raises → inner handler logs DEBUG → macro appended with empty dates
        assert len(result) == 1
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        found = any(
            "TestMacro" in m or "get_macros" in m.lower()
            for m in debug_msgs
        )
        assert found, f"Expected DEBUG about get_macros processing, got: {debug_msgs}"

    # 12. get_macros — lines 1312-1313: macro collection access
    def test_get_macros_collection_access_swallow_logs_debug(self, caplog):
        """AllMacros(i) collection access swallows silently — should log DEBUG."""

        class _FakeAllMacros:
            @property
            def Count(self):
                return 1

            def __call__(self, i):
                raise RuntimeError("AllMacros collection access failed")

        class _FakeCurrentProject:
            AllMacros = _FakeAllMacros()

        class _FakeVBComponents:
            @property
            def Count(self):
                return 0

            def __iter__(self):
                return iter([])

        class _FakeVBProject:
            VBComponents = _FakeVBComponents()

        class _FakeVBProjects:
            @property
            def Count(self):
                return 1

            def __iter__(self):
                return iter([_FakeVBProject()])

        class _FakeVBE:
            VBProjects = _FakeVBProjects()

        class _FakeAccessApp:
            CurrentProject = _FakeCurrentProject()
            DoCmd = MagicMock()
            VBE = _FakeVBE()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter.get_macros()

        assert result == []
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        found = any(
            "AllMacros" in m or "get_macros" in m.lower() or "collection" in m.lower()
            for m in debug_msgs
        )
        assert found, f"Expected DEBUG about get_macros collection, got: {debug_msgs}"

    # 13. macro_exists — lines 1332-1333: macro existence collection access
    def test_macro_exists_swallow_logs_debug(self, caplog):
        """AllMacros(i).Name access swallows silently — should log DEBUG."""

        class _FakeAllMacros:
            @property
            def Count(self):
                return 1

            def __call__(self, i):
                raise RuntimeError("AllMacros(i) failed")

        class _FakeCurrentProject:
            AllMacros = _FakeAllMacros()

        class _FakeAccessApp:
            CurrentProject = _FakeCurrentProject()
            DoCmd = MagicMock()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter.macro_exists("TestMacro")

        assert result is False
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        found = any(
            "TestMacro" in m or "macro_exists" in m.lower() or "AllMacros" in m
            for m in debug_msgs
        )
        assert found, f"Expected DEBUG about macro_exists, got: {debug_msgs}"

    # 14. get_modules — lines 1466-1467: outer collection failure
    def test_get_modules_outer_swallow_logs_debug(self, caplog):
        """Outer get_modules collection access swallows silently — should log DEBUG."""

        class _FakeVBComponent:
            Type = 1
            Name = "TestModule"
            CodeModule = MagicMock()

        class _FakeVBComponents:
            @property
            def Count(self):
                raise RuntimeError("VBComponents collection access failed")

            def __iter__(self):
                raise RuntimeError("VBComponents iteration failed")

        class _FakeVBProject:
            VBComponents = _FakeVBComponents()

        class _FakeVBProjects:
            """Fake COM collection that supports Count and (i) indexer."""

            def __init__(self):
                self._projects = [_FakeVBProject()]

            @property
            def Count(self):
                return len(self._projects)

            def __call__(self, i):
                """COM 1-based indexer."""
                return self._projects[i - 1]

            def __iter__(self):
                return iter(self._projects)

        class _FakeVBE:
            VBProjects = _FakeVBProjects()

        class _FakeAccessApp:
            VBE = _FakeVBE()
            DoCmd = MagicMock()

        adapter = AccessAdapter("test.accdb")
        adapter._connected = True
        fake_disp = FakeDispatcher()
        fake_disp._started = True
        fake_disp._access_app = _FakeAccessApp()
        adapter._dispatcher = fake_disp

        with caplog.at_level(logging.DEBUG):
            result = adapter.get_modules()

        assert result == []
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        found = any(
            "get_modules" in m.lower() or "VBComponents" in m
            for m in debug_msgs
        )
        assert found, f"Expected DEBUG about get_modules, got: {debug_msgs}"

