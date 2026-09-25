"""Structural Protocols for application-layer consumers.

These protocols capture what each consumer actually uses from use case classes,
avoiding the need for test fakes to inherit from concrete classes.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from ..errors import Err, Ok


class AdapterLifecycleProtocol(Protocol):
    """Lifecycle members every injected backend adapter must expose."""

    connected: bool

    def connect(self, db_path: str | None = None, /) -> Any:
        """Connect the adapter to the database at *db_path*."""
        ...

    def disconnect(self, /) -> None:
        ...


class MigrationAdapterProtocol(AdapterLifecycleProtocol, Protocol):
    """Backend adapter surface used by ``MigrationUseCases``."""

    def get_table_schema_plan(self, /) -> Any: ...
    def get_tables(self, /) -> Any: ...
    def get_columns(self, table_name: str, /) -> Any: ...
    def get_indexes(self, table_name: str, /) -> Any: ...
    def get_relationships(self, /) -> Any: ...
    def execute_query(self, sql: str, /) -> Any: ...


class MigrationConnectorProtocol(Protocol):
    """Connector surface used by ``MigrationUseCases``."""

    def table_exists(self, table: str, /) -> bool: ...
    def create_table(self, ddl: str, /) -> None: ...
    def insert_batch(self, table: str, columns: list[str], rows: list[tuple]) -> int: ...
    def rollback_table(self, table: str, /) -> None: ...
    def disconnect(self, /) -> None: ...
    def count_rows(self, table: str, /) -> int: ...


class AutomationAdapterProtocol(AdapterLifecycleProtocol, Protocol):
    """Backend adapter surface used by ``AutomationUseCases``."""

    def get_macros(self, /) -> Any:
        ...

    def get_modules(self, /) -> Any:
        ...

    def compile_vba(self, /) -> Any:
        ...


class MaintenanceAdapterProtocol(AdapterLifecycleProtocol, Protocol):
    """Backend adapter surface used by ``MaintenanceUseCases``."""

    def compact_repair(self, path: str, mode: str, /) -> Any:
        ...

    def recover(self, /) -> Any:
        ...


class VersioningAdapterProtocol(AdapterLifecycleProtocol, Protocol):
    """Backend adapter surface used by ``VersioningUseCases``."""

    def export_all_versioning(self, out_dir: str, /) -> Any:
        ...

    def import_all_versioning(self, input_dir: str, /) -> Any:
        ...

    def compare_versioning(self, export_dir: str, /) -> Any:
        ...

    def export_schema_ddl(self, out_dir: str, /) -> Any:
        ...


class MaintenanceUseCasesProtocol(Protocol):
    """CLI maintenance commands need these methods from MaintenanceUseCases."""

    def backup(self, path: str, *, retention: int | None = None) -> Ok | Err:
        ...

    def dev_copy_status(self) -> Ok | Err:
        ...

    def create_dev_copy(self, source: str | None = None) -> Ok | Err:
        ...

    def deploy_dev_copy(self, dev_path: str, target: str, *, confirm: bool, preflight_backup: bool = True) -> Ok | Err:
        ...

    def compact_repair(
        self, path: str, mode: Literal["compact", "repair", "compact_and_repair"], *, confirm: bool
    ) -> Ok | Err:
        ...

    def diagnose_environment(self) -> Ok | Err:
        ...

    def recover_access(self, *, confirm: bool) -> Ok | Err:
        ...


class VersioningUseCasesProtocol(Protocol):
    """CLI versioning commands need these methods from VersioningUseCases."""

    def export_object(self, kind: str, name: str, out_dir: str) -> Ok | Err:
        ...

    def export_all(self, out_dir: str) -> Ok | Err:
        ...

    def import_object(self, kind: str, name: str, file_path: str, confirm: bool) -> Ok | Err:
        ...

    def import_all(self, input_dir: str, confirm: bool) -> Ok | Err:
        ...

    def compare(self, export_dir: str) -> Ok | Err:
        ...

    def export_schema_ddl(self, out_dir: str) -> Ok | Err:
        ...


class AutomationUseCasesProtocol(Protocol):
    """CLI automation commands need these methods from AutomationUseCases."""

    def list_objects(self, object_kind: Literal["form", "report"]) -> Ok | Err:
        ...

    def object_exists(self, object_kind: Literal["form", "report"], name: str) -> Ok | Err:
        ...

    def create_object(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        record_source: str = "",
        properties: dict[str, Any] | None = None,
        confirm: bool = False,
    ) -> Ok | Err:
        ...

    def rename_object(
        self,
        object_kind: Literal["form", "report"],
        old_name: str,
        new_name: str,
        confirm: bool = False,
    ) -> Ok | Err:
        ...

    def delete_object(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        confirm: bool = False,
    ) -> Ok | Err:
        ...

    def get_properties(self, object_kind: Literal["form", "report"], name: str) -> Ok | Err:
        ...

    def get_controls(self, object_kind: Literal["form", "report"], name: str) -> Ok | Err:
        ...

    def add_control(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        control_type: str,
        control_name: str,
        section: int = 0,
        properties: dict[str, Any] | None = None,
        confirm: bool = False,
    ) -> Ok | Err:
        ...

    def remove_control(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        control_name: str,
        confirm: bool = False,
    ) -> Ok | Err:
        ...

    def get_sections(self, object_kind: Literal["form", "report"], name: str) -> Ok | Err:
        ...

    def export_to_text(self, object_kind: Literal["form", "report"], name: str) -> Ok | Err:
        ...

    def import_from_text(
        self,
        object_kind: Literal["form", "report"],
        name: str,
        text: str,
        confirm: bool = False,
    ) -> Ok | Err:
        ...

    def get_macros(self) -> Ok | Err:
        ...

    def macro_exists(self, name: str) -> Ok | Err:
        ...

    def create_macro(self, name: str, confirm: bool = False) -> Ok | Err:
        ...

    def rename_macro(self, old_name: str, new_name: str, confirm: bool = False) -> Ok | Err:
        ...

    def delete_macro(self, name: str, confirm: bool = False) -> Ok | Err:
        ...

    def run_macro(self, name: str, confirm: bool = False) -> Ok | Err:
        ...

    def get_macro_properties(self, name: str) -> Ok | Err:
        ...

    def get_modules(self) -> Ok | Err:
        ...

    def get_vba_code(self, module_name: str) -> Ok | Err:
        ...

    def set_vba_code(self, module_name: str, code: str, confirm: bool = False) -> Ok | Err:
        ...

    def add_vba_procedure(
        self,
        module_name: str,
        procedure_name: str,
        code: str,
        confirm: bool = False,
    ) -> Ok | Err:
        ...

    def compile_vba(self) -> Ok | Err:
        ...

    def save_database(self, confirm: bool = False) -> Ok | Err:
        ...

    def module_exists(self, module_name: str) -> Ok | Err:
        ...

    def create_module(self, module_name: str, module_type: int = 1, confirm: bool = False) -> Ok | Err:
        ...

    def delete_module(self, module_name: str, confirm: bool = False) -> Ok | Err:
        ...

    def rename_module(self, old_name: str, new_name: str, confirm: bool = False) -> Ok | Err:
        ...

    def vba_list_procedures(self, module_name: str) -> Ok | Err:
        ...

    def vba_get_procedure(self, module_name: str, procedure_name: str) -> Ok | Err:
        ...

    def vba_replace_procedure(
        self,
        module_name: str,
        procedure_name: str,
        code: str,
        confirm: bool = False,
    ) -> Ok | Err:
        ...
