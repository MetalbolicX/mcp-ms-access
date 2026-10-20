from __future__ import annotations

import hashlib

import pytest

from ms_access_mcp.application.documentation import (
    AppLogicEvidence,
    ContainerInventoryEvidence,
    ContentObjectEvidence,
    Coverage,
    CoverageStatus,
    EvidenceDoc,
    FactProvenance,
    FactStatus,
    FieldEvidence,
    IndexEvidence,
    QueryEvidence,
    ReferenceEvidence,
    RelationshipEvidence,
    RunMetadata,
    SourceEvidence,
    StartupPropertyEvidence,
    TableEvidence,
)
from ms_access_mcp.application.documentation_app_logic import (
    FAMILY_SCOPE_NAME,
    VBA_TRUST_PREREQUISITE,
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


@pytest.mark.parametrize("independent_key", [False, True])
def test_relationship_auto_index_is_not_child_uniqueness(independent_key: bool) -> None:
    evidence = make_evidence()
    child = evidence.tables[1]
    child.indexes = [IndexEvidence("pair", ["parent_id"], is_unique=True)]
    if independent_key:
        child.indexes.append(IndexEvidence("Independent", ["parent_id"], is_unique=True))
    markdown = render_document(evidence).markdown
    endpoint = "||" if independent_key else "}o"
    assert f'  t2 {endpoint}--|| t1 : "pair"' in markdown.splitlines()
    assert "| pair | parent_id | True | False | False |" in markdown


def test_relationship_auto_index_cannot_justify_parent_eligibility() -> None:
    evidence = make_evidence()
    parent = evidence.tables[0]
    parent.primary_key = FactStatus.observed([], provenance("table", "Parent", "primary_key"))
    parent.indexes = [IndexEvidence("reverse", ["id"], is_unique=True)]
    evidence.relationships.append(RelationshipEvidence(
        name="reverse", parent_table="Child", child_table="Parent",
        parent_columns=["parent_id"], child_columns=["id"],
    ))
    markdown = render_document(evidence).markdown
    assert ': "pair"' not in markdown
    assert "| pair | Parent → Child |" in markdown


def test_relationship_named_index_on_other_columns_remains_unique() -> None:
    evidence = make_evidence()
    evidence.tables[1].indexes = [IndexEvidence("pair", ["parent_id"], is_unique=True)]
    evidence.relationships[0].child_columns = ["other"]
    evidence.tables[1].fields.append(field("other", "Child"))
    evidence.relationships.append(RelationshipEvidence(
        name="other_relation", parent_table="Parent", child_table="Child",
        parent_columns=["id"], child_columns=["parent_id"],
    ))
    assert '  t2 ||--|| t1 : "other_relation"' in render_document(evidence).markdown.splitlines()


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


def test_linked_table_renders_local_link_source_in_database_report() -> None:
    evidence = make_evidence()
    linked = evidence.tables[1]
    linked.kind = "linked"
    linked.connect = ";DATABASE=D:\\fixtures\\sentinel.accdb"
    linked.source_table_name = "SentinelCustomers"
    markdown = render_document(evidence, kind="database").markdown
    assert f"- Linked table: {linked.name} → sentinel.accdb/SentinelCustomers" in markdown
    er = render_document(evidence, kind="er").markdown
    assert f"- Linked table: {linked.name} → sentinel.accdb/SentinelCustomers" in er


def test_local_tables_render_no_link_line() -> None:
    markdown = render_document(make_evidence()).markdown
    assert "Linked table:" not in markdown


def test_linked_render_never_leaks_a_credential_from_a_connect_string() -> None:
    """T1: a credential in hand-built evidence cannot reach report text."""
    evidence = make_evidence()
    linked = evidence.tables[1]
    linked.kind = "linked"
    linked.connect = "ODBC;DSN=Fixture;UID=canary-user;PWD=canary-secret"
    linked.source_table_name = "SentinelCustomers"
    markdown = render_document(evidence).markdown
    assert "canary-secret" not in markdown
    assert "canary-user" not in markdown
    assert "Linked table:" in markdown


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


def test_excluded_query_sql_renders_its_reason_without_a_coverage_gap() -> None:
    """A deliberately excluded SQL fact is visible in prose but never a coverage gap.

    Exclusion is an output policy (SQL is untrusted source text), so the document stays
    ``complete`` and no ``coverage.queries`` bucket records it; the renderer still states
    the exclusion instead of dropping the section silently.
    """
    evidence = make_evidence()
    # A known-empty parameter list keeps every other fact observed, so the baseline is
    # ``complete`` and the assertion isolates the effect of the excluded SQL fact.
    evidence.queries[0].parameters = FactStatus.observed([], provenance("query", "Q", "parameters"))
    assert render_document(evidence).status == "complete"
    evidence.queries[0].sql = FactStatus.excluded(
        "Saved-query SQL is excluded from emitted evidence by default",
        provenance("query", "Q", "sql"),
    )
    report = render_document(evidence)
    assert "- SQL **excluded**: Saved-query SQL is excluded from emitted evidence by default" in report.markdown
    assert report.status == "complete"
    assert report.coverage["queries"] == {
        "inspected": 1,
        "excluded": 0,
        "unsupported": 0,
        "failed": 0,
    }
    assert "- Excluded (queries)" not in report.markdown
    assert "```sql" not in report.markdown


def test_unsupported_cardinality_relationship_is_catalogued_but_not_diagrammed() -> None:
    report = render_document(make_evidence(justified=False))
    assert "cardinality not derivable from evidence" in report.markdown
    diagram = report.markdown.split("```mermaid", 1)[1].split("```", 1)[0]
    assert ': "pair"' not in diagram
    assert "| pair | Parent → Child |" in report.markdown


def test_query_catalogue_renders_observed_parameters_with_names_and_dao_type_names() -> None:
    """T1: the database query catalogue lists parameters as ``name (DAO type name)``."""
    evidence = make_evidence()
    evidence.queries[0].parameters = FactStatus.observed(
        [
            {"name": "[pCustomerID]", "type": 4, "type_name": "Long Integer"},
            {"name": "[pStatus]", "type": 10, "type_name": "Text"},
        ],
        provenance("query", "Q", "parameters"),
    )
    markdown = render_document(evidence).markdown
    assert "- Parameters: [pCustomerID] (Long Integer); [pStatus] (Text)" in markdown


def test_query_catalogue_marks_zero_parameter_queries_observed_empty() -> None:
    """A known-empty parameter list renders as observed empty, not as unknown."""
    evidence = make_evidence()
    evidence.queries[0].parameters = FactStatus.observed([], provenance("query", "Q", "parameters"))
    assert "- Parameters: observed empty" in render_document(evidence).markdown


def test_unavailable_query_parameters_render_reason_not_fabricated_values() -> None:
    """PRESERVE: a producer without parameter exposure stays honestly unavailable."""
    markdown = render_document(make_evidence()).markdown
    assert "- Parameters: unavailable: Query parameters are not decoded" in markdown


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


# ---------------------------------------------------------------------------
# T8/R3: application-logic catalogue sections (evidence-derived, never inlined)
# ---------------------------------------------------------------------------

# sha256 of the v1 database Markdown for ``make_evidence()``; the app-logic section must
# be purely additive, so evidence without app-logic facts keeps these exact bytes.
V1_DATABASE_MARKDOWN_SHA256 = "333c4c22a15cac74dd1d5c661438f4ca6181ad8e44c1fee2fc106ea9cd2042f9"
APP_LOGIC_METHOD = "access-application"
ENGINE_CONTAINER_METHOD = "engine_dao_containers"
FORM_SHA = "a" * 64
REPORT_SHA = "b" * 64
MACRO_SHA = "c" * 64
VBA_SHA = "d" * 64
VBA_SOURCE = 'Attribute VB_Name = "modMain"\nSub Main()\nVBA_SOURCE_SENTINEL\nEnd Sub'
FORM_SOURCE = "Version =20\nFORM_SOURCE_SENTINEL\nEnd\n"


def app_content(
    category: str,
    name: str,
    value: dict | None = None,
    *,
    state: str = "observed",
    reason: str | None = None,
) -> ContentObjectEvidence:
    prop = provenance(category, name, "content")
    fact = FactStatus.observed(value, prop) if state == "observed" else getattr(FactStatus, state)(reason, prop)
    return ContentObjectEvidence(category, name, APP_LOGIC_METHOD, fact)


def app_reference(name: str) -> ReferenceEvidence:
    values = {"path": "C:/vba.dll", "guid": "{GUID}", "major": 7, "minor": 1, "built_in": True}
    return ReferenceEvidence(
        name,
        APP_LOGIC_METHOD,
        *[FactStatus.observed(values[prop], provenance("reference", name, prop)) for prop in ("path", "guid", "major", "minor", "built_in")],
    )


def app_startup(name: str, value=None, *, state: str = "observed", reason: str | None = None) -> StartupPropertyEvidence:
    prop = provenance("startup_property", name, "value")
    fact = FactStatus.observed(value, prop) if state == "observed" else getattr(FactStatus, state)(reason, prop)
    return StartupPropertyEvidence(name, "engine_dao_properties", fact)


def app_container(category: str, container: str, objects: list | None = None, *, state: str = "observed", reason: str | None = None) -> ContainerInventoryEvidence:
    prop = provenance(category, container, "objects")
    fact = FactStatus.observed(objects, prop) if state == "observed" else getattr(FactStatus, state)(reason, prop)
    return ContainerInventoryEvidence(category, container, ENGINE_CONTAINER_METHOD, fact)


def catalogue_evidence() -> EvidenceDoc:
    evidence = make_evidence()
    evidence.queries[0].parameters = FactStatus.observed([], provenance("query", "Q", "parameters"))
    evidence.app_logic = AppLogicEvidence(
        containers=[app_container("form_inventory", "Forms", [{"name": "frmMain"}])],
        startup_properties=[app_startup("AppTitle", "My App")],
        content=[
            app_content("vba_module", "modMain", {"type": "Standard", "procedures": ["Main", "Helper"], "sha256": VBA_SHA, "bytes": 73, "source": VBA_SOURCE}),
            app_content("form", "frmMain", {"sha256": FORM_SHA, "bytes": 5130, "source": FORM_SOURCE}),
            app_content("report", "rptSales", {"sha256": REPORT_SHA, "bytes": 120}),
            app_content("macro", "AutoExec", {"sha256": MACRO_SHA, "bytes": 30}),
        ],
        references=[app_reference("VBA")],
    )
    return evidence


def test_evidence_without_app_logic_renders_the_v1_markdown_byte_for_byte() -> None:
    """R3/S4: the app-logic section is strictly additive; no app-logic data means v1 bytes."""
    baseline = render_document(make_evidence())
    assert hashlib.sha256(baseline.markdown.encode("utf-8")).hexdigest() == V1_DATABASE_MARKDOWN_SHA256
    assert "Application logic" not in baseline.markdown
    empty = make_evidence()
    empty.app_logic = AppLogicEvidence()
    assert render_document(empty).markdown == baseline.markdown


def test_app_logic_renders_catalogue_names_hashes_references_and_startup_values() -> None:
    """R3: labeled sections list facts and reference source by evidence ID plus hash."""
    markdown = render_document(catalogue_evidence()).markdown
    assert "## Application logic" in markdown
    assert "### VBA modules" in markdown
    assert f"| modMain | Standard | 2 — Main, Helper | `{VBA_SHA}` | 73 | `vba_module:modMain.content` |" in markdown
    assert "### Forms" in markdown
    assert f"| frmMain | `{FORM_SHA}` | 5130 | `form:frmMain.content` |" in markdown
    assert f"| rptSales | `{REPORT_SHA}` | 120 | `report:rptSales.content` |" in markdown
    assert f"| AutoExec | `{MACRO_SHA}` | 30 | `macro:AutoExec.content` |" in markdown
    assert "### Engine-level inventories" in markdown
    assert "- Form inventory (container `Forms`): frmMain" in markdown
    assert "### References" in markdown
    assert "| VBA | C:/vba.dll | {GUID} | 7.1 | True |" in markdown
    assert "### Startup properties" in markdown
    assert "- AppTitle = My App" in markdown


def test_app_logic_unavailable_family_and_failed_object_render_limitation_lines() -> None:
    """R3: unavailable/failed facts become visible limitation lines naming the reason."""
    evidence = make_evidence()
    evidence.app_logic = AppLogicEvidence(
        content=[
            app_content("vba_module", FAMILY_SCOPE_NAME, state="unavailable", reason=VBA_TRUST_PREREQUISITE),
            app_content("form", "frmBroken", state="failed", reason="SaveAsText export failed: form import error"),
        ]
    )
    markdown = render_document(evidence).markdown
    assert "### Limitations" in markdown
    assert "- VBA modules: unavailable — VBProject access requires the operator" in markdown
    assert '- Form "frmBroken": failed — SaveAsText export failed: form import error' in markdown


def test_app_logic_excluded_fact_renders_a_coverage_note_without_a_gap() -> None:
    """R3: exclusion is output policy, so it is a coverage note and never a partial doc."""
    evidence = make_evidence()
    evidence.queries[0].parameters = FactStatus.observed([], provenance("query", "Q", "parameters"))
    evidence.app_logic = AppLogicEvidence(
        startup_properties=[app_startup("AppTitle", state="excluded", reason="Startup values are excluded by caller policy")]
    )
    report = render_document(evidence)
    assert "### Coverage notes" in report.markdown
    assert '- Startup property "AppTitle": excluded — Startup values are excluded by caller policy' in report.markdown
    assert "### Limitations" not in report.markdown
    assert report.status == "complete"


def test_app_logic_vba_digest_and_byte_cells_render_without_a_gap() -> None:
    """A recorded digest fills the VBA table cells and suppresses the digest gap."""
    evidence = make_evidence()
    evidence.app_logic = AppLogicEvidence(
        content=[
            app_content(
                "vba_module",
                "modMain",
                {"type": "Standard", "procedures": [], "sha256": VBA_SHA, "bytes": 73},
            )
        ]
    )

    markdown = render_document(evidence).markdown

    assert f"| modMain | Standard | 0 | `{VBA_SHA}` | 73 | `vba_module:modMain.content` |" in markdown
    assert "source digest (sha256/bytes) is not recorded" not in markdown


def test_app_logic_opt_in_source_is_referenced_by_evidence_id_and_never_inlined() -> None:
    """R3: raw source is referenced by evidence ID; the text itself never reaches Markdown."""
    markdown = render_document(catalogue_evidence()).markdown
    assert "VBA_SOURCE_SENTINEL" not in markdown
    assert "FORM_SOURCE_SENTINEL" not in markdown
    assert "`vba_module:modMain.content`" in markdown
    assert "`form:frmMain.content`" in markdown


def test_app_logic_rendering_is_deterministic_and_part_of_the_fingerprint() -> None:
    """S4/Requirement 2: same evidence + options yields the same Markdown and fingerprint."""
    evidence = catalogue_evidence()
    first = render_document(evidence)
    second = render_document(evidence)
    assert first.markdown == second.markdown
    assert first.sha256 == second.sha256
    assert first.sha256 != V1_DATABASE_MARKDOWN_SHA256


def test_app_logic_sections_do_not_disturb_v1_sections_or_the_er_diagram() -> None:
    """PRESERVE: v1 sections and the schema-only Mermaid diagram are unchanged."""
    markdown = render_document(catalogue_evidence(), kind="database").markdown
    for section in (
        "Overview & inspection summary",
        "Data dictionary",
        "Keys & indexes",
        "Relationship catalogue",
        "Mermaid ER diagram",
        "Saved queries",
        "Coverage & limitations",
    ):
        assert section in markdown
    er = render_document(catalogue_evidence(), kind="er").markdown
    assert "## Application logic" in er
    assert "Saved queries" not in er
    diagram = er.split("```mermaid", 1)[1].split("```", 1)[0]
    assert "modMain" not in diagram and "frmMain" not in diagram


def test_interpretations_may_cite_app_logic_evidence_ids() -> None:
    """R3: app-logic facts join the known evidence-ID set; unknown IDs still fail."""
    report = render_document(
        catalogue_evidence(),
        interpretations=[{"evidence_ref": "form:frmMain.content", "text": "Main entry form"}],
    )
    assert "`form:frmMain.content` — Main entry form" in report.markdown
    with pytest.raises(ValueError, match="evidence"):
        render_document(catalogue_evidence(), interpretations=[{"evidence_ref": "form:Missing.content", "text": "guess"}])


def test_whole_family_fallback_renders_failed_limitations_not_absent_families() -> None:
    """S3: a Tier 2 fallback renders explicit failed families, distinct from absent ones."""
    evidence = make_evidence()
    evidence.app_logic = AppLogicEvidence(
        content=[
            app_content(category, FAMILY_SCOPE_NAME, state="failed", reason="the disposable copy could not be written")
            for category in ("form", "report", "macro", "vba_module")
        ],
        references=[
            ReferenceEvidence(
                FAMILY_SCOPE_NAME,
                "Access.Application",
                *[
                    FactStatus.failed("the disposable copy could not be written", provenance("reference", FAMILY_SCOPE_NAME, prop))
                    for prop in ("path", "guid", "major", "minor", "built_in")
                ],
            )
        ],
    )

    report = render_document(evidence)

    assert report.status == "partial"
    assert "### Limitations" in report.markdown
    for label in ("VBA modules", "Forms", "Reports", "Macros"):
        assert f"- {label}: failed — the disposable copy could not be written" in report.markdown
    assert "the disposable copy could not be written" in report.markdown.split("### Limitations", 1)[1]
    # The retained v1 sections are untouched by the degraded Tier 2 families.
    assert "## Data dictionary" in report.markdown
    assert "| Parent" in report.markdown
