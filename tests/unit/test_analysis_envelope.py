"""Envelope contract tests for query analysis."""

from __future__ import annotations

from typing import Any

from ms_access_mcp.application.analysis import QueryAnalyzerService
from ms_access_mcp.errors import Err, Ok


class FakeDatabaseUseCases:
    def __init__(self, *, schema_result: Any = None, query_result: Any = None) -> None:
        self.schema_result = schema_result or Ok(
            data={"name": "Users", "columns": [{"name": "id", "type": "Long Integer"}]}
        )
        self.query_result = query_result or Ok(
            data=[{"id": row_id} for row_id in range(20)], rowcount=20
        )
        self.schema_calls: list[tuple[str, str]] = []
        self.schema_batch_calls: list[tuple[list[str], str]] = []
        self.query_calls: list[tuple[str, str | None, str]] = []

    def get_table_schema(self, table_name: str, connection_name: str = "default") -> Any:
        self.schema_calls.append((table_name, connection_name))
        return self.schema_result

    def get_schemas_for(self, table_names: list[str], connection_name: str = "default") -> Any:
        self.schema_batch_calls.append((table_names, connection_name))
        return Ok(data=dict.fromkeys(table_names, self.schema_result.data))

    def query_data(
        self,
        sql: str,
        params: list[Any] | None = None,
        connection_name: str = "default",
    ) -> Any:
        self.query_calls.append((sql, connection_name if params is None else str(params), connection_name))
        return self.query_result


def _analyze(database: FakeDatabaseUseCases, **kwargs: Any) -> dict[str, Any]:
    return QueryAnalyzerService.analyze(
        sql="SELECT id FROM Users",
        params=None,
        db_use_cases=database,
        **kwargs,
    )


def test_schema_is_populated_from_ok_data():
    database = FakeDatabaseUseCases()

    result = _analyze(database, dry_run=True)

    assert result["schema"]["USERS"]["columns"] == [{"name": "id", "type": "Long Integer"}]


def test_multi_table_analysis_fetches_schemas_in_one_batch(monkeypatch):
    from ms_access_mcp.application.analysis import SQLComplexityAnalyzer, SQLParseResult

    monkeypatch.setattr(
        SQLComplexityAnalyzer,
        "parse",
        classmethod(lambda cls, sql: SQLParseResult(sql=sql, statement_type="SELECT", tables_involved=["USERS", "ORDERS"])),
    )
    database = FakeDatabaseUseCases()

    result = QueryAnalyzerService.analyze(
        sql="SELECT id FROM Users JOIN Orders ON Users.id = Orders.user_id",
        params=None,
        db_use_cases=database,
        dry_run=True,
    )

    assert result["tables_involved"] == ["USERS", "ORDERS"]
    assert database.schema_batch_calls == [(["USERS", "ORDERS"], "default")]


def test_successful_query_reports_execution_timing():
    result = _analyze(FakeDatabaseUseCases())

    assert result["timing"]["executed"] is True
    assert isinstance(result["timing"]["duration_ms"], (float, int))
    assert result["timing"]["duration_ms"] >= 0


def test_query_error_envelope_is_an_explicit_failure():
    result = _analyze(FakeDatabaseUseCases(query_result=Err("query denied")))

    assert result["success"] is False
    assert "query denied" in result["error"]
    assert result["timing"]["executed"] is False
    assert "query denied" in result["timing"]["error"]


def test_sample_size_limits_rows_used_for_analysis():
    database = FakeDatabaseUseCases()

    result = _analyze(database, sample_size=5)

    assert result["timing"]["sampled_rows"] == 5
    assert result["timing"]["rowcount"] == 20


def test_connection_name_round_trips_through_result_and_database_calls():
    database = FakeDatabaseUseCases()

    result = _analyze(database, connection_name="reporting")

    assert result["connection_name"] == "reporting"
    assert database.schema_batch_calls == [(["USERS"], "reporting")]
    assert database.query_calls[0][2] == "reporting"
