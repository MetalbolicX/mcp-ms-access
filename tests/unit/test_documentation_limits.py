"""T4a boundary tests for the standard documentation resource caps (S7).

Units under test are UTF-8 bytes: the evidence/report caps count the canonical JSON
serialization of the evidence/report object, the Markdown cap counts the Markdown
string, and the raw CLI ingress limit is the cap plus a documented envelope allowance.
Acceptance is inclusive (``<=`` cap); above the cap is an ``Err``/``isError`` with no
truncation and no destination or temporary-file side effect.
"""

from __future__ import annotations

import json

import pytest

from ms_access_mcp.application import documentation_limits as limits
from ms_access_mcp.application.documentation import (
    Coverage,
    EvidenceDoc,
    FactProvenance,
    FactStatus,
    FieldEvidence,
    RunMetadata,
    SourceEvidence,
    TableEvidence,
)
from ms_access_mcp.application.documentation_inspect import (
    DocumentationInspectService,
    InspectionCapabilities,
)
from ms_access_mcp.application.documentation_render import ReportDoc, render_document
from ms_access_mcp.application.documentation_save import (
    DocumentationSaveService,
    DocumentationWritePolicy,
)
from ms_access_mcp.errors import AccessMcpError
from ms_access_mcp.models import FieldInfo, TableInfo


def _provenance(prop: str) -> FactProvenance:
    return FactProvenance("field", "id", prop)


def _minimal_evidence(*, warnings: list[str] | None = None) -> EvidenceDoc:
    field = FieldEvidence(
        name="id",
        type=FactStatus.observed("LONG", _provenance("type")),
        size=FactStatus.observed(4, _provenance("size")),
        required=FactStatus.observed(True, _provenance("required")),
        allow_zero_length=FactStatus.observed(False, _provenance("allow_zero_length")),
        default_value=FactStatus.observed(None, _provenance("default_value")),
    )
    return EvidenceDoc(
        run=RunMetadata("run-1", "1.0", ["Items"], []),
        source=SourceEvidence("sample.accdb", "ACE", "DAO", "offline", "caller-managed"),
        coverage=Coverage(),
        tables=[
            TableEvidence(
                "Items",
                [field],
                FactStatus.observed(["id"], FactProvenance("table", "Items", "primary_key")),
            )
        ],
        warnings=list(warnings or []),
    )


def _evidence_dict_with_json_size(target: int) -> dict:
    """Return a valid evidence dict whose canonical JSON is exactly ``target`` bytes."""
    base = _minimal_evidence(warnings=["a"]).to_dict()
    base_size = limits.json_utf8_size(base, limit=10**9)
    assert base_size is not None
    length = target - (base_size - 1)
    assert length >= 1, f"target {target} is below the minimal evidence size {base_size}"
    base["warnings"] = ["a" * length]
    assert len(json.dumps(base).encode("utf-8")) == target
    return base


class _MinimalPort:
    """Smallest conforming inspection port: one local table with one field."""

    def capabilities(self) -> InspectionCapabilities:
        return InspectionCapabilities()

    def list_tables(self) -> list[TableInfo]:
        return [TableInfo(name="Items", fields=[FieldInfo(name="id", type="LONG", size=4, required=True, allow_zero_length=False, default_value=None)])]

    def get_table_link_metadata(self, table_name: str) -> dict | None:
        return None

    def get_indexes(self, table_name: str) -> list:
        return []

    def get_relationships(self) -> list:
        return []

    def get_queries(self) -> list:
        return []

    def get_query_parameters(self, query_name: str) -> list | None:
        return None

    def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# 1. Profile constants and unit definition
# ---------------------------------------------------------------------------


def test_standard_limit_profile_constants_are_pinned() -> None:
    """The selected standard profile is fixed to the recorded values."""
    assert limits.EVIDENCE_MAX_BYTES == 32 * 1024 * 1024
    assert limits.MARKDOWN_MAX_BYTES == 16 * 1024 * 1024
    assert limits.REPORT_JSON_MAX_BYTES == 16 * 1024 * 1024
    assert limits.CLI_ENVELOPE_ALLOWANCE_BYTES == 1024
    assert limits.raw_evidence_file_limit() == 32 * 1024 * 1024 + 1024
    assert limits.raw_report_file_limit() == 16 * 1024 * 1024 + 1024


def test_json_size_counts_utf8_escaped_bytes_not_python_characters() -> None:
    """The canonical size is UTF-8 JSON bytes with escapes, never ``len(str)``."""
    value = {"markdown": 'a"b\\c\né'}
    text = 'a"b\\c\né'
    measured = limits.json_utf8_size(value, limit=10**9)
    assert measured == len(json.dumps(value).encode("utf-8"))
    assert measured > len(text)


