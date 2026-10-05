"""Unit tests for application/db_export.py — connection-string hardening.

Plan 057 — SQL injection hardening for the application-layer export path.
"""

from __future__ import annotations

from ms_access_mcp.application.db_export import _build_iisam_insert_sql, export_data
from ms_access_mcp.errors import Err, Ok
from ms_access_mcp.models import QueryResult


class TestCsvExportStreaming:
    def test_csv_streaming_preserves_exact_output_bytes(self, tmp_path):
        class StreamRows:
            def __init__(self):
                self.rows = [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Zoë"}]

            def __iter__(self):
                return iter(self.rows)

            def __len__(self):
                raise AssertionError("CSV export must count while writing, not materialize rows")

            def __getitem__(self, _index):
                raise AssertionError("CSV export must stream rather than index the row set")

            def __deepcopy__(self, _memo):
                return self

        rows = StreamRows()

        class Adapter:
            def execute_query(self, sql, params=None, limit=None):
                assert sql == "SELECT * FROM [Users]"
                assert params is None and limit is None
                return QueryResult(success=True, rows=rows, count=2, columns=["id", "name"])

        output_path = tmp_path / "users.csv"
        result = export_data(
            Adapter(),
            policy=None,
            allowed_dirs=[tmp_path],
            table_or_query="Users",
            output_path=str(output_path),
            format="csv",
        )

        assert isinstance(result, Ok)
        assert result.rowcount == 2
        assert output_path.read_bytes() == b"id,name\r\n1,Ada\r\n2,Zo\xc3\xab\r\n"


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
        # Must return Err (path validation failure)
        assert isinstance(result, Err), (
            f"Expected Err for path with semicolon, got {type(result).__name__}: {result!r}"
        )
        assert "semicolon" in result.message.lower() or ";" in result.message

    def test_path_with_quote_is_rejected(self):
        """A path containing '"' must be rejected before interpolation."""
        result = _build_iisam_insert_sql("Users", r'C:\out"file\data.xlsx', "Sheet1")
        assert isinstance(result, Err), (
            f"Expected Err for path with quote, got {type(result).__name__}: {result!r}"
        )
        assert '"' in result.message

    def test_legal_path_unchanged(self):
        """A legal path without special characters is interpolated normally."""
        result = _build_iisam_insert_sql("Users", r"C:\exports\data.xlsx", "Sheet1")
        # Result must be Ok(str) and contain the absolute path
        assert isinstance(result, Ok), f"Expected Ok, got {type(result).__name__}: {result!r}"
        sql = result.data  # type: ignore[union-attr]
        assert r"C:\exports\data.xlsx" in sql
        assert "INSERT INTO" in sql
        assert "[Sheet1$]" in sql

    def test_closing_bracket_in_path_is_rejected(self):
        result = _build_iisam_insert_sql("t", r"C:\tmp\we]ird\out.xlsx", "Sheet1")
        assert isinstance(result, Err)

    def test_spaced_apostrophe_path_is_accepted(self):
        result = _build_iisam_insert_sql("t", r"C:\my dir\it's out.xlsx", "Sheet1")
        assert isinstance(result, Ok)
        assert r"DATABASE=C:\my dir\it's out.xlsx" in result.data
