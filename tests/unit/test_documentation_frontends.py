from __future__ import annotations

import json

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
        "documentation_inspect": {"path", "tables", "queries", "connection_ref"},
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
    assert "no query extraction" in er.lower()
