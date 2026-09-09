"""Tests for models.py and errors.py — TDD RED phase."""

from dataclasses import fields, is_dataclass

import pytest


class TestModels:
    """Verify all 14 dataclasses from oracle models/database.py are ported."""

    @pytest.mark.parametrize(
        "cls_name,expected_fields",
        [
            ("FieldInfo", {"name", "type", "size", "required", "allow_zero_length", "default_value", "is_autoincrement"}),
            ("TableInfo", {"name", "fields", "record_count", "primary_key"}),
            ("QueryInfo", {"name", "sql", "type"}),
            ("RelationshipInfo", {"name", "table", "foreign_table", "attributes", "columns", "foreign_columns"}),
            ("ForeignKeyInfo", {"name", "columns", "foreign_table", "foreign_columns"}),
            ("ControlInfo", {"name", "type", "properties"}),
            ("FormInfo", {"name", "record_source", "controls"}),
            ("ReportInfo", {"name", "record_source", "controls"}),
            ("MacroInfo", {"name", "type", "date_created", "date_modified"}),
            ("ModuleInfo", {"name", "type", "code"}),
            ("QueryResult", {"success", "rows", "count", "columns", "error"}),
            ("LinkedTableInfo", {"name", "source_table", "connect_string", "type", "attributes"}),
            ("IndexInfo", {"name", "columns", "is_unique", "is_primary", "ignore_nulls"}),
            ("DatabasePropertyInfo", {"name", "value", "type", "built_in"}),
        ],
    )
    def test_model_exists_and_is_dataclass(self, cls_name: str, expected_fields: set):
        """Each model is a stdlib dataclass with the correct fields."""
        from ms_access_mcp.models import (
            ControlInfo,
            DatabasePropertyInfo,
            FieldInfo,
            ForeignKeyInfo,
            FormInfo,
            IndexInfo,
            LinkedTableInfo,
            MacroInfo,
            ModuleInfo,
            QueryInfo,
            QueryResult,
            RelationshipInfo,
            ReportInfo,
            TableInfo,
        )
        registry = {
            "FieldInfo": FieldInfo,
            "TableInfo": TableInfo,
            "QueryInfo": QueryInfo,
            "RelationshipInfo": RelationshipInfo,
            "ForeignKeyInfo": ForeignKeyInfo,
            "ControlInfo": ControlInfo,
            "FormInfo": FormInfo,
            "ReportInfo": ReportInfo,
            "MacroInfo": MacroInfo,
            "ModuleInfo": ModuleInfo,
            "QueryResult": QueryResult,
            "LinkedTableInfo": LinkedTableInfo,
            "IndexInfo": IndexInfo,
            "DatabasePropertyInfo": DatabasePropertyInfo,
        }
        cls = registry[cls_name]
        assert is_dataclass(cls), f"{cls_name} must be a dataclass"
        actual_fields = {f.name for f in fields(cls)}
        assert actual_fields == expected_fields, f"{cls_name} fields mismatch: {actual_fields} vs {expected_fields}"


class TestErrors:
    """Verify error taxonomy from oracle."""

    def test_error_hierarchy_access_mcp_error(self):
        from ms_access_mcp.errors import AccessMcpError
        assert issubclass(AccessMcpError, Exception)

    def test_error_hierarchy_backend_unavailable(self):
        from ms_access_mcp.errors import AccessMcpError, BackendUnavailableError
        assert issubclass(BackendUnavailableError, AccessMcpError)

    def test_error_hierarchy_capability_mismatch(self):
        from ms_access_mcp.errors import AccessMcpError, BackendCapabilityMismatchError
        assert issubclass(BackendCapabilityMismatchError, AccessMcpError)

    def test_error_hierarchy_dao_operation(self):
        from ms_access_mcp.errors import AccessMcpError, DaoOperationError
        assert issubclass(DaoOperationError, AccessMcpError)

    def test_dao_operation_error_has_cause(self):
        from ms_access_mcp.errors import DaoOperationError
        original = ValueError("original cause")
        err = DaoOperationError("select failed", cause=original)
        assert err.message == "select failed"
        assert err.__cause__ is original

    def test_access_mcp_error_message(self):
        from ms_access_mcp.errors import AccessMcpError
        err = AccessMcpError("base error")
        assert str(err) == "base error"

    def test_backend_unavailable_error_message(self):
        from ms_access_mcp.errors import BackendUnavailableError
        err = BackendUnavailableError("DAO not available")
        assert str(err) == "DAO not available"

    def test_capability_mismatch_error_message(self):
        from ms_access_mcp.errors import BackendCapabilityMismatchError
        err = BackendCapabilityMismatchError("capability mismatch")
        assert str(err) == "capability mismatch"

    def test_dao_operation_error_message(self):
        from ms_access_mcp.errors import DaoOperationError
        err = DaoOperationError("dao error")
        assert str(err) == "dao error"
