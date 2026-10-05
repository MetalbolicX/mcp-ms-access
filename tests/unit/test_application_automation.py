"""Tests for automation.py — AutomationUseCases with fakes for TDD."""

from dataclasses import dataclass, field
from typing import Any, Literal, cast

from ms_access_mcp.application.database import Err, Ok

# =============================================================================
# Fake adapter info classes (mirroring what AccessAdapter returns)
# =============================================================================


@dataclass
class FormInfo:
    name: str
    record_source: str = ""
    properties: dict = field(default_factory=dict)


@dataclass
class ReportInfo:
    name: str
    record_source: str = ""
    properties: dict = field(default_factory=dict)


@dataclass
class MacroInfo:
    name: str
    properties: dict = field(default_factory=dict)


@dataclass
class ModuleInfo:
    name: str
    code: str = ""
    properties: dict = field(default_factory=dict)


@dataclass
class ControlInfo:
    name: str
    control_type: str = ""
    properties: dict = field(default_factory=dict)


# =============================================================================
# Fake AccessAdapter
# =============================================================================


class FakeAccessAdapter:
    """Fake adapter for testing AutomationUseCases."""

    def __init__(self):
        self.connected = False
        self.calls: list[tuple[str, tuple, dict]] = []
        self._forms = [FormInfo(name="Form1", record_source="tbl1")]
        self._reports = [ReportInfo(name="Report1", record_source="tbl1")]
        self._macros = [MacroInfo(name="Macro1")]
        self._modules = [ModuleInfo(name="Module1", code="Sub Foo()\nEnd Sub")]
        self._form_controls = {"Form1": [ControlInfo(name="Label1", control_type="Label")]}
        self._report_controls = {"Report1": [ControlInfo(name="TextBox1", control_type="TextBox")]}
        self._form_sections = {"Form1": [0, 1, 2]}  # form header, detail, footer
        self._report_sections = {"Report1": [0, 1]}  # report header, detail
        self._vba_procedures = {"Module1": ["Foo", "Bar"]}

    def connect(self, db_path: str | None = None, /) -> bool:
        self.connected = True
        self.calls.append(("connect", (db_path,), {}))
        return True

    def disconnect(self) -> None:
        self.connected = False

    # Forms
    def get_forms(self):
        self.calls.append(("get_forms", (), {}))
        return self._forms

    def form_exists(self, name: str):
        self.calls.append(("form_exists", (name,), {}))
        return any(f.name == name for f in self._forms)

    def open_form(self, name: str):
        self.calls.append(("open_form", (name,), {}))
        return True

    def close_form(self, name: str):
        self.calls.append(("close_form", (name,), {}))
        return True

    def create_form(self, name: str, record_source: str = "", properties: dict | None = None) -> bool:
        self.calls.append(("create_form", (name, record_source, properties), {}))
        self._forms.append(FormInfo(name=name, record_source=record_source))
        return True

    def rename_form(self, old_name: str, new_name: str) -> bool:
        self.calls.append(("rename_form", (old_name, new_name), {}))
        for f in self._forms:
            if f.name == old_name:
                f.name = new_name
                return True
        return False

    def delete_form(self, name: str) -> bool:
        self.calls.append(("delete_form", (name,), {}))
        self._forms = [f for f in self._forms if f.name != name]
        return True

    def get_form_properties(self, name: str):
        self.calls.append(("get_form_properties", (name,), {}))
        for f in self._forms:
            if f.name == name:
                return {"name": f.name, "record_source": f.record_source}
        return {}

    def set_form_property(self, name: str, property_name: str, value: Any):
        self.calls.append(("set_form_property", (name, property_name, value), {}))
        return True

    def set_form_properties(self, name: str, properties: dict):
        self.calls.append(("set_form_properties", (name, properties), {}))
        return True

    def get_form_controls(self, name: str):
        self.calls.append(("get_form_controls", (name,), {}))
        return self._form_controls.get(name, [])

    def get_control_properties(self, form_name: str, control_name: str):
        self.calls.append(("get_control_properties", (form_name, control_name), {}))
        return {"name": control_name}

    def set_control_property(self, form_name: str, control_name: str, property_name: str, value: Any):
        self.calls.append(("set_control_property", (form_name, control_name, property_name, value), {}))
        return True

    def set_control_properties(self, form_name: str, control_name: str, properties: dict):
        self.calls.append(("set_control_properties", (form_name, control_name, properties), {}))
        return True

    def add_control(self, form_name: str, control_type: str, control_name: str, section: int = 0, properties: dict | None = None):
        self.calls.append(("add_control", (form_name, control_type, control_name, section, properties), {}))
        return True

    def remove_control(self, form_name: str, control_name: str):
        self.calls.append(("remove_control", (form_name, control_name), {}))
        return True

    def get_control_event_procedures(self, form_name: str, control_name: str = ""):
        self.calls.append(("get_control_event_procedures", (form_name, control_name), {}))
        return [{"control": control_name or "Form", "event": "Click", "procedure": "cmd_Click"}]

    def set_control_event_procedure(self, form_name: str, control_name: str, event_name: str, code: str):
        self.calls.append(("set_control_event_procedure", (form_name, control_name, event_name, code), {}))
        return True

    def get_form_sections(self, name: str):
        self.calls.append(("get_form_sections", (name,), {}))
        return self._form_sections.get(name, [])

    def get_form_section_properties(self, name: str, section_id: int):
        self.calls.append(("get_form_section_properties", (name, section_id), {}))
        return {"section_id": section_id}

    def set_form_section_property(self, name: str, section_id: int, property_name: str, value: Any):
        self.calls.append(("set_form_section_property", (name, section_id, property_name, value), {}))
        return True

    def set_form_section_properties(self, name: str, section_id: int, properties: dict):
        self.calls.append(("set_form_section_properties", (name, section_id, properties), {}))
        return True

    def export_form_to_text(self, name: str):
        self.calls.append(("export_form_to_text", (name,), {}))
        return f"Form: {name}\nProperties:..."

    def import_form_from_text(self, name: str, text: str):
        self.calls.append(("import_form_from_text", (name, text), {}))
        return True

    # Reports (same shape as forms)
    def get_reports(self):
        self.calls.append(("get_reports", (), {}))
        return self._reports

    def report_exists(self, name: str):
        self.calls.append(("report_exists", (name,), {}))
        return any(r.name == name for r in self._reports)

    def create_report(self, name: str, record_source: str = "", properties: dict | None = None):
        self.calls.append(("create_report", (name, record_source, properties), {}))
        self._reports.append(ReportInfo(name=name, record_source=record_source))
        return True

    def rename_report(self, old_name: str, new_name: str):
        self.calls.append(("rename_report", (old_name, new_name), {}))
        for r in self._reports:
            if r.name == old_name:
                r.name = new_name
                return True
        return False

    def delete_report(self, name: str):
        self.calls.append(("delete_report", (name,), {}))
        self._reports = [r for r in self._reports if r.name != name]
        return True

    def get_report_properties(self, name: str):
        self.calls.append(("get_report_properties", (name,), {}))
        for r in self._reports:
            if r.name == name:
                return {"name": r.name, "record_source": r.record_source}
        return {}

    def set_report_property(self, name: str, property_name: str, value: Any):
        self.calls.append(("set_report_property", (name, property_name, value), {}))
        return True

    def set_report_properties(self, name: str, properties: dict):
        self.calls.append(("set_report_properties", (name, properties), {}))
        return True

    def get_report_controls(self, name: str):
        self.calls.append(("get_report_controls", (name,), {}))
        return self._report_controls.get(name, [])

    def get_report_control_properties(self, name: str, control_name: str):
        self.calls.append(("get_report_control_properties", (name, control_name), {}))
        return {"name": control_name}

    def set_report_control_property(self, name: str, control_name: str, property_name: str, value: Any):
        self.calls.append(("set_report_control_property", (name, control_name, property_name, value), {}))
        return True

    def set_report_control_properties(self, name: str, control_name: str, properties: dict):
        self.calls.append(("set_report_control_properties", (name, control_name, properties), {}))
        return True

    def add_report_control(self, name: str, control_type: str, control_name: str, section: int = 0, properties: dict | None = None):
        self.calls.append(("add_report_control", (name, control_type, control_name, section, properties), {}))
        return True

    def remove_report_control(self, name: str, control_name: str):
        self.calls.append(("remove_report_control", (name, control_name), {}))
        return True

    def get_report_sections(self, name: str):
        self.calls.append(("get_report_sections", (name,), {}))
        return self._report_sections.get(name, [])

    def get_report_section_properties(self, name: str, section_id: int):
        self.calls.append(("get_report_section_properties", (name, section_id), {}))
        return {"section_id": section_id}

    def set_report_section_property(self, name: str, section_id: int, property_name: str, value: Any):
        self.calls.append(("set_report_section_property", (name, section_id, property_name, value), {}))
        return True

    def set_report_section_properties(self, name: str, section_id: int, properties: dict):
        self.calls.append(("set_report_section_properties", (name, section_id, properties), {}))
        return True

    def export_report_to_text(self, name: str):
        self.calls.append(("export_report_to_text", (name,), {}))
        return f"Report: {name}\nProperties:..."

    def import_report_from_text(self, name: str, text: str):
        self.calls.append(("import_report_from_text", (name, text), {}))
        return True

    # Macros
    def get_macros(self):
        self.calls.append(("get_macros", (), {}))
        return self._macros

    def macro_exists(self, name: str):
        self.calls.append(("macro_exists", (name,), {}))
        return any(m.name == name for m in self._macros)

    def create_macro(self, name: str):
        self.calls.append(("create_macro", (name,), {}))
        self._macros.append(MacroInfo(name=name))
        return True

    def rename_macro(self, old_name: str, new_name: str):
        self.calls.append(("rename_macro", (old_name, new_name), {}))
        for m in self._macros:
            if m.name == old_name:
                m.name = new_name
                return True
        return False

    def delete_macro(self, name: str):
        self.calls.append(("delete_macro", (name,), {}))
        self._macros = [m for m in self._macros if m.name != name]
        return True

    def run_macro(self, name: str):
        self.calls.append(("run_macro", (name,), {}))
        return True

    def get_macro_properties(self, name: str):
        self.calls.append(("get_macro_properties", (name,), {}))
        return {"name": name}

    # VBA
    def get_vba_project_name(self):
        self.calls.append(("get_vba_project_name", (), {}))
        return "VBAProject"

    def get_modules(self):
        self.calls.append(("get_modules", (), {}))
        return self._modules

    def get_vba_code(self, module_name: str):
        self.calls.append(("get_vba_code", (module_name,), {}))
        return "Sub Foo()\nEnd Sub"

    def set_vba_code(self, module_name: str, code: str):
        self.calls.append(("set_vba_code", (module_name, code), {}))
        return True

    def add_vba_procedure(self, module_name: str, procedure_name: str, code: str):
        self.calls.append(("add_vba_procedure", (module_name, procedure_name, code), {}))
        return True

    def delete_module(self, module_name: str):
        self.calls.append(("delete_module", (module_name,), {}))
        self._modules = [m for m in self._modules if m.name != module_name]
        return True

    def create_module(self, module_name: str, module_type: int = 1):
        self.calls.append(("create_module", (module_name, module_type), {}))
        self._modules.append(ModuleInfo(name=module_name))
        return True

    def rename_module(self, old_name: str, new_name: str):
        self.calls.append(("rename_module", (old_name, new_name), {}))
        for m in self._modules:
            if m.name == old_name:
                m.name = new_name
                return True
        return False

    def module_exists(self, module_name: str):
        self.calls.append(("module_exists", (module_name,), {}))
        return any(m.name == module_name for m in self._modules)

    def save_database(self):
        self.calls.append(("save_database", (), {}))
        return True

    def compile_vba(self):
        self.calls.append(("compile_vba", (), {}))
        return {"success": True, "compileErrors": []}

    def vba_list_procedures(self, module_name: str):
        self.calls.append(("vba_list_procedures", (module_name,), {}))
        return self._vba_procedures.get(module_name, [])

    def vba_get_procedure(self, module_name: str, procedure_name: str):
        self.calls.append(("vba_get_procedure", (module_name, procedure_name), {}))
        return {"module": module_name, "name": procedure_name, "code": "Sub Foo()\nEnd Sub"}

    def vba_replace_procedure(self, module_name: str, procedure_name: str, code: str):
        self.calls.append(("vba_replace_procedure", (module_name, procedure_name, code), {}))
        return True


