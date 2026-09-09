"""Tests for versioning dataclasses in models.py."""

from __future__ import annotations

from ms_access_mcp.models import (
    CompareResult,
    ExportAllResult,
    ImportAllResult,
    ObjectKind,
    ObjectTextRef,
    SchemaDdlResult,
)


class TestObjectKindKindMap:
    def test_returns_five_entries(self):
        m = ObjectKind.KIND_MAP()
        assert len(m) == 5
        assert set(m.keys()) == {"form", "report", "module", "macro", "query"}

    def test_form_code_is_2(self):
        assert ObjectKind.KIND_MAP()["form"].save_as_text_code == 2

    def test_report_code_is_4(self):
        assert ObjectKind.KIND_MAP()["report"].save_as_text_code == 4

    def test_macro_code_is_8(self):
        assert ObjectKind.KIND_MAP()["macro"].save_as_text_code == 8

    def test_query_code_is_5(self):
        assert ObjectKind.KIND_MAP()["query"].save_as_text_code == 5

    def test_module_code_is_none(self):
        assert ObjectKind.KIND_MAP()["module"].save_as_text_code is None


class TestObjectTextRef:
    def test_construction(self):
        ref = ObjectTextRef(kind="form", name="frmTest")
        assert ref.kind == "form"
        assert ref.name == "frmTest"


class TestExportAllResult:
    def test_construction_success(self):
        result = ExportAllResult(
            success=True,
            exported={"forms": ["frmA"]},
            output_dir="/tmp/out",
            file_count=1,
        )
        assert result.success is True
        assert result.file_count == 1
        assert result.error is None

    def test_construction_with_error(self):
        result = ExportAllResult(
            success=False,
            exported={},
            output_dir="/tmp/out",
            file_count=0,
            error="Not connected",
        )
        assert result.success is False
        assert result.error == "Not connected"


class TestImportAllResult:
    def test_construction_success(self):
        result = ImportAllResult(
            success=True,
            imported={"forms": ["frmA"]},
        )
        assert result.success is True
        assert result.errors is None

    def test_construction_with_errors(self):
        result = ImportAllResult(
            success=False,
            imported={},
            errors=["frmBad: parse error"],
        )
        assert result.success is False
        assert len(result.errors) == 1


class TestCompareResult:
    def test_construction(self):
        new_ref = ObjectTextRef(kind="form", name="frmNew")
        result = CompareResult(
            new=[new_ref],
            missing=[],
            changed=[],
            unchanged=[],
        )
        assert len(result.new) == 1
        assert result.new[0].name == "frmNew"


class TestSchemaDdlResult:
    def test_construction_success(self):
        result = SchemaDdlResult(
            success=True,
            ddl_tables="/tmp/ddl_tables.sql",
            ddl_relationships="/tmp/ddl_relationships.sql",
            tables_exported=3,
            relationships_exported=1,
        )
        assert result.success is True
        assert result.tables_exported == 3

    def test_construction_error(self):
        result = SchemaDdlResult(
            success=False,
            error="Not connected",
        )
        assert result.success is False
        assert result.error == "Not connected"
