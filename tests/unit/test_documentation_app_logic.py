"""Tier 2 instantiation-level app-logic reader tests (T7).

Every test here fakes the COM boundary: no ``Access.Application`` is created, no COM call
runs, and no live Access process is started. The reader is a pure orchestrator over an
injected :class:`AccessLauncher`/:class:`AccessSession`, an injected identity reader, an
injected ``emit_access_identity`` sink, and an injected Job Object assigner, so the
ordering, security readback, copy lifecycle, containment warning, trust-refusal
degradation, 1-based reference diagnostics, and source opt-in can all be observed
directly.

The one deliberate real filesystem interaction is the disposable copy: the reader copies
the source into a unique scratch directory under an overridable root and removes it in a
``finally``. Tests use ``tmp_path`` as that root and assert the copy (never the original)
is the path handed to ``OpenCurrentDatabase``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ms_access_mcp.application import documentation_limits
from ms_access_mcp.application.documentation import (
    Coverage,
    EvidenceDoc,
    RunMetadata,
    SourceEvidence,
)
from ms_access_mcp.application.documentation_app_logic import (
    ACCESS_APPLICATION_METHOD,
    SAVE_AS_TEXT_FORM,
    SAVE_AS_TEXT_MACRO,
    SAVE_AS_TEXT_REPORT,
    AppLogicContent,
    AppLogicReader,
    VbaTrustRefusedError,
    apply_app_logic_content,
)
from ms_access_mcp.application.process_identity import AccessProcessIdentity
from ms_access_mcp.errors import AccessMcpError

PID = 4242
CREATION = 133_456_789_012_345_678


class _FakeJob:
    """A fake Job Object handle that only records its close."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self._events.append("job_close")


class _FakeSession:
    """Records an ordered event log and serves configured COM-shaped reads."""

    def __init__(
        self,
        *,
        version: str = "16.0",
        security_readback: int = 3,
        objects: dict[str, list[str]] | None = None,
        exports: dict[str, str] | None = None,
        vba_trust_refused: bool = False,
        vba_modules: list[dict[str, Any]] | None = None,
        reference_count: int = 0,
        reference_items: dict[int, Any] | None = None,
        reference_failures: dict[tuple[int, str], Exception] | None = None,
    ) -> None:
        self.events: list[Any] = []
        self.version = version
        self.security_readback = security_readback
        self.objects = objects or {"AllForms": [], "AllReports": [], "AllMacros": [], "AllModules": []}
        self.exports = exports or {}
        self.export_types: dict[str, int] = {}
        self.vba_trust_refused = vba_trust_refused
        self.vba_modules = vba_modules or []
        self._reference_count = reference_count
        self._reference_items = reference_items or {}
        self._reference_failures = reference_failures or {}
        self.opened_path: str | None = None

    def application_version(self) -> str:
        self.events.append("version")
        return self.version

    def set_automation_security(self, level: int) -> None:
        self.events.append(("security_set", level))

    def read_automation_security(self) -> int:
        self.events.append("security_read")
        return self.security_readback

    def access_application_pid(self) -> int:
        self.events.append("pid")
        return PID

    def open_current_database(self, path: str) -> None:
        self.events.append("open")
        self.opened_path = path

    def close_current_database(self) -> None:
        self.events.append("close_current")

    def quit_application(self) -> None:
        self.events.append("quit")

    def current_project_object_names(self, collection: str) -> list[str]:
        self.events.append(("names", collection))
        return list(self.objects.get(collection, []))

    def save_as_text(self, object_type: int, name: str, destination: str) -> None:
        self.events.append(("save", object_type, name))
        self.export_types[name] = object_type
        text = self.exports[name]
        Path(destination).write_bytes(text.encode("utf-16-le"))

    def vba_module_count(self) -> int:
        self.events.append("vba_count")
        if self.vba_trust_refused:
            raise VbaTrustRefusedError("VBProject access denied")
        return len(self.vba_modules)

    def _vba(self, index: int, key: str) -> Any:
        self.events.append(("vba_read", index, key))
        return self.vba_modules[index - 1][key]

    def vba_module_name(self, index: int) -> str:
        return str(self._vba(index, "name"))

    def vba_module_type(self, index: int) -> str:
        return str(self._vba(index, "type"))

    def vba_procedures(self, index: int) -> list[str]:
        return list(self._vba(index, "procedures"))

    def vba_module_source(self, index: int) -> str:
        return str(self._vba(index, "source"))

    def reference_count(self) -> int:
        self.events.append("reference_count")
        return self._reference_count

    def reference_item(self, index: int) -> Any:
        self.events.append(("reference_item", index))
        failure = self._reference_failures.get((index, "item"))
        if failure is not None:
            raise failure
        return self._reference_items[index]

    def _reference_field(self, index: int, item: Any, field: str) -> Any:
        self.events.append(("reference_field", index, field))
        failure = self._reference_failures.get((index, field))
        if failure is not None:
            raise failure
        return item[field]

    def reference_name(self, index: int, item: Any) -> str:
        return str(self._reference_field(index, item, "name"))

    def reference_path(self, index: int, item: Any) -> str:
        return str(self._reference_field(index, item, "path"))

    def reference_guid(self, index: int, item: Any) -> str:
        return str(self._reference_field(index, item, "guid"))

    def reference_major(self, index: int, item: Any) -> int:
        return int(self._reference_field(index, item, "major"))

    def reference_minor(self, index: int, item: Any) -> int:
        return int(self._reference_field(index, item, "minor"))

    def reference_built_in(self, index: int, item: Any) -> bool:
        return bool(self._reference_field(index, item, "built_in"))


