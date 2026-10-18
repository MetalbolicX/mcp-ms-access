from __future__ import annotations

import json

import pytest

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
from ms_access_mcp.server import build_server


def _minimal_evidence() -> dict:
    def provenance(prop: str) -> FactProvenance:
        return FactProvenance("field", "id", prop)

    field = FieldEvidence(
        name="id",
        type=FactStatus.observed("LONG", provenance("type")),
        size=FactStatus.observed(4, provenance("size")),
        required=FactStatus.observed(True, provenance("required")),
        allow_zero_length=FactStatus.observed(False, provenance("allow_zero_length")),
        default_value=FactStatus.observed(None, provenance("default_value")),
    )
    evidence = EvidenceDoc(
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
    )
    return evidence.to_dict()


def _tool_names(profile: str) -> set[str]:
    server = build_server(profile)
    return set(server._tool_manager._tools)


def _tool_schema(profile: str, name: str) -> dict:
    return build_server(profile)._tool_manager._tools[name].parameters


def test_documentation_tools_are_analysis_only_and_typed():
    assert {"documentation_inspect", "documentation_render", "documentation_save"} <= _tool_names("analysis")
    assert not ({"documentation_inspect", "documentation_render", "documentation_save"} & _tool_names("core"))
    expected = {
        "documentation_inspect": {"path", "tables", "queries", "connection_ref", "include_query_sql"},
        "documentation_render": {"evidence", "kind", "language", "interpretations"},
        "documentation_save": {"report", "output_path", "confirm", "overwrite", "expected_fingerprint"},
    }
    for name, properties in expected.items():
        schema = _tool_schema("analysis", name)
        actual = schema["properties"]
        assert properties <= set(actual)
        assert "params" not in actual


def test_documentation_render_is_pure_and_deterministic():
    from ms_access_mcp.mcp_tools.analysis import documentation_render

    first = documentation_render(_minimal_evidence())
    second = documentation_render(_minimal_evidence())
    assert first.isError is False
    assert json.loads(first.content[0].text) == json.loads(second.content[0].text)
    payload = json.loads(first.content[0].text)
    assert payload["success"] is True
    assert payload["data"]["kind"] == "database"
    assert "# Access database documentation" in payload["data"]["markdown"]


def test_documentation_save_requires_operator_permission_and_confirm(monkeypatch, tmp_path):
    from ms_access_mcp.mcp_tools.analysis import documentation_save

    monkeypatch.delenv("ACCESS_MCP_ALLOW_DOC_WRITE", raising=False)
    denied = documentation_save({"markdown": "report"}, str(tmp_path / "report.md"), confirm=True)
    assert denied.isError is True
    assert "permission denied" in json.loads(denied.content[0].text)["error"]

    monkeypatch.setenv("ACCESS_MCP_ALLOW_DOC_WRITE", "1")
    unconfirmed = documentation_save({"markdown": "report"}, str(tmp_path / "report.md"), confirm=False)
    assert unconfirmed.isError is True
    assert "confirm=True" in json.loads(unconfirmed.content[0].text)["error"]


def test_documentation_cli_surface_has_required_arguments():
    from ms_access_mcp.cli import build_parser

    parser = build_parser()
    main = next(action for action in parser._actions if action.dest == "command")
    family = main.choices["documentation"]
    action = next(item for item in family._actions if item.dest == "documentation_action")
    subcommands = action.choices
    assert set(subcommands) == {"inspect", "render", "save"}
    save_flags = {flag for item in subcommands["save"]._actions for flag in item.option_strings}
    assert {"--allow-doc-write", "--yes", "--overwrite", "--expected-fingerprint"} <= save_flags


@pytest.mark.parametrize("wrapped", [True, False], ids=["envelope", "bare"])
def test_documentation_cli_render_accepts_evidence_file(wrapped, tmp_path, capsys):
    from ms_access_mcp.cli import build_parser

    evidence = _minimal_evidence()
    payload = {"success": True, "data": evidence, "rowcount": None} if wrapped else evidence
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    args = build_parser().parse_args(["documentation", "render", "--evidence", str(path), "--json"])

    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["success"] is True
    assert "# Access database documentation" in result["data"]["markdown"]


