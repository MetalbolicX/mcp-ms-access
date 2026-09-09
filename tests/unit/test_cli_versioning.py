"""Tests for versioning CLI subcommands — TDD."""

from __future__ import annotations

from argparse import Namespace

from ms_access_mcp.application.database import Ok


class FakeVersioningUseCases:
    """Fake VersioningUseCases for testing CLI in isolation."""

    def __init__(self, readonly: bool = True, db_path: str | None = None):
        self.readonly = readonly
        self.db_path = db_path

    def export_object(self, kind: str, name: str, out_dir: str):
        return Ok(data={"kind": kind, "name": name, "data": "text", "output_path": f"{out_dir}/{kind}s_{name}.txt"}, rowcount=1)

    def export_all(self, out_dir: str):
        return Ok(data={"output_dir": out_dir, "file_count": 5, "exported": {"forms": ["Form1"]}}, rowcount=1)

    def import_object(self, kind: str, name: str, file_path: str, confirm: bool):
        return Ok(data={"kind": kind, "name": name, "input_path": file_path}, rowcount=1)

    def import_all(self, input_dir: str, confirm: bool):
        return Ok(data={"imported": {"forms": ["Form1"]}, "errors": None, "input_dir": input_dir}, rowcount=1)

    def compare(self, export_dir: str):
        return Ok(data={"new": [], "missing": [], "changed": [], "unchanged": []}, rowcount=1)

    def export_schema_ddl(self, out_dir: str):
        return Ok(data={"ddl_tables": "", "ddl_relationships": "", "tables_exported": 0, "relationships_exported": 0}, rowcount=1)


class TestVersioningCli:
    """Test versioning CLI handlers."""

    def test_cmd_export_object_happy(self, tmp_path):
        from ms_access_mcp.cli import _cmd_versioning_export_object

        out_dir = str(tmp_path / "out")
        uc = FakeVersioningUseCases()
        args = Namespace(kind="form", name="Form1", out_dir=out_dir, json=False)
        rc = _cmd_versioning_export_object(args, uc)
        assert rc == 0

    def test_cmd_export_all_happy(self, tmp_path):
        from ms_access_mcp.cli import _cmd_versioning_export_all

        out_dir = str(tmp_path / "out")
        uc = FakeVersioningUseCases()
        args = Namespace(out_dir=out_dir, json=False)
        rc = _cmd_versioning_export_all(args, uc)
        assert rc == 0

    def test_cmd_import_object_requires_yes(self, tmp_path):
        from ms_access_mcp.cli import _cmd_versioning_import_object

        file_path = str(tmp_path / "form.txt")
        uc = FakeVersioningUseCases()
        args = Namespace(kind="form", name="Form1", file_path=file_path, confirm=False, yes=False, json=False)
        rc = _cmd_versioning_import_object(args, uc)
        assert rc == 2

    def test_cmd_import_object_happy(self, tmp_path):
        from ms_access_mcp.cli import _cmd_versioning_import_object

        file_path = str(tmp_path / "form.txt")
        uc = FakeVersioningUseCases()
        args = Namespace(kind="form", name="Form1", file_path=file_path, confirm=True, yes=True, json=False)
        rc = _cmd_versioning_import_object(args, uc)
        assert rc == 0

    def test_cmd_import_all_requires_yes(self, tmp_path):
        from ms_access_mcp.cli import _cmd_versioning_import_all

        input_dir = str(tmp_path / "in")
        uc = FakeVersioningUseCases()
        args = Namespace(input_dir=input_dir, confirm=False, yes=False, json=False)
        rc = _cmd_versioning_import_all(args, uc)
        assert rc == 2

    def test_cmd_import_all_happy(self, tmp_path):
        from ms_access_mcp.cli import _cmd_versioning_import_all

        input_dir = str(tmp_path / "in")
        uc = FakeVersioningUseCases()
        args = Namespace(input_dir=input_dir, confirm=True, yes=True, json=False)
        rc = _cmd_versioning_import_all(args, uc)
        assert rc == 0

    def test_cmd_compare_happy(self, tmp_path):
        from ms_access_mcp.cli import _cmd_versioning_compare

        export_dir = str(tmp_path / "export")
        uc = FakeVersioningUseCases()
        args = Namespace(export_dir=export_dir, json=False)
        rc = _cmd_versioning_compare(args, uc)
        assert rc == 0

    def test_cmd_export_schema_ddl_happy(self, tmp_path):
        from ms_access_mcp.cli import _cmd_versioning_export_schema_ddl

        out_dir = str(tmp_path / "out")
        uc = FakeVersioningUseCases()
        args = Namespace(out_dir=out_dir, json=False)
        rc = _cmd_versioning_export_schema_ddl(args, uc)
        assert rc == 0
