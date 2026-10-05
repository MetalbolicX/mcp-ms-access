"""Versioning use cases — export/import objects, compare, schema DDL.

Constructor-injected backends, immutable result envelopes.
All destructive ops gated via safety.require_acknowledgment + write policy.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import Err, Ok, Result
from ..errors import err as _err
from ..errors import ok as _ok
from ..safety import WritePolicy, is_path_allowed
from ._adapter_lifecycle import AdapterLifecycle
from .protocols import VersioningAdapterProtocol

__all__ = ["VersioningUseCases"]


class VersioningUseCases(AdapterLifecycle[VersioningAdapterProtocol]):
    """Constructor-injected versioning operations.

    No globals, no container. Each instance holds a reference to the
    active backend adapter and write policy.
    """

    _backend = "com"
    _capabilities_name = "AUTOMATION_CAPS"

    _KIND_METHODS = {
        "form": ("export_form_to_text", "import_form_from_text", "forms", ".txt"),
        "report": ("export_report_to_text", "import_report_from_text", "reports", ".txt"),
        "macro": ("export_macro_to_text", "import_macro_from_text", "macros", ".txt"),
        "query": ("export_query_to_text", "import_query_from_text", "queries", ".txt"),
        "module": ("export_module_to_text", "import_module_from_text", "modules", ".bas"),
    }

    def __init__(
        self,
        adapter: VersioningAdapterProtocol | None = None,
        readonly: bool = True,
        db_path: str | None = None,
        allowed_dirs: list[Path] | None = None,
    ) -> None:
        self._adapter = adapter
        self._policy = WritePolicy(readonly=readonly)
        self._db_path = db_path
        self._allowed_dirs = allowed_dirs or []

    def _confirm_gate(self, op: str, confirm: bool) -> Err | None:
        if ack := self._policy.require_acknowledgment(op, confirm):
            return _err(ack)
        return None

    def _write_gate(self) -> Err | None:
        if not self._policy.allow_write():
            return _err("Write operations are disabled (readonly mode)")
        return None

    def _dangerous(self, operation: str, confirm: bool, needs_write: bool = True) -> Err | None:
        if err := self._confirm_gate(operation, confirm):
            return err
        if needs_write:
            if err := self._write_gate():
                return err
        return None

    def _call_adapter(self, method_name: str, *args, **kwargs) -> Err | Ok:
        if err := self._ensure_adapter():
            return err
        try:
            result = getattr(self._adapter, method_name)(*args, **kwargs)
            return _ok(data=result)
        except Exception as e:
            return _err(str(e))

    def _call_adapter_bool(self, method_name: str, *args, **kwargs) -> Err | Ok:
        if err := self._ensure_adapter():
            return err
        try:
            result = getattr(self._adapter, method_name)(*args, **kwargs)
            return _ok(data=result, rowcount=1 if result else 0)
        except Exception as e:
            return _err(str(e))

    def _resolve_kind(self, kind: str) -> tuple[str, str, str, str] | Err:
        entry = self._KIND_METHODS.get(kind)
        if entry is None:
            return _err(f"unknown object kind: {kind}")
        return entry

    def export_object(self, kind: str, name: str, out_dir: str) -> Result:
        if not is_path_allowed(out_dir, self._allowed_dirs, must_exist=False):
            return _err(f"Output directory not allowed: {out_dir}")

        resolved = self._resolve_kind(kind)
        if isinstance(resolved, Err):
            return resolved

        export_method = resolved[0]
        if err := self._ensure_adapter():
            return err

        try:
            data = getattr(self._adapter, export_method)(name)
            prefix, ext = resolved[2], resolved[3]
            safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)
            output = Path(out_dir) / f"{prefix}_{safe_name}{ext}"
            output_path = str(output)
            if not is_path_allowed(output.parent, self._allowed_dirs, must_exist=False):
                return _err("Output path not allowed")
            output.parent.mkdir(parents=True, exist_ok=True)
            if not is_path_allowed(output_path, self._allowed_dirs, must_exist=False):
                return _err("Output path not allowed")
            output.write_bytes(data.encode("utf-8"))
            return _ok(data={"kind": kind, "name": name, "data": data, "output_path": output_path})
        except Exception as e:
            return _err(str(e))

    def export_all(self, out_dir: str) -> Result:
        if not is_path_allowed(out_dir, self._allowed_dirs, must_exist=False):
            return _err(f"Output directory not allowed: {out_dir}")

        if err := self._ensure_adapter():
            return err
        assert self._adapter is not None  # noqa: S101 - _ensure_adapter establishes the application invariant

        try:
            result = self._adapter.export_all_versioning(out_dir)
            return _ok(
                data={
                    "output_dir": result.get("output_dir", out_dir),
                    "file_count": result.get("file_count", 0),
                    "exported": result.get("exported", {}),
                }
            )
        except Exception as e:
            return _err(str(e))

    def import_object(self, kind: str, name: str, file_path: str, confirm: bool) -> Result:
        if err := self._dangerous(f"import_{kind}", confirm):
            return err

        if not is_path_allowed(file_path, self._allowed_dirs):
            return _err(f"Input file not allowed: {file_path}")

        resolved = self._resolve_kind(kind)
        if isinstance(resolved, Err):
            return resolved

        import_method = resolved[1]
        if err := self._ensure_adapter():
            return err

        try:
            with Path(file_path).open("rb") as f:
                raw = f.read()
            if raw.startswith(b"\xff\xfe"):
                text_data = raw[2:].decode("utf-16-le", errors="replace")
            else:
                text_data = raw.decode("utf-8", errors="replace")

            getattr(self._adapter, import_method)(name, text_data)
            return _ok(data={"kind": kind, "name": name, "input_path": file_path})
        except Exception as e:
            return _err(str(e))

    def import_all(self, input_dir: str, confirm: bool) -> Result:
        if err := self._dangerous("import_all", confirm):
            return err

        if not is_path_allowed(input_dir, self._allowed_dirs):
            return _err(f"Input directory not allowed: {input_dir}")

        if err := self._ensure_adapter():
            return err
        assert self._adapter is not None  # noqa: S101 - _ensure_adapter establishes the application invariant

        try:
            result = self._adapter.import_all_versioning(input_dir)
            return _ok(
                data={
                    "imported": result.get("imported", {}),
                    "errors": result.get("errors"),
                    "input_dir": input_dir,
                }
            )
        except Exception as e:
            return _err(str(e))

    def compare(self, export_dir: str) -> Result:
        if not is_path_allowed(export_dir, self._allowed_dirs):
            return _err(f"Export directory not allowed: {export_dir}")

        if err := self._ensure_adapter():
            return err
        assert self._adapter is not None  # noqa: S101 - _ensure_adapter establishes the application invariant

        try:
            result = self._adapter.compare_versioning(export_dir)
            return _ok(
                data={
                    "new": result.get("new", []),
                    "missing": result.get("missing", []),
                    "changed": result.get("changed", []),
                    "unchanged": result.get("unchanged", []),
                }
            )
        except Exception as e:
            return _err(str(e))

    def export_schema_ddl(self, out_dir: str) -> Result:
        if not is_path_allowed(out_dir, self._allowed_dirs, must_exist=False):
            return _err(f"Output directory not allowed: {out_dir}")

        if err := self._ensure_adapter():
            return err
        assert self._adapter is not None  # noqa: S101 - _ensure_adapter establishes the application invariant

        try:
            result = self._adapter.export_schema_ddl(out_dir)
            return _ok(
                data={
                    "ddl_tables": result.get("ddl_tables", ""),
                    "ddl_relationships": result.get("ddl_relationships", ""),
                    "tables_exported": result.get("tables_exported", 0),
                    "relationships_exported": result.get("relationships_exported", 0),
                }
            )
        except Exception as e:
            return _err(str(e))