@pytest.mark.parametrize("wrapped", [True, False], ids=["envelope", "bare"])
def test_documentation_cli_save_accepts_report_file(wrapped, tmp_path, capsys, monkeypatch):
    from ms_access_mcp.application.documentation_render import render_document
    from ms_access_mcp.cli import build_parser

    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    report = render_document(EvidenceDoc.from_dict(_minimal_evidence())).to_dict()
    payload = {"success": True, "data": report, "rowcount": None} if wrapped else report
    path = tmp_path / "report.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    destination = tmp_path / "report.md"
    args = build_parser().parse_args([
        "documentation", "save", "--report", str(path), "--output-path", str(destination),
        "--allow-doc-write", "--yes", "--json",
    ])

    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["success"] is True
    assert destination.read_text(encoding="utf-8") == report["markdown"]


@pytest.mark.parametrize("action", ["render", "save"])
def test_documentation_cli_surfaces_failed_envelope(action, tmp_path, capsys):
    from ms_access_mcp.cli import build_parser

    path = tmp_path / "failed.json"
    path.write_text(json.dumps({"success": False, "error": "Inspection failed upstream"}), encoding="utf-8")
    flags = ["--evidence", str(path)] if action == "render" else [
        "--report", str(path), "--output-path", str(tmp_path / "report.md"), "--allow-doc-write", "--yes",
    ]
    args = build_parser().parse_args(["documentation", action, *flags, "--json"])

    assert args.func(args) != 0
    result = json.loads(capsys.readouterr().out)
    assert result["success"] is False
    assert result["error"] == "Inspection failed upstream"
    assert not (tmp_path / "report.md").exists()


def test_documentation_inspect_app_logic_parameter_defaults_off() -> None:
    """Requirement 6: the MCP tool exposes the app-logic opt-in as a default-off boolean."""
    schema = _tool_schema("analysis", "documentation_inspect")
    assert "include_app_logic" in schema["properties"]
    assert schema["properties"]["include_app_logic"]["default"] is False


def test_documentation_inspect_passes_the_app_logic_opt_in_to_the_service(monkeypatch) -> None:
    """Requirement 6: default off, and the flag-on value reaches the inspection facade."""
    from ms_access_mcp.mcp_tools import analysis

    seen: list[bool] = []

    def _capture(_path: str, **kwargs: object) -> EvidenceDoc:
        seen.append(bool(kwargs["include_app_logic"]))
        return EvidenceDoc.from_dict(_minimal_evidence())

    monkeypatch.setattr(analysis, "run_documentation_inspection", _capture)

    default_call = analysis.documentation_inspect("db.accdb")
    opted_in = analysis.documentation_inspect("db.accdb", include_app_logic=True)

    assert seen == [False, True]
    assert json.loads(default_call.content[0].text)["success"] is True
    assert json.loads(opted_in.content[0].text)["success"] is True
    default_data = json.loads(default_call.content[0].text)["data"]
    # Default off adds no category; the emitted data stays the v1 shape (+status).
    assert set(default_data) == {"schema", "run", "source", "coverage", "warnings", "data", "status"}
    assert "app_logic" not in json.dumps(default_data)


def test_documentation_prompts_render_deterministically_and_use_real_tools():
    import asyncio

    server = build_server("core")
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    for prompt_name, path in (("document_database", "db.accdb"), ("generate_er_documentation", "er.accdb")):
        result = asyncio.run(server.get_prompt(prompt_name, {"path": path}))
        text = result.messages[0].content.text
        again = asyncio.run(server.get_prompt(prompt_name, {"path": path}))
        assert text == again.messages[0].content.text
        assert path in text
        assert all(tool in names or tool in {"documentation_inspect", "documentation_render", "documentation_save"} for tool in ("documentation_inspect", "documentation_render", "documentation_save"))
        assert all(tool in text for tool in ("documentation_inspect", "documentation_render", "documentation_save"))
        assert "no-completeness" in text.lower() or "do not claim completeness" in text.lower()
    er = asyncio.run(server.get_prompt("generate_er_documentation", {"path": "er.accdb"})).messages[0].content.text
    assert 'kind="er"' in er
    # The prompt states the default-vs-opt-in SQL contract instead of the old
    # contradiction that claimed no saved-query SQL extraction ever happens.
    assert "no query extraction" not in er.lower()
    assert "--include-query-sql" in er
    assert "default" in er.lower()
