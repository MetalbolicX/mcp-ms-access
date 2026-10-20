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
import shutil
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ms_access_mcp.application import documentation_app_logic, documentation_limits
from ms_access_mcp.application.documentation import (
    AppLogicEvidence,
    ContainerInventoryEvidence,
    Coverage,
    EvidenceDoc,
    FactProvenance,
    FactStatus,
    RunMetadata,
    SourceEvidence,
)
from ms_access_mcp.application.documentation_app_logic import (
    ACCESS_APPLICATION_METHOD,
    FAMILY_SCOPE_NAME,
    SAVE_AS_TEXT_FORM,
    SAVE_AS_TEXT_MACRO,
    SAVE_AS_TEXT_REPORT,
    AppLogicContent,
    AppLogicReader,
    ContainmentError,
    VbaTrustRefusedError,
    apply_app_logic_content,
)
from ms_access_mcp.application.documentation_render import render_document
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
    identity_reader: Callable[[int], AccessProcessIdentity | None] | None = None,
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
        if identity_reader is not None:
            return identity_reader(pid)
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
    # S2 A4: a successful removal never invents a residue warning.
    assert content.warnings == []


# ---------------------------------------------------------------------------
# S2 A4: a failed scratch removal is best-effort and surfaces as one warning
# ---------------------------------------------------------------------------

_SCRATCH_SECRET = "hunter2secret"


def test_scratch_removal_failure_adds_one_sanitized_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S2 A4: a raising removal keeps the evidence and adds exactly one warning."""

    def failing_rmtree(_path: object, *_args: object, **_kwargs: object) -> None:
        raise OSError(f"cannot remove the directory: PWD={_SCRATCH_SECRET}")

    monkeypatch.setattr(documentation_app_logic, "shutil", _shutil_seam(rmtree=failing_rmtree))
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())

    content = _reader(session, scratch_root=tmp_path).read(str(source))

    # The read itself stays successful: same families, same observed facts.
    assert [item.name for item in content.content] == ["frmMain", "rptSales", "AutoExec"]
    assert all(item.content.state == "observed" for item in content.content)
    assert len(content.warnings) == 1
    warning = content.warnings[0]
    assert warning.startswith("The disposable scratch copy directory could not be removed")
    assert "OSError" in warning
    # Sanitized: neither the exception's credential text nor the scratch path is echoed.
    assert _SCRATCH_SECRET not in warning
    assert str(tmp_path) not in warning
    # The residue is real, which is exactly why it must be reported.
    assert len(_scratch_dirs(tmp_path)) == 1


def test_scratch_removal_that_leaves_the_directory_behind_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S2 A4: a silently incomplete removal is detected, not assumed to have worked."""
    monkeypatch.setattr(documentation_app_logic, "shutil", _shutil_seam(rmtree=lambda *_a, **_k: None))
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())

    content = _reader(session, scratch_root=tmp_path).read(str(source))

    assert len(content.warnings) == 1
    assert content.warnings[0].startswith("The disposable scratch copy directory could not be removed")
    assert str(tmp_path) not in content.warnings[0]
    assert len(_scratch_dirs(tmp_path)) == 1


