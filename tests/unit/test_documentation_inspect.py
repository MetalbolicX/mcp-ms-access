from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

import pytest

from ms_access_mcp.application.documentation_inspect import (
    DaoInspectionPort,
    DocumentationInspectService,
    InspectionCapabilities,
    InspectionPort,
)
from ms_access_mcp.backends.schema_inspector import QueryParameterInfo
from ms_access_mcp.errors import AccessMcpError
from ms_access_mcp.models import FieldInfo, IndexInfo, QueryInfo, RelationshipInfo, TableInfo


@dataclass
class FakePort:
    tables: list[TableInfo] | Exception
    indexes: dict[str, list[IndexInfo] | Exception]
    relationships: list[RelationshipInfo] | Exception
    queries: list[QueryInfo] | Exception
    link_metadata: dict[str, dict[str, Any] | Exception] | None = None
    query_parameters: dict[str, list[QueryParameterInfo] | Exception] | None = None
    caps: InspectionCapabilities = field(default_factory=InspectionCapabilities)
    closed: bool = False

    def capabilities(self) -> InspectionCapabilities:
        return self.caps

    def list_tables(self) -> list[TableInfo]:
        if isinstance(self.tables, Exception):
            raise self.tables
        return self.tables

    def get_table_link_metadata(self, table_name: str) -> dict[str, Any] | None:
        if self.link_metadata is None:
            return None
        result = self.link_metadata.get(table_name)
        if isinstance(result, Exception):
            raise result
        return result

    def get_query_parameters(self, query_name: str) -> list[QueryParameterInfo] | None:
        if self.query_parameters is None:
            return None
        result = self.query_parameters.get(query_name)
        if isinstance(result, Exception):
            raise result
        return result

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
    link_metadata: dict[str, dict[str, Any] | Exception] | None = None,
    query_parameters: dict[str, list[QueryParameterInfo] | Exception] | None = None,
    caps: InspectionCapabilities | None = None,
) -> FakePort:
    return FakePort(
        tables=[] if tables is None else tables,
        indexes={} if indexes is None else indexes,
        relationships=[] if relationships is None else relationships,
        queries=[] if queries is None else queries,
        link_metadata={} if link_metadata is None else link_metadata,
        query_parameters=query_parameters,
        caps=caps if caps is not None else InspectionCapabilities(),
    )


def inspect_with(fake: FakePort, tables: list[str] | None = None, queries: list[str] | None = None):
    return DocumentationInspectService().inspect(fake, tables=tables, queries=queries)


def test_fake_port_signatures_match_production_port() -> None:
    for method_name in (
        "capabilities",
        "list_tables",
        "get_table_link_metadata",
        "get_indexes",
        "get_relationships",
        "get_queries",
        "get_query_parameters",
        "close",
    ):
        assert inspect.signature(getattr(FakePort, method_name)) == inspect.signature(
            getattr(DaoInspectionPort, method_name)
        )
        assert inspect.signature(getattr(FakePort, method_name)) == inspect.signature(
            getattr(InspectionPort, method_name)
        )


def test_field_defaults_map_to_observed_facts_without_gaps() -> None:
    """F1: FieldInfo defaults become observed facts; a real default is never null/unavailable."""
    document = inspect_with(
        port(
            tables=[
                TableInfo(
                    "T",
                    fields=[
                        FieldInfo("Status", "Text", default_value='"Active"'),
                        FieldInfo("Name", "Text", default_value=""),
                    ],
                )
            ],
            indexes={"T": []},
        )
    )
    defaults = {item.name: item.default_value for item in document.tables[0].fields}
    assert defaults["Status"].state == "observed" and defaults["Status"].value == '"Active"'
    assert defaults["Name"].state == "observed" and defaults["Name"].value == ""
    # Readable defaults are real capability, so they must not force partial coverage.
    assert document.status == "complete"


def test_field_default_none_maps_to_unavailable_while_real_defaults_stay_observed() -> None:
    """A port that does not expose a default must not fabricate `observed null` at the seam."""
    document = inspect_with(
        port(
            tables=[
                TableInfo(
                    "T",
                    fields=[
                        FieldInfo("NotExposed", "Text", default_value=None),
                        FieldInfo("Status", "Text", default_value='"Active"'),
                    ],
                )
            ],
            indexes={"T": []},
        )
    )
    defaults = {item.name: item.default_value for item in document.tables[0].fields}
    missing = defaults["NotExposed"]
    assert missing.state == "unavailable"
    assert missing.reason and missing.reason.strip()
    assert missing.provenance.object_kind == "field"
    assert missing.provenance.object_name == "NotExposed"
    assert missing.provenance.property == "default_value"
    real = defaults["Status"]
    assert real.state == "observed" and real.value == '"Active"'
    # An unexposed default is a real gap, so it must not let the document report complete.
    assert document.status == "partial"


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