# ---------------------------------------------------------------------------
# 2. Evidence cap: generated evidence and decoded application inputs
# ---------------------------------------------------------------------------


def test_decoded_evidence_cap_is_inclusive_and_rejects_one_byte_over(monkeypatch) -> None:
    target = 1800
    data = _evidence_dict_with_json_size(target)

    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", target + 1)
    EvidenceDoc.from_dict(data).to_dict()

    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", target)
    restored = EvidenceDoc.from_dict(data)
    assert restored.to_dict()["warnings"] == data["warnings"]

    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", target - 1)
    with pytest.raises(AccessMcpError, match=str(target - 1)):
        EvidenceDoc.from_dict(data)


def test_generated_inspection_evidence_is_capped_at_the_service_seam(monkeypatch) -> None:
    generated = DocumentationInspectService().inspect(_MinimalPort(), tables=None, queries=None)
    size = len(json.dumps(generated.to_dict()).encode("utf-8"))

    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", size)
    DocumentationInspectService().inspect(_MinimalPort(), tables=None, queries=None)

    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", size - 1)
    with pytest.raises(AccessMcpError, match=str(size - 1)):
        DocumentationInspectService().inspect(_MinimalPort(), tables=None, queries=None)


def test_render_rejects_evidence_over_the_cap_through_the_direct_service(monkeypatch) -> None:
    evidence = _minimal_evidence()
    size = len(json.dumps(evidence.to_dict()).encode("utf-8"))
    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", size - 1)
    with pytest.raises(AccessMcpError, match=str(size - 1)):
        render_document(evidence)


# ---------------------------------------------------------------------------
# 3. Markdown cap: render and save
# ---------------------------------------------------------------------------


def test_render_markdown_cap_is_inclusive(monkeypatch) -> None:
    evidence = _minimal_evidence()
    baseline = render_document(evidence)
    markdown_bytes = len(baseline.markdown.encode("utf-8"))

    monkeypatch.setattr(limits, "MARKDOWN_MAX_BYTES", markdown_bytes)
    assert render_document(evidence).markdown == baseline.markdown

    monkeypatch.setattr(limits, "MARKDOWN_MAX_BYTES", markdown_bytes - 1)
    with pytest.raises(AccessMcpError, match=str(markdown_bytes - 1)):
        render_document(evidence)