def test_scratch_removal_warning_does_not_mask_a_degraded_tier_two_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S2 A4: the residue warning is additive; the degradation warning survives."""

    def failing_rmtree(_path: object, *_args: object, **_kwargs: object) -> None:
        raise OSError("the residue could not be removed")

    def failing_copy(_source: object, _destination: object) -> None:
        raise OSError("the disposable copy could not be written")

    monkeypatch.setattr(
        documentation_app_logic, "shutil", _shutil_seam(rmtree=failing_rmtree, copy2=failing_copy)
    )
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())

    content = _reader(session, scratch_root=tmp_path).read(str(source))

    assert len(content.warnings) == 2
    assert any("could not be written" in warning for warning in content.warnings)
    assert any("could not be removed" in warning for warning in content.warnings)


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

    # S2/WP4: the readback mismatch is a containment failure, typed and hard, so the
    # worker can never turn it into partial success.
    with pytest.raises(ContainmentError, match="AutomationSecurity") as raised:
        reader.read(str(source))

    assert isinstance(raised.value, AccessMcpError)
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


def test_reader_assigns_the_job_when_the_live_identity_still_matches(tmp_path: Path) -> None:
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())
    reader = _reader(session, scratch_root=tmp_path)

    content = reader.read(str(source))

    assert session.events.count("job_assign") == 1
    assert content.warnings == []


def test_reader_skips_job_assignment_when_the_creation_time_changed_since_capture(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())
    reads = iter(
        [
            AccessProcessIdentity(pid=PID, creation=CREATION, image="MSACCESS.EXE"),
            AccessProcessIdentity(pid=PID, creation=CREATION + 1, image="MSACCESS.EXE"),
        ]
    )
    reader = _reader(session, scratch_root=tmp_path, identity_reader=lambda _pid: next(reads))

    content = reader.read(str(source))

    assert "job_assign" not in session.events
    assert "open" in session.events
    assert any("identity" in warning.lower() for warning in content.warnings)
    assert any("kill net" in warning.lower() for warning in content.warnings)


def test_reader_skips_job_assignment_when_the_live_image_changed_since_capture(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())
    reads = iter(
        [
            AccessProcessIdentity(pid=PID, creation=CREATION, image="MSACCESS.EXE"),
            AccessProcessIdentity(pid=PID, creation=CREATION, image="WINWORD.EXE"),
        ]
    )
    reader = _reader(session, scratch_root=tmp_path, identity_reader=lambda _pid: next(reads))

    content = reader.read(str(source))

    assert "job_assign" not in session.events
    assert any("identity" in warning.lower() for warning in content.warnings)


def test_reader_skips_job_assignment_when_the_live_identity_is_unreadable(tmp_path: Path) -> None:
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())
    reads = iter([AccessProcessIdentity(pid=PID, creation=CREATION, image="MSACCESS.EXE"), None])
    reader = _reader(session, scratch_root=tmp_path, identity_reader=lambda _pid: next(reads))

    content = reader.read(str(source))

    assert "job_assign" not in session.events
    assert any("unreadable" in warning.lower() for warning in content.warnings)


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


def test_vba_modules_always_carry_source_digest_and_bytes_without_source(tmp_path: Path) -> None:
    """Agreement 9: every source object carries a content hash + byte size.

    The VBA module source is hashed over its UTF-8 encoding (same scheme as the
    export families); the raw ``source`` stays opt-in-only.
    """
    source = _source(tmp_path)
    text = "Sub Main()\nEnd Sub\n"
    session = _FakeSession(
        objects=_default_objects(),
        exports=_default_exports(),
        vba_modules=[{"name": "modMain", "type": "Standard", "procedures": ["Main"], "source": text}],
    )
    reader = _reader(session, scratch_root=tmp_path)

    content = reader.read(str(source))

    module = next(item for item in content.content if item.category == "vba_module")
    encoded = text.encode("utf-8")
    assert module.content.value["sha256"] == hashlib.sha256(encoded).hexdigest()
    assert module.content.value["bytes"] == len(encoded)
    assert "source" not in module.content.value

    opt_in = reader.read(str(source), include_source=True)
    module_opt = next(item for item in opt_in.content if item.category == "vba_module")
    assert module_opt.content.value["source"] == text
    assert module_opt.content.value["sha256"] == module.content.value["sha256"]


def test_reader_vba_digest_round_trips_and_renders_without_the_missing_digest_gap(
    tmp_path: Path,
) -> None:
    """Positive digest path: the reader's VBA digest survives evidence and reaches Markdown.

    The digest is validation-visible (``to_dict`` validates) and byte-stable across the
    serialization round trip, and the rendered VBA table carries it. Because the digest
    *is* recorded, the "source digest is not recorded" limitation line must be absent,
    and the raw module text stays out of Markdown while the source opt-in is off.
    """
    source = _source(tmp_path)
    text = 'Attribute VB_Name = "modMain"\nSub Main()\nVBA_DIGEST_SENTINEL\nEnd Sub\n'
    session = _FakeSession(
        objects=_default_objects(),
        exports=_default_exports(),
        vba_modules=[{"name": "modMain", "type": "Standard", "procedures": ["Main"], "source": text}],
    )
    content = _reader(session, scratch_root=tmp_path).read(str(source))
    document = apply_app_logic_content(_minimal_document(), content)
    encoded = text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()

    decoded = EvidenceDoc.from_dict(json.loads(json.dumps(document.to_dict())))

    assert decoded == document
    assert decoded.app_logic is not None
    module = next(item for item in decoded.app_logic.content if item.category == "vba_module")
    assert module.content.value == {
        "type": "Standard",
        "procedures": ["Main"],
        "sha256": digest,
        "bytes": len(encoded),
    }
    markdown = render_document(decoded).markdown
    assert f"| modMain | Standard | 1 — Main | `{digest}` | {len(encoded)} | `vba_module:modMain.content` |" in markdown
    assert "source digest (sha256/bytes) is not recorded" not in markdown
    assert "VBA_DIGEST_SENTINEL" not in markdown


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


# ---------------------------------------------------------------------------
# 7. WP4: ordinary Tier 2 failures degrade to failed facts; containment stays hard
# ---------------------------------------------------------------------------


COM_FAILURE = 0x80004005
_WHOLE_STAGE_PROBE = "whole stage probe"


class _ComFaultError(Exception):
    """A duck-typed operational COM failure: one failing HRESULT plus its message."""

    def __init__(self, hresult: int, message: str) -> None:
        super().__init__(hresult, message)
        self.hresult = hresult

    def __str__(self) -> str:
        return str(self.args[1])


class _HresultArgumentError(Exception):
    """A COM failure that carries its status only in ``args[0]`` (no ``hresult``)."""


class _BoolHresultError(RuntimeError):
    """A defect whose ``hresult`` attribute is a bool, which is never an HRESULT."""

    hresult = True


class _RaisingSession(_FakeSession):
    """A session whose one named whole-stage read raises a pre-built error."""

    def __init__(self, stage: str, error: BaseException, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._stage = stage
        self._error = error

    def application_version(self) -> str:
        self.events.append("version")
        if self._stage == "version":
            raise self._error
        return self.version

    def open_current_database(self, path: str) -> None:
        if self._stage == "open":
            self.events.append("open")
            self.opened_path = path
            raise self._error
        super().open_current_database(path)


class _OpenFailingSession(_RaisingSession):
    """A session whose armed open fails with an operational COM-shaped error."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__("open", _ComFaultError(COM_FAILURE, message), **kwargs)