# =============================================================================
# Tests
# =============================================================================


class TestAutomationUseCasesConstruction:
    """Test AutomationUseCases initialization."""

    def test_default_readonly(self):
        """Default is readonly mode."""
        from ms_access_mcp.application.automation import AutomationUseCases

        uc = AutomationUseCases()
        assert uc._policy.readonly is True

    def test_explicit_readonly_false(self):
        """readonly=False enables writes."""
        from ms_access_mcp.application.automation import AutomationUseCases

        uc = AutomationUseCases(readonly=False)
        assert uc._policy.readonly is False

    def test_constructor_injects_adapter(self):
        """Constructor accepts adapter injection."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        assert uc._adapter is fake

    def test_constructor_accepts_db_path(self):
        """Constructor accepts db_path for lazy initialization."""
        from ms_access_mcp.application.automation import AutomationUseCases

        uc = AutomationUseCases(db_path="test.accdb")
        assert uc._db_path == "test.accdb"


class TestEnsureAdapter:
    """Test _ensure_adapter behavior."""

    def test_ensure_adapter_uses_injected_adapter(self, monkeypatch):
        """Injected adapter is used without calling BackendSelector."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        uc._ensure_adapter()
        assert uc._adapter is fake
        assert uc._adapter is not None
        assert uc._adapter.connected is True

    def test_ensure_adapter_creates_on_demand(self, monkeypatch):
        """When adapter is None, creates via BackendSelector."""
        from ms_access_mcp.application.automation import AutomationUseCases

        monkeypatch.setattr(
            "ms_access_mcp.backends.BackendSelector.get_adapter",
            lambda *a, **kw: FakeAccessAdapter(),
        )
        uc = AutomationUseCases(db_path="test.accdb")
        uc._ensure_adapter()
        assert isinstance(uc._adapter, FakeAccessAdapter)

    def test_ensure_adapter_returns_err_on_backend_unavailable(self, monkeypatch):
        """BackendUnavailableError returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases
        from ms_access_mcp.errors import BackendUnavailableError

        def raise_unavailable(*a, **kw):
            raise BackendUnavailableError("COM not available")

        monkeypatch.setattr(
            "ms_access_mcp.backends.BackendSelector.get_adapter",
            raise_unavailable,
        )
        uc = AutomationUseCases(db_path="test.accdb")
        result = uc._ensure_adapter()
        assert isinstance(result, Err)
        assert "Backend unavailable" in result.message

    def test_ensure_adapter_returns_err_on_capability_mismatch(self, monkeypatch):
        """BackendCapabilityMismatchError returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases
        from ms_access_mcp.errors import BackendCapabilityMismatchError

        def raise_mismatch(*a, **kw):
            raise BackendCapabilityMismatchError("ODBC can't do VBA")

        monkeypatch.setattr(
            "ms_access_mcp.backends.BackendSelector.get_adapter",
            raise_mismatch,
        )
        uc = AutomationUseCases(db_path="test.accdb")
        result = uc._ensure_adapter()
        assert isinstance(result, Err)
        assert "Backend unavailable" in result.message


