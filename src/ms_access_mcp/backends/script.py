"""Shared SQL script parsing utilities.

This module provides the `_parse_script_lines` function used by both ADO and DAO
backends to parse SQL scripts into individual statements.
"""

from __future__ import annotations

__all__ = ["parse_script_lines"]


def parse_script_lines(sql_script: str) -> list[str]:
    """Parse a SQL script into individual statements.

    Strips -- comment lines and splits on semicolons. Empty statements
    are filtered out.

    Args:
        sql_script: Multi-statement SQL script

    Returns:
        List of individual SQL statements (trimmed, non-empty)

    Note:
        This parser does NOT handle semicolons inside string literals.
        This is the same limitation as the oracle implementation.

    """
    lines: list[str] = []
    for line in sql_script.splitlines():
        # Strip -- comments
        stripped = line.split("--")[0]
        if stripped:
            lines.append(stripped)

    # Join lines and split on semicolons
    combined = " ".join(lines)
    statements = combined.split(";")

    # Filter empty statements
    return [stmt.strip() for stmt in statements if stmt.strip()]
