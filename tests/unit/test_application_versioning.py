"""Tests for application/versioning.py — VersioningUseCases with fakes for TDD."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ms_access_mcp.application.database import Err, Ok


@dataclass
class ObjectInfo:
    name: str
    code: str = ""


class FakeVersioningAdapter:
    """Fake adapter for testing VersioningUseCases."""

    def __init__(self, connected: bool = True):
        self.connected = connected
        self.calls: list[tuple[str, tuple, dict]] = []
        self._forms = [ObjectInfo(name="Form1", code="form code")]
        self._reports = [ObjectInfo(name="Report1", code="report code")]
        self._macros = [ObjectInfo(name="Macro1", code="macro code")]
        self._queries = [ObjectInfo(name="Query1", code="query code")]
        self._modules = [ObjectInfo(name="Module1", code="module code")]

    def connect(self, db_path: str) -> bool:
        self.connected = True
        self.calls.append(("connect", (db_path,), {}))
        return True

    def disconnect(self) -> None:
        self.connected = False

    def export_form_to_text(self, name: str) -> str:
        self.calls.append(("export_form_to_text", (name,), {}))
        obj = next((f for f in self._forms if f.name == name), None)
        return obj.code if obj else ""

    def import_form_from_text(self, name: str, text_data: str) -> bool:
        self.calls.append(("import_form_from_text", (name, text_data), {}))
        return True

    def export_report_to_text(self, name: str) -> str:
        self.calls.append(("export_report_to_text", (name,), {}))
        obj = next((r for r in self._reports if r.name == name), None)
        return obj.code if obj else ""

    def import_report_from_text(self, name: str, text_data: str) -> bool:
        self.calls.append(("import_report_from_text", (name, text_data), {}))
        return True

    def export_macro_to_text(self, name: str) -> str:
        self.calls.append(("export_macro_to_text", (name,), {}))
        obj = next((m for m in self._macros if m.name == name), None)
        return obj.code if obj else ""

    def import_macro_from_text(self, name: str, text_data: str) -> bool:
        self.calls.append(("import_macro_from_text", (name, text_data), {}))
        return True

    def export_query_to_text(self, name: str) -> str:
        self.calls.append(("export_query_to_text", (name,), {}))
        obj = next((q for q in self._queries if q.name == name), None)
        return obj.code if obj else ""

    def import_query_from_text(self, name: str, text_data: str) -> bool:
        self.calls.append(("import_query_from_text", (name, text_data), {}))
        return True

    def export_module_to_text(self, name: str) -> str:
        self.calls.append(("export_module_to_text", (name,), {}))
        obj = next((m for m in self._modules if m.name == name), None)
        return obj.code if obj else ""

    def import_module_from_text(self, name: str, code: str) -> bool:
        self.calls.append(("import_module_from_text", (name, code), {}))
        return True

    def export_all_versioning(self, out_dir: str) -> dict[str, Any]:
        self.calls.append(("export_all_versioning", (out_dir,), {}))
        return {
            "success": True,
            "exported": {
                "forms": ["Form1"],
                "reports": ["Report1"],
                "macros": ["Macro1"],
                "queries": ["Query1"],
                "modules": ["Module1"],
            },
            "output_dir": out_dir,
            "file_count": 5,
        }

    def import_all_versioning(self, input_dir: str) -> dict[str, Any]:
        self.calls.append(("import_all_versioning", (input_dir,), {}))
        return {
            "success": True,
            "imported": {
                "forms": ["Form1"],
                "reports": ["Report1"],
                "macros": ["Macro1"],
                "queries": ["Query1"],
                "modules": ["Module1"],
            },
            "errors": None,
        }

    def compare_versioning(self, export_dir: str) -> dict[str, Any]:
        self.calls.append(("compare_versioning", (export_dir,), {}))
        return {
            "new": [],
            "missing": [],
            "changed": [{"type": "form", "name": "Form1"}],
            "unchanged": [{"type": "report", "name": "Report1"}],
        }

    def export_schema_ddl(self, out_dir: str) -> dict[str, Any]:
        self.calls.append(("export_schema_ddl", (out_dir,), {}))
        return {
            "ddl_tables": "CREATE TABLE Foo (ID Integer);",
            "ddl_relationships": "ALTER TABLE Foo ADD CONSTRAINT;",
            "tables_exported": 1,
            "relationships_exported": 1,
        }

    def list_forms(self):
        return self._forms

    def list_reports(self):
        return self._reports

    def list_macros(self):
        return self._macros

    def list_queries(self):
        return self._queries

    def list_modules(self):
        return self._modules


class TestVersioningUseCases:
    """Tests for VersioningUseCases public methods."""

    def test_export_object_form_happy(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=True, db_path=None)
        out_dir = str(tmp_path / "out")
        result = uc.export_object("form", "Form1", out_dir)

        assert isinstance(result, Ok)
        assert result.data["kind"] == "form"
        assert result.data["name"] == "Form1"
        assert result.data["data"] == "form code"
        assert "output_path" in result.data

    def test_export_object_report_happy(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=True, db_path=None)
        out_dir = str(tmp_path / "out")
        result = uc.export_object("report", "Report1", out_dir)

        assert isinstance(result, Ok)
        assert result.data["kind"] == "report"
        assert result.data["name"] == "Report1"

    def test_export_object_module_happy(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=True, db_path=None)
        out_dir = str(tmp_path / "out")
        result = uc.export_object("module", "Module1", out_dir)

        assert isinstance(result, Ok)
        assert result.data["kind"] == "module"
        assert result.data["name"] == "Module1"

    def test_export_object_unknown_kind(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=True, db_path=None)
        out_dir = str(tmp_path / "out")
        result = uc.export_object("unknown", "Name", out_dir)

        assert isinstance(result, Err)

    def test_export_object_adapter_error(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        class FailingAdapter(FakeVersioningAdapter):
            def export_form_to_text(self, name: str) -> str:
                raise RuntimeError("adapter error")

        adapter = FailingAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=True, db_path=None)
        out_dir = str(tmp_path / "out")
        result = uc.export_object("form", "Form1", out_dir)

        assert isinstance(result, Err)
        assert "adapter error" in result.message

    def test_export_all_happy(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=True, db_path=None)
        out_dir = str(tmp_path / "out")
        result = uc.export_all(out_dir)

        assert isinstance(result, Ok)
        assert result.data["output_dir"] == out_dir
        assert result.data["file_count"] == 5
        assert "exported" in result.data

    def test_compare_happy(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=True, db_path=None)
        export_dir = str(tmp_path / "export")
        result = uc.compare(export_dir)

        assert isinstance(result, Ok)
        assert "new" in result.data
        assert "missing" in result.data
        assert "changed" in result.data
        assert "unchanged" in result.data
        assert len(result.data["changed"]) == 1
        assert result.data["changed"][0]["name"] == "Form1"

    def test_export_schema_ddl_happy(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=True, db_path=None)
        out_dir = str(tmp_path / "out")
        result = uc.export_schema_ddl(out_dir)

        assert isinstance(result, Ok)
        assert "ddl_tables" in result.data
        assert "ddl_relationships" in result.data
        assert result.data["tables_exported"] == 1

    def test_import_object_confirm_gate(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=False, db_path=None)
        file_path = str(tmp_path / "form.txt")
        result = uc.import_object("form", "Form1", file_path, confirm=False)

        assert isinstance(result, Err)
        assert "requires explicit confirmation" in result.message

    def test_import_object_happy(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=False, db_path=None)
        file_path = tmp_path / "form.txt"
        file_path.write_text("form code")
        result = uc.import_object("form", "Form1", str(file_path), confirm=True)

        assert isinstance(result, Ok)
        assert result.data["kind"] == "form"
        assert result.data["name"] == "Form1"
        assert result.data["input_path"] == str(file_path)

    def test_import_object_readonly_gate(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=True, db_path=None)
        file_path = str(tmp_path / "form.txt")
        result = uc.import_object("form", "Form1", file_path, confirm=True)

        assert isinstance(result, Err)
        assert "disabled" in result.message

    def test_import_all_confirm_gate(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=False, db_path=None)
        input_dir = str(tmp_path / "in")
        result = uc.import_all(input_dir, confirm=False)

        assert isinstance(result, Err)
        assert "requires explicit confirmation" in result.message

    def test_import_all_happy(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=False, db_path=None)
        input_dir = str(tmp_path / "in")
        result = uc.import_all(input_dir, confirm=True)

        assert isinstance(result, Ok)
        assert "imported" in result.data
        assert result.data["input_dir"] == input_dir

    def test_import_all_readonly_gate(self, tmp_path):
        from ms_access_mcp.application.versioning import VersioningUseCases

        adapter = FakeVersioningAdapter()
        uc = VersioningUseCases(adapter=adapter, readonly=True, db_path=None)
        input_dir = str(tmp_path / "in")
        result = uc.import_all(input_dir, confirm=True)

        assert isinstance(result, Err)
        assert "disabled" in result.message