def _reader(
    session: _FakeSession,
    *,
    scratch_root: Path,
    events: list[Any] | None = None,
    identity: AccessProcessIdentity | None = None,
    job: Any = "default",
    job_factory_fails: bool = False,
) -> AppLogicReader:
    log = events if events is not None else session.events

    def emit(pid: int, creation: int, image: str) -> None:
        log.append(("emit", pid, creation, image))

    def assign(_pid: int) -> Any:
        log.append("job_assign")
        if job_factory_fails:
            return None
        return _FakeJob(log) if job == "default" else job

    def read_identity(pid: int) -> AccessProcessIdentity | None:
        if identity is None:
            return AccessProcessIdentity(pid=pid, creation=CREATION, image="MSACCESS.EXE")
        return identity

    return AppLogicReader(
        launcher=lambda: session,
        identity_reader=read_identity,
        emit_identity=emit,
        assign_job=assign,
        scratch_root=scratch_root,
    )


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "source.accdb"
    path.write_bytes(b"not-a-real-access-file")
    return path


def _default_objects() -> dict[str, list[str]]:
    return {
        "AllForms": ["frmMain"],
        "AllReports": ["rptSales"],
        "AllMacros": ["AutoExec"],
        "AllModules": ["modMain"],
    }


def _default_exports() -> dict[str, str]:
    return {"frmMain": "FORM frmMain\r\nEnd\r\n", "rptSales": "REPORT rptSales\r\n", "AutoExec": "MACRO AutoExec\r\n"}


# ---------------------------------------------------------------------------
# 1. Copy + cleanup lifecycle and open discipline
# ---------------------------------------------------------------------------


def test_reader_copies_to_a_unique_scratch_dir_and_removes_it(tmp_path: Path) -> None:
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())
    reader = _reader(session, scratch_root=tmp_path)

    content = reader.read(str(source))

    assert content.content
    opened = Path(session.opened_path or "")
    # The reader never opens the original; it opens a copy inside its own scratch dir.
    assert opened != source
    assert opened.name == source.name
    assert tmp_path in opened.parents
    # The disposable copy is gone after evidence capture.
    assert not opened.parent.exists()
    assert source.read_bytes() == b"not-a-real-access-file"


def test_reader_emits_the_owned_identity_before_it_opens_the_armed_copy(tmp_path: Path) -> None:
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())
    _reader(session, scratch_root=tmp_path).read(str(source))

    events = session.events
    emit_index = next(index for index, event in enumerate(events) if isinstance(event, tuple) and event[0] == "emit")
    open_index = events.index("open")
    assert emit_index < open_index
    assert ("emit", PID, CREATION, "MSACCESS.EXE") in events


def test_reader_aborts_without_opening_when_security_readback_mismatches(tmp_path: Path) -> None:
    source = _source(tmp_path)
    session = _FakeSession(security_readback=1, objects=_default_objects(), exports=_default_exports())
    reader = _reader(session, scratch_root=tmp_path)

    with pytest.raises(AccessMcpError, match="AutomationSecurity"):
        reader.read(str(source))

    assert "open" not in session.events
    assert not any(isinstance(event, tuple) and event[0] == "emit" for event in session.events)


