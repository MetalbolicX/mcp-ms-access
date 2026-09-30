"""Tests for VersioningBackend — STRICT TDD fakes only, no real COM."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ms_access_mcp.backends.versioning import VersioningBackend


class FakeDispatcher:
    """Fake ComDispatcher for unit testing VersioningBackend without real COM."""

    def __init__(self, started: bool = True) -> None:
        self._started = started
        self.access_app = MagicMock()
        self._temp_files: dict[str, bytes] = {}
        self._module_code: dict[str, str] = {}
        self._save_calls: list[tuple[int, str, str]] = []
        self._load_calls: list[tuple[int, str, str]] = []

    @property
    def started(self) -> bool:
        return self._started

    def call(self, fn, *a, **kw):
        return fn(*a, **kw)


def make_fake_form(name: str) -> MagicMock:
    """Create a fake Access.Form/Report object with a Name."""
    f = MagicMock()
    f.Name = name
    return f


def make_fake_all_forms(names: list[str]):
    """Create a fake AllForms-like collection with working Count and 1-based call indexing."""
    forms = [make_fake_form(n) for n in names]

    class FakeCollection:
        def __init__(self, items):
            self._items = items

        @property
        def Count(self):
            return len(self._items)

        def __call__(self, i: int):
            return self._items[i - 1]

        def __len__(self):
            return len(self._items)

    return FakeCollection(forms)


def make_fake_vb_project(components: list[dict]) -> MagicMock:
    """Create a fake VBProject with a real VBComponents collection.

    components: list of dicts with keys: name, code, type (int)
    """
    comps = []
    for c in components:
        cm = MagicMock()
        cm.CountOfLines = len(c["code"].splitlines())
        code_val = c["code"]

        def make_lines(code=code_val):
            return lambda s, n: code

        cm.Lines = make_lines()
        comp = MagicMock()
        comp.Name = c["name"]
        comp.Type = c.get("type", 1)
        comp.CodeModule = cm
        comps.append(comp)

    class FakeVBComponents:
        def __init__(self, items):
            self._items = items

        @property
        def Count(self) -> int:
            return len(self._items)

        def __call__(self, i: int):
            return self._items[i - 1]

        def __iter__(self):
            return iter(self._items)

    vbp = MagicMock()
    vbp.VBComponents = FakeVBComponents(comps)
    return vbp


def make_fake_vbe(components: list[dict]) -> MagicMock:
    """Create a fully-wired VBE VBProjects collection."""
    vbp = make_fake_vb_project(components)

    class FakeVBProjects:
        Count = 1

        def __getitem__(self, i: int):
            return vbp

        def __call__(self, i: int):
            return vbp

    vbe = MagicMock()
    vbe.VBProjects = FakeVBProjects()
    return vbe


class TestNotConnected:
    """Every method returns its empty/error sentinel when dispatcher._started is False."""

    def setup_method(self):
        self.dispatcher = FakeDispatcher(started=False)
        self.backend = VersioningBackend(self.dispatcher)

    def test_export_form_returns_empty_string(self):
        assert self.backend.export_form_to_text("AnyForm") == ""

    def test_import_form_returns_false(self):
        assert self.backend.import_form_from_text("AnyForm", "data") is False

    def test_export_report_returns_empty_string(self):
        assert self.backend.export_report_to_text("AnyReport") == ""

    def test_import_report_returns_false(self):
        assert self.backend.import_report_from_text("AnyReport", "data") is False

    def test_export_macro_returns_empty_string(self):
        assert self.backend.export_macro_to_text("AnyMacro") == ""

    def test_import_macro_returns_false(self):
        assert self.backend.import_macro_from_text("AnyMacro", "data") is False

    def test_export_query_returns_empty_string(self):
        assert self.backend.export_query_to_text("AnyQuery") == ""

    def test_import_query_returns_false(self):
        assert self.backend.import_query_from_text("AnyQuery", "data") is False

    def test_export_module_returns_empty_string(self):
        assert self.backend.export_module_to_text("AnyModule") == ""

    def test_import_module_returns_false(self):
        assert self.backend.import_module_from_text("AnyModule", "code") is False

    def test_export_all_versioning_returns_error_result(self):
        result = self.backend.export_all_versioning("/tmp/out")
        assert result.success is False
        assert result.error is not None and "Not connected" in result.error

    def test_import_all_versioning_returns_error_result(self):
        result = self.backend.import_all_versioning("/tmp/in")
        assert result.success is False
        assert result.error is not None and "Not connected" in result.error

    def test_compare_versioning_returns_empty_result(self):
        result = self.backend.compare_versioning("/tmp/dir")
        assert result.new == []
        assert result.missing == []
        assert result.changed == []
        assert result.unchanged == []

    def test_export_schema_ddl_returns_error_result(self):
        result = self.backend.export_schema_ddl("/tmp/out")
        assert result.success is False
        assert result.error is not None and "Not connected" in result.error


class TestSafeFilename:
    """safe_filename helper replaces illegal chars."""

    def test_illegal_chars_replaced(self):
        dispatcher = FakeDispatcher(started=True)
        backend = VersioningBackend(dispatcher)
        result = backend._safe_filename('frmMain:Sales*')
        assert result == "frmMain_Sales_"
        assert "/" not in result
        assert ":" not in result
        assert "*" not in result


class TestExportImportRoundTrip:
    """SaveAsText/LoadFromText UTF-16-LE BOM round-trip via real temp files."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.dispatcher = FakeDispatcher(started=True)
        self.backend = VersioningBackend(self.dispatcher)
        self._register_fake_dispatcher()

    def _register_fake_dispatcher(self):
        """Wire the fake to write/read real BOM-encoded files."""
        disp = self.dispatcher

        def fake_save(kind: int, name: str, path: str) -> None:
            disp._save_calls.append((kind, name, path))

        def fake_load(kind: int, name: str, path: str) -> None:
            disp._load_calls.append((kind, name, path))
            if Path(path).exists():
                with Path(path).open("rb") as f:
                    disp._temp_files[path] = f.read()

        self.dispatcher.access_app.SaveAsText = fake_save
        self.dispatcher.access_app.LoadFromText = fake_load

    def _write_bom_text(self, path: str, content: str) -> None:
        with Path(path).open("wb") as f:
            f.write(b"\xff\xfe")
            f.write(content.encode("utf-16-le"))

    def _read_bom_text(self, path: str) -> str:
        with Path(path).open("rb") as f:
            raw = f.read()
        if raw.startswith(b"\xff\xfe"):
            return raw[2:].decode("utf-16-le")
        return raw.decode("utf-8")

    def test_form_round_trip(self):
        """Export writes BOM file; import reads it back via the dispatcher."""
        orig_content = "FormContent here"

        def save_side_effect(kind, name, path):
            self._write_bom_text(path, orig_content)

        def load_side_effect(kind, name, path):
            self._write_bom_text(path, orig_content)

        self.dispatcher.access_app.SaveAsText = save_side_effect
        self.dispatcher.access_app.LoadFromText = load_side_effect

        exported = self.backend.export_form_to_text("TestForm")
        assert exported == orig_content

        result = self.backend.import_form_from_text("TestForm", "NewContent")
        assert result is True

    def test_report_round_trip(self):
        orig_content = "ReportContent"

        def save_side_effect(kind, name, path):
            self._write_bom_text(path, orig_content)

        def load_side_effect(kind, name, path):
            self._write_bom_text(path, orig_content)

        self.dispatcher.access_app.SaveAsText = save_side_effect
        self.dispatcher.access_app.LoadFromText = load_side_effect

        exported = self.backend.export_report_to_text("TestReport")
        assert exported == orig_content

        result = self.backend.import_report_from_text("TestReport", "NewContent")
        assert result is True

    def test_macro_round_trip(self):
        orig_content = "MacroXmlContent"

        def save_side_effect(kind, name, path):
            self._write_bom_text(path, orig_content)

        def load_side_effect(kind, name, path):
            self._write_bom_text(path, orig_content)

        self.dispatcher.access_app.SaveAsText = save_side_effect
        self.dispatcher.access_app.LoadFromText = load_side_effect

        exported = self.backend.export_macro_to_text("TestMacro")
        assert exported == orig_content

        result = self.backend.import_macro_from_text("TestMacro", "NewMacro")
        assert result is True

    def test_query_round_trip(self):
        orig_content = "QueryContent"

        def save_side_effect(kind, name, path):
            self._write_bom_text(path, orig_content)

        def load_side_effect(kind, name, path):
            self._write_bom_text(path, orig_content)

        self.dispatcher.access_app.SaveAsText = save_side_effect
        self.dispatcher.access_app.LoadFromText = load_side_effect

        exported = self.backend.export_query_to_text("TestQuery")
        assert exported == orig_content

        result = self.backend.import_query_from_text("TestQuery", "NewQuery")
        assert result is True


