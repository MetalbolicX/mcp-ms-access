"""CLI formatting utilities — extracted for reuse across CLI and MCP server entry-points."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .application.database import Err, Ok


def cli_allowed_dirs() -> list[Path]:
    """Read ``ACCESS_MCP_ALLOWED_DIRS`` for the CLI subprocess (semicolon on Windows)."""
    raw = os.environ.get("ACCESS_MCP_ALLOWED_DIRS", "")
    if not raw:
        return []
    sep = ";" if os.name == "nt" else ":"
    return [Path(p).expanduser() for p in raw.split(sep) if p.strip()]


def result_to_dict(result: Ok | Err) -> dict[str, Any]:
    """Convert result to dict for JSON output."""
    if isinstance(result, Ok):
        return {"success": True, "data": result.data, "rowcount": result.rowcount}
    return {"success": False, "error": result.message}


def format_result(result: Ok | Err, json_output: bool = False) -> int:
    """Format a result and return exit code.

    Args:
        result: Ok or Err from a use case
        json_output: If True, output JSON

    Returns:
        Exit code: 0 for success, 1 for error
    """
    if json_output:
        print(json.dumps(result_to_dict(result)))
        return 0
    if isinstance(result, Ok):
        if result.data is not None:
            print(result.data)
        print("Success")
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1