class _FamilyDefectSession(_FakeSession):
    """A session whose form-family read raises a programmer defect."""

    def current_project_object_names(self, collection: str) -> list[str]:
        if collection == "AllForms":
            self.events.append(("names", collection))
            raise TypeError("the export family reader was called with the wrong arity")
        return super().current_project_object_names(collection)


class _SecuritySetFailingSession(_FakeSession):
    """A session that refuses the ForceDisable write before any open."""

    def set_automation_security(self, level: int) -> None:
        self.events.append(("security_set", level))
        raise RuntimeError("AutomationSecurity is locked by policy")


class _InvalidPidSession(_FakeSession):
    """A session whose owned-PID read raises the invalid-PID ValueError."""

    def access_application_pid(self) -> int:
        self.events.append("pid")
        raise ValueError("invalid PID value returned for the owned Access instance")


class _InterruptingSession(_FakeSession):
    """A session interrupted mid-read by a console interrupt or shutdown request."""

    def __init__(self, interrupt: type[BaseException], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._interrupt = interrupt

    def current_project_object_names(self, collection: str) -> list[str]:
        self.events.append(("names", collection))
        raise self._interrupt


def _scratch_dirs(root: Path) -> list[str]:
    return sorted(path.name for path in root.iterdir() if path.name.startswith("ms_access_doc_v2_"))


def _assert_failed_fallback(content: AppLogicContent, *, detail: str) -> None:
    """A WP4 fallback reports every family and reference as failed, with one warning."""
    assert [item.category for item in content.content] == ["form", "report", "macro", "vba_module"]
    assert all(item.name == FAMILY_SCOPE_NAME for item in content.content)
    assert all(item.content.state == "failed" for item in content.content)
    assert all(detail in (item.content.reason or "") for item in content.content)
    assert len(content.references) == 1
    reference = content.references[0]
    assert reference.name == FAMILY_SCOPE_NAME
    for fact in (reference.path, reference.guid, reference.major, reference.minor, reference.built_in):
        assert fact.state == "failed"
        assert detail in (fact.reason or "")
    assert content.warnings
    assert all(detail in warning for warning in content.warnings)


def _shutil_seam(**overrides: Any) -> SimpleNamespace:
    """The reader's ``shutil`` seam with only the named calls overridden."""
    namespace = SimpleNamespace(copy2=shutil.copy2, rmtree=shutil.rmtree)
    for name, replacement in overrides.items():
        setattr(namespace, name, replacement)
    return namespace


def _reader_with_stage_failure(
    stage: str, error: BaseException, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[AppLogicReader, _FakeSession | None]:
    """A reader whose named whole-stage call raises ``error``."""
    if stage == "copy":

        def failing_copy(_source: object, _destination: object) -> None:
            raise error

        monkeypatch.setattr(documentation_app_logic, "shutil", _shutil_seam(copy2=failing_copy))
        session = _FakeSession(objects=_default_objects(), exports=_default_exports())
        return _reader(session, scratch_root=tmp_path), session
    if stage == "dispatch":

        def failing_launch() -> Any:
            raise error

        return AppLogicReader(launcher=failing_launch, scratch_root=tmp_path), None
    session = _RaisingSession(stage, error, objects=_default_objects(), exports=_default_exports())
    return _reader(session, scratch_root=tmp_path), session


# S1: only operational filesystem/COM failures may degrade a whole-stage boundary.
_OPERATIONAL_STAGE_ERRORS = [
    pytest.param(lambda: OSError(f"{_WHOLE_STAGE_PROBE}: the file operation failed"), id="oserror"),
    pytest.param(lambda: _ComFaultError(COM_FAILURE, f"{_WHOLE_STAGE_PROBE}: the COM call failed"), id="hresult-attribute"),
    pytest.param(
        lambda: _HresultArgumentError(COM_FAILURE, f"{_WHOLE_STAGE_PROBE}: unsigned args[0]"), id="unsigned-args0"
    ),
    pytest.param(
        lambda: _HresultArgumentError(-2147467259, f"{_WHOLE_STAGE_PROBE}: signed args[0]"), id="signed-args0"
    ),
]

# S1: an unexpected defect -- including an int/bool that is not a failing HRESULT -- is hard.
_PROGRAMMER_DEFECTS = [
    pytest.param(lambda: ValueError(f"{_WHOLE_STAGE_PROBE}: wrong number of values"), id="value-error"),
    pytest.param(lambda: TypeError(f"{_WHOLE_STAGE_PROBE}: unsupported operand"), id="type-error"),
    pytest.param(lambda: AttributeError(f"{_WHOLE_STAGE_PROBE}: no such attribute"), id="attribute-error"),
    pytest.param(lambda: RuntimeError(f"{_WHOLE_STAGE_PROBE}: bare runtime error"), id="runtime-error"),
    pytest.param(
        lambda: _HresultArgumentError(0, f"{_WHOLE_STAGE_PROBE}: success status"), id="non-failing-int"
    ),
    pytest.param(lambda: _BoolHresultError(f"{_WHOLE_STAGE_PROBE}: bool status"), id="bool-hresult"),
]


@pytest.mark.parametrize("stage", ["copy", "dispatch", "version", "open"])
@pytest.mark.parametrize("make_error", _OPERATIONAL_STAGE_ERRORS)
def test_operational_whole_stage_failures_keep_partial_degradation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, make_error: Callable[[], BaseException]
) -> None:
    """S1: filesystem/COM-shape failures at a whole-stage boundary still degrade."""

    source = _source(tmp_path)
    original = source.read_bytes()
    reader, session = _reader_with_stage_failure(stage, make_error(), tmp_path, monkeypatch)

    content = reader.read(str(source))

    _assert_failed_fallback(content, detail=_WHOLE_STAGE_PROBE)
    assert source.read_bytes() == original
    assert _scratch_dirs(tmp_path) == []
    if stage == "open":
        assert session is not None
        assert "quit" in session.events


@pytest.mark.parametrize("stage", ["copy", "dispatch", "version", "open"])
@pytest.mark.parametrize("make_error", _PROGRAMMER_DEFECTS)
def test_programmer_defects_at_a_whole_stage_boundary_stay_hard_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, make_error: Callable[[], BaseException]
) -> None:
    """S1: an unexpected defect never masquerades as successful partial evidence."""

    source = _source(tmp_path)
    original = source.read_bytes()
    error = make_error()
    reader, session = _reader_with_stage_failure(stage, error, tmp_path, monkeypatch)

    with pytest.raises(type(error)) as raised:
        reader.read(str(source))

    assert raised.value is error
    assert source.read_bytes() == original
    assert _scratch_dirs(tmp_path) == []
    if stage == "open":
        assert session is not None
        assert "close_current" in session.events
        assert "quit" in session.events
        assert "job_close" in session.events