def test_reader_proceeds_with_a_pid_kill_net_warning_when_job_assignment_fails(tmp_path: Path) -> None:
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())
    reader = _reader(session, scratch_root=tmp_path, job_factory_fails=True)

    content = reader.read(str(source))

    assert "open" in session.events
    assert content.warnings
    assert any("job" in warning.lower() for warning in content.warnings)


# ---------------------------------------------------------------------------
# 2. Unsupported runtime: no open attempt, facts unavailable
# ---------------------------------------------------------------------------


def test_unsupported_runtime_reports_unavailable_facts_without_opening(tmp_path: Path) -> None:
    source = _source(tmp_path)
    session = _FakeSession(version="15.0", objects=_default_objects(), exports=_default_exports())
    reader = _reader(session, scratch_root=tmp_path)

    content = reader.read(str(source))

    assert "open" not in session.events
    assert content.content
    assert all(item.content.state == "unavailable" for item in content.content)
    assert all("15.0" in (item.content.reason or "") for item in content.content)


# ---------------------------------------------------------------------------
# 3. SaveAsText constants, decoding, hash, and method metadata
# ---------------------------------------------------------------------------


def test_reader_uses_the_correct_save_as_text_constants_and_hashes_decoded_source(tmp_path: Path) -> None:
    source = _source(tmp_path)
    exports = _default_exports()
    session = _FakeSession(objects=_default_objects(), exports=exports)
    reader = _reader(session, scratch_root=tmp_path)

    content = reader.read(str(source))

    assert session.export_types["frmMain"] == SAVE_AS_TEXT_FORM == 2
    assert session.export_types["rptSales"] == SAVE_AS_TEXT_REPORT == 3
    assert session.export_types["AutoExec"] == SAVE_AS_TEXT_MACRO == 4
    by_name = {item.name: item for item in content.content}
    form = by_name["frmMain"]
    expected = exports["frmMain"]
    assert form.content.state == "observed"
    assert form.content.value["sha256"] == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert form.content.value["bytes"] == len(expected.encode("utf-8"))
    assert form.method == ACCESS_APPLICATION_METHOD
    # Default off: the raw untrusted source text is not embedded.
    assert "source" not in form.content.value


def test_source_opt_in_embeds_the_raw_export_text(tmp_path: Path) -> None:
    source = _source(tmp_path)
    exports = _default_exports()
    session = _FakeSession(objects=_default_objects(), exports=exports)
    reader = _reader(session, scratch_root=tmp_path)

    content = reader.read(str(source), include_source=True)

    form = next(item for item in content.content if item.name == "frmMain")
    assert form.content.value["source"] == exports["frmMain"]


# ---------------------------------------------------------------------------
# 4. VBA trust refusal degrades the family, never the whole read
# ---------------------------------------------------------------------------


def test_vba_trust_refusal_reports_unavailable_and_keeps_other_families(tmp_path: Path) -> None:
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports(), vba_trust_refused=True)
    reader = _reader(session, scratch_root=tmp_path)

    content = reader.read(str(source), include_source=True)

    vba = next(item for item in content.content if item.category == "vba_module")
    assert vba.content.state == "unavailable"
    assert "Trust access to the VBA project object model" in (vba.content.reason or "")
    assert [item.name for item in content.content if item.category == "form"] == ["frmMain"]


def test_vba_modules_carry_name_type_and_procedures(tmp_path: Path) -> None:
    source = _source(tmp_path)
    session = _FakeSession(
        objects=_default_objects(),
        exports=_default_exports(),
        vba_modules=[{"name": "modMain", "type": "Standard", "procedures": ["Main"], "source": "Sub Main()\nEnd Sub"}],
    )
    reader = _reader(session, scratch_root=tmp_path)

    content = reader.read(str(source))

    module = next(item for item in content.content if item.category == "vba_module")
    assert module.name == "modMain"
    assert module.content.state == "observed"
    assert module.content.value["type"] == "Standard"
    assert module.content.value["procedures"] == ["Main"]