# =============================================================================
# Object-kind consolidated tests (forms and reports)
# =============================================================================


class TestListObjects:
    """Test list_objects for forms and reports."""

    def test_list_objects_form_returns_ok(self):
        """list_objects form returns Ok with forms."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.list_objects("form")
        assert isinstance(result, Ok)
        assert isinstance(result.data, list)
        assert len(result.data) == 1
        assert result.data[0]["name"] == "Form1"

    def test_list_objects_report_returns_ok(self):
        """list_objects report returns Ok with reports."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.list_objects("report")
        assert isinstance(result, Ok)
        assert isinstance(result.data, list)
        assert len(result.data) == 1
        assert result.data[0]["name"] == "Report1"

    def test_list_objects_invalid_kind_returns_err(self):
        """Invalid object_kind returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.list_objects(cast(Literal["form", "report"], "invalid"))
        assert isinstance(result, Err)
        assert "unknown object_kind" in result.message

    def test_list_objects_rowcount(self):
        """list_objects returns rowcount=len(data)."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.list_objects("form")
        assert isinstance(result, Ok)
        assert result.rowcount == len(result.data)


class TestObjectExists:
    """Test object_exists for forms and reports."""

    def test_object_exists_form_true(self):
        """form_exists returns True for existing form."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.object_exists("form", "Form1")
        assert isinstance(result, Ok)
        assert result.data is True

    def test_object_exists_form_false(self):
        """form_exists returns False for non-existing form."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.object_exists("form", "NonExistent")
        assert isinstance(result, Ok)
        assert result.data is False

    def test_object_exists_report_true(self):
        """report_exists returns True for existing report."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.object_exists("report", "Report1")
        assert isinstance(result, Ok)
        assert result.data is True

    def test_object_exists_invalid_kind(self):
        """Invalid object_kind returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.object_exists(cast(Literal["form", "report"], "invalid"), "Something")
        assert isinstance(result, Err)


