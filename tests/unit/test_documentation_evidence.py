from __future__ import annotations

import pytest

from ms_access_mcp.application.documentation import (
    Coverage,
    CoverageStatus,
    EvidenceDoc,
    FactProvenance,
    FactStatus,
    FieldEvidence,
    QueryEvidence,
    RelationshipEvidence,
    RunMetadata,
    SourceEvidence,
    TableEvidence,
    redact_connection_secrets,
    sanitize_connection_string,
)


def provenance(kind: str = "query", name: str = "Q", prop: str = "sql") -> FactProvenance:
    return FactProvenance(object_kind=kind, object_name=name, property=prop)


def make_document(sql: FactStatus[str] | None = None) -> EvidenceDoc:
    table = TableEvidence(
        name="Parent",
        fields=[
            FieldEvidence(
                name="id",
                type=FactStatus.observed("Long", provenance("field", "id", "type")),
                size=FactStatus.observed(4, provenance("field", "id", "size")),
                required=FactStatus.observed(True, provenance("field", "id", "required")),
                allow_zero_length=FactStatus.observed(False, provenance("field", "id", "allow_zero_length")),
                default_value=FactStatus.observed(None, provenance("field", "id", "default_value")),
            )
        ],
        primary_key=FactStatus.observed(["id"], provenance("table", "Parent", "primary_key")),
        indexes=[],
    )
    relationship = RelationshipEvidence(
        name="ParentChild",
        parent_table="Parent",
        child_table="Child",
        parent_columns=["tenant_id", "parent_id"],
        child_columns=["tenant_id", "id"],
        attributes=FactStatus.observed({}, FactProvenance("relationship", "ParentChild", "attributes")),
    )
    return EvidenceDoc(
        run=RunMetadata(run_id="run-1", extractor_version="1.0", scope_tables=["Parent", "Child"], scope_queries=["Q"]),
        source=SourceEvidence(path="db.accdb", format="accdb", backend_identity="DAO/ACE", acquisition_mode="offline_read_only", consistency_marker="caller_asserted_closed"),
        coverage=Coverage(
            tables=CoverageStatus(inspected=["Parent", "Child"]),
            queries=CoverageStatus(inspected=["Q"]),
            relationships=CoverageStatus(inspected=["ParentChild"]),
        ),
        tables=[table],
        relationships=[relationship],
        queries=[QueryEvidence(name="Q", sql=sql or FactStatus.observed("SELECT 1", provenance()), type=FactStatus.observed("select", provenance("query", "Q", "type")), parameters=FactStatus.observed([], provenance("query", "Q", "parameters")))],
    )


def test_fact_status_value_is_only_legal_for_observed_state() -> None:
    observed = FactStatus.observed([], provenance("table", "T", "indexes"))
    assert observed.value == []
    unavailable = FactStatus.unavailable("engine did not expose it", provenance())
    assert unavailable.value is None
    with pytest.raises(ValueError):
        FactStatus(state="failed", value="unexpected", reason="read failed", provenance=provenance()).validate()
    with pytest.raises(ValueError):
        FactStatus(state="failed", value=None, reason="read failed", provenance=provenance()).validate()
    with pytest.raises(ValueError):
        FactStatus(state="failed", reason=None, provenance=provenance()).validate()


def test_evidence_round_trip_preserves_typed_equality() -> None:
    evidence = make_document()
    evidence.validate()
    assert EvidenceDoc.from_dict(evidence.to_dict()) == evidence


def test_composite_relationship_pairs_remain_aligned_and_ordered() -> None:
    evidence = make_document()
    restored = EvidenceDoc.from_dict(evidence.to_dict())
    relationship = restored.relationships[0]
    assert list(zip(relationship.parent_columns, relationship.child_columns, strict=True)) == [
        ("tenant_id", "tenant_id"),
        ("parent_id", "id"),
    ]


def test_blank_query_sql_is_not_observed_empty_but_failed_sql_retains_reason() -> None:
    failed_sql = FactStatus.failed("QueryDef SQL could not be read", provenance())
    evidence = make_document(sql=failed_sql)
    evidence.validate()
    assert evidence.queries[0].sql.state == "failed"
    assert evidence.queries[0].sql.reason == "QueryDef SQL could not be read"
    with pytest.raises(ValueError):
        QueryEvidence(name="Q", sql=FactStatus.observed("", provenance()), type=FactStatus.observed("select", provenance("query", "Q", "type"))).validate()


def test_table_link_metadata_defaults_local_and_round_trips_when_linked() -> None:
    evidence = make_document()
    local = evidence.tables[0]
    assert (local.kind, local.connect, local.source_table_name) == ("local", None, None)
    assert local.to_dict()["kind"] == "local"
    local.kind = "linked"
    local.connect = ";DATABASE=C:\\fixtures\\sentinel.accdb"
    local.source_table_name = "SentinelCustomers"
    restored = EvidenceDoc.from_dict(evidence.to_dict())
    assert restored == evidence
    assert restored.tables[0].kind == "linked"
    assert restored.tables[0].connect == ";DATABASE=C:\\fixtures\\sentinel.accdb"
    assert restored.tables[0].source_table_name == "SentinelCustomers"