def test_save_markdown_cap_counts_utf8_bytes_and_leaves_no_output(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    monkeypatch.setattr(limits, "MARKDOWN_MAX_BYTES", 10)
    service = DocumentationSaveService(DocumentationWritePolicy(allowed=True))
    # "é" is one Python character but two UTF-8 bytes.
    accepted = service.save("é" * 5, str(tmp_path / "accepted.md"), confirm=True)
    assert accepted.bytes_written == 10

    destination = tmp_path / "nested" / "rejected.md"
    with pytest.raises(AccessMcpError, match="10"):
        service.save("é" * 6, str(destination), confirm=True)
    assert not destination.exists()
    assert not destination.parent.exists()
    assert list(tmp_path.rglob("*.tmp")) == []


# ---------------------------------------------------------------------------
# 4. Report JSON cap: render output and decoded save input
# ---------------------------------------------------------------------------


def test_report_json_cap_is_inclusive_for_decoded_report(monkeypatch) -> None:
    data = render_document(_minimal_evidence()).to_dict()
    size = len(json.dumps(data).encode("utf-8"))

    monkeypatch.setattr(limits, "REPORT_JSON_MAX_BYTES", size)
    assert ReportDoc.from_dict(data).to_dict()["sha256"] == data["sha256"]

    monkeypatch.setattr(limits, "REPORT_JSON_MAX_BYTES", size - 1)
    with pytest.raises(AccessMcpError, match=str(size - 1)):
        ReportDoc.from_dict(data)


def test_rendered_report_json_over_the_cap_is_rejected_on_emission(monkeypatch) -> None:
    size = len(json.dumps(render_document(_minimal_evidence()).to_dict()).encode("utf-8"))
    monkeypatch.setattr(limits, "REPORT_JSON_MAX_BYTES", size - 1)
    with pytest.raises(AccessMcpError, match=str(size - 1)):
        render_document(_minimal_evidence()).to_dict()


# ---------------------------------------------------------------------------
# 5. Bounded CLI ingress
# ---------------------------------------------------------------------------


def test_bounded_json_read_accepts_exact_limit_and_rejects_before_parsing(tmp_path) -> None:
    limit = 64
    exact = tmp_path / "exact.json"
    payload = json.dumps({"a": "b"}).encode("utf-8")
    exact.write_bytes(payload + b" " * (limit - len(payload)))
    assert limits.read_bounded_json_file(exact, limit=limit) == {"a": "b"}

    over = tmp_path / "over.json"
    over.write_bytes(b"{" + b"x" * limit)  # limit + 1 bytes, deliberately not JSON
    with pytest.raises(AccessMcpError, match=str(limit)):
        limits.read_bounded_json_file(over, limit=limit)


def test_real_default_evidence_ingress_limit_rejects_an_oversized_file(tmp_path) -> None:
    path = tmp_path / "huge.json"
    with path.open("wb") as handle:
        handle.write(b"{")
        handle.truncate(limits.raw_evidence_file_limit() + 1)
    with pytest.raises(AccessMcpError, match=str(limits.raw_evidence_file_limit())):
        limits.read_evidence_json_file(path)


def test_cli_render_rejects_oversized_evidence_before_parsing(monkeypatch, tmp_path, capsys) -> None:
    from ms_access_mcp.cli import build_parser

    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", 32)
    raw_limit = limits.raw_evidence_file_limit()
    path = tmp_path / "big.json"
    path.write_bytes(b"{" + b"x" * raw_limit)  # raw_limit + 1 bytes

    args = build_parser().parse_args(["documentation", "render", "--evidence", str(path), "--json"])
    assert args.func(args) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["success"] is False
    assert str(raw_limit) in payload["error"]
    assert captured.err == ""


def test_cli_render_accepts_an_exact_cap_ok_envelope_inside_the_raw_allowance(
    monkeypatch, tmp_path, capsys
) -> None:
    from ms_access_mcp.cli import build_parser

    target = 1800
    data = _evidence_dict_with_json_size(target)
    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", target)
    raw = json.dumps({"success": True, "data": data, "rowcount": None}).encode("utf-8")
    assert target < len(raw) <= limits.raw_evidence_file_limit()
    path = tmp_path / "evidence.json"
    path.write_bytes(raw)

    args = build_parser().parse_args(["documentation", "render", "--evidence", str(path), "--json"])
    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["success"] is True
    assert "# Access database documentation" in result["data"]["markdown"]


def test_cli_save_rejects_oversized_report_before_creating_destination(monkeypatch, tmp_path, capsys) -> None:
    from ms_access_mcp.cli import build_parser

    monkeypatch.setattr(limits, "REPORT_JSON_MAX_BYTES", 16)
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    raw_limit = limits.raw_report_file_limit()
    path = tmp_path / "big-report.json"
    path.write_bytes(b"{" + b"x" * raw_limit)
    destination = tmp_path / "nested" / "report.md"

    args = build_parser().parse_args([
        "documentation", "save", "--report", str(path), "--output-path", str(destination),
        "--allow-doc-write", "--yes", "--json",
    ])
    assert args.func(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    assert str(raw_limit) in payload["error"]
    assert not destination.exists()
    assert not destination.parent.exists()


# ---------------------------------------------------------------------------
# 6. MCP frontend parity
# ---------------------------------------------------------------------------


def test_mcp_render_rejects_oversized_evidence_as_iserror(monkeypatch) -> None:
    from ms_access_mcp.mcp_tools.analysis import documentation_render

    data = _evidence_dict_with_json_size(1800)
    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", 1799)
    result = documentation_render(data)
    assert result.isError is True
    payload = json.loads(result.content[0].text)
    assert payload["success"] is False
    assert "1799" in payload["error"]


def test_mcp_save_rejects_oversized_report_json_as_iserror(monkeypatch, tmp_path) -> None:
    from ms_access_mcp.mcp_tools.analysis import documentation_save

    report = render_document(_minimal_evidence()).to_dict()
    size = len(json.dumps(report).encode("utf-8"))
    monkeypatch.setattr(limits, "REPORT_JSON_MAX_BYTES", size - 1)
    monkeypatch.setenv("ACCESS_MCP_ALLOW_DOC_WRITE", "1")
    destination = tmp_path / "out.md"

    result = documentation_save(report, str(destination), confirm=True)
    assert result.isError is True
    assert not destination.exists()


# ---------------------------------------------------------------------------
# 7. PRESERVE: ordinary smaller evidence/report behavior is unchanged
# ---------------------------------------------------------------------------


def test_small_evidence_round_trips_and_renders_with_stable_fingerprint() -> None:
    evidence = _minimal_evidence()
    assert EvidenceDoc.from_dict(evidence.to_dict()) == evidence
    first = render_document(evidence)
    second = render_document(EvidenceDoc.from_dict(evidence.to_dict()))
    assert first.markdown == second.markdown
    assert first.sha256 == second.sha256


def test_small_markdown_save_writes_exact_bytes_and_sha(monkeypatch, tmp_path) -> None:
    import hashlib

    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    service = DocumentationSaveService(DocumentationWritePolicy(allowed=True))
    content = "# Report\n"
    destination = tmp_path / "report.md"
    result = service.save(content, str(destination), confirm=True)
    assert destination.read_text(encoding="utf-8") == content
    assert result.bytes_written == len(content.encode("utf-8"))
    assert result.sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 8. B1: the final emitted evidence payload (including top-level status) is capped
# ---------------------------------------------------------------------------


def _status_overhead(evidence: EvidenceDoc) -> int:
    """Bytes the frontends add by inserting top-level ``status`` after ``to_dict``."""
    base = evidence.to_dict()
    final = dict(base)
    final["status"] = evidence.status
    return len(json.dumps(final).encode("utf-8")) - len(json.dumps(base).encode("utf-8"))


def _evidence_with_final_payload_size(target: int) -> EvidenceDoc:
    """Evidence whose ``to_dict`` plus top-level status canonical JSON is exactly ``target``."""
    probe = EvidenceDoc.from_dict(_evidence_dict_with_json_size(target))
    overhead = _status_overhead(probe)
    evidence = EvidenceDoc.from_dict(_evidence_dict_with_json_size(target - overhead))
    payload = evidence.to_dict()
    payload["status"] = evidence.status
    assert len(json.dumps(payload).encode("utf-8")) == target
    return evidence


def _run_cli_inspect(monkeypatch, evidence: EvidenceDoc, capsys):
    from ms_access_mcp import cli

    # The supervised facade is the one source-open seam; inject the evidence it returns.
    monkeypatch.setattr(cli, "run_documentation_inspection", lambda *a, **k: evidence)
    args = cli.build_parser().parse_args(["documentation", "inspect", "--db", "core.accdb", "--json"])
    code = args.func(args)
    return code, capsys.readouterr()


def _run_mcp_inspect(monkeypatch, evidence: EvidenceDoc):
    from ms_access_mcp.mcp_tools import analysis

    class _FakeDatabase:
        def inspection_context(self) -> tuple[str, list]:
            return "auto", []

    monkeypatch.setattr(analysis, "_new_db", lambda readonly=True: _FakeDatabase())
    monkeypatch.setattr(analysis, "run_documentation_inspection", lambda *a, **k: evidence)
    return analysis.documentation_inspect("core.accdb")


def test_cli_inspect_rejects_final_status_payload_over_the_cap(monkeypatch, capsys) -> None:
    target = 1800
    evidence = EvidenceDoc.from_dict(_evidence_dict_with_json_size(target))
    overhead = _status_overhead(evidence)
    assert overhead > 0
    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", target)

    code, captured = _run_cli_inspect(monkeypatch, evidence, capsys)
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["success"] is False
    assert str(target) in payload["error"]
    assert captured.err == ""


def test_mcp_inspect_rejects_final_status_payload_over_the_cap(monkeypatch) -> None:
    target = 1800
    evidence = EvidenceDoc.from_dict(_evidence_dict_with_json_size(target))
    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", target)
    result = _run_mcp_inspect(monkeypatch, evidence)
    assert result.isError is True
    payload = json.loads(result.content[0].text)
    assert payload["success"] is False
    assert str(target) in payload["error"]


def test_cli_and_mcp_accept_and_roundtrip_an_exact_final_cap_payload(monkeypatch, capsys) -> None:
    target = 1800
    evidence = _evidence_with_final_payload_size(target)
    monkeypatch.setattr(limits, "EVIDENCE_MAX_BYTES", target)

    code, captured = _run_cli_inspect(monkeypatch, evidence, capsys)
    assert code == 0
    cli_payload = json.loads(captured.out)["data"]
    assert len(json.dumps(cli_payload).encode("utf-8")) == target
    # The accepted cap-bound payload self-roundtrips under the same inclusive cap.
    assert EvidenceDoc.from_dict(cli_payload) == evidence

    result = _run_mcp_inspect(monkeypatch, evidence)
    assert result.isError is False
    mcp_payload = json.loads(result.content[0].text)["data"]
    assert mcp_payload == cli_payload
    assert len(json.dumps(mcp_payload).encode("utf-8")) == target
    assert EvidenceDoc.from_dict(mcp_payload) == evidence