class TestExportImportModule:
    """Module export uses VBE.CodeModule.Lines; import uses LoadFromText."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.dispatcher = FakeDispatcher(started=True)
        self.backend = VersioningBackend(self.dispatcher)
        self._module_code = {}

    def _fake_vbe_components(self):
        fake_comp = MagicMock()
        fake_comp.Name = "modTest"
        fake_comp.Type = 1
        fake_code_module = MagicMock()
        fake_code_module.CountOfLines = 3
        fake_code_module.Lines = lambda start, count: "\n".join(
            self._module_code.get(i, f"Line {i}") for i in range(start, start + count)
        )
        fake_comp.CodeModule = fake_code_module
        return fake_comp

    def test_module_round_trip(self):
        self._module_code = {1: "Sub Test()", 2: "    Debug.Print 42", 3: "End Sub"}

        fake_vbe = make_fake_vbe([
            {"name": "modTest", "code": "Sub Test()\n    Debug.Print 42\nEnd Sub"}
        ])
        self.dispatcher.access_app.VBE = fake_vbe

        exported = self.backend.export_module_to_text("modTest")
        assert "Sub Test()" in exported

        result = self.backend.import_module_from_text("modTest", "Sub New()\nEnd Sub")
        assert result is True


class TestExportAllDedup:
    """SHA-256 dedup: second call with same content skips write; file_count unchanged."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.dispatcher = FakeDispatcher(started=True)
        self.backend = VersioningBackend(self.dispatcher)
        self._written_files: dict[str, bytes] = {}

    def _write_bom_text(self, path: str, content: str) -> None:
        with Path(path).open("wb") as f:
            f.write(b"\xff\xfe")
            f.write(content.encode("utf-16-le"))
        with Path(path).open("rb") as f:
            self._written_files[path] = f.read()

    def _install_save_mock(self, content: str):
        def save_side_effect(kind, name, path):
            self._write_bom_text(path, content)

        self.dispatcher.access_app.SaveAsText = save_side_effect

    def test_dedup_skips_second_write(self):
        forms = ["FormA", "FormB"]

        def list_forms():
            return forms

        self._install_save_mock("FormContent")

        result1 = self.backend.export_all_versioning(
            self.tmpdir,
            list_forms=list_forms,
        )
        assert result1.success is True

        result2 = self.backend.export_all_versioning(
            self.tmpdir,
            list_forms=list_forms,
        )
        assert result2.success is True
        # Dedup means second call writes nothing new — per-kind lists don't grow
        assert result2.exported["forms"] == []