# ---------------------------------------------------------------------------
# 5. References: 1-based enumeration and separated per-property diagnostics
# ---------------------------------------------------------------------------


def test_references_are_read_1_based_with_separated_property_diagnostics(tmp_path: Path) -> None:
    source = _source(tmp_path)
    items = {
        1: {"name": "VBA", "path": "C:/vba.dll", "guid": "{AAAA}", "major": 7, "minor": 1, "built_in": True},
        2: {"name": "Broken", "path": "C:/broken.dll", "guid": "{BBBB}", "major": 1, "minor": 0, "built_in": False},
    }
    session = _FakeSession(
        objects={},
        exports={},
        reference_count=2,
        reference_items=items,
        reference_failures={(2, "guid"): RuntimeError("excepinfo: 0x800A01C4 bad ordinal")},
    )
    reader = _reader(session, scratch_root=tmp_path)

    content = reader.read(str(source))

    item_indexes = [event[1] for event in session.events if isinstance(event, tuple) and event[0] == "reference_item"]
    assert item_indexes == [1, 2]
    assert 0 not in item_indexes
    first, second = content.references
    assert first.name == "VBA"
    assert first.guid.state == "observed"
    assert first.major.value == 7
    assert first.built_in.value is True
    assert second.name == "Broken"
    assert second.path.state == "observed"
    assert second.guid.state == "failed"
    assert "excepinfo" in (second.guid.reason or "")


# ---------------------------------------------------------------------------
# 6. Merge into evidence + cap rejection
# ---------------------------------------------------------------------------


def _minimal_document() -> EvidenceDoc:
    return EvidenceDoc(
        run=RunMetadata("run-1", "1.0", [], []),
        source=SourceEvidence("source.accdb", "accdb", "DAO", "offline", "closed"),
        coverage=Coverage(),
    )


def test_apply_app_logic_content_adds_the_tier_two_categories(tmp_path: Path) -> None:
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())
    content = _reader(session, scratch_root=tmp_path).read(str(source))

    document = apply_app_logic_content(_minimal_document(), content)

    assert document.app_logic is not None
    assert document.app_logic.content
    payload = document.to_dict()
    assert "content" in payload["data"]["app_logic"]
    assert "references" in payload["data"]["app_logic"]


def test_oversized_opt_in_source_is_rejected_without_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Shrink the evidence cap so the oversized-source path is exercised without
    # materializing a real 32 MiB export; the rejection still goes through the same
    # ``ensure_evidence_within_limit`` helper as production.
    monkeypatch.setattr(documentation_limits, "EVIDENCE_MAX_BYTES", 4096)
    source = _source(tmp_path)
    huge = "x" * 8192
    session = _FakeSession(objects=_default_objects(), exports={"frmMain": huge})
    content = _reader(session, scratch_root=tmp_path).read(str(source), include_source=True)
    document = apply_app_logic_content(_minimal_document(), content)

    with pytest.raises(AccessMcpError, match="exceeds the allowed"):
        document.to_dict()


def test_app_logic_evidence_serializes_the_new_categories_round_trip(tmp_path: Path) -> None:
    source = _source(tmp_path)
    session = _FakeSession(
        objects=_default_objects(),
        exports=_default_exports(),
        vba_modules=[{"name": "modMain", "type": "Standard", "procedures": ["Main"], "source": ""}],
        reference_count=1,
        reference_items={1: {"name": "VBA", "path": "C:/vba.dll", "guid": "{A}", "major": 7, "minor": 1, "built_in": True}},
    )
    content = _reader(session, scratch_root=tmp_path).read(str(source))
    document = apply_app_logic_content(_minimal_document(), content)
    payload = document.to_dict()

    decoded = EvidenceDoc.from_dict(json.loads(json.dumps(payload)))

    assert decoded.app_logic is not None
    assert len(decoded.app_logic.content) == len(content.content)
    assert decoded.app_logic.references[0].name == "VBA"


def test_app_logic_content_defaults_empty_so_a_default_document_stays_v1() -> None:
    empty = AppLogicContent()
    document = apply_app_logic_content(_minimal_document(), empty)
    assert document.app_logic is not None
    assert document.app_logic.content == []
    assert document.app_logic.references == []
    # A default-off document (no app_logic at all) is still the byte-identical v1 shape.
    assert "app_logic" not in json.dumps(_minimal_document().to_dict())
