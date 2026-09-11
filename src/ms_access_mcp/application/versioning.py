"""Versioning use cases — export/import objects, compare, schema DDL.

Constructor-injected backends, immutable result envelopes.
All destructive ops gated via safety.require_acknowledgment + write policy.
"""

from __future__ import annotations

import os
from typing import Any

from ..errors import Err, Ok, Result
from ..errors import err as _err
from ..errors import ok as _ok
from ..safety import WritePolicy

__all__ = ["VersioningUseCases"]


class VersioningUseCases:
    """Constructor-injected versioning operations.

    No globals, no container. Each instance holds a reference to the
    active backend adapter and write policy.
    """

    _KIND_METHODS = {
        "form": ("export_form_to_text", "import_form_from_text", "forms", ".txt"),
        "report": ("export_report_to_text", "import_report_from_text", "reports", ".txt"),
        "macro": ("export_macro_to_text", "import_macro_from_text", "macros", ".txt"),
        "query": ("export_query_to_text", "import_query_from_text", "queries", ".txt"),
        "module": ("export_module_to_text", "import_module_from_text", "modules", ".bas"),
    }

    def __init__(
        self,
        adapter: Any = None,
        readonly: bool = True,
        db_path: str | None = None,
    ) -> None:
        self._adapter: Any = adapter
        self._policy = WritePolicy(readonly=readonly)
        self._db_path = db_path

    def _ensure_adapter(self) -> Err | None:
        if self._adapter is not None:
            if not getattr(self._adapter, "connected", False):
                self._adapter.connect(self._db_path)
            return None

        from ..backends import AUTOMATION_CAPS, BackendSelector
        from ..capabilities import BackendCapabilityMismatchError, BackendUnavailableError

        if self._db_path is None:
            return _err("Backend unavailable: db_path is required to create an adapter")

        try:
            self._adapter = BackendSelector.get_adapter(
                db_path=self._db_path,
                backend="com",
                capabilities=AUTOMATION_CAPS,
            )
            self._adapter.connect(self._db_path)
            return None
        except BackendUnavailableError as e:
            self._adapter = None
            return _err(f"Backend unavailable: {e}")
        except BackendCapabilityMismatchError as e:
            self._adapter = None
            return _err(f"Backend unavailable: {e}")
        except Exception as e:
            self._adapter = None
            return _err(f"Backend unavailable: {e}")

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
            output_path = os.path.join(out_dir, f"{prefix}_{safe_name}{ext}")
            return _ok(data={"kind": kind, "name": name, "data": data, "output_path": output_path})
        except Exception as e:
            return _err(str(e))

    def export_all(self, out_dir: str) -> Result:
        if err := self._ensure_adapter():
            return err

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

        resolved = self._resolve_kind(kind)
        if isinstance(resolved, Err):
            return resolved

        import_method = resolved[1]
        if err := self._ensure_adapter():
            return err

        try:
            with open(file_path, "rb") as f:
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

        if err := self._ensure_adapter():
            return err

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
        if err := self._ensure_adapter():
            return err

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
        if err := self._ensure_adapter():
            return err

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