def test_per_family_best_effort_catches_are_not_narrowed(tmp_path: Path) -> None:
    """PRESERVE: a defect inside one family is still that family's own failed fact."""

    source = _source(tmp_path)
    session = _FamilyDefectSession(objects=_default_objects(), exports=_default_exports())

    content = _reader(session, scratch_root=tmp_path).read(str(source))

    form = next(item for item in content.content if item.category == "form")
    assert form.name == FAMILY_SCOPE_NAME
    assert form.content.state == "failed"
    assert "wrong arity" in (form.content.reason or "")
    assert [item.name for item in content.content if item.category == "report"] == ["rptSales"]
    assert [item.name for item in content.content if item.category == "macro"] == ["AutoExec"]
    assert content.warnings == []


def test_copy_failure_returns_failed_family_facts_and_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S1/S3: an ordinary copy error degrades instead of losing the Tier 2 report."""

    def failing_copy(_source: object, _destination: object) -> None:
        raise OSError("the disposable copy could not be written")

    monkeypatch.setattr(documentation_app_logic, "shutil", _shutil_seam(copy2=failing_copy))
    source = _source(tmp_path)
    original = source.read_bytes()
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())

    content = _reader(session, scratch_root=tmp_path).read(str(source))

    _assert_failed_fallback(content, detail="could not be written")
    assert "open" not in session.events
    assert source.read_bytes() == original
    assert _scratch_dirs(tmp_path) == []


def test_scratch_dir_creation_failure_returns_failed_family_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S1/S3: an ordinary scratch-directory error also degrades, never raises."""

    def failing_mkdtemp(**_kwargs: object) -> str:
        raise OSError("the scratch directory could not be created")

    monkeypatch.setattr(
        documentation_app_logic,
        "tempfile",
        SimpleNamespace(mkdtemp=failing_mkdtemp, gettempdir=lambda: str(tmp_path)),
    )
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())

    content = _reader(session, scratch_root=tmp_path).read(str(source))

    _assert_failed_fallback(content, detail="could not be created")
    assert "open" not in session.events