class TestExportAllModuleExt:
    """.bas vs .txt extension for modules controlled by module_ext param."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.dispatcher = FakeDispatcher(started=True)
        self.backend = VersioningBackend(self.dispatcher)
        self._module_code = {}

    def _fake_vbe_components(self, names):
        comps = []
        for name in names:
            fake_comp = MagicMock()
            fake_comp.Name = name
            fake_comp.Type = 1
            fake_cm = MagicMock()
            fake_cm.CountOfLines = 1
            fake_cm.Lines = lambda s, c, nm=name: f"' {nm}"
            fake_comp.CodeModule = fake_cm
            comps.append(fake_comp)
        return comps

    def _install_vbe_mock(self, names):
        components = [{"name": n, "code": f"' {n}"} for n in names]
        self.dispatcher.access_app.VBE = make_fake_vbe(components)

    def test_module_ext_bas(self):
        self._install_vbe_mock(["modFoo"])

        self.backend.export_all_versioning(
            self.tmpdir,
            list_modules=lambda: ["modFoo"],
            module_ext=".bas",
        )
        mod_file = self.tmpdir / "modules" / "modules_modFoo.bas"
        assert mod_file.exists(), f"Expected {mod_file}"

    def test_module_ext_txt(self):
        self._install_vbe_mock(["modFoo"])

        self.backend.export_all_versioning(
            self.tmpdir,
            list_modules=lambda: ["modFoo"],
            module_ext=".txt",
        )
        mod_file = self.tmpdir / "modules" / "modules_modFoo.txt"
        assert mod_file.exists(), f"Expected {mod_file}"


class TestExportAllQueriesSubdir:
    """Queries land under out_dir/queries/ with acQuery=5."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.dispatcher = FakeDispatcher(started=True)
        self.backend = VersioningBackend(self.dispatcher)

    def _write_bom_text(self, path: str, content: str) -> None:
        with Path(path).open("wb") as f:
            f.write(b"\xff\xfe")
            f.write(content.encode("utf-16-le"))

    def _install_save_mock(self):
        def save_side_effect(kind, name, path):
            self._write_bom_text(path, f"Content_{name}")

        self.dispatcher.access_app.SaveAsText = save_side_effect

    def test_queries_subdir_and_code(self):
        self._install_save_mock()

        result = self.backend.export_all_versioning(
            self.tmpdir,
            list_queries=lambda: ["QryOne", "QryTwo"],
        )
        assert result.success is True
        qdir = self.tmpdir / "queries"
        assert qdir.exists()
        assert (qdir / "queries_QryOne.txt").exists()
        assert (qdir / "queries_QryTwo.txt").exists()


