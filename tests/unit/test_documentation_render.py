from __future__ import annotations

import pytest

from ms_access_mcp.application.documentation import (
    Coverage,
    CoverageStatus,
    EvidenceDoc,
    FactProvenance,
    FactStatus,
    FieldEvidence,
    IndexEvidence,
    QueryEvidence,
    RelationshipEvidence,
    RunMetadata,
    SourceEvidence,
    TableEvidence,
)
from ms_access_mcp.application.documentation_render import ReportDoc, render_document


def provenance(kind: str, name: str, prop: str) -> FactProvenance:
    return FactProvenance(kind, name, prop)


def field(name: str, table: str) -> FieldEvidence:
    return FieldEvidence(
        name=name,
        type=FactStatus.observed("Long", provenance("field", name, "type")),
        size=FactStatus.observed(4, provenance("field", name, "size")),
        required=FactStatus.observed(True, provenance("field", name, "required")),
        allow_zero_length=FactStatus.observed(False, provenance("field", name, "allow_zero_length")),
        default_value=FactStatus.observed(None, provenance("field", name, "default_value")),
    )


def make_evidence(*, unusual: bool = False, justified: bool = True, failed_sql: bool = False) -> EvidenceDoc:
    parent_name = "P arent[🧪]|" if unusual else "Parent"
    child_name = "Child [二]|" if unusual else "Child"
    parent_field = "id | [x]" if unusual else "id"
    child_field = "parent id" if unusual else "parent_id"
    parent = TableEvidence(
        name=parent_name,
        fields=[field(parent_field, parent_name)],
        primary_key=FactStatus.observed([parent_field], provenance("table", parent_name, "primary_key")),
        indexes=[IndexEvidence("PK", [parent_field], is_unique=True, is_primary=True)],
    )
    child = TableEvidence(
        name=child_name,
        fields=[field(child_field, child_name)],
        primary_key=FactStatus.observed([], provenance("table", child_name, "primary_key")),
        indexes=[],
    )
    relationship = RelationshipEvidence(
        name="pair",
        parent_table=parent_name,
        child_table=child_name,
        parent_columns=[parent_field if justified else "not-a-key"],
        child_columns=[child_field],
        attributes=FactStatus.observed({"cascade_updates": True}, provenance("relationship", "pair", "attributes")),
    )
    sql = FactStatus.failed("QueryDef SQL read failed", provenance("query", "Q", "sql")) if failed_sql else FactStatus.observed("SELECT 1;\nSELECT 2;", provenance("query", "Q", "sql"))
    return EvidenceDoc(
        run=RunMetadata("run-1", "1", [parent_name, child_name], ["Q"]),
        source=SourceEvidence("db.accdb", "accdb", "DAO", "offline", "closed"),
        coverage=Coverage(
            tables=CoverageStatus(inspected=[parent_name, child_name]),
            queries=CoverageStatus(inspected=["Q"]),
            relationships=CoverageStatus(inspected=["pair"]),
        ),
        tables=[parent, child],
        relationships=[relationship],
        queries=[QueryEvidence(name="Q", sql=sql, type=FactStatus.observed("select", provenance("query", "Q", "type")))],
    )


@pytest.mark.parametrize("child_key", ["none", "unrelated_pk", "nonunique", "unique", "pk"])
def test_child_cardinality_uses_only_matching_unique_keys(child_key: str) -> None:
    evidence = make_evidence()
    child = evidence.tables[1]
    if child_key in ("unique", "nonunique"):
        child.indexes = [IndexEvidence("FK", ["parent_id"], is_unique=child_key == "unique")]
    elif child_key == "pk":
        child.primary_key = FactStatus.observed(["parent_id"], provenance("table", "Child", "primary_key"))
    elif child_key == "unrelated_pk":
        child.fields.append(field("id", "Child"))
        child.primary_key = FactStatus.observed(["id"], provenance("table", "Child", "primary_key"))
    endpoint = "||" if child_key in ("unique", "pk") else "}o"
    assert f'  t2 {endpoint}--|| t1 : "pair"' in render_document(evidence).markdown.splitlines()


