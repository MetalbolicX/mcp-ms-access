from __future__ import annotations

import inspect
from dataclasses import dataclass

import pytest

from ms_access_mcp.application.documentation_inspect import (
    DaoInspectionPort,
    DocumentationInspectService,
    InspectionPort,
)
from ms_access_mcp.errors import AccessMcpError
from ms_access_mcp.models import FieldInfo, IndexInfo, QueryInfo, RelationshipInfo, TableInfo


@dataclass
class FakePort:
    tables: list[TableInfo] | Exception
    indexes: dict[str, list[IndexInfo] | Exception]
    relationships: list[RelationshipInfo] | Exception
    queries: list[QueryInfo] | Exception
    closed: bool = False

    def list_tables(self) -> list[TableInfo]:
        if isinstance(self.tables, Exception):
            raise self.tables
        return self.tables

    def get_indexes(self, table_name: str) -> list[IndexInfo]:
        result = self.indexes.get(table_name, [])
        if isinstance(result, Exception):
            raise result
        return result

    def get_relationships(self) -> list[RelationshipInfo]:
        if isinstance(self.relationships, Exception):
            raise self.relationships
        return self.relationships

    def get_queries(self) -> list[QueryInfo]:
        if isinstance(self.queries, Exception):
            raise self.queries
        return self.queries

    def close(self) -> None:
        self.closed = True


def port(
    *,
    tables: list[TableInfo] | Exception | None = None,
    indexes: dict[str, list[IndexInfo] | Exception] | None = None,
    relationships: list[RelationshipInfo] | Exception | None = None,
    queries: list[QueryInfo] | Exception | None = None,
) -> FakePort:
    return FakePort(
        tables=[] if tables is None else tables,
        indexes={} if indexes is None else indexes,
        relationships=[] if relationships is None else relationships,
        queries=[] if queries is None else queries,
    )


def inspect_with(fake: FakePort, tables: list[str] | None = None, queries: list[str] | None = None):
    return DocumentationInspectService().inspect(fake, tables=tables, queries=queries)


def test_fake_port_signatures_match_production_port() -> None:
    for method_name in ("list_tables", "get_indexes", "get_relationships", "get_queries", "close"):
        assert inspect.signature(getattr(FakePort, method_name)) == inspect.signature(
            getattr(DaoInspectionPort, method_name)
        )
        assert inspect.signature(getattr(FakePort, method_name)) == inspect.signature(
            getattr(InspectionPort, method_name)
        )


def test_unknown_requested_names_raise_and_list_names() -> None:
    fake = port(tables=[TableInfo("Customers")], queries=[QueryInfo("QKnown", "SELECT 1", "select")])
    with pytest.raises(AccessMcpError, match="MissingTable"):
        inspect_with(fake, tables=["MissingTable"])
    assert fake.closed
    fake = port(tables=[TableInfo("Customers")], queries=[QueryInfo("QKnown", "SELECT 1", "select")])
    with pytest.raises(AccessMcpError, match="MissingQuery"):
        inspect_with(fake, queries=["MissingQuery"])
    assert fake.closed


def test_none_selects_all_and_empty_list_selects_none() -> None:
    all_port = port(
        tables=[TableInfo("Customers"), TableInfo("MSysHidden"), TableInfo("~temp")],
        queries=[QueryInfo("Q1", "SELECT 1", "select"), QueryInfo("MSysQ", "SELECT 2", "select")],
    )
    all_doc = inspect_with(all_port)
    assert [table.name for table in all_doc.tables] == ["Customers"]
    assert [query.name for query in all_doc.queries] == ["Q1"]
    none_doc = inspect_with(port(tables=[TableInfo("Customers")], queries=[QueryInfo("Q1", "SELECT 1", "select")]), tables=[], queries=[])
    assert none_doc.tables == []
    assert none_doc.queries == []


def test_primary_key_is_derived_from_index_not_table_info() -> None:
    document = inspect_with(port(
        tables=[TableInfo("T", fields=[FieldInfo("id", "Long")], primary_key=[])],
        indexes={"T": [IndexInfo("Primary", ["id"], is_unique=True, is_primary=True)]},
    ))
    assert document.tables[0].primary_key.state == "observed"
    assert document.tables[0].primary_key.value == ["id"]


def test_index_failure_is_recorded_and_other_table_continues() -> None:
    fake = port(
        tables=[TableInfo("Broken"), TableInfo("Good")],
        indexes={"Broken": RuntimeError("index read failed"), "Good": []},
    )
    document = inspect_with(fake)
    assert document.tables[0].primary_key.state == "failed"
    assert "index read failed" in document.tables[0].primary_key.reason
    assert document.tables[1].primary_key.state == "observed"
    assert document.coverage.tables.failed["Broken"]
    assert document.status == "partial"


def test_query_sql_exception_is_failed_not_blank_and_type_mapping_is_coarse() -> None:
    document = inspect_with(port(queries=[
        QueryInfo("Failed", "", "select"),
        QueryInfo("Select", "SELECT 1", "select"),
        QueryInfo("Action", "UPDATE T SET X=1", "action"),
        QueryInfo("DDL", "CREATE TABLE T (X INT)", "ddl"),
        QueryInfo("Other", "CALL X", "procedure"),
    ]))
    queries = {query.name: query for query in document.queries}
    assert queries["Failed"].sql.state == "failed"
    assert queries["Failed"].sql.reason
    assert {name: queries[name].type.value for name in ("Select", "Action", "DDL", "Other")} == {
        "Select": "select", "Action": "action", "DDL": "ddl", "Other": "unknown"
    }
    assert all(query.parameters.state == "unavailable" for query in queries.values())


def test_composite_relationship_pairs_are_preserved_and_attributes_unavailable() -> None:
    document = inspect_with(port(relationships=[RelationshipInfo(
        "R", "Child", "Parent", "1", columns=["c1", "c2"], foreign_columns=["p1", "p2"]
    )]))
    relationship = document.relationships[0]
    assert list(zip(relationship.parent_columns, relationship.child_columns, strict=True)) == [("p1", "c1"), ("p2", "c2")]
    assert relationship.parent_table == "Parent"
    assert relationship.child_table == "Child"
    assert relationship.attributes.state == "unavailable"
    assert relationship.attributes.reason


def test_total_read_failures_are_coverage_failures_and_close_runs_on_exception() -> None:
    fake = port(relationships=RuntimeError("relationships failed"), queries=RuntimeError("queries failed"))
    document = inspect_with(fake)
    assert document.coverage.relationships.failed
    assert document.coverage.queries.failed
    assert document.status == "partial"
    assert fake.closed


def test_coverage_counts_include_inspected_excluded_unsupported_and_failed() -> None:
    fake = port(tables=[TableInfo("T"), TableInfo("MSysT")], indexes={"T": RuntimeError("bad")})
    document = inspect_with(fake, tables=["T"])
    assert len(document.coverage.tables.inspected) == 1
    assert len(document.coverage.tables.excluded) == 1
    assert len(document.coverage.tables.unsupported) == 0
    assert len(document.coverage.tables.failed) == 1


def test_port_close_runs_even_when_enumeration_raises() -> None:
    fake = port(tables=RuntimeError("enumeration broke"))
    with pytest.raises(Exception, match="enumeration broke"):
        inspect_with(fake)
    assert fake.closed