class TestOpenObject:
    """Test open_object for forms and reports."""

    def test_open_form_ok(self):
        """open_form returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.open_object("form", "Form1")
        assert isinstance(result, Ok)

    def test_open_report_err_not_supported(self):
        """open_object report returns Err (not supported)."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.open_object("report", "Report1")
        assert isinstance(result, Err)
        assert "not supported" in result.message

    def test_close_form_ok(self):
        """close_form returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.close_object("form", "Form1")
        assert isinstance(result, Ok)

    def test_close_report_err_not_supported(self):
        """close_object report returns Err (not supported)."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.close_object("report", "Report1")
        assert isinstance(result, Err)
        assert "not supported" in result.message


class TestCreateObject:
    """Test create_object with confirm gate."""

    def test_create_object_without_confirm_err(self):
        """create_object without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.create_object("form", "NewForm", confirm=False)
        assert isinstance(result, Err)
        assert "confirmation" in result.message

    def test_create_object_with_confirm_and_readonly_err(self):
        """create_object with confirm but readonly=True returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=True)
        result = uc.create_object("form", "NewForm", confirm=True)
        assert isinstance(result, Err)
        assert "readonly" in result.message

    def test_create_object_with_confirm_and_not_readonly_ok(self):
        """create_object with confirm=True and readonly=False returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.create_object("form", "NewForm", confirm=True)
        assert isinstance(result, Ok)

    def test_create_object_with_record_source(self):
        """create_object passes record_source to adapter."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.create_object("form", "NewForm", record_source="tbl2", confirm=True)
        assert isinstance(result, Ok)
        assert ("create_form", ("NewForm", "tbl2", None), {}) in fake.calls


class TestRenameObject:
    """Test rename_object with confirm gate."""

    def test_rename_object_without_confirm_err(self):
        """rename_object without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.rename_object("form", "Form1", "Form2", confirm=False)
        assert isinstance(result, Err)
        assert "confirmation" in result.message

    def test_rename_object_with_confirm_and_readonly_err(self):
        """rename_object with confirm but readonly=True returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=True)
        result = uc.rename_object("form", "Form1", "Form2", confirm=True)
        assert isinstance(result, Err)
        assert "readonly" in result.message

    def test_rename_object_with_confirm_ok(self):
        """rename_object with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.rename_object("form", "Form1", "Form2", confirm=True)
        assert isinstance(result, Ok)


class TestDeleteObject:
    """Test delete_object with confirm gate."""

    def test_delete_object_without_confirm_err(self):
        """delete_object without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.delete_object("form", "Form1", confirm=False)
        assert isinstance(result, Err)
        assert "confirmation" in result.message

    def test_delete_object_with_confirm_and_readonly_err(self):
        """delete_object with confirm but readonly=True returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=True)
        result = uc.delete_object("form", "Form1", confirm=True)
        assert isinstance(result, Err)
        assert "readonly" in result.message

    def test_delete_object_with_confirm_ok(self):
        """delete_object with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.delete_object("form", "Form1", confirm=True)
        assert isinstance(result, Ok)


class TestGetProperties:
    """Test get_properties (read-only)."""

    def test_get_properties_form(self):
        """get_properties form returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_properties("form", "Form1")
        assert isinstance(result, Ok)
        assert result.data["name"] == "Form1"

    def test_get_properties_report(self):
        """get_properties report returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_properties("report", "Report1")
        assert isinstance(result, Ok)
        assert result.data["name"] == "Report1"