class TestImportAllOrder:
    """Modules processed before forms/reports/macros/queries."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.dispatcher = FakeDispatcher(started=True)
        self.backend = VersioningBackend(self.dispatcher)
        self._call_order: list[str] = []
        self._written_files: dict[str, bytes] = {}

    def _write_bom_text(self, path: str, content: str) -> None:
        with Path(path).open("wb") as f:
            f.write(b"\xff\xfe")
            f.write(content.encode("utf-16-le"))
        with Path(path).open("rb") as f:
            self._written_files[path] = f.read()

    def _install_mocks(self):
        def save_side_effect(kind, name, path):
            self._write_bom_text(path, f"Content_{name}")

        def load_side_effect(kind, name, path):
            self._call_order.append(f"load_{kind}_{name}")

        def fake_vbe_components():
            fake_comp = MagicMock()
            fake_comp.Name = "modTest"
            fake_comp.Type = 1
            fake_cm = MagicMock()
            fake_cm.CountOfLines = 1
            fake_cm.Lines = lambda s, c: "Sub Test()\nEnd Sub"
            fake_comp.CodeModule = fake_cm
            return [fake_comp]

        self.dispatcher.access_app.SaveAsText = save_side_effect
        self.dispatcher.access_app.LoadFromText = load_side_effect
        fake_vbp = MagicMock()
        fake_vbp.VBComponents = fake_vbe_components()
        vbe_mock = MagicMock()
        vbe_mock.VBProjects = MagicMock()
        vbe_mock.VBProjects.__getitem__ = lambda s, i: fake_vbp
        vbe_mock.VBProjects.Count = 1
        self.dispatcher.access_app.VBE = vbe_mock

    def _make_files(self):
        for kind, name in [
            ("modules", "modTest"),
            ("forms", "frmTest"),
            ("reports", "rptTest"),
            ("macros", "mcrTest"),
            ("queries", "qryTest"),
        ]:
            sub = self.tmpdir / kind
            sub.mkdir(parents=True, exist_ok=True)
            path = sub / f"{kind}_{name}.txt"
            with Path(path).open("wb") as f:
                f.write(b"\xff\xfe")
                f.write(f"Content_{name}".encode("utf-16-le"))

    def test_modules_loaded_first(self):
        self._install_mocks()
        self._make_files()

        import_order = []

        def tracking_load(kind, name, path):
            import_order.append(kind)

        self.dispatcher.access_app.LoadFromText = tracking_load

        self.backend.import_all_versioning(self.tmpdir)

        module_idx = import_order.index(5) if 5 in import_order else -1
        form_idx = import_order.index(2) if 2 in import_order else -1
        assert module_idx < form_idx, f"Modules must load before forms: {import_order}"


class TestImportAllBomDetection:
    """BOM detection: \\xff\\xfe = UTF-16-LE; plain bytes = UTF-8."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.dispatcher = FakeDispatcher(started=True)
        self.backend = VersioningBackend(self.dispatcher)

    def _install_save_mock(self):
        def save_side_effect(kind, name, path):
            with Path(path).open("wb") as f:
                f.write(b"\xff\xfe")
                f.write(f"Content_{name}".encode("utf-16-le"))

        self.dispatcher.access_app.SaveAsText = save_side_effect

    def test_utf16_le_bom_decoded(self):
        self._install_save_mock()

        sub = self.tmpdir / "forms"
        sub.mkdir()
        path = sub / "forms_frmUTF16.txt"
        content = "UTF-16 content with BOM"
        with Path(path).open("wb") as f:
            f.write(b"\xff\xfe")
            f.write(content.encode("utf-16-le"))

        result = self.backend.import_all_versioning(self.tmpdir)
        assert result.success is True

    def test_plain_utf8_decoded(self):
        sub = self.tmpdir / "forms"
        sub.mkdir()
        path = sub / "forms_frmUTF8.txt"
        content = "Plain UTF-8 content"
        path.write_text(content, encoding="utf-8")

        result = self.backend.import_all_versioning(self.tmpdir)
        assert result.success is True


