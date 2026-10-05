"""Shared SQL script parsing utilities.

This module provides the `_parse_script_lines` function used by both ADO and DAO
backends to parse SQL scripts into individual statements.
"""

from __future__ import annotations

__all__ = ["parse_script_lines"]


def parse_script_lines(sql_script: str) -> list[str]:
    """Parse a SQL script into individual statements.

    Strips -- comments outside string literals and splits on semicolons
    outside string literals. Empty statements are filtered out. A semicolon
    appearing after -- on the same line is part of the comment and is not
    treated as a statement terminator; content after that comment marker is
    discarded.

    Args:
        sql_script: Multi-statement SQL script

    Returns:
        List of individual SQL statements (trimmed, non-empty)

    Note:
        Single-quoted literals and Jet-style doubled single quotes are
        recognized. Other SQL quoting forms are not interpreted.

    """
    statements: list[str] = []
    current: list[str] = []
    in_literal = False
    in_comment = False
    index = 0

    while index < len(sql_script):
        char = sql_script[index]

        if in_comment:
            if char in "\r\n":
                in_comment = False
                current.append(" ")
                if char == "\r" and index + 1 < len(sql_script) and sql_script[index + 1] == "\n":
                    index += 1
            index += 1
            continue

        if in_literal:
            current.append(char)
            if char == "'":
                if index + 1 < len(sql_script) and sql_script[index + 1] == "'":
                    current.append("'")
                    index += 1
                else:
                    in_literal = False
            index += 1
            continue

        if char == "'":
            in_literal = True
            current.append(char)
        elif char == "-" and index + 1 < len(sql_script) and sql_script[index + 1] == "-":
            in_comment = True
            index += 1
        elif char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current.clear()
        elif char in "\r\n":
            if char == "\r" and index + 1 < len(sql_script) and sql_script[index + 1] == "\n":
                index += 1
            current.append(" ")
        else:
            current.append(char)
        index += 1

    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements
