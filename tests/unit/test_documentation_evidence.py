from __future__ import annotations

from collections.abc import Callable

import pytest

from ms_access_mcp.application.documentation import (
    ContentObjectEvidence,
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
    safe_documentation_error_reason,
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


VBA_SHA256 = "d" * 64


def vba_content(value: object = None, *, name: str = "modMain", state: str = "observed", reason: str | None = None) -> ContentObjectEvidence:
    prop = provenance("vba_module", name, "content")
    fact = FactStatus.observed(value, prop) if state == "observed" else getattr(FactStatus, state)(reason, prop)
    return ContentObjectEvidence("vba_module", name, "access-application", fact)


def form_content(value: object = None, *, name: str = "frmMain", state: str = "observed", reason: str | None = None) -> ContentObjectEvidence:
    prop = provenance("form", name, "content")
    fact = FactStatus.observed(value, prop) if state == "observed" else getattr(FactStatus, state)(reason, prop)
    return ContentObjectEvidence("form", name, "access-application", fact)


# The two validator branches that share the digest/size contract (S3 H1).
CONTENT_BRANCHES = [
    pytest.param(vba_content, "Observed vba_module content", id="vba_module"),
    pytest.param(form_content, "Observed form/report/macro content", id="form"),
]


@pytest.mark.parametrize(
    "value",
    [
        {"type": "Standard", "procedures": ["Main"], "bytes": 42},
        {"type": "Standard", "procedures": ["Main"], "sha256": "", "bytes": 42},
        {"type": "Standard", "procedures": ["Main"], "sha256": 42, "bytes": 42},
    ],
    ids=["missing", "empty", "non-string"],
)
def test_observed_vba_module_content_requires_a_sha256_string(value: dict[str, object]) -> None:
    """S1: observed VBA evidence carries a digest exactly like SaveAsText content."""
    with pytest.raises(ValueError, match="Observed vba_module content requires a sha256 string") as excinfo:
        vba_content(value).validate()
    assert str(excinfo.value) == "Observed vba_module content requires a sha256 string"


@pytest.mark.parametrize(
    "value",
    [
        {"type": "Standard", "sha256": VBA_SHA256},
        {"type": "Standard", "sha256": VBA_SHA256, "bytes": True},
        {"type": "Standard", "sha256": VBA_SHA256, "bytes": 42.0},
    ],
    ids=["missing", "bool", "float"],
)
def test_observed_vba_module_content_requires_a_byte_size_integer(value: dict[str, object]) -> None:
    """S1: a byte size must be an int and never a bool or a float."""
    with pytest.raises(ValueError, match="Observed vba_module content requires a byte size integer") as excinfo:
        vba_content(value).validate()
    assert str(excinfo.value) == "Observed vba_module content requires a byte size integer"


def test_minimal_observed_vba_module_content_with_a_digest_is_valid() -> None:
    """S1: type plus procedures plus the digest is the minimal accepted shape."""
    evidence = vba_content({"type": "Standard", "procedures": ["Main"], "sha256": VBA_SHA256, "bytes": 42})
    assert evidence.validate() is None
    assert ContentObjectEvidence.from_dict(evidence.to_dict()) == evidence


def test_observed_vba_module_content_stays_valid_without_type_and_procedures() -> None:
    """S1: ``type``/``procedures`` stay optional; only the digest is required."""
    assert vba_content({"sha256": VBA_SHA256, "bytes": 42}).validate() is None


@pytest.mark.parametrize("state", ["failed", "unavailable"])
def test_non_observed_vba_module_family_fact_needs_no_digest(state: str) -> None:
    """S1: a non-observed family fact returns early and stays valid without a digest."""
    fact = vba_content(None, name="<all objects>", state=state, reason="VBProject access failed")
    assert fact.validate() is None


@pytest.mark.parametrize(
    "digest",
    [
        "D" * 64,
        "d" * 63,
        "d" * 65,
        "g" * 64,
        " d" * 32,
        "sha256:" + "d" * 64,
    ],
    ids=["uppercase", "too-short", "too-long", "non-hex", "whitespace", "prefixed"],
)
@pytest.mark.parametrize(("make_content", "prefix"), CONTENT_BRANCHES)
def test_observed_content_sha256_must_be_64_lowercase_hex(
    make_content: Callable[..., ContentObjectEvidence], prefix: str, digest: str
) -> None:
    """S3 H1: a recorded digest must be exactly 64 lowercase hex characters."""
    with pytest.raises(ValueError) as excinfo:
        make_content({"sha256": digest, "bytes": 42}).validate()

    assert str(excinfo.value) == f"{prefix} requires a 64-character lowercase hexadecimal sha256"


@pytest.mark.parametrize(("make_content", "prefix"), CONTENT_BRANCHES)
def test_observed_content_byte_size_must_not_be_negative(
    make_content: Callable[..., ContentObjectEvidence], prefix: str
) -> None:
    """S3 H1: a byte size is a count, so a negative integer is rejected in both branches."""
    with pytest.raises(ValueError) as excinfo:
        make_content({"sha256": VBA_SHA256, "bytes": -1}).validate()

    assert str(excinfo.value) == f"{prefix} requires a non-negative byte size"


@pytest.mark.parametrize("size", [0, 4096])
@pytest.mark.parametrize(("make_content", "prefix"), CONTENT_BRANCHES)
def test_observed_content_accepts_zero_and_positive_byte_sizes(
    make_content: Callable[..., ContentObjectEvidence], prefix: str, size: int
) -> None:
    """S3 H1: zero and positive sizes stay valid, so an empty export is still recorded."""
    assert make_content({"sha256": VBA_SHA256, "bytes": size}).validate() is None


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"type": "Standard", "bytes": 42}, "Observed vba_module content requires a sha256 string"),
        (
            {"type": "Standard", "sha256": 42, "bytes": 42},
            "Observed vba_module content requires a sha256 string",
        ),
        ({"type": "Standard", "sha256": VBA_SHA256}, "Observed vba_module content requires a byte size integer"),
        (
            {"type": "Standard", "sha256": VBA_SHA256, "bytes": True},
            "Observed vba_module content requires a byte size integer",
        ),
    ],
    ids=["missing-digest", "non-string-digest", "missing-size", "bool-size"],
)
def test_the_shape_checks_keep_the_existing_messages_for_missing_and_wrong_types(
    value: dict[str, object], message: str
) -> None:
    """PRESERVE: a valid digest with a missing/bool size still reports the original message."""
    with pytest.raises(ValueError) as excinfo:
        vba_content(value).validate()
    assert str(excinfo.value) == message


# ---------------------------------------------------------------------------
# Strict class-only diagnostics: the OS code is a real int or nothing (S2)
# ---------------------------------------------------------------------------

_HOSTILE_PATH = "C:/Users/fakeuser123/secret"


class _FormattingCode(int):
    """An ``int`` subclass whose ``__format__`` returns arbitrary text (a leak vector)."""

    def __format__(self, _spec: str) -> str:
        return _HOSTILE_PATH


def _os_error_with(*, errno: object = None, winerror: object = None) -> OSError:
    """A bare ``OSError`` whose numeric fields are set exactly as given."""
    error = OSError("hidden")
    error.errno = errno  # type: ignore[assignment]
    error.winerror = winerror  # type: ignore[attr-defined]
    return error


@pytest.mark.parametrize(
    ("errno", "winerror", "expected"),
    [
        pytest.param(_FormattingCode(13), None, "OSError", id="subclass-errno"),
        pytest.param(_FormattingCode(13), 32, "OSError: [WinError 32]", id="subclass-errno-real-winerror"),
        pytest.param(13, _FormattingCode(32), "OSError: [Errno 13]", id="subclass-winerror"),
        pytest.param(True, None, "OSError", id="bool-errno"),
        pytest.param(13, True, "OSError: [Errno 13]", id="bool-winerror"),
        pytest.param(_HOSTILE_PATH, None, "OSError", id="string-errno"),
        pytest.param(13, _HOSTILE_PATH, "OSError: [Errno 13]", id="string-winerror"),
        pytest.param(_HOSTILE_PATH, _HOSTILE_PATH, "OSError", id="both-hostile"),
    ],
)
def test_non_int_os_codes_are_rejected_instead_of_formatted(
    errno: object, winerror: object, expected: str
) -> None:
    """S2: only a real ``int`` code is rendered; a bool, a string, or an int subclass is not."""
    reason = safe_documentation_error_reason(_os_error_with(errno=errno, winerror=winerror))

    assert reason == expected
    assert _HOSTILE_PATH not in reason


def test_genuine_int_os_codes_keep_the_established_output() -> None:
    """PRESERVE: real integer codes keep the class/code shape (winerror preferred)."""
    assert safe_documentation_error_reason(_os_error_with(errno=13)) == "OSError: [Errno 13]"
    assert safe_documentation_error_reason(_os_error_with(errno=13, winerror=32)) == "OSError: [WinError 32]"
    assert safe_documentation_error_reason(_os_error_with()) == "OSError"