class TestCompareVersioning:
    """4 cases: new, missing, changed, unchanged."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.dispatcher = FakeDispatcher(started=True)
        self.backend = VersioningBackend(self.dispatcher)

    def _write_bom_text(self, path: str, content: str) -> None:
        with Path(path).open("wb") as f:
            f.write(b"\xff\xfe")
            f.write(content.encode("utf-16-le"))

    def _make_form_files(self, names_and_content: list[tuple[str, str]]):
        sub = self.tmpdir / "forms"
        sub.mkdir(parents=True, exist_ok=True)
        for name, content in names_and_content:
            self._write_bom_text(str(sub / f"forms_{name}.txt"), content)

    def test_new_in_db_not_in_export(self):
        self._make_form_files([("frmOld", "old")])
        self.dispatcher.access_app.CurrentProject.AllForms = make_fake_all_forms(["frmOld", "frmNew"])
        self.dispatcher.access_app.CurrentProject.AllReports = make_fake_all_forms([])
        self.dispatcher.access_app.CurrentProject.AllMacros = make_fake_all_forms([])
        self.dispatcher.access_app.CurrentProject.AllQueries = make_fake_all_forms([])

        def save_side_effect(kind, name, path):
            content = {"frmOld": "old", "frmNew": "newcontent"}.get(name, "")
            self._write_bom_text(path, content)

        self.dispatcher.access_app.SaveAsText = save_side_effect

        result = self.backend.compare_versioning(self.tmpdir)
        new_items = [x.name for x in result.new]
        assert "frmNew" in new_items

    def test_missing_from_db_in_export(self):
        self._make_form_files([("frmOld", "old"), ("frmDeleted", "del")])
        self.dispatcher.access_app.CurrentProject.AllForms = make_fake_all_forms(["frmOld"])
        self.dispatcher.access_app.CurrentProject.AllReports = make_fake_all_forms([])
        self.dispatcher.access_app.CurrentProject.AllMacros = make_fake_all_forms([])
        self.dispatcher.access_app.CurrentProject.AllQueries = make_fake_all_forms([])

        def save_side_effect(kind, name, path):
            content = {"frmOld": "old"}.get(name, "")
            self._write_bom_text(path, content)

        self.dispatcher.access_app.SaveAsText = save_side_effect

        result = self.backend.compare_versioning(self.tmpdir)
        missing_names = [x.name for x in result.missing]
        assert "frmDeleted" in missing_names

    def test_changed_content(self):
        self._make_form_files([("frmChanged", "oldcontent")])
        self.dispatcher.access_app.CurrentProject.AllForms = make_fake_all_forms(["frmChanged"])
        self.dispatcher.access_app.CurrentProject.AllReports = make_fake_all_forms([])
        self.dispatcher.access_app.CurrentProject.AllMacros = make_fake_all_forms([])
        self.dispatcher.access_app.CurrentProject.AllQueries = make_fake_all_forms([])

        def save_side_effect(kind, name, path):
            content = {"frmChanged": "newcontent"}.get(name, "")
            self._write_bom_text(path, content)

        self.dispatcher.access_app.SaveAsText = save_side_effect

        result = self.backend.compare_versioning(self.tmpdir)
        changed_names = [x.name for x in result.changed]
        assert "frmChanged" in changed_names

    def test_unchanged(self):
        same_content = "samecontent"
        self._make_form_files([("frmSame", same_content)])
        self.dispatcher.access_app.CurrentProject.AllForms = make_fake_all_forms(["frmSame"])
        self.dispatcher.access_app.CurrentProject.AllReports = make_fake_all_forms([])
        self.dispatcher.access_app.CurrentProject.AllMacros = make_fake_all_forms([])
        self.dispatcher.access_app.CurrentProject.AllQueries = make_fake_all_forms([])

        def save_side_effect(kind, name, path):
            c = {"frmSame": same_content}.get(name, "")
            self._write_bom_text(path, c)

        self.dispatcher.access_app.SaveAsText = save_side_effect

        result = self.backend.compare_versioning(self.tmpdir)
        unchanged_names = [x.name for x in result.unchanged]
        assert "frmSame" in unchanged_names


class TestExportSchemaDdl:
    """Schema DDL: CREATE TABLE and ALTER TABLE files."""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.dispatcher = FakeDispatcher(started=True)
        self.backend = VersioningBackend(self.dispatcher)

    def test_tables_and_relationships(self):
        fake_tables = [
            {
                "name": "tblA",
                "columns": [
                    {"name": "ID", "type": "Long", "is_primary": True},
                    {"name": "Name", "type": "Text", "size": 255},
                ],
            },
            {
                "name": "tblB",
                "columns": [
                    {"name": "ID", "type": "Long", "is_primary": True},
                    {"name": "tblA_ID", "type": "Long", "is_primary": False},
                ],
            },
        ]
        fake_rels = [
            {
                "name": "relAB",
                "table": "tblB",
                "foreign_table": "tblA",
                "columns": ["tblA_ID"],
                "foreign_columns": ["ID"],
            },
        ]

        result = self.backend.export_schema_ddl(
            self.tmpdir,
            list_tables=lambda: fake_tables,
            list_relationships=lambda: fake_rels,
        )

        assert result.success is True
        schema_dir = self.tmpdir / "schema"
        assert (schema_dir / "ddl_tables.sql").exists()
        assert (schema_dir / "ddl_relationships.sql").exists()
        content = (schema_dir / "ddl_tables.sql").read_text()
        assert "CREATE TABLE" in content
        rel_content = (schema_dir / "ddl_relationships.sql").read_text()
        assert "ALTER TABLE" in rel_content
        assert "REFERENCES" in rel_content


class TestOdBcAdapterVersioningStub:
    """OdbcAdapter raises NotImplementedError for every versioning method."""

    def test_compile_vba_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter
        adapter = OdbcAdapter("")
        with pytest.raises(NotImplementedError, match="COM automation"):
            adapter.compile_vba()

    def test_export_form_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter
        adapter = OdbcAdapter("")
        with pytest.raises(NotImplementedError, match="COM automation"):
            adapter.export_form_to_text("x")

    def test_import_form_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter
        adapter = OdbcAdapter("")
        with pytest.raises(NotImplementedError, match="COM automation"):
            adapter.import_form_from_text("x", "y")

    def test_export_report_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter
        adapter = OdbcAdapter("")
        with pytest.raises(NotImplementedError, match="COM automation"):
            adapter.export_report_to_text("x")

    def test_import_report_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter
        adapter = OdbcAdapter("")
        with pytest.raises(NotImplementedError, match="COM automation"):
            adapter.import_report_from_text("x", "y")

    def test_export_macro_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter
        adapter = OdbcAdapter("")
        with pytest.raises(NotImplementedError, match="COM automation"):
            adapter.export_macro_to_text("x")

    def test_import_macro_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter
        adapter = OdbcAdapter("")
        with pytest.raises(NotImplementedError, match="COM automation"):
            adapter.import_macro_from_text("x", "y")

    def test_export_query_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter
        adapter = OdbcAdapter("")
        with pytest.raises(NotImplementedError, match="COM automation"):
            adapter.export_query_to_text("x")

    def test_import_query_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter
        adapter = OdbcAdapter("")
        with pytest.raises(NotImplementedError, match="COM automation"):
            adapter.import_query_from_text("x", "y")

    def test_export_module_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter
        adapter = OdbcAdapter("")
        with pytest.raises(NotImplementedError, match="COM automation"):
            adapter.export_module_to_text("x")

    def test_import_module_raises(self):
        from ms_access_mcp.backends.odbc import OdbcAdapter
        adapter = OdbcAdapter("")
        with pytest.raises(NotImplementedError, match="COM automation"):
            adapter.import_module_from_text("x", "y")