def test_launch_failure_returns_failed_family_facts_and_cleans_the_scratch_dir(tmp_path: Path) -> None:
    """S1/S3: a dispatch failure is ordinary, so it degrades and still cleans up."""

    def failing_launch() -> Any:
        raise _ComFaultError(0x80040154, "Class not registered: Access.Application")

    reader = AppLogicReader(launcher=failing_launch, scratch_root=tmp_path)
    source = _source(tmp_path)

    content = reader.read(str(source))

    _assert_failed_fallback(content, detail="Class not registered")
    assert _scratch_dirs(tmp_path) == []


def test_open_failure_returns_failed_family_facts_after_teardown(tmp_path: Path) -> None:
    """S2/S3: an ordinary open error degrades only after the teardown calls ran."""

    source = _source(tmp_path)
    original = source.read_bytes()
    session = _OpenFailingSession(
        "the disposable copy could not be locked",
        objects=_default_objects(),
        exports=_default_exports(),
    )

    content = _reader(session, scratch_root=tmp_path).read(str(source))

    _assert_failed_fallback(content, detail="could not be locked")
    assert "open" in session.events
    # Only the disposable copy was ever handed to the armed open.
    assert Path(session.opened_path or "") != source
    assert Path(session.opened_path or "").name == source.name
    assert "close_current" in session.events
    assert "quit" in session.events
    assert "job_close" in session.events
    assert source.read_bytes() == original
    assert _scratch_dirs(tmp_path) == []


