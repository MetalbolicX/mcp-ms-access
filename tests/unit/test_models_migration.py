"""Tests for pydantic migration models in models/migration.py."""

from __future__ import annotations

from datetime import datetime

from ms_access_mcp.models import (
    ColumnSchema,
    TableSchema,
)
from ms_access_mcp.models.migration import (
    ExtractedSchema,
    MigrationJob,
    TableResult,
    TableTransferConfig,
)


class TestExtractedSchema:
    """Tests for ExtractedSchema pydantic model."""

    def test_default_version_is_1_0(self) -> None:
        schema = ExtractedSchema(source="test.accdb")
        assert schema.version == "1.0"

    def test_default_extracted_at_is_set(self) -> None:
        schema = ExtractedSchema(source="test.accdb")
        assert schema.extracted_at is not None
        assert isinstance(schema.extracted_at, datetime)

    def test_default_tables_is_empty_list(self) -> None:
        schema = ExtractedSchema(source="test.accdb")
        assert schema.tables == []

    def test_default_relationships_is_empty_list(self) -> None:
        schema = ExtractedSchema(source="test.accdb")
        assert schema.relationships == []

    def test_default_unknown_metadata_is_empty_flags(self) -> None:
        schema = ExtractedSchema(source="test.accdb")
        assert schema.unknown_metadata.primary_keys is False
        assert schema.unknown_metadata.foreign_keys is False
        assert schema.unknown_metadata.defaults is False
        assert schema.unknown_metadata.indexes is False
        assert schema.unknown_metadata.autoincrement is False

    def test_nested_dataclass_tables_serialized(self) -> None:
        """Pydantic v2 serializes nested dataclass TableSchema natively."""
        table = TableSchema(
            name="Customers",
            columns=[ColumnSchema(name="id", source_type="Long Integer")],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )
        schema = ExtractedSchema(source="test.accdb", tables=[table])
        assert len(schema.tables) == 1
        assert schema.tables[0].name == "Customers"
        assert schema.tables[0].columns[0].name == "id"

    def test_model_dump_includes_nested_dataclasses(self) -> None:
        """model_dump serializes nested dataclasses to dicts."""
        table = TableSchema(
            name="Orders",
            columns=[ColumnSchema(name="order_id", source_type="Long Integer")],
            primary_key=["order_id"],
            foreign_keys=[],
            indexes=[],
        )
        schema = ExtractedSchema(source="test.accdb", tables=[table])
        dumped = schema.model_dump()
        assert dumped["source"] == "test.accdb"
        assert len(dumped["tables"]) == 1
        assert dumped["tables"][0]["name"] == "Orders"


class TestMigrationJob:
    """Tests for MigrationJob pydantic model."""

    def test_default_status_is_pending(self) -> None:
        job = MigrationJob(id="abc12345")
        assert job.status == "pending"

    def test_default_phase_is_extract(self) -> None:
        job = MigrationJob(id="abc12345")
        assert job.phase == "extract"

    def test_default_progress_is_zero(self) -> None:
        job = MigrationJob(id="abc12345")
        assert job.progress == 0

    def test_default_results_is_empty_list(self) -> None:
        job = MigrationJob(id="abc12345")
        assert job.results == []

    def test_default_errors_is_empty_list(self) -> None:
        job = MigrationJob(id="abc12345")
        assert job.errors == []

    def test_completed_at_is_none_by_default(self) -> None:
        job = MigrationJob(id="abc12345")
        assert job.completed_at is None

    def test_current_table_can_be_set(self) -> None:
        job = MigrationJob(id="abc12345", current_table="Customers")
        assert job.current_table == "Customers"

    def test_nested_table_results(self) -> None:
        """MigrationJob can hold nested TableResult models."""
        result = TableResult(
            table="Customers",
            source_rows=100,
            rows_transferred=100,
            duration_ms=500,
            success=True,
        )
        job = MigrationJob(id="abc12345", results=[result])
        assert len(job.results) == 1
        assert job.results[0].table == "Customers"
        assert job.results[0].source_rows == 100


class TestTableResult:
    """Tests for TableResult pydantic model."""

    def test_default_success_is_true(self) -> None:
        result = TableResult(table="Customers")
        assert result.success is True

    def test_default_error_is_none(self) -> None:
        result = TableResult(table="Customers")
        assert result.error is None

    def test_default_strategy_used_is_batch(self) -> None:
        result = TableResult(table="Customers")
        assert result.strategy_used == "batch"

    def test_all_fields_set(self) -> None:
        result = TableResult(
            table="Orders",
            source_rows=500,
            rows_transferred=500,
            duration_ms=2500,
            success=True,
            strategy_used="batch",
            verification={"row_count": 500},
        )
        assert result.table == "Orders"
        assert result.source_rows == 500
        assert result.rows_transferred == 500
        assert result.duration_ms == 2500
        assert result.success is True
        assert result.verification == {"row_count": 500}

    def test_failed_result_with_error(self) -> None:
        result = TableResult(
            table="Orders",
            source_rows=500,
            rows_transferred=0,
            duration_ms=100,
            success=False,
            error="Connection refused",
        )
        assert result.success is False
        assert result.error == "Connection refused"
        assert result.rows_transferred == 0


class TestTableTransferConfig:
    """Tests for TableTransferConfig pydantic model."""

    def test_all_fields_optional(self) -> None:
        config = TableTransferConfig()
        assert config.columns is None
        assert config.where is None
        assert config.order_by is None

    def test_columns_override(self) -> None:
        config = TableTransferConfig(columns=["id", "name", "email"])
        assert config.columns == ["id", "name", "email"]

    def test_where_clause(self) -> None:
        config = TableTransferConfig(where="status = 'active'")
        assert config.where == "status = 'active'"

    def test_order_by(self) -> None:
        config = TableTransferConfig(order_by=["name ASC", "created_at DESC"])
        assert config.order_by == ["name ASC", "created_at DESC"]

    def test_combined_config(self) -> None:
        config = TableTransferConfig(
            columns=["id", "name"],
            where="active = 1",
            order_by=["name"],
        )
        assert config.columns == ["id", "name"]
        assert config.where == "active = 1"
        assert config.order_by == ["name"]
