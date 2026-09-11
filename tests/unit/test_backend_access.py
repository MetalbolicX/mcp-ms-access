"""Tests for access.py — AccessAdapter with dispatcher-fake for TDD."""

import logging
import threading
from typing import Any
from unittest.mock import MagicMock

from ms_access_mcp.backends.access import AccessAdapter


class FakeDispatcher:
    """Fake dispatcher that invokes _do functions directly for unit testing.

    Simulates the STA-threaded COM call pattern without requiring win32com.
    """

    def __init__(self):
        self._started = False
        self._access_app: Any = None
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
        self._started = False


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


# -------------------------------------------------------------------------- #
# Helper method tests — don't need COM
# -------------------------------------------------------------------------- #

class TestControlTypeMapping:
    """Tests for _control_type_name and _control_type_id helpers."""

    def test_control_type_name_textbox(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._control_type_name(100) == "TextBox"

    def test_control_type_name_command_button(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._control_type_name(102) == "CommandButton"

    def test_control_type_name_unknown(self):
        adapter = AccessAdapter("test.accdb")
        assert "Control(999)" in adapter._control_type_name(999)

    def test_control_type_id_textbox(self):
        adapter = AccessAdapter("test.accdb")
        assert adapter._control_type_id("TextBox") == 100

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

    def test_macro_template_defined(self):
        from ms_access_mcp.backends.access import _MACRO_TEMPLATE
        assert "TemplateMacro" in _MACRO_TEMPLATE
        assert "<?xml" in _MACRO_TEMPLATE


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
            @property
            def VBE(self):
                return VBEWithVBProjects(vbe_proj)

            @property
            def CurrentProject(self):
                return FakeCurrentProject(all_modules_coll)

        fake_app = FakeAccessAppWithVBE()
        fake_disp._access_app = fake_app

        def fake_call(fn, *args, **kwargs):
            return fn()

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
            def CreateForm(self):
                return form_obj

            def Close(self, ac_type, name, save):
                pass

        class FakeAccessApp:
            DoCmd = FakeDoCmd()

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
                def CreateForm(self):
                    raise exc

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
