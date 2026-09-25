"""Export use cases — data export to CSV/JSON/XLSX formats."""

from __future__ import annotations

import csv
import dataclasses
import inspect
import json
from functools import cache
from pathlib import Path
from typing import Any, Literal

from ..backends.access import AccessAdapter
from ..errors import Err, Ok, Result, err, ok
from ..models import QueryResult
from .sql_builder import build_select
from .xlsx_capability import IisamProviderProbe

_ok = ok
_err = err


@cache
def _method_signature(method: Any) -> inspect.Signature:
    """Cached inspect.signature lookup for adapter query methods."""
    return inspect.signature(method)


def _sanitize_sheet_name(name: str) -> Result:
    """Validate and sanitize a sheet name for xlsx export.

    Rules:
    - Must not be empty
    - Must be at most 31 characters
    - Must not contain ]
    - Trailing $ is stripped

    Returns Ok with sanitized name, or Err with message.
    """
    if not name or not name.strip():
        return _err("Sheet name must not be empty.")
    # Strip trailing $
    sanitized = name.rstrip("$").strip()
    if not sanitized:
        return _err("Sheet name must not be empty after normalization.")
    if "]" in sanitized:
        return _err(f"Sheet name must not contain ']': {sanitized!r}")
    if len(sanitized) > 31:
        return _err(f"Sheet name must be at most 31 characters: {len(sanitized)} > 31")
    return _ok(sanitized)


def _build_iisam_insert_sql(table_or_query: str, output_path: str, sheet_name: str) -> Result:
    """Build the IISAM INSERT INTO SQL for xlsx export.

    Returns: INSERT INTO [Excel 12.0;HDR=YES;DATABASE=<abs>].[<sheet>$] <select>

    Returns Err if output_path contains ';', '"', or ']' — connection-string
    injection and bracket-escape vectors in the DATABASE= segment.
    """
    if any(ch in output_path for ch in (';', '"', "]")):
        return _err(
            f"Output path must not contain ';', '\"' or ']' characters "
            f"(connection-string / bracket-escape vectors in the DATABASE= segment). Got: {output_path!r}"
        )
    abs_path = str(Path(output_path).absolute())
    select_clause = build_select(table_or_query, None, None, None)
    return _ok(f"INSERT INTO [Excel 12.0;HDR=YES;DATABASE={abs_path}].[{sheet_name}$] {select_clause}")


def _query_adapter(adapter: Any, sql: str) -> Result:
    """Execute a SELECT on the adapter and return rows."""
    try:
        method = getattr(adapter, "query_data", None) or getattr(adapter, "execute_query", None)
        if method is None:
            return _err(f"{type(adapter).__name__} does not support query_data")
        sig = _method_signature(getattr(method, "__func__", method))
        kwargs: dict[str, Any] = {}
        if "params" in sig.parameters:
            kwargs["params"] = None
        if "limit" in sig.parameters:
            kwargs["limit"] = None
        result = method(sql, **kwargs)
        if isinstance(result, QueryResult):
            result = dataclasses.asdict(result)
        if isinstance(result, dict):
            if "rows" in result:
                return _ok(data=result.get("rows", []), rowcount=result.get("count", 0))
            if "data" in result:
                return _ok(data=result.get("data", []), rowcount=result.get("rowcount", 0))
            return _ok(data=result, rowcount=len(result))
        return _ok(data=result, rowcount=len(result) if result else 0)
    except Exception as e:
        return _err(str(e))


def export_data(
    adapter: Any,
    policy: Any,
    allowed_dirs: list[Any],
    table_or_query: str,
    output_path: str,
    format: Literal["csv", "json", "xlsx"] = "csv",
    sheet_name: str = "Sheet1",
) -> Result:
    """Export table or query data to a file.

    Args:
        adapter: Resolved backend adapter
        policy: WritePolicy instance for path and write checks
        allowed_dirs: List of allowed directory paths
        table_or_query: Table name or query name
        output_path: Output file path
        format: Export format ("csv", "json", or "xlsx")
        sheet_name: Sheet name for xlsx export (default "Sheet1");
            must be 1-31 chars; ] not allowed; trailing $ is stripped.

    Returns:
        Ok with rowcount, or Err
    """
    from ..safety import is_path_allowed

    if not is_path_allowed(output_path, allowed_dirs, must_exist=False):
        return _err(f"Output path not allowed: {output_path}")

    # Sanitize sheet name for xlsx
    sheet_name_sanitized: str | None = None
    if format == "xlsx":
        sanitized_result = _sanitize_sheet_name(sheet_name)
        if isinstance(sanitized_result, Err):
            return sanitized_result
        sheet_name_sanitized = sanitized_result.data  # type: ignore[assignment]

    # xlsx routing: AccessAdapter uses DoCmd; probe-gated SQL fallback for DAO/ADO
    if format == "xlsx":
        if isinstance(adapter, AccessAdapter):
            affected = adapter.export_table_to_xlsx(table_or_query, output_path)
            return _ok(rowcount=affected)
        if IisamProviderProbe.is_excel_provider_registered():
            if sheet_name_sanitized is None:
                return _err("Sheet name sanitization failed")
            sql_result = _build_iisam_insert_sql(table_or_query, output_path, sheet_name_sanitized)
            if isinstance(sql_result, Err):
                return sql_result
            sql = sql_result.data  # type: ignore[union-attr]
            affected = adapter.execute_raw_sql(sql)
            return _ok(rowcount=affected)
        msg = (
            "xlsx export on this backend requires either the 'access' "
            "backend or the ACE Excel IISAM provider. Install Microsoft "
            "Access Database Engine 2016 Redistributable, or reconnect "
            "with the 'access' backend."
        )
        return _err(msg)

    try:
        # Fetch the data using the adapter directly
        sql = build_select(table_or_query, None, None, None)
        result = _query_adapter(adapter, sql)
        if isinstance(result, Err):
            return result

        rows = result.data if isinstance(result, Ok) else []

        # Export to file
        if format == "csv":
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                if rows:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
                else:
                    f.write("")
        else:  # json
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2, default=str)

        return _ok(rowcount=len(rows))
    except Exception as e:
        return _err(str(e))