def test_open_failure_reason_is_scrubbed_of_connection_secrets(tmp_path: Path) -> None:
    """S3: fallback reasons go through the same secret-redaction boundary as v1."""

    source = _source(tmp_path)
    session = _OpenFailingSession(
        'open failed: Provider=X;PWD=hunter2secret;',
        objects=_default_objects(),
        exports=_default_exports(),
    )

    content = _reader(session, scratch_root=tmp_path).read(str(source))

    serialized = json.dumps([item.content.reason for item in content.content] + content.warnings)
    assert "hunter2secret" not in serialized
    assert "PWD" in serialized


def test_security_set_failure_is_a_containment_error_not_a_fallback(tmp_path: Path) -> None:
    """S2: an unarmable ForceDisable never becomes partial evidence."""

    source = _source(tmp_path)
    session = _SecuritySetFailingSession(objects=_default_objects(), exports=_default_exports())

    with pytest.raises(ContainmentError, match="AutomationSecurity") as raised:
        _reader(session, scratch_root=tmp_path).read(str(source))

    assert isinstance(raised.value, AccessMcpError)
    assert "open" not in session.events
    assert not any(isinstance(event, tuple) and event[0] == "emit" for event in session.events)


def test_identity_capture_value_error_is_a_containment_error_not_a_fallback(tmp_path: Path) -> None:
    """S2: the invalid-PID ValueError from identity capture must never be swallowed."""

    source = _source(tmp_path)
    session = _InvalidPidSession(objects=_default_objects(), exports=_default_exports())

    with pytest.raises(ContainmentError, match="identity") as raised:
        _reader(session, scratch_root=tmp_path).read(str(source))

    assert isinstance(raised.value.__cause__, ValueError)
    assert "open" not in session.events


def test_unreadable_owned_identity_remains_a_hard_error(tmp_path: Path) -> None:
    """PRESERVE: an unreadable owned identity stayed an error before WP4 and does now."""

    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())
    reader = _reader(session, scratch_root=tmp_path, identity_reader=lambda _pid: None)

    with pytest.raises(ContainmentError, match="identity"):
        reader.read(str(source))

    assert "open" not in session.events


