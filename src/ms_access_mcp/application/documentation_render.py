"""Render documentation evidence as deterministic Markdown.

Mermaid nodes use stable aliases ``t1`` through ``tn`` in evidence table order;
original table names are listed separately after the diagram. Field attributes are
shown only when their type fact is observed. A relationship is diagrammed only if
its parent columns exactly match an observed primary/unique index; the child end is
one-to-one (``||``) only when its columns match an observed primary/unique index,
and otherwise is many (``}o``). Relationships without proven parent uniqueness are
omitted from the diagram and remain visible in the catalogue. No cardinality is
inferred from relationship flags. Relationship auto-indexes (an index whose name
matches a relationship on that table's same child columns) remain listed as
observed but cannot justify uniqueness at either diagram endpoint, even when DAO
reports Unique=True. Independent primary/unique keys still justify uniqueness.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

from ms_access_mcp.application.documentation import (
    EvidenceDoc,
    FactStatus,
    RelationshipEvidence,
    sanitize_connection_string,
)
from ms_access_mcp.application.documentation_limits import (
    ensure_markdown_within_limit,
    ensure_report_within_limit,
)

ReportKind = Literal["database", "er"]
REPORT_SCHEMA = "access.documentation.report/1"


@dataclass
class ReportDoc:
    schema: Literal["access.documentation.report/1"]
    kind: ReportKind
    markdown: str
    status: Literal["complete", "partial"]
    coverage: dict[str, Any]
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema": self.schema,
            "kind": self.kind,
            "markdown": self.markdown,
            "status": self.status,
            "coverage": self.coverage,
            "sha256": self.sha256,
        }
        ensure_report_within_limit(result)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportDoc:
        ensure_report_within_limit(data)
        if data.get("schema") != REPORT_SCHEMA:
            raise ValueError(f"Unsupported report schema: {data.get('schema')}")
        report = cls(
            schema=data["schema"],
            kind=data["kind"],
            markdown=data["markdown"],
            status=data["status"],
            coverage=data["coverage"],
            sha256=data["sha256"],
        )
        expected = hashlib.sha256(report.markdown.encode("utf-8")).hexdigest()
        if report.sha256 != expected:
            raise ValueError("Report SHA-256 does not match Markdown bytes")
        if report.kind not in ("database", "er") or report.status not in ("complete", "partial"):
            raise ValueError("Invalid report kind or status")
        return report


def render_document(
    evidence: EvidenceDoc,
    *,
    kind: ReportKind = "database",
    interpretations: list[dict[str, str]] | None = None,
    language: str = "en",
) -> ReportDoc:
    """Render evidence without I/O; report bytes are stable for the same inputs."""
    if language != "en":
        raise ValueError(f"Unsupported language: {language}; only English is currently qualified")
    if kind not in ("database", "er"):
        raise ValueError(f"Unsupported report kind: {kind}")
    # Serializing here both validates and enforces the evidence byte cap; the renderer
    # then reads the same validated object directly.
    evidence.to_dict()
    interpretations = interpretations or []
    known_refs = _known_refs(evidence)
    for item in interpretations:
        if set(item) != {"evidence_ref", "text"} or not isinstance(item["text"], str):
            raise ValueError("Interpretations require evidence_ref and text strings")
        if item["evidence_ref"] not in known_refs:
            raise ValueError(f"Unknown evidence reference: {item['evidence_ref']}")

    sections = ["# Access database documentation", _overview(evidence, include_queries=kind == "database")]
    sections.extend((_dictionary(evidence), _keys(evidence), _relationships(evidence)))
    sections.append(_diagram(evidence))
    if kind == "database":
        sections.extend((_queries(evidence), _coverage(evidence)))
    if interpretations:
        sections.append(_interpretations(interpretations))
    markdown = "\n\n".join(section.rstrip() for section in sections) + "\n"
    ensure_markdown_within_limit(markdown)
    coverage = _coverage_summary(evidence)
    return ReportDoc(
        schema=REPORT_SCHEMA,
        kind=kind,
        markdown=markdown,
        status=evidence.status,
        coverage=coverage,
        sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    )


def _overview(evidence: EvidenceDoc, *, include_queries: bool) -> str:
    lines = [
        "## Overview & inspection summary",
        "",
        f"- Source: {_inline(evidence.source.path)} ({_inline(evidence.source.format)})",
        f"- Backend: {_inline(evidence.source.backend_identity)}",
        f"- Inspection mode: {_inline(evidence.source.acquisition_mode)}; consistency marker: {_inline(evidence.source.consistency_marker)}",
        f"- Run: {_inline(evidence.run.run_id)}; extractor: {_inline(evidence.run.extractor_version)}",
        f"- Status: **{evidence.status}**",
        "- Tables: " + ", ".join(_inline(name) for name in evidence.run.scope_tables),
    ]
    if include_queries:
        lines.append("- Saved queries: " + ", ".join(_inline(name) for name in evidence.run.scope_queries))
    lines.extend(f"- Warning: {_inline(warning)}" for warning in evidence.warnings)
    return "\n".join(lines)


def _dictionary(evidence: EvidenceDoc) -> str:
    lines = ["## Data dictionary"]
    for table in evidence.tables:
        lines.extend(("", f"### {_inline(table.name)}", ""))
        if table.kind == "linked":
            lines.append(
                f"- Linked table: {_inline(table.name)} → "
                f"{_inline(_linked_source(table.connect, table.source_table_name))}"
            )
            lines.append("")
        lines.extend(("| Field | Type | Size | Required | Allow zero length | Default |", "|---|---|---:|---|---|---|"))
        for item in table.fields:
            cells = [
                item.name,
                _fact_cell(item.type),
                _fact_cell(item.size),
                _fact_cell(item.required),
                _fact_cell(item.allow_zero_length),
                _fact_cell(item.default_value),
            ]
            lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
        if not table.fields:
            lines.append("| _No fields observed_ | — | — | — | — | — |")
    return "\n".join(lines)


def _keys(evidence: EvidenceDoc) -> str:
    lines = ["## Keys & indexes"]
    for table in evidence.tables:
        pk = _fact_cell(table.primary_key)
        lines.extend(("", f"### {_inline(table.name)}", f"- Primary key: {_cell(pk)}"))
        if table.indexes:
            lines.extend(("", "| Index | Columns | Unique | Primary | Ignore nulls |", "|---|---|---:|---:|---:|"))
            for index in table.indexes:
                lines.append("| " + " | ".join(_cell(value) for value in (index.name, ", ".join(index.columns), str(index.is_unique), str(index.is_primary), str(index.ignore_nulls))) + " |")
        else:
            lines.append("- Indexes: observed empty")
    return "\n".join(lines)


def _relationships(evidence: EvidenceDoc) -> str:
    lines = ["## Relationship catalogue", "", "| Relationship | Parent → child | Column pairs (ALIGNED) | Attributes | Diagram eligibility |", "|---|---|---|---|---|"]
    for relation in evidence.relationships:
        pairs = "; ".join(f"{parent} → {child}" for parent, child in zip(relation.parent_columns, relation.child_columns, strict=True))
        derivable = _relationship_cardinality(evidence, relation)
        reason = "cardinality not derivable from evidence" if not derivable else "included"
        parent_child = f"{relation.parent_table} → {relation.child_table}"
        lines.append("| " + " | ".join(_cell(value) for value in (relation.name, parent_child, pairs, _fact_cell(relation.attributes), reason)) + " |")
    if not evidence.relationships:
        lines.append("| _No relationships observed_ | — | — | — | — |")
    return "\n".join(lines)


def _diagram(evidence: EvidenceDoc) -> str:
    aliases = {table.name: f"t{index}" for index, table in enumerate(evidence.tables, 1)}
    lines = ["## Mermaid ER diagram", "", "```mermaid", "erDiagram"]
    for table in evidence.tables:
        alias = aliases[table.name]
        lines.append(f"  {alias} {{")
        for item in table.fields:
            if item.type.state == "observed":
                lines.append(f"    {_mermaid_type(str(item.type.value))} {_mermaid_name(item.name)}")
        lines.append("  }")
    for relation in evidence.relationships:
        if not _relationship_cardinality(evidence, relation):
            continue
        parent_end = "||"
        child_end = "||" if _matches_unique(evidence, relation.child_table, relation.child_columns) else "}o"
        lines.append(f"  {aliases[relation.child_table]} {child_end}--{parent_end} {aliases[relation.parent_table]} : \"{_mermaid_label(relation.name)}\"")
    lines.extend(("```", "", "| Alias | Table |", "|---|---|"))
    for name, alias in aliases.items():
        lines.append(f"| {alias} | {_cell(name)} |")
    return "\n".join(lines)


def _queries(evidence: EvidenceDoc) -> str:
    lines = ["## Saved queries"]
    for query in evidence.queries:
        lines.extend((
            "",
            f"### {_inline(query.name)}",
            f"- Type: {_fact_cell(query.type)}",
            f"- Parameters: {_parameters_cell(query.parameters)}",
            "- SQL (source text; not executed):",
        ))
        if query.sql.state == "observed":
            lines.extend(("", "```sql", query.sql.value, "```"))
        else:
            lines.append(f"  - SQL **{query.sql.state}**: {_inline(query.sql.reason or '')}")
    if not evidence.queries:
        lines.extend(("", "No saved queries observed."))
    return "\n".join(lines)


def _coverage(evidence: EvidenceDoc) -> str:
    summary = _coverage_summary(evidence)
    lines = ["## Coverage & limitations", "", f"- Status: **{evidence.status}**"]
    for category, values in summary.items():
        if category == "warnings":
            continue
        lines.append(f"- {category.title()}: inspected {values['inspected']}; excluded {values['excluded']}; unsupported {values['unsupported']}; failed {values['failed']}")
    for category, field_name in (("Excluded", "excluded"), ("Unsupported", "unsupported"), ("Failed", "failed")):
        for group in ("tables", "queries", "relationships"):
            for name, reason in getattr(evidence.coverage, group).to_dict()[field_name].items():
                lines.append(f"- {category} ({group}): {_inline(name)} — {_inline(reason)}")
    for warning in evidence.warnings:
        lines.append(f"- Warning: {_inline(warning)}")
    return "\n".join(lines)


def _interpretations(items: list[dict[str, str]]) -> str:
    lines = ["## Interpretations (generated by the client, not facts)"]
    lines.extend(f"- `{item['evidence_ref']}` — {item['text']}" for item in items)
    return "\n".join(lines)


def _coverage_summary(evidence: EvidenceDoc) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for category in ("tables", "queries", "relationships"):
        value = getattr(evidence.coverage, category)
        result[category] = {
            "inspected": len(value.inspected),
            "excluded": len(value.excluded),
            "unsupported": len(value.unsupported),
            "failed": len(value.failed),
        }
    result["warnings"] = len(evidence.warnings)
    return result


def _known_refs(evidence: EvidenceDoc) -> set[str]:
    refs: set[str] = set()
    for table in evidence.tables:
        facts: list[FactStatus[Any]] = [table.primary_key]
        facts.extend(fact for field in table.fields for fact in (field.type, field.size, field.required, field.allow_zero_length, field.default_value))
        for fact in facts:
            refs.add(_fact_ref(fact))
    for relation in evidence.relationships:
        refs.add(_fact_ref(relation.attributes))
    for query in evidence.queries:
        for fact in (query.sql, query.type, query.parameters):
            refs.add(_fact_ref(fact))
    return refs


def _fact_ref(fact: FactStatus[Any]) -> str:
    provenance = fact.provenance
    return f"{provenance.object_kind}:{provenance.object_name}.{provenance.property}"


def _fact_cell(fact: FactStatus[Any]) -> str:
    if fact.state == "observed":
        value = fact.value
        return "observed empty" if value == [] else "observed null" if value is None else str(value)
    return f"{fact.state}: {fact.reason}"


def _parameters_cell(fact: FactStatus[Any]) -> str:
    """Render a query's declared parameters as ``name (DAO type name)`` entries.

    Non-observed facts keep the standard ``state: reason`` form; an observed empty
    list stays a visible known empty, never fabricated parameter names.
    """
    if fact.state != "observed":
        return _fact_cell(fact)
    parameters = fact.value
    if not parameters:
        return "observed empty"
    return "; ".join(
        f"{_inline(str(parameter['name']))} ({_inline(str(parameter['type_name']))})"
        for parameter in parameters
    )


def _relationship_cardinality(evidence: EvidenceDoc, relation: RelationshipEvidence) -> bool:
    return _matches_unique(evidence, relation.parent_table, relation.parent_columns)


def _matches_unique(evidence: EvidenceDoc, table_name: str, columns: list[str]) -> bool:
    table = next((item for item in evidence.tables if item.name == table_name), None)
    if table is None:
        return False
    if table.primary_key.state == "observed" and list(table.primary_key.value) == columns:
        return True
    # DAO can report Unique=True for relationship-created FK indexes; that
    # observation is not proof that the child values are unique.
    auto_indexes = {
        (relation.name, tuple(relation.child_columns))
        for relation in evidence.relationships
        if relation.child_table == table_name
    }
    return any(
        index.is_unique
        and index.columns == columns
        and (index.name, tuple(index.columns)) not in auto_indexes
        for index in table.indexes
    )


def _linked_source(connect: str | None, source_table_name: str | None) -> str:
    """Render a linked table's local source as ``database-file/source-table``.

    The connection string is sanitized defensively before any part of it is shown, so a
    credential that survived to hand-built evidence cannot leak into report text.
    """
    database = sanitize_connection_string(connect) or ""
    match = re.search(r"(?:^|;)database=([^;]*)", database, flags=re.IGNORECASE)
    if match:
        database = match.group(1)
    database = re.split(r"[\\/]", database)[-1]
    if not database:
        database = "unknown source"
    return f"{database}/{source_table_name}" if source_table_name else database


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>").replace("\r", "")


def _inline(value: str) -> str:
    return _cell(value).replace("`", "\\`")


def _mermaid_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not safe or safe[0].isdigit():
        safe = "f_" + safe
    return safe


def _mermaid_type(value: str) -> str:
    return _mermaid_name(value)


def _mermaid_label(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ").replace("\r", " ")