def test_unknown_table_kind_and_local_link_metadata_are_rejected() -> None:
    evidence = make_document()
    evidence.tables[0].kind = "external"
    with pytest.raises(ValueError, match="table kind"):
        evidence.validate()
    evidence.tables[0].kind = "local"
    evidence.tables[0].connect = ";DATABASE=x"
    with pytest.raises(ValueError, match="Local tables"):
        evidence.validate()


def test_observed_query_parameters_round_trip_with_name_type_and_type_name() -> None:
    """T1: an observed parameter list carries name, DAO type code, and DAO type name."""
    evidence = make_document()
    evidence.queries[0].parameters = FactStatus.observed(
        [{"name": "[pCustomerID]", "type": 4, "type_name": "Long Integer"}],
        provenance("query", "Q", "parameters"),
    )
    restored = EvidenceDoc.from_dict(evidence.to_dict())
    assert restored == evidence
    assert restored.queries[0].parameters.state == "observed"
    assert restored.queries[0].parameters.value == [
        {"name": "[pCustomerID]", "type": 4, "type_name": "Long Integer"}
    ]


def test_observed_query_parameters_require_name_and_integer_type() -> None:
    """A parameter fact cannot claim a name/type representation it does not carry."""
    evidence = make_document()
    evidence.queries[0].parameters = FactStatus.observed(
        [{"name": "[pCustomerID]", "type": "Long"}],
        provenance("query", "Q", "parameters"),
    )
    with pytest.raises(ValueError, match="parameter"):
        evidence.validate()


def test_status_is_complete_without_gaps_and_partial_with_non_excluded_gap() -> None:
    evidence = make_document()
    assert evidence.status == "complete"
    evidence.queries[0].sql = FactStatus.failed("read failed", provenance())
    assert evidence.status == "partial"
    excluded = FactStatus.excluded("not requested", provenance())
    evidence.queries[0].sql = excluded
    assert evidence.status == "complete"
    evidence.coverage.queries.failed["Q"] = "enumeration failed"
    assert evidence.status == "partial"


def test_sanitize_connection_string_drops_credential_pairs_and_preserves_the_rest() -> None:
    """T1: credential pairs are removed; local catalogue metadata is preserved."""
    assert sanitize_connection_string(None) is None
    assert sanitize_connection_string("") == ""
    assert (
        sanitize_connection_string(";DATABASE=C:\\x\\s.accdb;UID=canary-user;PWD=canary-secret")
        == ";DATABASE=C:\\x\\s.accdb"
    )
    assert sanitize_connection_string("ODBC;DSN=Foo;PWD=canary-secret") == "ODBC;DSN=Foo"


def test_sanitize_connection_string_handles_braced_and_semicolon_values() -> None:
    """T1: a braced value containing semicolons is removed whole, not partially."""
    sanitized = sanitize_connection_string(";DATABASE=x.accdb;PWD={can;ary};UID={a}}b}")
    assert sanitized == ";DATABASE=x.accdb"
    assert "can" not in sanitized and "ary" not in sanitized


def test_sanitize_connection_string_fails_safe_on_unbalanced_braces() -> None:
    """T1: an unparseable string becomes a redaction marker, never a partial secret."""
    assert sanitize_connection_string(";PWD={unbalanced;canary-secret") == "[redacted connection string]"
    assert sanitize_connection_string("canary};PWD=x") == "[redacted connection string]"


def test_redact_connection_secrets_scrubs_credential_values_in_free_text() -> None:
    """T1: error text keeps its context but loses every credential value."""
    assert redact_connection_secrets("open failed for ;DATABASE=x;PWD=canary-secret") == (
        "open failed for ;DATABASE=x;PWD=***"
    )
    assert "canary" not in redact_connection_secrets("PWD={can;ary} rest")
    assert redact_connection_secrets("") == ""


def test_redact_connection_secrets_removes_braced_values_with_escaped_braces() -> None:
    """B1: ``}}`` escapes a brace inside a value; the whole value must be removed."""
    redacted = redact_connection_secrets("open failed for ;DATABASE=x;PWD={can}}ary}")
    assert redacted == "open failed for ;DATABASE=x;PWD=***"
    assert "ary" not in redacted


def test_redact_connection_secrets_handles_multiple_escaped_pairs_and_keeps_context() -> None:
    """B1: several escaped pairs are removed and non-credential context is preserved."""
    redacted = redact_connection_secrets("PWD={can}}ary};UID={u}}v} tail")
    assert redacted == "PWD=***;UID=*** tail"
    assert "ary" not in redacted and "u}}v" not in redacted