@pytest.mark.parametrize("complete", [True, False])
def test_composite_child_cardinality_requires_full_unique_key_match(complete: bool) -> None:
    evidence = make_evidence()
    relation = evidence.relationships[0]
    relation.parent_columns = ["tenant", "id"]
    relation.child_columns = ["tenant_id", "parent_id"]
    evidence.tables[0].fields.append(field("tenant", "Parent"))
    evidence.tables[0].primary_key = FactStatus.observed(["tenant", "id"], provenance("table", "Parent", "primary_key"))
    evidence.tables[1].fields.append(field("tenant_id", "Child"))
    columns = ["tenant_id", "parent_id"] if complete else ["tenant_id"]
    evidence.tables[1].indexes = [IndexEvidence("FK", columns, is_unique=True)]
    endpoint = "||" if complete else "}o"
    assert f'  t2 {endpoint}--|| t1 : "pair"' in render_document(evidence).markdown.splitlines()


def test_composite_relationship_pairs_render_in_aligned_order() -> None:
    evidence = make_evidence()
    rel = evidence.relationships[0]
    rel.parent_columns = ["tenant", "id"]
    rel.child_columns = ["tenant_id", "parent_id"]
    evidence.tables[0].fields.extend([field("tenant", "Parent")])
    evidence.tables[1].fields.extend([field("tenant_id", "Child")])
    evidence.tables[0].primary_key = FactStatus.observed(["tenant", "id"], provenance("table", "Parent", "primary_key"))
    evidence.tables[0].indexes = [IndexEvidence("PK", ["tenant", "id"], is_unique=True, is_primary=True)]
    report = render_document(evidence)
    assert "tenant → tenant_id; id → parent_id" in report.markdown
    assert "ALIGNED" in report.markdown


def test_unusual_names_are_aliased_in_mermaid_and_pipes_escaped_in_tables() -> None:
    report = render_document(make_evidence(unusual=True))
    assert "t1" in report.markdown and "t2" in report.markdown
    assert "P arent[🧪]\\|" in report.markdown
    assert "id \\| [x]" in report.markdown
    assert "t1 {\n" in report.markdown
    assert "P arent[🧪]|" not in report.markdown.split("```mermaid", 1)[1].split("```", 1)[0]


def test_failed_query_sql_renders_reason_not_empty_code_fence() -> None:
    report = render_document(make_evidence(failed_sql=True))
    assert "QueryDef SQL read failed" in report.markdown
    assert "```sql\n\n```" not in report.markdown


def test_unsupported_cardinality_relationship_is_catalogued_but_not_diagrammed() -> None:
    report = render_document(make_evidence(justified=False))
    assert "cardinality not derivable from evidence" in report.markdown
    diagram = report.markdown.split("```mermaid", 1)[1].split("```", 1)[0]
    assert ': "pair"' not in diagram
    assert "| pair | Parent → Child |" in report.markdown


def test_database_and_er_reports_have_expected_section_sets() -> None:
    evidence = make_evidence()
    database = render_document(evidence, kind="database").markdown
    er = render_document(evidence, kind="er").markdown
    assert all(section in database for section in ("Overview & inspection summary", "Data dictionary", "Keys & indexes", "Relationship catalogue", "Saved queries", "Coverage & limitations"))
    assert "Saved queries" not in er
    assert "Data dictionary" in er and "Relationship catalogue" in er and "Mermaid ER diagram" in er


def test_interpretations_are_labelled_and_unknown_evidence_refs_rejected() -> None:
    report = render_document(make_evidence(), interpretations=[{"evidence_ref": "field:id.type", "text": "Likely identifier"}])
    assert "Interpretations (generated by the client, not facts)" in report.markdown
    assert "`field:id.type` — Likely identifier" in report.markdown
    with pytest.raises(ValueError, match="evidence"):
        render_document(make_evidence(), interpretations=[{"evidence_ref": "field:Missing.id.type", "text": "guess"}])


def test_report_is_deterministic_and_round_trips() -> None:
    evidence = make_evidence()
    first = render_document(evidence)
    second = render_document(evidence)
    assert first.markdown == second.markdown
    assert first.sha256 == second.sha256
    assert ReportDoc.from_dict(first.to_dict()) == first


def test_partial_status_propagates_from_evidence() -> None:
    evidence = make_evidence(failed_sql=True)
    assert render_document(evidence).status == "partial"


def test_only_english_is_accepted() -> None:
    with pytest.raises(ValueError, match="language"):
        render_document(make_evidence(), language="fr")