def test_query_parameter_metadata_reaches_evidence_with_name_type_and_dao_type_name() -> None:
    """T1: a port that exposes parameters produces an observed, closed gap-free fact."""
    document = inspect_with(
        port(
            queries=[QueryInfo("qryOrdersByCustomer", "PARAMETERS [pCustomerID] Long; SELECT 1", "select")],
            query_parameters={
                "qryOrdersByCustomer": [QueryParameterInfo("[pCustomerID]", 4, "Long Integer")]
            },
        )
    )
    parameters = document.queries[0].parameters
    assert parameters.state == "observed"
    assert parameters.value == [{"name": "[pCustomerID]", "type": 4, "type_name": "Long Integer"}]
    assert parameters.provenance.object_kind == "query"
    assert parameters.provenance.object_name == "qryOrdersByCustomer"
    assert parameters.provenance.property == "parameters"
    # Parameters are readable here, so this is not a coverage gap.
    assert document.coverage.queries.unsupported == {}


def test_query_parameters_none_maps_to_unavailable_at_the_seam() -> None:
    """A port that cannot supply parameters (None) must map to unavailable, never empty/null."""
    document = inspect_with(port(queries=[QueryInfo("Q", "SELECT 1", "select")], query_parameters=None))
    parameters = document.queries[0].parameters
    assert parameters.state == "unavailable"
    assert parameters.reason and parameters.reason.strip()
    assert parameters.provenance.property == "parameters"
    assert document.coverage.queries.unsupported["Q"]
    assert document.status == "partial"


def test_zero_parameter_query_is_observed_empty_not_unavailable() -> None:
    """A DAO-exposed zero-parameter query is a known empty, a different thing from unknown."""
    document = inspect_with(port(queries=[QueryInfo("qryOpenOrders", "SELECT 1", "select")], query_parameters={"qryOpenOrders": []}))
    assert document.queries[0].parameters.state == "observed"
    assert document.queries[0].parameters.value == []
    assert document.coverage.queries.unsupported == {}


def test_query_parameter_read_failure_is_failed_not_fabricated_empty() -> None:
    """A failing parameter read is an explicit failed fact, not an empty success."""
    document = inspect_with(
        port(
            queries=[QueryInfo("Q", "SELECT 1", "select")],
            query_parameters={"Q": RuntimeError("parameter read failed")},
        )
    )
    parameters = document.queries[0].parameters
    assert parameters.state == "failed"
    assert "parameter read failed" in (parameters.reason or "")
    assert document.coverage.queries.failed["Q"]
    assert document.status == "partial"


def _reduced_capabilities() -> InspectionCapabilities:
    """Return an ODBC-like capability set naming every property it cannot observe."""
    return InspectionCapabilities(
        backend_identity="ODBC",
        unavailable={
            "field_required": "required is not exposed",
            "field_allow_zero_length": "allow-zero-length is not exposed",
            "primary_key": "the primary index is not flagged",
            "relationships": "relationships are not exposed",
            "query_sql": "saved-query SQL is not exposed",
            "query_type": "saved-query category is not exposed",
        },
    )


def test_reduced_capabilities_map_only_their_dependent_facts_to_unavailable() -> None:
    """T2: an ODBC-like port records its gaps as `unavailable`, never as fabricated values."""
    fake = port(
        tables=[
            TableInfo(
                "T",
                fields=[
                    FieldInfo(
                        "id",
                        "INTEGER",
                        size=10,
                        required=True,
                        allow_zero_length=False,
                        default_value="0",
                    )
                ],
            )
        ],
        indexes={"T": [IndexInfo("PK_T", ["id"], is_unique=True, is_primary=True)]},
        # A read here would be a bug: the capability gap must short-circuit it.
        relationships=RuntimeError("relationships must not be read when unavailable"),
        queries=[QueryInfo("Q", "SELECT 1", "select")],
        query_parameters={"Q": [QueryParameterInfo("[p]", 4, "INTEGER")]},
        caps=_reduced_capabilities(),
    )
    document = inspect_with(fake)
    assert document.source.backend_identity == "ODBC"
    field = document.tables[0].fields[0]
    assert field.type.state == "observed" and field.type.value == "INTEGER"
    assert field.size.state == "observed" and field.size.value == 10
    assert field.required.state == "unavailable" and field.required.reason == "required is not exposed"
    assert field.allow_zero_length.state == "unavailable"
    assert field.allow_zero_length.reason == "allow-zero-length is not exposed"
    # An exposed default is still observed: the capability only gates its own fact.
    assert field.default_value.state == "observed" and field.default_value.value == "0"
    assert document.tables[0].primary_key.state == "unavailable"
    assert document.tables[0].primary_key.reason == "the primary index is not flagged"
    assert document.coverage.tables.unsupported["T"] == "the primary index is not flagged"
    assert document.relationships == []
    assert document.coverage.relationships.unsupported["relationships"] == "relationships are not exposed"
    assert document.coverage.relationships.failed == {}
    assert document.queries[0].sql.state == "unavailable"
    assert document.queries[0].sql.reason == "saved-query SQL is not exposed"
    assert document.queries[0].type.state == "unavailable"
    assert document.queries[0].type.reason == "saved-query category is not exposed"
    assert document.queries[0].parameters.state == "observed"
    assert document.status == "partial"


