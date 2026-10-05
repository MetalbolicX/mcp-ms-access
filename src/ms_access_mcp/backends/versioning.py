"""Versioning backend — SaveAsText/LoadFromText wrapper via dispatcher."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from ..models import (
    CompareResult,
    ExportAllResult,
    ImportAllResult,
    ObjectKind,
    ObjectTextRef,
    SchemaDdlResult,
)
from .protocols import VersioningDispatcherProtocol

MODULE_EXT = ".bas"


class VersioningBackend:
    """SaveAsText/LoadFromText operations for forms, reports, macros, queries, and VBA modules.

    Every COM call goes through self._dispatcher.call(_do).
    Disconnected state (dispatcher not started) returns empty/error sentinels.
    """

    def __init__(self, dispatcher: VersioningDispatcherProtocol) -> None:
        """Initialize versioning operations with a dispatcher."""
        self._dispatcher = dispatcher

    # ------------------------------------------------------------------ #
    # Object-kind constants (Access AcObjectType)
    # ------------------------------------------------------------------ #

    _AC_FORM = 2
    _AC_REPORT = 4
    _AC_MACRO = 8
    _AC_QUERY = 5
    _AC_MODULE = 5  # used only for LoadFromText; modules export via VBE

    # ------------------------------------------------------------------ #
    # Not-connected guard
    # ------------------------------------------------------------------ #

    def _check_connected(self) -> bool:
        return self._dispatcher._started

    # ------------------------------------------------------------------ #
    # Internal SaveAsText / LoadFromText helpers
    # ------------------------------------------------------------------ #

    def _save_as_text(self, kind: int, name: str) -> str:
        """Export an Access object to text via SaveAsText.

        Writes UTF-16-LE BOM to a temp file, calls SaveAsText on the STA thread,
        reads the result back and strips the BOM.
        Returns empty string on failure.
        """
        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".txt", prefix="mcp_exp_")
            os.close(fd)

            def _do() -> None:
                self._dispatcher.access_app.SaveAsText(kind, name, temp_path)

            self._dispatcher.call(_do)

            with Path(temp_path).open("rb") as f:
                raw = f.read()
            return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
        except Exception:
            return ""
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink()
                except Exception:
                    pass

    def _load_from_text(self, kind: int, name: str, text_data: str) -> bool:
        """Import an Access object from text via LoadFromText.

        Writes UTF-16-LE BOM + content to a temp file, calls LoadFromText on the STA thread.
        Returns True on success, False on failure.
        """
        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".txt", prefix="mcp_imp_")
            os.close(fd)
            with Path(temp_path).open("wb") as f:
                f.write(b"\xff\xfe")
                f.write(text_data.encode("utf-16-le"))

            def _do() -> None:
                self._dispatcher.access_app.LoadFromText(kind, name, temp_path)

            self._dispatcher.call(_do)
            return True
        except Exception:
            return False
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink()
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    # Single-object export/import (SaveAsText family)
    # ------------------------------------------------------------------ #

    def export_form_to_text(self, form_name: str) -> str:
        """Export a form to text, or return an empty string on failure."""
        if not self._check_connected():
            return ""
        return self._save_as_text(self._AC_FORM, form_name)

    def import_form_from_text(self, form_name: str, text_data: str) -> bool:
        """Import form text and return whether the operation succeeded."""
        if not self._check_connected():
            return False
        return self._load_from_text(self._AC_FORM, form_name, text_data)

    def export_report_to_text(self, report_name: str) -> str:
        """Export a report to text, or return an empty string on failure."""
        if not self._check_connected():
            return ""
        return self._save_as_text(self._AC_REPORT, report_name)

    def import_report_from_text(self, report_name: str, text_data: str) -> bool:
        """Import report text and return whether the operation succeeded."""
        if not self._check_connected():
            return False
        return self._load_from_text(self._AC_REPORT, report_name, text_data)

    def export_macro_to_text(self, macro_name: str) -> str:
        """Export a macro to text, or return an empty string on failure."""
        if not self._check_connected():
            return ""
        return self._save_as_text(self._AC_MACRO, macro_name)

    def import_macro_from_text(self, macro_name: str, text_data: str) -> bool:
        """Import macro text and return whether the operation succeeded."""
        if not self._check_connected():
            return False
        return self._load_from_text(self._AC_MACRO, macro_name, text_data)

    def export_query_to_text(self, query_name: str) -> str:
        """Export a query to text, or return an empty string on failure."""
        if not self._check_connected():
            return ""
        return self._save_as_text(self._AC_QUERY, query_name)

    def import_query_from_text(self, query_name: str, text_data: str) -> bool:
        """Import query text and return whether the operation succeeded."""
        if not self._check_connected():
            return False
        return self._load_from_text(self._AC_QUERY, query_name, text_data)

    # ------------------------------------------------------------------ #
    # Module export/import (VBE.CodeModule — not SaveAsText)
    # ------------------------------------------------------------------ #

    def export_module_to_text(self, module_name: str) -> str:
        """Export a VBA module to text, or return an empty string on failure."""
        if not self._check_connected():
            return ""

        def _do() -> str:
            try:
                vbe = self._dispatcher.access_app.VBE
                for i in range(1, vbe.VBProjects.Count + 1):
                    vbp = vbe.VBProjects(i)
                    for j in range(1, vbp.VBComponents.Count + 1):
                        comp = vbp.VBComponents(j)
                        if comp.Name == module_name:
                            cm = comp.CodeModule
                            count = cm.CountOfLines
                            if count > 0:
                                return cm.Lines(1, count)
                            return ""
                return ""
            except Exception:
                return ""

        return self._dispatcher.call(_do)

    def import_module_from_text(self, module_name: str, code: str) -> bool:
        """Import VBA module code and return whether the operation succeeded."""
        if not self._check_connected():
            return False

        def _do() -> bool:
            try:
                vbe = self._dispatcher.access_app.VBE
                target = None
                for i in range(1, vbe.VBProjects.Count + 1):
                    vbp = vbe.VBProjects(i)
                    for j in range(1, vbp.VBComponents.Count + 1):
                        comp = vbp.VBComponents(j)
                        if comp.Name == module_name:
                            target = comp
                            break
                    if target:
                        break

                if target is None:
                    # Lazy import: backends.access imports this module at
                    # package init, so a top-level import would cycle.
                    from .access.vba import load_object_from_text

                    text_data = f"Attribute VB_Name = \"{module_name}\"\r\n{code}"
                    return load_object_from_text(
                        self._dispatcher.access_app,
                        self._AC_MODULE,
                        module_name,
                        text_data,
                    )

                try:
                    cm = target.CodeModule
                    cm.DeleteLines(1, cm.CountOfLines)
                    cm.AddFromString(code)
                    return True
                except Exception:
                    return False
            except Exception:
                return False

        try:
            return self._dispatcher.call(_do)
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Safe filename
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_filename(name: str) -> str:
        return re.sub(r'[\\/:*?"<>|]', "_", name)

    # ------------------------------------------------------------------ #
    # Batch export
    # ------------------------------------------------------------------ #

    def export_all_versioning(
        self,
        out_dir: Path | str,
        *,
        dedup: bool = True,
        module_ext: str = MODULE_EXT,
        list_forms: Callable[[], Sequence[Any]] | None = None,
        list_reports: Callable[[], Sequence[Any]] | None = None,
        list_modules: Callable[[], Sequence[Any]] | None = None,
        list_macros: Callable[[], Sequence[Any]] | None = None,
        list_queries: Callable[[], Sequence[Any]] | None = None,
    ) -> ExportAllResult:
        """Export requested Access objects and return the batch result."""
        if not self._check_connected():
            return ExportAllResult(
                success=False,
                exported={},
                output_dir=str(out_dir),
                file_count=0,
                error="Not connected to database",
            )

        out_path = Path(out_dir)
        kind_map = ObjectKind.KIND_MAP()

        subdirs = {
            "forms": out_path / "forms",
            "reports": out_path / "reports",
            "modules": out_path / "modules",
            "macros": out_path / "macros",
            "queries": out_path / "queries",
        }
        for sub in subdirs.values():
            sub.mkdir(parents=True, exist_ok=True)

        exported: dict[str, list[str]] = {k: [] for k in subdirs}
        file_count = 0

        def write_if_changed(kind_singular: str, subdir: Path, filename: str, content: str) -> bool:
            nonlocal file_count
            file_path = subdir / filename
            if dedup and file_path.exists():
                existing = file_path.read_bytes()
                incoming = content.encode("utf-16-le")
                incoming_bom = b"\xff\xfe" + incoming
                if existing == incoming_bom:
                    return False
            with file_path.open("wb") as f:
                f.write(b"\xff\xfe")
                f.write(content.encode("utf-16-le"))
            file_count += 1
            return True

        if callable(list_forms):
            ac_code = kind_map["form"].save_as_text_code
            assert ac_code is not None  # noqa: S101 - callable list_forms requires form export metadata
            for form_name in list_forms():
                content = self._save_as_text(ac_code, form_name)
                if content:
                    safe = self._safe_filename(form_name)
                    filename = f"forms_{safe}.txt"
                    if write_if_changed("form", subdirs["forms"], filename, content):
                        exported["forms"].append(form_name)

        if callable(list_reports):
            ac_code = kind_map["report"].save_as_text_code
            assert ac_code is not None  # noqa: S101 - callable list_reports requires report export metadata
            for report_name in list_reports():
                content = self._save_as_text(ac_code, report_name)
                if content:
                    safe = self._safe_filename(report_name)
                    filename = f"reports_{safe}.txt"
                    if write_if_changed("report", subdirs["reports"], filename, content):
                        exported["reports"].append(report_name)

        if callable(list_macros):
            ac_code = kind_map["macro"].save_as_text_code
            assert ac_code is not None  # noqa: S101 - callable list_macros requires macro export metadata
            for macro_name in list_macros():
                content = self._save_as_text(ac_code, macro_name)
                if content:
                    safe = self._safe_filename(macro_name)
                    filename = f"macros_{safe}.txt"
                    if write_if_changed("macro", subdirs["macros"], filename, content):
                        exported["macros"].append(macro_name)

        if callable(list_queries):
            ac_code = kind_map["query"].save_as_text_code
            assert ac_code is not None  # noqa: S101 - callable list_queries requires query export metadata
            for query_name in list_queries():
                content = self._save_as_text(ac_code, query_name)
                if content:
                    safe = self._safe_filename(query_name)
                    filename = f"queries_{safe}.txt"
                    if write_if_changed("query", subdirs["queries"], filename, content):
                        exported["queries"].append(query_name)

        if callable(list_modules):
            for module_name in list_modules():
                content = self.export_module_to_text(module_name)
                if content:
                    safe = self._safe_filename(module_name)
                    ext = module_ext if module_ext else MODULE_EXT
                    filename = f"modules_{safe}{ext}"
                    if write_if_changed("module", subdirs["modules"], filename, content):
                        exported["modules"].append(module_name)

        return ExportAllResult(
            success=True,
            exported=exported,
            output_dir=str(out_path),
            file_count=file_count,
        )

    # ------------------------------------------------------------------ #
    # Batch import
    # ------------------------------------------------------------------ #

    def import_all_versioning(self, input_dir: Path | str) -> ImportAllResult:
        """Import versioned Access objects and return the batch result."""
        if not self._check_connected():
            return ImportAllResult(
                success=False,
                imported={},
                error="Not connected to database",
            )

        input_path = Path(input_dir)
        if not input_path.exists():
            return ImportAllResult(
                success=False,
                imported={},
                error=f"Input directory does not exist: {input_dir}",
            )

        imported: dict[str, list[str]] = {
            "modules": [],
            "forms": [],
            "reports": [],
            "macros": [],
            "queries": [],
        }
        errors: list[str] = []

        # Process in order: modules first (so VBA compiles before dependent objects)
        processing_order = [
            ("modules", 5, None),
            ("forms", 2, self.import_form_from_text),
            ("reports", 4, self.import_report_from_text),
            ("macros", 8, self.import_macro_from_text),
            ("queries", 5, self.import_query_from_text),
        ]

        for subdir_name, ac_code, import_fn in processing_order:
            subdir = input_path / subdir_name
            if not subdir.is_dir():
                continue

            patterns = [f"{subdir_name}_*.txt"]
            if subdir_name == "modules":
                patterns.append(f"{subdir_name}_*{MODULE_EXT}")
            file_paths = sorted({path for pattern in patterns for path in subdir.glob(pattern)})
            for file_path in file_paths:
                content = self._decode_file(file_path)
                name = file_path.stem[len(subdir_name) + 1:]
                try:
                    if import_fn is None:
                        ok = self.import_module_from_text(name, content)
                    else:
                        ok = import_fn(name, content)
                    if ok:
                        imported[subdir_name].append(name)
                    else:
                        errors.append(f"{subdir_name}/{name}: import returned False")
                except Exception as e:
                    errors.append(f"{subdir_name}/{name}: {e}")

        return ImportAllResult(
            success=True,
            imported=imported,
            errors=errors if errors else None,
        )

    @staticmethod
    def _decode_file(path: Path) -> str:
        with path.open("rb") as f:
            raw = f.read()
        if raw.startswith(b"\xff\xfe"):
            return raw[2:].decode("utf-16-le", errors="replace")
        return raw.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------ #
    # Compare
    # ------------------------------------------------------------------ #

    def compare_versioning(self, export_dir: Path | str) -> CompareResult:
        """Compare database objects with exported text and return the differences."""
        if not self._check_connected():
            return CompareResult(new=[], missing=[], changed=[], unchanged=[])

        export_path = Path(export_dir)
        kind_map = ObjectKind.KIND_MAP()

        def scan_dir(subdir: Path, prefix: str, module_files: bool = False) -> dict[str, str]:
            result = {}
            if not subdir.is_dir():
                return result
            patterns = [f"{prefix}_*.txt"]
            if module_files:
                patterns.append(f"{prefix}_*{MODULE_EXT}")
            for fp in sorted({path for pattern in patterns for path in subdir.glob(pattern)}):
                name = fp.stem[len(prefix) + 1:]
                result[name] = VersioningBackend._decode_file(fp)
            return result

        db_objects: dict[str, dict[str, str]] = {}
        exported_objects: dict[str, dict[str, str]] = {}

        def _make_export_fn(k_code: int):
            return lambda n: self._save_as_text(k_code, n)

        for kind_name, kind_obj in kind_map.items():
            if kind_name == "module":
                export_fn = self.export_module_to_text
            elif kind_obj.save_as_text_code is not None:
                export_fn = _make_export_fn(kind_obj.save_as_text_code)  # noqa: E731
            else:
                continue

            db_names: list[str] = []
            if kind_name == "form":
                db_names = self._list_db_forms()
            elif kind_name == "report":
                db_names = self._list_db_reports()
            elif kind_name == "macro":
                db_names = self._list_db_macros()
            elif kind_name == "query":
                db_names = self._list_db_queries()
            elif kind_name == "module":
                db_names = self._list_db_modules()

            for name in db_names:
                if kind_name not in db_objects:
                    db_objects[kind_name] = {}
                try:
                    content = export_fn(name)
                    db_objects[kind_name][name] = content
                except Exception:
                    pass

            # Exported files: subdir and filename prefix use plural kind_name
            subdir = export_path / f"{kind_name}s"
            exported_objects[kind_name] = scan_dir(
                subdir, f"{kind_name}s", module_files=kind_name == "module"
            )

        new: list[ObjectTextRef] = []
        missing: list[ObjectTextRef] = []
        changed: list[ObjectTextRef] = []
        unchanged: list[ObjectTextRef] = []

        for kind_name in kind_map:
            db_dict = db_objects.get(kind_name, {})
            ex_dict = exported_objects.get(kind_name, {})

            for name in db_dict:
                if name not in ex_dict:
                    new.append(ObjectTextRef(kind=cast(Literal["form", "report", "module", "macro", "query"], kind_name), name=name))
                elif db_dict[name] != ex_dict[name]:
                    changed.append(ObjectTextRef(kind=cast(Literal["form", "report", "module", "macro", "query"], kind_name), name=name))
                else:
                    unchanged.append(ObjectTextRef(kind=cast(Literal["form", "report", "module", "macro", "query"], kind_name), name=name))

            for name in ex_dict:
                if name not in db_dict:
                    missing.append(ObjectTextRef(kind=cast(Literal["form", "report", "module", "macro", "query"], kind_name), name=name))

        return CompareResult(new=new, missing=missing, changed=changed, unchanged=unchanged)

    def _list_db_forms(self) -> list[str]:
        try:
            def _do() -> list[str]:
                names = []
                all_forms = self._dispatcher.access_app.CurrentProject.AllForms
                for i in range(all_forms.Count):
                    names.append(all_forms(i).Name)
                return names
            return self._dispatcher.call(_do)
        except Exception:
            return []

    def _list_db_reports(self) -> list[str]:
        try:
            def _do() -> list[str]:
                names = []
                all_reports = self._dispatcher.access_app.CurrentProject.AllReports
                for i in range(all_reports.Count):
                    names.append(all_reports(i).Name)
                return names
            return self._dispatcher.call(_do)
        except Exception:
            return []

    def _list_db_macros(self) -> list[str]:
        try:
            def _do() -> list[str]:
                names = []
                all_macros = self._dispatcher.access_app.CurrentProject.AllMacros
                for i in range(all_macros.Count):
                    names.append(all_macros(i).Name)
                return names
            return self._dispatcher.call(_do)
        except Exception:
            return []

    def _list_db_queries(self) -> list[str]:
        try:
            def _do() -> list[str]:
                names = []
                all_queries = self._dispatcher.access_app.CurrentProject.AllQueries
                for i in range(all_queries.Count):
                    names.append(all_queries(i).Name)
                return names
            return self._dispatcher.call(_do)
        except Exception:
            return []

    def _list_db_modules(self) -> list[str]:
        try:
            def _do() -> list[str]:
                names = []
                vbe = self._dispatcher.access_app.VBE
                for i in range(1, vbe.VBProjects.Count + 1):
                    vbp = vbe.VBProjects(i)
                    for j in range(1, vbp.VBComponents.Count + 1):
                        comp = vbp.VBComponents(j)
                        names.append(comp.Name)
                return names
            return self._dispatcher.call(_do)
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Schema DDL
    # ------------------------------------------------------------------ #

    def export_schema_ddl(
        self,
        out_dir: Path | str,
        *,
        list_tables: Callable[[], Sequence[Any]] | None = None,
        list_relationships: Callable[[], Sequence[Any]] | None = None,
    ) -> SchemaDdlResult:
        """Export available schema metadata as DDL and return the result."""
        if not self._check_connected():
            return SchemaDdlResult(success=False, error="Not connected")

        out_path = Path(out_dir)
        schema_dir = out_path / "schema"
        schema_dir.mkdir(parents=True, exist_ok=True)

        tables_path = schema_dir / "ddl_tables.sql"
        rels_path = schema_dir / "ddl_relationships.sql"

        tables_exported = 0
        if callable(list_tables):
            table_ddl_lines: list[str] = []
            for tbl in list_tables():
                name = tbl.get("name", "") if isinstance(tbl, dict) else getattr(tbl, "name", str(tbl))
                cols = tbl.get("columns", []) if isinstance(tbl, dict) else getattr(tbl, "columns", [])
                col_defs = []
                for col in cols:
                    cname = col.get("name", "") if isinstance(col, dict) else getattr(col, "name", "")
                    ctype = col.get("type", "TEXT") if isinstance(col, dict) else getattr(col, "type", "TEXT")
                    size = col.get("size", 0) if isinstance(col, dict) else getattr(col, "size", 0)
                    if size > 0:
                        col_defs.append(f"  [{cname}] {ctype}({size})")
                    else:
                        col_defs.append(f"  [{cname}] {ctype}")
                if col_defs:
                    table_ddl_lines.append(f"CREATE TABLE [{name}] (\n" + ",\n".join(col_defs) + "\n);\n")
                    tables_exported += 1
            tables_path.write_text("\n".join(table_ddl_lines), encoding="utf-8")

        rels_exported = 0
        if callable(list_relationships):
            rel_ddl_lines: list[str] = []
            for rel in list_relationships():
                rname = rel.get("name", "") if isinstance(rel, dict) else getattr(rel, "name", "")
                tbl = rel.get("table", "") if isinstance(rel, dict) else getattr(rel, "table", "")
                ftbl = rel.get("foreign_table", "") if isinstance(rel, dict) else getattr(rel, "foreign_table", "")
                cols = rel.get("columns", []) if isinstance(rel, dict) else getattr(rel, "columns", [])
                fcols = rel.get("foreign_columns", []) if isinstance(rel, dict) else getattr(rel, "foreign_columns", [])
                if tbl and ftbl and cols and fcols:
                    rel_ddl_lines.append(
                        f"ALTER TABLE [{tbl}] ADD CONSTRAINT [{rname}] "
                        f"FOREIGN KEY ({', '.join(f'[{c}]' for c in cols)}) "
                        f"REFERENCES [{ftbl}]({', '.join(f'[{fc}]' for fc in fcols)});\n"
                    )
                    rels_exported += 1
            rels_path.write_text("\n".join(rel_ddl_lines), encoding="utf-8")

        return SchemaDdlResult(
            success=True,
            ddl_tables=str(tables_path),
            ddl_relationships=str(rels_path),
            tables_exported=tables_exported,
            relationships_exported=rels_exported,
        )