class TestSetProperty:
    """Test set_property with confirm gate."""

    def test_set_property_without_confirm_err(self):
        """set_property without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.set_property("form", "Form1", "Caption", "NewCaption", confirm=False)
        assert isinstance(result, Err)
        assert "confirmation" in result.message

    def test_set_property_with_confirm_and_readonly_err(self):
        """set_property with confirm but readonly=True returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=True)
        result = uc.set_property("form", "Form1", "Caption", "NewCaption", confirm=True)
        assert isinstance(result, Err)
        assert "readonly" in result.message

    def test_set_property_with_confirm_ok(self):
        """set_property with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.set_property("form", "Form1", "Caption", "NewCaption", confirm=True)
        assert isinstance(result, Ok)


class TestSetProperties:
    """Test set_properties with confirm gate."""

    def test_set_properties_without_confirm_err(self):
        """set_properties without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.set_properties("form", "Form1", {"Caption": "New"}, confirm=False)
        assert isinstance(result, Err)

    def test_set_properties_with_confirm_ok(self):
        """set_properties with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.set_properties("form", "Form1", {"Caption": "New"}, confirm=True)
        assert isinstance(result, Ok)


class TestGetControls:
    """Test get_controls (read-only)."""

    def test_get_controls_form(self):
        """get_controls form returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_controls("form", "Form1")
        assert isinstance(result, Ok)
        assert isinstance(result.data, list)


class TestGetControlProperties:
    """Test get_control_properties (read-only)."""

    def test_get_control_properties_form(self):
        """get_control_properties form returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_control_properties("form", "Form1", "Label1")
        assert isinstance(result, Ok)


class TestSetControlProperty:
    """Test set_control_property with confirm gate."""

    def test_set_control_property_without_confirm_err(self):
        """set_control_property without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.set_control_property("form", "Form1", "Label1", "Caption", "New", confirm=False)
        assert isinstance(result, Err)

    def test_set_control_property_with_confirm_ok(self):
        """set_control_property with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.set_control_property("form", "Form1", "Label1", "Caption", "New", confirm=True)
        assert isinstance(result, Ok)


class TestSetControlProperties:
    """Test set_control_properties with confirm gate."""

    def test_set_control_properties_without_confirm_err(self):
        """set_control_properties without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.set_control_properties("form", "Form1", "Label1", {"Caption": "New"}, confirm=False)
        assert isinstance(result, Err)

    def test_set_control_properties_with_confirm_ok(self):
        """set_control_properties with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.set_control_properties("form", "Form1", "Label1", {"Caption": "New"}, confirm=True)
        assert isinstance(result, Ok)


class TestAddControl:
    """Test add_control with confirm gate."""

    def test_add_control_without_confirm_err(self):
        """add_control without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.add_control("form", "Form1", "TextBox", "TextBox1", section=1, confirm=False)
        assert isinstance(result, Err)

    def test_add_control_with_confirm_ok(self):
        """add_control with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.add_control("form", "Form1", "TextBox", "TextBox1", section=1, confirm=True)
        assert isinstance(result, Ok)


class TestRemoveControl:
    """Test remove_control with confirm gate."""

    def test_remove_control_without_confirm_err(self):
        """remove_control without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.remove_control("form", "Form1", "Label1", confirm=False)
        assert isinstance(result, Err)

    def test_remove_control_with_confirm_ok(self):
        """remove_control with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.remove_control("form", "Form1", "Label1", confirm=True)
        assert isinstance(result, Ok)


class TestGetControlEventProcedures:
    """Test get_control_event_procedures (read-only)."""

    def test_get_control_event_procedures_form(self):
        """get_control_event_procedures form returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_control_event_procedures("form", "Form1", "Label1")
        assert isinstance(result, Ok)
        assert isinstance(result.data, list)


class TestSetControlEventProcedure:
    """Test set_control_event_procedure with confirm gate."""

    def test_set_control_event_procedure_without_confirm_err(self):
        """set_control_event_procedure without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.set_control_event_procedure("form", "Form1", "Label1", "Click", "Sub", confirm=False)
        assert isinstance(result, Err)

    def test_set_control_event_procedure_with_confirm_ok(self):
        """set_control_event_procedure with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.set_control_event_procedure("form", "Form1", "Label1", "Click", "Sub", confirm=True)
        assert isinstance(result, Ok)


class TestGetSections:
    """Test get_sections (read-only)."""

    def test_get_sections_form(self):
        """get_sections form returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_sections("form", "Form1")
        assert isinstance(result, Ok)
        assert result.data == [0, 1, 2]


class TestGetSectionProperties:
    """Test get_section_properties (read-only)."""

    def test_get_section_properties_form(self):
        """get_section_properties form returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_section_properties("form", "Form1", 0)
        assert isinstance(result, Ok)


class TestSetSectionProperty:
    """Test set_section_property with confirm gate."""

    def test_set_section_property_without_confirm_err(self):
        """set_section_property without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.set_section_property("form", "Form1", 0, "Visible", False, confirm=False)
        assert isinstance(result, Err)

    def test_set_section_property_with_confirm_ok(self):
        """set_section_property with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.set_section_property("form", "Form1", 0, "Visible", False, confirm=True)
        assert isinstance(result, Ok)


class TestSetSectionProperties:
    """Test set_section_properties with confirm gate."""

    def test_set_section_properties_without_confirm_err(self):
        """set_section_properties without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.set_section_properties("form", "Form1", 0, {"Visible": False}, confirm=False)
        assert isinstance(result, Err)

    def test_set_section_properties_with_confirm_ok(self):
        """set_section_properties with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.set_section_properties("form", "Form1", 0, {"Visible": False}, confirm=True)
        assert isinstance(result, Ok)


