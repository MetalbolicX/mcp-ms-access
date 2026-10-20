from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from ms_access_mcp.application.documentation_inspect import (
    DaoInspectionPort,
    DocumentationInspectService,
    InspectionCapabilities,
    InspectionCapabilityUnavailableError,
    InspectionPort,
    OdbcInspectionPort,
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
    container_objects: dict[str, Any] | None = None
    database_properties: dict[str, str] | Exception | None = None
    # Sanitized warnings the port recorded while reading the property catalogue (S2 A3).
    property_omissions: list[str] = field(default_factory=list)
    # Records every engine-level app-logic read, so "default off means never read" is
    # observable rather than inferred.
    app_logic_calls: list[str] = field(default_factory=list)

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

    def list_container_objects(self, container_name: str) -> list[dict[str, Any]]:
        self.app_logic_calls.append(f"container:{container_name}")
        if self.container_objects is None:
            # The default double models an engine (ODBC) with no DAO Containers surface.
            raise InspectionCapabilityUnavailableError(
                "Access ODBC does not expose DAO Containers; engine-level container inventory requires the DAO engine"
            )
        result = self.container_objects.get(container_name, [])
        if isinstance(result, Exception):
            raise result
        return result

    def get_database_properties(self) -> dict[str, str]:
        self.app_logic_calls.append("properties")
        if self.database_properties is None:
            # The default double models an engine (ODBC) with no DAO Properties surface.
            raise InspectionCapabilityUnavailableError(
                "Access ODBC does not expose DAO database properties; startup properties require the DAO engine"
            )
        if isinstance(self.database_properties, Exception):
            raise self.database_properties
        return self.database_properties

    def database_property_omissions(self) -> list[str]:
        return list(self.property_omissions)

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
    container_objects: dict[str, Any] | None = None,
    database_properties: dict[str, str] | Exception | None = None,
    database_property_omissions: list[str] | None = None,
) -> FakePort:
    return FakePort(
        tables=[] if tables is None else tables,
        indexes={} if indexes is None else indexes,
        relationships=[] if relationships is None else relationships,
        queries=[] if queries is None else queries,
        link_metadata={} if link_metadata is None else link_metadata,
        query_parameters=query_parameters,
        caps=caps if caps is not None else InspectionCapabilities(),
        container_objects=container_objects,
        database_properties=database_properties,
        property_omissions=[] if database_property_omissions is None else database_property_omissions,
    )


def inspect_with(
    fake: FakePort,
    tables: list[str] | None = None,
    queries: list[str] | None = None,
    *,
    include_query_sql: bool = False,
    include_app_logic: bool = False,
):
    return DocumentationInspectService().inspect(
        fake,
        tables=tables,
        queries=queries,
        include_query_sql=include_query_sql,
        include_app_logic=include_app_logic,
    )


