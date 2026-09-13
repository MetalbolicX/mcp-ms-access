"""Pydantic migration models — ExtractedSchema, MigrationJob, TableResult, TableTransferConfig.

These models use pydantic for validation and serialization while REUSING
the dataclass TableSchema family (ColumnSchema, ForeignKeySchema, IndexSchema,
TableSchema, UnknownMetadata) from models/__init__.py as nested field types.
Pydantic v2 serializes stdlib dataclasses natively.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..models import (
    ForeignKeySchema,
    TableSchema,
    UnknownMetadata,
)

__all__ = [
    "ExtractedSchema",
    "MigrationJob",
    "TableResult",
    "TableTransferConfig",
]


class ExtractedSchema(BaseModel):
    """Schema extracted from an Access database for migration.

    Attributes:
        source: Path to the source Access database.
        version: Schema version identifier (default "1.0").
        extracted_at: ISO-8601 timestamp of extraction.
        tables: List of table schemas.
        relationships: List of foreign key relationships.
        unknown_metadata: Flags for schema aspects that could not be determined.
    """

    source: str
    version: str = "1.0"
    extracted_at: datetime = Field(default_factory=datetime.utcnow)
    tables: list[TableSchema] = Field(default_factory=list)
    relationships: list[ForeignKeySchema] = Field(default_factory=list)
    unknown_metadata: UnknownMetadata = Field(default_factory=UnknownMetadata)

    model_config = {"arbitrary_types_allowed": True}


class TableResult(BaseModel):
    """Result of migrating a single table.

    Attributes:
        table: Name of the table.
        source_rows: Number of rows in the source table.
        rows_transferred: Number of rows successfully transferred.
        duration_ms: Time taken to transfer the table in milliseconds.
        success: Whether the table transfer succeeded.
        error: Error message if transfer failed.
        strategy_used: Transfer strategy used (e.g., "batch").
        verification: Verification result dict with row counts and optionally sampled content.
    """

    table: str
    source_rows: int = 0
    rows_transferred: int = 0
    duration_ms: int = 0
    success: bool = True
    error: str | None = None
    strategy_used: str = "batch"
    verification: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


class MigrationJob(BaseModel):
    """Migration job tracking state.

    Attributes:
        id: Short job ID (uuid4().hex[:8]).
        status: Job status (pending, running, completed, failed).
        phase: Current phase (extract, upload, transfer).
        current_table: Name of the table currently being processed.
        progress: Progress percentage (0-100).
        started_at: Timestamp when the job started.
        completed_at: Timestamp when the job completed (None if not yet complete).
        results: List of per-table results.
        errors: List of error messages encountered.
    """

    id: str
    status: str = "pending"
    phase: str = "extract"
    current_table: str | None = None
    progress: int = 0
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    results: list[TableResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class TableTransferConfig(BaseModel):
    """Per-table transfer configuration overrides.

    Attributes:
        columns: Optional list of column names to transfer (None = all).
        where: Optional WHERE clause to filter rows.
        order_by: Optional ORDER BY clause to specify row ordering.
    """

    columns: list[str] | None = None
    where: str | None = None
    order_by: list[str] | None = None

    model_config = {"arbitrary_types_allowed": True}