class TestExportToText:
    """Test export_to_text (read-only)."""

    def test_export_form_to_text(self):
        """export_to_text form returns Ok with text."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.export_to_text("form", "Form1")
        assert isinstance(result, Ok)
        assert "object_kind" in result.data
        assert "name" in result.data
        assert "text" in result.data
        assert result.data["object_kind"] == "form"
        assert result.data["name"] == "Form1"

    def test_export_report_to_text(self):
        """export_to_text report returns Ok with text."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.export_to_text("report", "Report1")
        assert isinstance(result, Ok)
        assert result.data["object_kind"] == "report"


class TestImportFromText:
    """Test import_from_text with confirm gate."""

    def test_import_from_text_without_confirm_err(self):
        """import_from_text without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.import_from_text("form", "NewForm", "Form: NewForm", confirm=False)
        assert isinstance(result, Err)

    def test_import_from_text_with_confirm_ok(self):
        """import_from_text with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.import_from_text("form", "NewForm", "Form: NewForm", confirm=True)
        assert isinstance(result, Ok)


# =============================================================================
# Macro tests
# =============================================================================


class TestMacros:
    """Test macro use cases."""

    def test_get_macros(self):
        """get_macros returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_macros()
        assert isinstance(result, Ok)
        assert len(result.data) == 1

    def test_macro_exists_true(self):
        """macro_exists returns True for existing macro."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.macro_exists("Macro1")
        assert isinstance(result, Ok)
        assert result.data is True

    def test_macro_exists_false(self):
        """macro_exists returns False for non-existing macro."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.macro_exists("NonExistent")
        assert isinstance(result, Ok)
        assert result.data is False

    def test_create_macro_without_confirm_err(self):
        """create_macro without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.create_macro("NewMacro", confirm=False)
        assert isinstance(result, Err)

    def test_create_macro_with_confirm_ok(self):
        """create_macro with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.create_macro("NewMacro", confirm=True)
        assert isinstance(result, Ok)

    def test_rename_macro_without_confirm_err(self):
        """rename_macro without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.rename_macro("Macro1", "Macro2", confirm=False)
        assert isinstance(result, Err)

    def test_rename_macro_with_confirm_ok(self):
        """rename_macro with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.rename_macro("Macro1", "Macro2", confirm=True)
        assert isinstance(result, Ok)

    def test_delete_macro_without_confirm_err(self):
        """delete_macro without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.delete_macro("Macro1", confirm=False)
        assert isinstance(result, Err)

    def test_delete_macro_with_confirm_ok(self):
        """delete_macro with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.delete_macro("Macro1", confirm=True)
        assert isinstance(result, Ok)

    def test_run_macro_without_confirm_err(self):
        """run_macro without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.run_macro("Macro1", confirm=False)
        assert isinstance(result, Err)

    def test_run_macro_with_confirm_and_readonly_err(self):
        """run_macro with confirm but readonly=True returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=True)
        result = uc.run_macro("Macro1", confirm=True)
        assert isinstance(result, Err)
        assert "readonly" in result.message

    def test_run_macro_with_confirm_and_not_readonly_ok(self):
        """run_macro with confirm=True and readonly=False returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.run_macro("Macro1", confirm=True)
        assert isinstance(result, Ok)

    def test_get_macro_properties(self):
        """get_macro_properties returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_macro_properties("Macro1")
        assert isinstance(result, Ok)


# =============================================================================
# VBA tests
# =============================================================================


class TestVBA:
    """Test VBA use cases."""

    def test_get_vba_project_name(self):
        """get_vba_project_name returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_vba_project_name()
        assert isinstance(result, Ok)
        assert result.data == "VBAProject"

    def test_get_modules(self):
        """get_modules returns Ok with dataclasses converted to dicts."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_modules()
        assert isinstance(result, Ok)
        assert isinstance(result.data, list)
        assert all(isinstance(item, dict) for item in result.data)
        assert result.data[0]["name"] == "Module1"

    def test_get_vba_code(self):
        """get_vba_code returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_vba_code("Module1")
        assert isinstance(result, Ok)
        assert "Sub Foo()" in result.data

    def test_set_vba_code_without_confirm_err(self):
        """set_vba_code without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.set_vba_code("Module1", "Sub Foo()\nEnd Sub", confirm=False)
        assert isinstance(result, Err)

    def test_set_vba_code_with_confirm_but_readonly_err(self):
        """set_vba_code with confirm but readonly=True returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=True)
        result = uc.set_vba_code("Module1", "Sub Foo()\nEnd Sub", confirm=True)
        assert isinstance(result, Err)
        assert "readonly" in result.message

    def test_set_vba_code_with_confirm_and_not_readonly_ok(self):
        """set_vba_code with confirm=True and readonly=False returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.set_vba_code("Module1", "Sub Foo()\nEnd Sub", confirm=True)
        assert isinstance(result, Ok)

    def test_add_vba_procedure_without_confirm_err(self):
        """add_vba_procedure without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.add_vba_procedure("Module1", "NewProc", "Sub NewProc()\nEnd Sub", confirm=False)
        assert isinstance(result, Err)

    def test_add_vba_procedure_with_confirm_ok(self):
        """add_vba_procedure with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.add_vba_procedure("Module1", "NewProc", "Sub NewProc()\nEnd Sub", confirm=True)
        assert isinstance(result, Ok)

    def test_delete_module_without_confirm_err(self):
        """delete_module without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.delete_module("Module1", confirm=False)
        assert isinstance(result, Err)

    def test_delete_module_with_confirm_ok(self):
        """delete_module with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.delete_module("Module1", confirm=True)
        assert isinstance(result, Ok)

    def test_create_module_without_confirm_err(self):
        """create_module without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.create_module("NewModule", confirm=False)
        assert isinstance(result, Err)

    def test_create_module_with_confirm_ok(self):
        """create_module with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.create_module("NewModule", confirm=True)
        assert isinstance(result, Ok)

    def test_rename_module_without_confirm_err(self):
        """rename_module without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.rename_module("Module1", "Module2", confirm=False)
        assert isinstance(result, Err)

    def test_rename_module_with_confirm_ok(self):
        """rename_module with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.rename_module("Module1", "Module2", confirm=True)
        assert isinstance(result, Ok)

    def test_module_exists_true(self):
        """module_exists returns True for existing module."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.module_exists("Module1")
        assert isinstance(result, Ok)
        assert result.data is True

    def test_module_exists_false(self):
        """module_exists returns False for non-existing module."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.module_exists("NonExistent")
        assert isinstance(result, Ok)
        assert result.data is False

    def test_save_database_without_confirm_err(self):
        """save_database without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.save_database(confirm=False)
        assert isinstance(result, Err)

    def test_save_database_with_confirm_ok(self):
        """save_database with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.save_database(confirm=True)
        assert isinstance(result, Ok)

    def test_compile_vba_success_no_errors(self):
        """compile_vba treats command acceptance as success; errors are optional."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.compile_vba()
        assert isinstance(result, Ok)
        assert result.data["compileErrors"] == []

    def test_compile_vba_omits_errors_when_backend_does_not_provide_them(self):
        """An accepted compile command must not imply an empty detected-error list."""
        from ms_access_mcp.application.automation import AutomationUseCases

        class FakeWithoutErrors(FakeAccessAdapter):
            def compile_vba(self):
                return {"success": True}

        result = AutomationUseCases(adapter=FakeWithoutErrors()).compile_vba()
        assert isinstance(result, Ok)
        assert "compileErrors" not in result.data

    def test_compile_vba_passes_backend_provided_compile_errors(self):
        """compileErrors pass through only when provided by the backend."""
        from ms_access_mcp.application.automation import AutomationUseCases

        class FakeWithErrors(FakeAccessAdapter):
            def compile_vba(self):
                return {"success": True, "compileErrors": [{"module": "Module1", "error": "Syntax error"}]}

        fake = FakeWithErrors()
        uc = AutomationUseCases(adapter=fake)
        result = uc.compile_vba()
        assert isinstance(result, Ok)
        assert len(result.data["compileErrors"]) == 1

    def test_compile_vba_failure(self):
        """compile_vba returns Err when success=False."""
        from ms_access_mcp.application.automation import AutomationUseCases

        class FakeWithFailure(FakeAccessAdapter):
            def compile_vba(self):
                return {"success": False, "error": "Compile failed"}

        fake = FakeWithFailure()
        uc = AutomationUseCases(adapter=fake)
        result = uc.compile_vba()
        assert isinstance(result, Err)
        assert "Compile failed" in result.message

    def test_vba_list_procedures(self):
        """vba_list_procedures returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.vba_list_procedures("Module1")
        assert isinstance(result, Ok)
        assert "Foo" in result.data

    def test_vba_get_procedure(self):
        """vba_get_procedure returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.vba_get_procedure("Module1", "Foo")
        assert isinstance(result, Ok)
        assert result.data["name"] == "Foo"

    def test_vba_replace_procedure_without_confirm_err(self):
        """vba_replace_procedure without confirm returns Err."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.vba_replace_procedure("Module1", "Foo", "Sub Foo()\nEnd Sub", confirm=False)
        assert isinstance(result, Err)

    def test_vba_replace_procedure_with_confirm_ok(self):
        """vba_replace_procedure with confirm=True returns Ok."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.vba_replace_procedure("Module1", "Foo", "Sub Foo()\nEnd Sub", confirm=True)
        assert isinstance(result, Ok)