def test_fake_port_signatures_match_production_port() -> None:
    for method_name in (
        "capabilities",
        "list_tables",
        "get_table_link_metadata",
        "get_indexes",
        "get_relationships",
        "get_queries",
        "get_query_parameters",
        "database_property_omissions",
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
    document = inspect_with(
        port(
            queries=[
                QueryInfo("Failed", "", "select"),
                QueryInfo("Select", "SELECT 1", "select"),
                QueryInfo("Action", "UPDATE T SET X=1", "action"),
                QueryInfo("DDL", "CREATE TABLE T (X INT)", "ddl"),
                QueryInfo("Other", "CALL X", "procedure"),
            ]
        ),
        include_query_sql=True,
    )
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
    """T2: an ODBC-like port records its gaps as `unavailable`, never as fabricated values.

    The call opts in with ``include_query_sql=True`` (the default excludes SQL first), so
    the existing ODBC SQL-unavailable expectation stays observable.
    """
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
    document = inspect_with(fake, include_query_sql=True)
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


# ---------------------------------------------------------------------------
# Saved-query SQL opt-in: include_query_sql (default false)
# ---------------------------------------------------------------------------


def test_query_sql_is_excluded_by_default_and_hidden_from_emitted_evidence() -> None:
    """Default off: the emitted SQL fact is ``excluded``, not a gap and not the SQL text.

    The exclusion is a deliberate output policy applied before any capability check, so
    the emitted evidence never carries the SQL text even though ``get_queries`` (the
    metadata enumeration that also supplies names/type) still returns it internally.
    """
    document = inspect_with(
        port(
            queries=[QueryInfo("Q", "SELECT secret FROM T", "select")],
            query_parameters={"Q": []},
        )
    )
    sql = document.queries[0].sql
    assert sql.state == "excluded"
    assert sql.reason and sql.reason.strip()
    assert sql.provenance.object_kind == "query"
    assert sql.provenance.object_name == "Q"
    assert sql.provenance.property == "sql"
    assert "SELECT secret FROM T" not in json.dumps(document.to_dict())
    # Exclusion of the SQL fact is not a coverage failure and leaves query facts intact.
    assert document.coverage.queries.excluded == {}
    assert document.coverage.queries.unsupported == {}
    assert document.coverage.queries.failed == {}
    assert document.queries[0].type.state == "observed" and document.queries[0].type.value == "select"
    assert document.queries[0].parameters.state == "observed"
    assert document.status == "complete"


def test_exclusion_wins_over_a_query_sql_capability_gap() -> None:
    """R3: exclusion happens before the capability check, so ODBC reports excluded by default."""
    document = inspect_with(
        port(
            queries=[QueryInfo("Q", "SELECT 1", "select")],
            query_parameters={"Q": []},
            caps=_reduced_capabilities(),
        )
    )
    sql = document.queries[0].sql
    assert sql.state == "excluded"
    assert "not exposed" not in (sql.reason or "")
    assert document.queries[0].type.state == "unavailable"
    assert document.queries[0].type.reason == "saved-query category is not exposed"


def test_include_query_sql_opt_in_preserves_observed_dao_sql() -> None:
    """On: the DAO port keeps its existing observed/failed SQL semantics."""
    observed = inspect_with(
        port(queries=[QueryInfo("Q", "SELECT 1", "select")], query_parameters={"Q": []}),
        include_query_sql=True,
    )
    assert observed.queries[0].sql.state == "observed"
    assert observed.queries[0].sql.value == "SELECT 1"
    failed = inspect_with(
        port(queries=[QueryInfo("Q", "", "select")], query_parameters={"Q": []}),
        include_query_sql=True,
    )
    assert failed.queries[0].sql.state == "failed"
    assert failed.queries[0].sql.reason


def test_include_query_sql_opt_in_keeps_odbc_sql_unavailable() -> None:
    """On: an ODBC-capability port still reports the capability reason as ``unavailable``."""
    document = inspect_with(
        port(
            queries=[QueryInfo("Q", "SELECT 1", "select")],
            query_parameters={"Q": []},
            caps=_reduced_capabilities(),
        ),
        include_query_sql=True,
    )
    assert document.queries[0].sql.state == "unavailable"
    assert document.queries[0].sql.reason == "saved-query SQL is not exposed"


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


def test_linked_table_is_classified_before_index_reads_and_reports_explicit_gap() -> None:
    """T1: a linked table is classified first; its fields/indexes are never read."""
    fake = port(
        tables=[
            TableInfo("LinkedCustomers", fields=[FieldInfo("CustomerID", "Long Integer")]),
            TableInfo("LocalNotes", fields=[FieldInfo("NotesID", "Long Integer")]),
        ],
        indexes={
            "LinkedCustomers": RuntimeError("linked index read must not run"),
            "LocalNotes": [IndexInfo("PK_LocalNotes", ["NotesID"], is_unique=True, is_primary=True)],
        },
        link_metadata={
            "LinkedCustomers": {
                "connect": ";DATABASE=C:\\fixtures\\sentinel.accdb",
                "source_table_name": "SentinelCustomers",
            }
        },
    )
    document = inspect_with(fake)
    linked, local = document.tables
    assert linked.kind == "linked"
    assert linked.connect == ";DATABASE=C:\\fixtures\\sentinel.accdb"
    assert linked.source_table_name == "SentinelCustomers"
    assert linked.fields == []
    assert linked.indexes == []
    assert linked.primary_key.state == "unavailable"
    assert linked.primary_key.reason
    assert "linked" in document.coverage.tables.unsupported["LinkedCustomers"].lower()
    # The linked index read never ran, so no failure was recorded.
    assert document.coverage.tables.failed == {}
    # PRESERVE: the ordinary local table keeps its fields and index-derived primary key.
    assert [field.name for field in local.fields] == ["NotesID"]
    assert local.indexes[0].name == "PK_LocalNotes"
    assert local.primary_key.state == "observed" and local.primary_key.value == ["NotesID"]
    assert local.kind == "local"
    assert document.status == "partial"


def test_unreadable_index_capability_is_unsupported_not_failed() -> None:
    """T1: a port refusing an index read as a known capability gap is ``unsupported``."""
    fake = port(
        tables=[TableInfo("T", fields=[FieldInfo("id", "Long Integer")])],
        indexes={"T": InspectionCapabilityUnavailableError("indexes are not exposed")},
    )
    document = inspect_with(fake)
    table = document.tables[0]
    assert table.kind == "local"
    assert [field.name for field in table.fields] == ["id"]
    assert table.primary_key.state == "unavailable"
    assert table.primary_key.reason == "indexes are not exposed"
    assert document.coverage.tables.unsupported["T"] == "indexes are not exposed"
    assert document.coverage.tables.failed == {}


def test_link_metadata_credentials_are_removed_before_evidence() -> None:
    """T1: a synthetic credential in a connect string never reaches evidence."""
    fake = port(
        tables=[TableInfo("LinkedCustomers")],
        link_metadata={
            "LinkedCustomers": {
                "connect": ";DATABASE=C:\\fixtures\\sentinel.accdb;UID=canary-user;PWD=canary-secret",
                "source_table_name": "SentinelCustomers",
            }
        },
    )
    document = inspect_with(fake)
    linked = document.tables[0]
    assert "canary-secret" not in linked.connect
    assert "canary-user" not in linked.connect
    assert "PWD" not in linked.connect and "UID" not in linked.connect
    assert "sentinel.accdb" in linked.connect
    assert "canary-secret" not in json.dumps(document.to_dict())


def test_credentials_in_a_read_error_are_redacted_from_coverage() -> None:
    """T1: a credential embedded in an error message is redacted before coverage."""
    fake = port(
        tables=[TableInfo("T")],
        link_metadata={"T": RuntimeError("open failed for ;DATABASE=x.accdb;PWD=canary-secret")},
    )
    document = inspect_with(fake)
    reason = document.coverage.tables.failed["T"]
    assert "canary-secret" not in reason
    assert "PWD=***" in reason
    assert document.tables[0].primary_key.reason == reason


def test_malformed_connect_string_fails_safe_without_leaking_the_value() -> None:
    """T1: an unparseable braced value is replaced, never partially leaked."""
    fake = port(
        tables=[TableInfo("LinkedCustomers")],
        link_metadata={
            "LinkedCustomers": {
                "connect": ";DATABASE=x;PWD={unbalanced;canary-secret",
                "source_table_name": "SentinelCustomers",
            }
        },
    )
    document = inspect_with(fake)
    linked = document.tables[0]
    assert linked.kind == "linked"
    assert "canary-secret" not in linked.connect


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


# ---------------------------------------------------------------------------
# Tier 1 engine-level inventory: include_app_logic (default false)
# ---------------------------------------------------------------------------


def _app_logic_port(
    *,
    container_objects: dict[str, Any] | None = None,
    database_properties: dict[str, str] | Exception | None = None,
    database_property_omissions: list[str] | None = None,
) -> FakePort:
    return port(
        container_objects=container_objects,
        database_properties=database_properties,
        database_property_omissions=database_property_omissions,
    )


def test_include_app_logic_defaults_off_and_keeps_the_v1_evidence_shape() -> None:
    """Default off (S4/agreement 10): no new category, no new warning, no app-logic read.

    The v1 evidence shape is pinned byte-for-byte at the key/category level: the top
    level is unchanged and ``data`` still carries exactly status/tables/relationships/
    queries. The fake records every engine-level app-logic read, so "never read when
    off" is observed rather than inferred.
    """
    fake = _app_logic_port()
    document = inspect_with(fake, include_app_logic=False)
    payload = document.to_dict()
    assert set(payload) == {"schema", "run", "source", "coverage", "warnings", "data"}
    assert set(payload["data"]) == {"status", "tables", "relationships", "queries"}
    assert payload["warnings"] == []
    assert "app_logic" not in json.dumps(payload)
    assert fake.app_logic_calls == []
    # The opt-in is default-off at the service seam itself, not only in this test.
    parameter = inspect.signature(DocumentationInspectService.inspect).parameters["include_app_logic"]
    assert parameter.default is False


def test_include_app_logic_true_reads_engine_dao_containers_and_startup_properties() -> None:
    """On: the five additive categories carry observed engine-level facts."""
    fake = _app_logic_port(
        container_objects={
            "Forms": [
                {"name": "frmMain", "last_modified": "2024-01-02", "date_created": "2023-12-01"}
            ],
            "Reports": [{"name": "rptSales"}],
            "Scripts": [{"name": "AutoExec"}],
            "Modules": [{"name": "modMain"}],
        },
        database_properties={
            "StartupForm": "frmMain",
            "AppTitle": "Acme",
            "AllowFullMenus": "-1",
            "NotAStartupProperty": "ignored",
        },
    )
    document = inspect_with(fake, include_app_logic=True)
    app_logic = document.app_logic
    assert app_logic is not None
    assert [item.category for item in app_logic.containers] == [
        "form_inventory",
        "report_inventory",
        "macro_inventory",
        "module_inventory",
    ]
    by_category = {item.category: item for item in app_logic.containers}
    assert by_category["form_inventory"].container == "Forms"
    assert by_category["form_inventory"].method == "engine_dao_containers"
    assert by_category["form_inventory"].objects.state == "observed"
    assert by_category["form_inventory"].objects.value == [
        {"name": "frmMain", "last_modified": "2024-01-02", "date_created": "2023-12-01"}
    ]
    assert by_category["report_inventory"].objects.value == [{"name": "rptSales"}]
    assert by_category["macro_inventory"].objects.value == [{"name": "AutoExec"}]
    assert by_category["module_inventory"].objects.value == [{"name": "modMain"}]
    properties = {item.name: item for item in app_logic.startup_properties}
    # Generic enumeration filtered to the startup allowlist: unknown properties are absent.
    assert set(properties) == {"StartupForm", "AppTitle", "AllowFullMenus"}
    assert properties["StartupForm"].method == "engine_dao_properties"
    assert properties["StartupForm"].value.state == "observed"
    assert properties["StartupForm"].value.value == "frmMain"
    assert properties["AllowFullMenus"].value.value == "-1"
    # Stable evidence IDs follow the existing ``kind:name.property`` provenance scheme.
    refs = {
        f"{item.objects.provenance.object_kind}:{item.objects.provenance.object_name}."
        f"{item.objects.provenance.property}"
        for item in app_logic.containers
    }
    assert refs == {
        "form_inventory:Forms.objects",
        "report_inventory:Reports.objects",
        "macro_inventory:Scripts.objects",
        "module_inventory:Modules.objects",
    }
    assert properties["StartupForm"].value.provenance.object_kind == "startup_property"
    assert properties["StartupForm"].value.provenance.object_name == "StartupForm"
    assert properties["StartupForm"].value.provenance.property == "value"
    assert "app_logic" in document.to_dict()["data"]
    assert document.status == "complete"


def test_odbc_port_reports_every_app_logic_category_unavailable_with_the_engine_limit() -> None:
    """Fail-honest: an engine without DAO Containers/Properties is unavailable, never empty."""
    document = inspect_with(_app_logic_port(), include_app_logic=True)
    app_logic = document.app_logic
    assert app_logic is not None
    assert len(app_logic.containers) == 4
    assert all(item.objects.state == "unavailable" for item in app_logic.containers)
    assert all("ODBC" in (item.objects.reason or "") for item in app_logic.containers)
    assert app_logic.startup_properties
    assert all(item.value.state == "unavailable" for item in app_logic.startup_properties)
    assert all("ODBC" in (item.value.reason or "") for item in app_logic.startup_properties)
    assert document.status == "partial"


def test_odbc_inspection_port_names_the_engine_limitation_for_app_logic_reads() -> None:
    """The real ODBC port refuses both app-logic surfaces with a DAO limitation reason."""
    odbc = OdbcInspectionPort(None)
    with pytest.raises(InspectionCapabilityUnavailableError) as containers:
        odbc.list_container_objects("Forms")
    assert "DAO" in str(containers.value)
    with pytest.raises(InspectionCapabilityUnavailableError) as properties:
        odbc.get_database_properties()
    assert "DAO" in str(properties.value)


def test_one_container_failure_stays_failed_and_does_not_erase_the_other_facts() -> None:
    """Per-category isolation: one failed container leaves every other fact intact."""
    fake = _app_logic_port(
        container_objects={
            "Forms": RuntimeError("Forms container read failed"),
            "Reports": [{"name": "rptSales"}],
            "Scripts": [],
            "Modules": [{"name": "modMain"}],
        },
        database_properties={"StartupForm": "frmMain"},
    )
    document = inspect_with(fake, include_app_logic=True)
    app_logic = document.app_logic
    assert app_logic is not None
    by_category = {item.category: item for item in app_logic.containers}
    assert by_category["form_inventory"].objects.state == "failed"
    assert "Forms container read failed" in (by_category["form_inventory"].objects.reason or "")
    assert by_category["report_inventory"].objects.state == "observed"
    assert by_category["report_inventory"].objects.value == [{"name": "rptSales"}]
    # An empty container is a known-empty observation, distinct from an unread one.
    assert by_category["macro_inventory"].objects.state == "observed"
    assert by_category["macro_inventory"].objects.value == []
    assert by_category["module_inventory"].objects.value == [{"name": "modMain"}]
    assert [item.value.state for item in app_logic.startup_properties] == ["observed"]
    assert document.status == "partial"


def test_startup_property_read_failure_is_failed_for_every_allowlisted_property() -> None:
    """A failed property enumeration is one ``failed`` fact per allowlisted property."""
    fake = _app_logic_port(
        container_objects={"Forms": [], "Reports": [], "Scripts": [], "Modules": []},
        database_properties=RuntimeError("properties read failed"),
    )
    document = inspect_with(fake, include_app_logic=True)
    app_logic = document.app_logic
    assert app_logic is not None
    assert app_logic.startup_properties
    assert all(item.value.state == "failed" for item in app_logic.startup_properties)
    assert all("properties read failed" in (item.value.reason or "") for item in app_logic.startup_properties)
    assert all(item.objects.state == "observed" for item in app_logic.containers)


# ---------------------------------------------------------------------------
# T7: source opt-in precedence is validated at the extraction service seam
# ---------------------------------------------------------------------------


def test_service_rejects_source_opt_in_without_the_app_logic_opt_in() -> None:
    """Requirement 6: raw source text is only meaningful with the catalogue opt-in.

    The contradictory request fails closed at the service seam (the same seam that
    validates unknown requested names) instead of silently reading nothing.
    """
    fake = _app_logic_port()
    with pytest.raises(AccessMcpError, match="include_app_logic"):
        DocumentationInspectService().inspect(
            fake,
            tables=None,
            queries=None,
            include_app_logic=False,
            include_app_logic_source=True,
        )
    assert fake.app_logic_calls == []


def test_service_accepts_the_source_opt_in_alongside_app_logic() -> None:
    """PRESERVE: the opt-in pair is accepted; the service itself reads no Tier 2 content."""
    fake = _app_logic_port()
    document = DocumentationInspectService().inspect(
        fake,
        tables=None,
        queries=None,
        include_app_logic=True,
        include_app_logic_source=True,
    )
    assert document.app_logic is not None


def test_service_source_opt_in_defaults_off() -> None:
    parameter = inspect.signature(DocumentationInspectService.inspect).parameters["include_app_logic_source"]
    assert parameter.default is False


# ---------------------------------------------------------------------------
# DAO port normalization: ACE NUL padding, optional dates, per-property isolation
# ---------------------------------------------------------------------------
# These drive the production ``DaoInspectionPort`` over a fake DAO ``Database``
# handle, so the normalization is observed through the port's public reads and not
# by calling the module's private text/date helpers directly. No COM, no ACE.

_ABSENT = object()


def _dao_date(attribute: str, value: Any) -> Any:
    """Model a DAO date read: missing attributes are absent, ``Exception``s raise."""
    if value is _ABSENT:
        msg = f"DAO Document does not expose {attribute}"
        raise AttributeError(msg)
    if isinstance(value, Exception):
        raise value
    return value


class _FakeDaoDocument:
    """DAO ``Document`` double for the two optional date attributes."""

    def __init__(self, name: Any, *, last_updated: Any = _ABSENT, date_created: Any = _ABSENT) -> None:
        self.Name = name
        self._last_updated = last_updated
        self._date_created = date_created

    @property
    def LastUpdated(self) -> Any:
        return _dao_date("LastUpdated", self._last_updated)

    @property
    def DateCreated(self) -> Any:
        return _dao_date("DateCreated", self._date_created)


class _FakeDaoProperty:
    """DAO ``Property`` double whose name or value read may fail."""

    def __init__(self, name: Any, value: Any = "") -> None:
        self._name = name
        self._value = value

    @property
    def Name(self) -> Any:
        if isinstance(self._name, Exception):
            raise self._name
        return self._name

    @property
    def Value(self) -> Any:
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


class _FakeDaoContainer:
    """DAO ``Container`` double exposing its ``Documents`` collection."""

    def __init__(self, documents: list[Any]) -> None:
        self.Documents = list(documents)


class _FakeDaoDatabase:
    """Fake DAO ``Database`` handle: per-container Documents plus Properties."""

    def __init__(
        self,
        *,
        documents: dict[str, list[Any]] | None = None,
        properties: list[Any] | None = None,
    ) -> None:
        self.documents = documents or {}
        self.Properties = list(properties or [])

    def Containers(self, container_name: str) -> _FakeDaoContainer:
        return _FakeDaoContainer(self.documents.get(container_name, []))


class _InlineDaoDispatcher:
    """Dispatcher double that runs every dispatched read inline over a fake handle."""

    def __init__(self, database: _FakeDaoDatabase) -> None:
        self.current_db = database

    def call(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        return operation(*args, **kwargs)


def test_dao_port_strips_ace_nul_padding_from_names_and_property_values() -> None:
    """ACE pads engine strings with trailing NULs; evidence never carries them."""
    database = _FakeDaoDatabase(
        documents={"Forms": [_FakeDaoDocument("frmMain\x00\x00")]},
        properties=[_FakeDaoProperty("AppTitle\x00", "Acme\x00\x00")],
    )
    dao_port = DaoInspectionPort(_InlineDaoDispatcher(database))

    assert dao_port.list_container_objects("Forms") == [{"name": "frmMain"}]
    assert dao_port.get_database_properties() == {"AppTitle": "Acme"}


def test_dao_port_omits_only_the_dates_the_engine_does_not_expose() -> None:
    """An absent, raising, or null date is omitted; a readable one is recorded."""
    database = _FakeDaoDatabase(
        documents={
            "Forms": [
                _FakeDaoDocument("frmMain", last_updated="2024-01-02 10:00:00"),
                _FakeDaoDocument("frmSales", last_updated=RuntimeError("DAO 3251"), date_created=None),
                _FakeDaoDocument("frmOrders", last_updated="2024-02-03", date_created="2023-12-01"),
            ]
        }
    )
    dao_port = DaoInspectionPort(_InlineDaoDispatcher(database))

    assert dao_port.list_container_objects("Forms") == [
        {"name": "frmMain", "last_modified": "2024-01-02 10:00:00"},
        {"name": "frmSales"},
        {"name": "frmOrders", "last_modified": "2024-02-03", "date_created": "2023-12-01"},
    ]


def test_dao_port_records_a_real_datetime_date_as_its_stripped_string() -> None:
    """S3 H4: a DAO date is a real ``datetime``, recorded as the documented stripped text."""
    database = _FakeDaoDatabase(
        documents={
            "Forms": [
                _FakeDaoDocument(
                    "frmMain",
                    last_updated=datetime(2024, 1, 2, 10, 30, 15),
                    date_created=datetime(2023, 12, 1, 0, 0, 0),
                )
            ]
        }
    )
    dao_port = DaoInspectionPort(_InlineDaoDispatcher(database))

    assert dao_port.list_container_objects("Forms") == [
        {
            "name": "frmMain",
            "last_modified": "2024-01-02 10:30:15",
            "date_created": "2023-12-01 00:00:00",
        }
    ]


def test_dao_port_keeps_sibling_properties_when_one_property_read_fails() -> None:
    """One unreadable property (live DAO 5003251) never erases the whole catalogue."""
    database = _FakeDaoDatabase(
        properties=[
            _FakeDaoProperty("AppTitle", "Acme"),
            _FakeDaoProperty("Connection", RuntimeError("DAO 5003251")),
            _FakeDaoProperty(RuntimeError("DAO 3265: property name unreadable"), "orphan"),
            _FakeDaoProperty("StartupForm\x00", "frmMain"),
        ]
    )
    dao_port = DaoInspectionPort(_InlineDaoDispatcher(database))

    assert dao_port.get_database_properties() == {"AppTitle": "Acme", "StartupForm": "frmMain"}


def test_dao_port_reports_the_skipped_property_count_and_readable_names() -> None:
    """S2 A3: omissions are still omitted, and one warning names how many were skipped."""
    database = _FakeDaoDatabase(
        properties=[
            _FakeDaoProperty("AppTitle", "Acme"),
            _FakeDaoProperty("Connection", RuntimeError("DAO 5003251")),
            _FakeDaoProperty(RuntimeError("DAO 3265: property name unreadable"), "orphan"),
            _FakeDaoProperty("StartupForm\x00", "frmMain"),
        ]
    )
    dao_port = DaoInspectionPort(_InlineDaoDispatcher(database))

    assert dao_port.get_database_properties() == {"AppTitle": "Acme", "StartupForm": "frmMain"}
    # One sanitized warning: the count plus only the names that were themselves readable.
    assert dao_port.database_property_omissions() == [
        "2 database properties could not be read and were omitted from the "
        "startup-property catalogue (readable names: Connection)"
    ]
    # The failure detail never reaches the warning: only the readable name is echoed.
    assert all("5003251" not in warning and "3265" not in warning for warning in dao_port.database_property_omissions())


def test_dao_port_reports_a_single_skipped_property_without_a_readable_name() -> None:
    """S2 A3: an unreadable name is counted but never guessed or invented."""
    database = _FakeDaoDatabase(
        properties=[
            _FakeDaoProperty("AppTitle", "Acme"),
            _FakeDaoProperty(RuntimeError("DAO 3265: property name unreadable"), "orphan"),
        ]
    )
    dao_port = DaoInspectionPort(_InlineDaoDispatcher(database))

    assert dao_port.get_database_properties() == {"AppTitle": "Acme"}
    assert dao_port.database_property_omissions() == [
        "1 database property could not be read and was omitted from the startup-property catalogue"
    ]


def test_dao_port_reports_no_omission_warning_when_every_property_is_readable() -> None:
    """S2 A3/S4: a fully readable catalogue adds no warning, so success stays identical."""
    database = _FakeDaoDatabase(properties=[_FakeDaoProperty("AppTitle", "Acme")])
    dao_port = DaoInspectionPort(_InlineDaoDispatcher(database))

    # Before any read there is nothing to report.
    assert dao_port.database_property_omissions() == []
    assert dao_port.get_database_properties() == {"AppTitle": "Acme"}
    assert dao_port.database_property_omissions() == []


# ---------------------------------------------------------------------------
# Service-side app-logic normalization: malformed container entries, startup values
# ---------------------------------------------------------------------------


def test_malformed_container_entries_are_filtered_and_only_name_and_dates_survive() -> None:
    """A port's inventory is normalized to ``name`` plus readable date strings."""
    fake = _app_logic_port(
        container_objects={
            "Forms": [
                {"name": "frmMain", "last_modified": " 2024-01-02 ", "extra": "dropped"},
                "not-an-entry",
                {"name": None},
                {"name": "   "},
                {"noname": "frmMissing"},
                {"name": "frmOrders", "last_modified": None, "date_created": 17},
            ]
        },
        database_properties={},
    )

    document = inspect_with(fake, include_app_logic=True)

    app_logic = document.app_logic
    assert app_logic is not None
    by_category = {item.category: item for item in app_logic.containers}
    assert by_category["form_inventory"].objects.state == "observed"
    assert by_category["form_inventory"].objects.value == [
        {"name": "frmMain", "last_modified": "2024-01-02"},
        {"name": "frmOrders"},
    ]
    # The other three containers were read and are known-empty, not failed.
    assert [by_category[category].objects.value for category in ("report_inventory", "macro_inventory", "module_inventory")] == [[], [], []]
    assert [by_category[category].objects.state for category in ("report_inventory", "macro_inventory", "module_inventory")] == ["observed", "observed", "observed"]
    assert document.status == "complete"


def test_startup_property_none_and_non_string_values_are_normalized_not_invented() -> None:
    """A null value becomes an empty string; non-strings become their text, never ``None``."""
    fake = _app_logic_port(
        container_objects={},
        database_properties={
            "AppTitle": None,
            "StartupForm": "frmMain",
            "AllowFullMenus": -1,
            "ShowNavigationPane": True,
        },
    )

    document = inspect_with(fake, include_app_logic=True)

    app_logic = document.app_logic
    assert app_logic is not None
    properties = {item.name: item.value for item in app_logic.startup_properties}
    assert set(properties) == {"AppTitle", "StartupForm", "AllowFullMenus", "ShowNavigationPane"}
    assert all(fact.state == "observed" for fact in properties.values())
    assert properties["AppTitle"].value == ""
    assert properties["StartupForm"].value == "frmMain"
    assert properties["AllowFullMenus"].value == "-1"
    assert properties["ShowNavigationPane"].value == "True"


def test_include_app_logic_forwards_one_skipped_property_warning_without_a_status_change() -> None:
    """S2 A3: the port's omission warning reaches ``warnings``; facts/status do not move."""
    warning = (
        "2 database properties could not be read and were omitted from the "
        "startup-property catalogue (readable names: Connection)"
    )
    fake = _app_logic_port(
        container_objects={},
        database_properties={"AppTitle": "Acme", "StartupForm": "frmMain"},
        database_property_omissions=[warning],
    )

    document = inspect_with(fake, include_app_logic=True)

    # Exactly one warning, on the success path, and never a status change.
    assert document.warnings == [warning]
    assert document.to_dict()["warnings"] == [warning]
    assert document.status == "complete"
    # The readable siblings are unchanged observed facts; nothing was invented.
    assert document.app_logic is not None
    properties = {item.name: item.value for item in document.app_logic.startup_properties}
    assert set(properties) == {"AppTitle", "StartupForm"}
    assert all(fact.state == "observed" for fact in properties.values())


def test_include_app_logic_success_carries_no_property_omission_warning() -> None:
    """S2 A3/S4: a fully readable catalogue keeps the successful payload unchanged."""
    fake = _app_logic_port(container_objects={}, database_properties={"AppTitle": "Acme"})

    document = inspect_with(fake, include_app_logic=True)

    assert document.warnings == []
    assert document.status == "complete"


def test_property_omission_warning_is_never_read_without_the_app_logic_opt_in() -> None:
    """PRESERVE: default-off never reads properties, so it can never gain a warning."""
    fake = _app_logic_port(
        container_objects={},
        database_properties={"AppTitle": "Acme"},
        database_property_omissions=["1 database property could not be read"],
    )

    document = inspect_with(fake)

    assert document.warnings == []
    assert fake.app_logic_calls == []
