"""Unit tests for application/db_export.py — connection-string hardening.

Plan 057 — SQL injection hardening for the application-layer export path.
"""

from __future__ import annotations

from ms_access_mcp.application.db_export import _build_iisam_insert_sql
from ms_access_mcp.errors import Err, Ok


class TestBuildIisamInsertSqlConnectionStringHardening:
    r"""Connection string must not allow path-injection via ';' or '"' characters.

    The IISAM connection string format is:
        [Excel 12.0;HDR=YES;DATABASE=<abs_path>].[<sheet>$]
    A path containing ';' can inject additional connection properties, e.g.
        C:\out;HDR=NO\x.mdb  ->  DATABASE=C:\out;HDR=NO  (HDR overridden!)
    A path containing '"' can close the DATABASE value early and inject.
    """

    def test_path_with_semicolon_is_rejected(self):
        """A path containing ';' in any segment must be rejected before interpolation."""
        result = _build_iisam_insert_sql("Users", r"C:\out;HDR=NO\data.xlsx", "Sheet1")
        # Either returns Err, or the returned SQL does not contain the raw path
        if isinstance(result, Err):
            assert "semicolon" in result.message.lower() or ";" in result.message
        else:
            sql = result if isinstance(result, str) else str(result)
            assert r"C:\out;HDR=NO\data.xlsx" not in sql, (
                f"Path with semicolon appears raw in SQL: {sql!r}"
            )

    def test_path_with_quote_is_rejected(self):
        """A path containing '\"' must be rejected before interpolation."""
        result = _build_iisam_insert_sql("Users", r'C:\out"file\data.xlsx', "Sheet1")
        if isinstance(result, Err):
            assert '"' in result.message
        else:
            sql = result if isinstance(result, str) else str(result)
            assert r'C:\out"file\data.xlsx' not in sql, (
                f"Path with quote appears raw in SQL: {sql!r}"
            )

    def test_legal_path_unchanged(self):
        """A legal path without special characters is interpolated normally."""
        result = _build_iisam_insert_sql("Users", r"C:\exports\data.xlsx", "Sheet1")
        # Result must be Ok(str) and contain the absolute path
        assert isinstance(result, str), f"Expected str, got {type(result).__name__}: {result!r}"
        assert r"C:\exports\data.xlsx" in result
        assert "INSERT INTO" in result
        assert "[Sheet1$]" in result