# =============================================================================
# Dispatch tests (form vs report)
# =============================================================================


class TestDispatchFormVsReport:
    """Test that object_kind correctly dispatches to form or report methods."""

    def test_list_objects_form_calls_get_forms(self):
        """list_objects form calls get_forms."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        uc.list_objects("form")
        assert ("get_forms", (), {}) in fake.calls

    def test_list_objects_report_calls_get_reports(self):
        """list_objects report calls get_reports."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        uc.list_objects("report")
        assert ("get_reports", (), {}) in fake.calls

    def test_create_object_form_calls_create_form(self):
        """create_object form calls create_form."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        uc.create_object("form", "NewForm", confirm=True)
        assert any(c[0] == "create_form" for c in fake.calls)

    def test_create_object_report_calls_create_report(self):
        """create_object report calls create_report."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        uc.create_object("report", "NewReport", confirm=True)
        assert any(c[0] == "create_report" for c in fake.calls)

    def test_rename_object_form_calls_rename_form(self):
        """rename_object form calls rename_form."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        uc.rename_object("form", "Form1", "Form2", confirm=True)
        assert ("rename_form", ("Form1", "Form2"), {}) in fake.calls

    def test_rename_object_report_calls_rename_report(self):
        """rename_object report calls rename_report."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        uc.rename_object("report", "Report1", "Report2", confirm=True)
        assert ("rename_report", ("Report1", "Report2"), {}) in fake.calls

    def test_delete_object_form_calls_delete_form(self):
        """delete_object form calls delete_form."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        uc.delete_object("form", "Form1", confirm=True)
        assert ("delete_form", ("Form1",), {}) in fake.calls

    def test_delete_object_report_calls_delete_report(self):
        """delete_object report calls delete_report."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        uc.delete_object("report", "Report1", confirm=True)
        assert ("delete_report", ("Report1",), {}) in fake.calls

    def test_set_property_form_calls_set_form_property(self):
        """set_property form calls set_form_property."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        uc.set_property("form", "Form1", "Caption", "New", confirm=True)
        assert ("set_form_property", ("Form1", "Caption", "New"), {}) in fake.calls

    def test_set_property_report_calls_set_report_property(self):
        """set_property report calls set_report_property."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        uc.set_property("report", "Report1", "Caption", "New", confirm=True)
        assert ("set_report_property", ("Report1", "Caption", "New"), {}) in fake.calls

    def test_export_to_text_form_calls_export_form_to_text(self):
        """export_to_text form calls export_form_to_text."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        uc.export_to_text("form", "Form1")
        assert ("export_form_to_text", ("Form1",), {}) in fake.calls

    def test_export_to_text_report_calls_export_report_to_text(self):
        """export_to_text report calls export_report_to_text."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        uc.export_to_text("report", "Report1")
        assert ("export_report_to_text", ("Report1",), {}) in fake.calls

    def test_import_from_text_form_calls_import_form_from_text(self):
        """import_from_text form calls import_form_from_text."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        uc.import_from_text("form", "NewForm", "Form text", confirm=True)
        assert ("import_form_from_text", ("NewForm", "Form text"), {}) in fake.calls

    def test_import_from_text_report_calls_import_report_from_text(self):
        """import_from_text report calls import_report_from_text."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        uc.import_from_text("report", "NewReport", "Report text", confirm=True)
        assert ("import_report_from_text", ("NewReport", "Report text"), {}) in fake.calls


# =============================================================================
# Dataclass to dict conversion tests
# =============================================================================


class TestDataclassConversion:
    """Test that dataclasses from adapter are converted to dicts."""

    def test_form_info_converted_to_dict(self):
        """FormInfo is converted to dict via asdict."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.list_objects("form")
        assert isinstance(result, Ok)
        # Verify data is a dict, not a FormInfo instance
        assert isinstance(result.data[0], dict)
        assert result.data[0]["name"] == "Form1"
        assert result.data[0]["record_source"] == "tbl1"

    def test_report_info_converted_to_dict(self):
        """ReportInfo is converted to dict via asdict."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.list_objects("report")
        assert isinstance(result, Ok)
        assert isinstance(result.data[0], dict)
        assert result.data[0]["name"] == "Report1"

    def test_macro_info_converted_to_dict(self):
        """MacroInfo is converted to dict via asdict."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_macros()
        assert isinstance(result, Ok)
        assert isinstance(result.data[0], dict)
        assert result.data[0]["name"] == "Macro1"

    def test_module_info_converted_to_dict(self):
        """ModuleInfo is converted to dict via asdict."""
        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeAccessAdapter()
        uc = AutomationUseCases(adapter=fake)
        result = uc.get_modules()
        assert isinstance(result, Ok)
        assert isinstance(result.data[0], dict)
        assert result.data[0]["name"] == "Module1"
        assert result.data[0]["code"] == "Sub Foo()\nEnd Sub"


# =============================================================================
# Adapter method failures
# =============================================================================


class TestAdapterFailures:
    """Test handling of adapter method failures."""

    def test_create_object_returns_false_is_err(self):
        """create_object returning False returns Err."""

        class FakeFalseReturn(FakeAccessAdapter):
            def create_form(self, name, record_source="", properties=None):
                return False

        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeFalseReturn()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.create_object("form", "NewForm", confirm=True)
        assert isinstance(result, Err)
        assert "operation failed" in result.message

    def test_rename_object_returns_false_is_err(self):
        """rename_object returning False returns Err."""

        class FakeFalseReturn(FakeAccessAdapter):
            def rename_form(self, old_name, new_name):
                return False

        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeFalseReturn()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.rename_object("form", "NonExistent", "NewName", confirm=True)
        assert isinstance(result, Err)
        assert "operation failed" in result.message

    def test_delete_object_returns_false_is_err(self):
        """delete_object returning False returns Err."""

        class FakeFalseReturn(FakeAccessAdapter):
            def delete_form(self, name):
                return False

        from ms_access_mcp.application.automation import AutomationUseCases

        fake = FakeFalseReturn()
        uc = AutomationUseCases(adapter=fake, readonly=False)
        result = uc.delete_object("form", "NonExistent", confirm=True)
        assert isinstance(result, Err)
        assert "operation failed" in result.message