def test_identity_announcement_failure_is_a_containment_error_not_a_fallback(tmp_path: Path) -> None:
    """S2: the owned-identity announcement is containment, so it is never degraded."""

    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())

    def failing_emit(_pid: int, _creation: int, _image: str) -> None:
        raise OSError("the protocol stdout stream is closed")

    reader = AppLogicReader(
        launcher=lambda: session,
        identity_reader=lambda pid: AccessProcessIdentity(pid=pid, creation=CREATION, image="MSACCESS.EXE"),
        emit_identity=failing_emit,
        assign_job=lambda _pid: _FakeJob(session.events),
        scratch_root=tmp_path,
    )

    with pytest.raises(ContainmentError, match="identity"):
        reader.read(str(source))

    assert "open" not in session.events


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_interrupt_is_never_converted_to_partial_evidence(
    tmp_path: Path, interrupt: type[BaseException]
) -> None:
    """S2/S3: KeyboardInterrupt and SystemExit propagate and never become partial."""

    source = _source(tmp_path)
    session = _InterruptingSession(interrupt, objects=_default_objects(), exports=_default_exports())

    with pytest.raises(interrupt):
        _reader(session, scratch_root=tmp_path).read(str(source))

    assert "quit" in session.events
    assert _scratch_dirs(tmp_path) == []


class _ContainmentInterruptSession(_FakeSession):
    """A session that raises the given interrupt during one containment step."""

    def __init__(self, step: str, interrupt: type[BaseException], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._step = step
        self._interrupt = interrupt

    def set_automation_security(self, level: int) -> None:
        if self._step == "security_set":
            raise self._interrupt
        super().set_automation_security(level)

    def read_automation_security(self) -> int:
        if self._step == "security_read":
            raise self._interrupt
        return super().read_automation_security()

    def access_application_pid(self) -> int:
        if self._step == "pid":
            raise self._interrupt
        return super().access_application_pid()


_CONTAINMENT_INTERRUPT_STEPS = [
    "security_set",
    "security_read",
    "pid",
    "identity_read",
    "announce",
    "job_assign",
]


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("step", _CONTAINMENT_INTERRUPT_STEPS)
def test_interrupt_during_a_containment_step_propagates_and_still_tears_down(
    tmp_path: Path, interrupt: type[BaseException], step: str
) -> None:
    """S3 H4: an interrupt at any containment step escapes and teardown still runs.

    Every containment boundary catches ``Exception`` only, so a console interrupt or a
    shutdown request must never be wrapped into ``ContainmentError`` or degraded into
    partial evidence. The session teardown (close/quit) and the scratch removal still run
    before the interrupt leaves the reader.
    """
    source = _source(tmp_path)
    session = _ContainmentInterruptSession(step, interrupt, objects=_default_objects(), exports=_default_exports())

    def read_identity(pid: int) -> AccessProcessIdentity:
        if step == "identity_read":
            raise interrupt
        return AccessProcessIdentity(pid=pid, creation=CREATION, image="MSACCESS.EXE")

    def emit(pid: int, creation: int, image: str) -> None:
        session.events.append(("emit", pid, creation, image))
        if step == "announce":
            raise interrupt

    def assign(_pid: int) -> Any:
        session.events.append("job_assign")
        if step == "job_assign":
            raise interrupt
        return _FakeJob(session.events)

    reader = AppLogicReader(
        launcher=lambda: session,
        identity_reader=read_identity,
        emit_identity=emit,
        assign_job=assign,
        scratch_root=tmp_path,
    )

    with pytest.raises(interrupt):
        reader.read(str(source))

    assert "open" not in session.events
    assert "close_current" in session.events
    assert "quit" in session.events
    assert _scratch_dirs(tmp_path) == []


def test_unexpected_job_assignment_error_is_a_containment_error(tmp_path: Path) -> None:
    """S3 H4: an unexpected error inside job assignment is containment, never partial."""
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())

    def failing_assign(_pid: int) -> Any:
        raise TypeError("the job assignment plumbing is wrong")

    reader = AppLogicReader(
        launcher=lambda: session,
        identity_reader=lambda pid: AccessProcessIdentity(pid=pid, creation=CREATION, image="MSACCESS.EXE"),
        emit_identity=lambda _pid, _creation, _image: None,
        assign_job=failing_assign,
        scratch_root=tmp_path,
    )

    with pytest.raises(ContainmentError, match="Job Object assignment") as raised:
        reader.read(str(source))

    assert isinstance(raised.value.__cause__, TypeError)
    assert "open" not in session.events
    assert "quit" in session.events
    assert _scratch_dirs(tmp_path) == []