def test_capability_reason_ignores_blank_reasons_and_unknown_capabilities() -> None:
    capabilities = InspectionCapabilities(backend_identity="ODBC", unavailable={"primary_key": "   "})
    assert capabilities.reason("primary_key") is None
    assert capabilities.reason("field_size") is None
    assert InspectionCapabilities().backend_identity == "DAO"
    assert InspectionCapabilities().reason("primary_key") is None


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


def test_relationship_outside_the_requested_table_subset_is_excluded_not_kept() -> None:
    """A table subset scopes the relationship catalogue to endpoints it selected.

    ``tables=None`` selects every table and every relationship stays inspected (the
    PRESERVE half of this test). With an explicit subset, a relationship whose parent or
    child table was not selected is deliberately not inspected: keeping it would place a
    relationship in evidence that references a table absent from the document, which the
    deterministic renderer cannot resolve to a Mermaid node. The honest state is
    ``excluded`` with a scope reason, so the omission is visible in coverage instead of
    silently dropping the relationship.
    """
    relationships = [
        RelationshipInfo("RIn", "Child", "Parent", "1", columns=["parent_id"], foreign_columns=["id"]),
        RelationshipInfo("ROut", "Other", "Parent", "1", columns=["parent_id"], foreign_columns=["id"]),
    ]
    subset_document = inspect_with(
        port(tables=[TableInfo("Parent"), TableInfo("Child"), TableInfo("Other")], relationships=relationships),
        tables=["Parent", "Child"],
    )
    assert [item.name for item in subset_document.relationships] == ["RIn"]
    assert subset_document.coverage.relationships.inspected == ["RIn"]
    assert "subset" in subset_document.coverage.relationships.excluded["ROut"]
    selected = {"Parent", "Child"}
    for relationship in subset_document.relationships:
        assert relationship.parent_table in selected and relationship.child_table in selected

    # PRESERVE: without an explicit subset no relationship is dropped.
    unfiltered_document = inspect_with(
        port(tables=[TableInfo("Parent"), TableInfo("Child"), TableInfo("Other")], relationships=relationships)
    )
    assert [item.name for item in unfiltered_document.relationships] == ["RIn", "ROut"]
    assert unfiltered_document.coverage.relationships.excluded == {}


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


def test_linked_table_local_catalogue_metadata_reaches_evidence() -> None:
    fake = port(
        tables=[
            TableInfo("LinkedCustomers", fields=[FieldInfo("CustomerID", "Long Integer")]),
            TableInfo("LocalNotes", fields=[FieldInfo("NotesID", "Long Integer")]),
        ],
        indexes={"LinkedCustomers": [], "LocalNotes": []},
        link_metadata={
            "LinkedCustomers": {
                "connect": ";DATABASE=C:\\fixtures\\sentinel.accdb",
                "source_table_name": "SentinelCustomers",
            }
        },
    )
    linked, local = inspect_with(fake).tables
    assert linked.kind == "linked"
    assert linked.connect == ";DATABASE=C:\\fixtures\\sentinel.accdb"
    assert linked.source_table_name == "SentinelCustomers"
    assert linked.to_dict()["kind"] == "linked"
    assert local.kind == "local"
    assert local.connect is None and local.source_table_name is None


def test_link_metadata_read_failure_marks_the_table_failed() -> None:
    fake = port(
        tables=[TableInfo("LinkedCustomers")],
        indexes={"LinkedCustomers": []},
        link_metadata={"LinkedCustomers": RuntimeError("link metadata read failed")},
    )
    document = inspect_with(fake)
    assert document.tables[0].name == "LinkedCustomers"
    assert "link metadata read failed" in document.coverage.tables.failed["LinkedCustomers"]
    assert document.status == "partial"


def test_port_close_runs_even_when_enumeration_raises() -> None:
    fake = port(tables=RuntimeError("enumeration broke"))
    with pytest.raises(Exception, match="enumeration broke"):
        inspect_with(fake)
    assert fake.closed