def test_fallback_content_keeps_v1_and_tier_one_facts_and_reports_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S1/S3: the merge is additive, so retained facts survive a Tier 2 fallback."""

    def failing_copy(_source: object, _destination: object) -> None:
        raise OSError("the disposable copy could not be written")

    monkeypatch.setattr(documentation_app_logic, "shutil", _shutil_seam(copy2=failing_copy))
    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())
    content = _reader(session, scratch_root=tmp_path).read(str(source))
    document = _minimal_document()
    document.app_logic = AppLogicEvidence(
        containers=[
            ContainerInventoryEvidence(
                "form",
                "Forms",
                "DAO.Containers",
                FactStatus.observed([{"name": "frmMain"}], FactProvenance("form", "Forms", "objects")),
            )
        ]
    )

    merged = apply_app_logic_content(document, content)
    payload = merged.to_dict()

    assert payload["data"]["status"] == "partial"
    assert payload["data"]["app_logic"]["containers"][0]["objects"]["state"] == "observed"
    assert payload["data"]["app_logic"]["containers"][0]["objects"]["value"] == [{"name": "frmMain"}]
    assert [item["category"] for item in payload["data"]["app_logic"]["content"]] == [
        "form",
        "report",
        "macro",
        "vba_module",
    ]
    assert payload["data"]["app_logic"]["references"][0]["name"] == FAMILY_SCOPE_NAME
    assert payload["warnings"]
    # The fallback is a fact-level degradation, never a schema or payload-limit change.
    merged.validate()


def test_successful_read_is_unchanged_and_carries_no_degradation_warning(tmp_path: Path) -> None:
    """PRESERVE: the successful Tier 2 read is untouched by the WP4 fallback path."""

    source = _source(tmp_path)
    session = _FakeSession(objects=_default_objects(), exports=_default_exports())

    content = _reader(session, scratch_root=tmp_path).read(str(source))

    assert content.warnings == []
    assert [item.name for item in content.content] == ["frmMain", "rptSales", "AutoExec"]
    assert all(item.content.state == "observed" for item in content.content)
    assert _scratch_dirs(tmp_path) == []


def test_read_level_interrupt_propagates_and_teardown_still_removes_the_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S2 A4: an interrupt inside the armed read is never masked by best-effort teardown."""
    created: list[Path] = []
    real_mkdtemp = documentation_app_logic.tempfile.mkdtemp

    def tracking_mkdtemp(*args: Any, **kwargs: Any) -> str:
        path = Path(real_mkdtemp(*args, **kwargs))
        created.append(path)
        return str(path)

    def interrupting_armed(self: Any, copy_path: str, workdir: Path, include_source: bool) -> AppLogicContent:
        raise KeyboardInterrupt

    source = tmp_path / "source.accdb"
    source.write_bytes(b"probe")
    monkeypatch.setattr(documentation_app_logic.tempfile, "mkdtemp", tracking_mkdtemp)
    monkeypatch.setattr(AppLogicReader, "_read_armed", interrupting_armed)

    with pytest.raises(KeyboardInterrupt):
        AppLogicReader(scratch_root=tmp_path).read(str(source), include_source=False)

    assert len(created) == 1
    assert not created[0].exists()
