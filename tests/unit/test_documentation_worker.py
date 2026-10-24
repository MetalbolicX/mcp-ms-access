"""Worker-side identity-frame tests for the documentation IPC protocol.

An armed (app-logic) documentation read instantiates ``Access.Application`` inside the
spawned Python child, so the child must announce that owned instance to the supervisor
*before* it opens any source. The announcement is one flushed
``@@ACCESS_IDENTITY@@ {json}`` line on the protocol stdout, ahead of the final response
object. The supervisor reaps the announced PID after the Python child dies; without the
frame the parent would have no ownership evidence for that ``MSACCESS.EXE``.

These tests use in-memory streams only: no Access, no COM, no spawned child.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ms_access_mcp.application import documentation_app_logic, documentation_worker
from ms_access_mcp.application.documentation import (
    AppLogicEvidence,
    ContainerInventoryEvidence,
    Coverage,
    EvidenceDoc,
    FactProvenance,
    FactStatus,
    FieldEvidence,
    RunMetadata,
    SourceEvidence,
    TableEvidence,
)
from ms_access_mcp.application.documentation_worker import (
    AccessIdentity,
    build_request,
    decode_request,
    decode_response,
    decode_response_with_identities,
    emit_access_identity,
    split_access_identity_frames,
)
from ms_access_mcp.application.process_identity import AccessProcessIdentity
from ms_access_mcp.backends.access.app_logic_session import (
    VBA_TRUST_PREREQUISITE,
    VbaTrustRefusedError,
)
from ms_access_mcp.backends.access.vba import VbaProjectUnavailableError
from ms_access_mcp.errors import AccessMcpError

FRAME_PREFIX = "@@ACCESS_IDENTITY@@ "
CREATION = 133_456_789_012_345_678
_MISSING = object()  # distinguishes "absent" from an explicit null in request builders


class _RecordingStream(io.BytesIO):
    """Binary stream that counts flushes, so "written and flushed once" is observable."""

    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1


def _frame_line(pid: int = 41_000, creation: int = CREATION, image: str = "MSACCESS.EXE") -> bytes:
    stream = _RecordingStream()
    emit_access_identity(pid, creation, image, stream=stream)
    return stream.getvalue()


# ---------------------------------------------------------------------------
# 1. The writer emits exactly one flushed frame line
# ---------------------------------------------------------------------------


def test_emit_access_identity_writes_exactly_one_flushed_frame_line() -> None:
    stream = _RecordingStream()

    emit_access_identity(41_000, CREATION, "MSACCESS.EXE", stream=stream)

    written = stream.getvalue()
    assert written.startswith(FRAME_PREFIX.encode("utf-8"))
    assert written.endswith(b"\n")
    assert written.count(b"\n") == 1
    assert stream.flushes == 1
    payload = json.loads(written.decode("utf-8")[len(FRAME_PREFIX) :])
    assert payload == {
        "access_pid": 41_000,
        "access_creation": CREATION,
        "access_image": "MSACCESS.EXE",
    }


# ---------------------------------------------------------------------------
# 2. Frame parsing: round trip and strict rejection
# ---------------------------------------------------------------------------


def test_split_access_identity_frames_round_trips_the_worker_frame() -> None:
    body = b'{"ok":true}'

    frames, remaining = split_access_identity_frames(_frame_line() + body)

    assert frames == [AccessIdentity(pid=41_000, creation=CREATION, image="MSACCESS.EXE")]
    assert remaining == body


def test_split_access_identity_frames_returns_a_v1_body_byte_identical() -> None:
    body = b'{"ok":true,"evidence":{}}'

    frames, remaining = split_access_identity_frames(body)

    assert frames == []
    assert remaining == body


def test_decode_response_with_identities_records_frames_and_decodes_the_body() -> None:
    identities, payload = decode_response_with_identities(_frame_line(pid=7, creation=99) + b'{"ok":true}')

    assert identities == [AccessIdentity(pid=7, creation=99, image="MSACCESS.EXE")]
    assert payload == {"ok": True}


@pytest.mark.parametrize(
    "damaged",
    [
        b'@@ACCESS_IDENTITY@@ {not json}\n',
        b'@@ACCESS_IDENTITY@@ {"access_creation": 1, "access_image": "MSACCESS.EXE"}\n',
        b'@@ACCESS_IDENTITY@@ {"access_pid": 0, "access_creation": 1, "access_image": "MSACCESS.EXE"}\n',
        b'@@ACCESS_IDENTITY@@ {"access_pid": "9", "access_creation": 1, "access_image": "MSACCESS.EXE"}\n',
        b'@@ACCESS_IDENTITY@@ {"access_pid": 9, "access_creation": 1, "access_image": ""}\n',
        b'@@ACCESS_IDENTITY@@ {"access_pid": 9, "access_creation": 1}',
    ],
)
def test_a_damaged_frame_is_never_skipped_or_accepted(damaged: bytes) -> None:
    frames, remaining = split_access_identity_frames(damaged + b'{"ok":true}')

    assert frames == []
    assert remaining == damaged + b'{"ok":true}'
    with pytest.raises(AccessMcpError, match="malformed"):
        decode_response(remaining)


# ---------------------------------------------------------------------------
# 3. include_app_logic request field (default off) reaches the inspection seam
# ---------------------------------------------------------------------------


def _request(include_app_logic: object = _MISSING) -> bytes:
    request: dict[str, object] = {
        "db_path": "core.accdb",
        "backend": "dao",
        "allowed_dirs": [],
        "tables": None,
        "queries": None,
    }
    if include_app_logic is not _MISSING:
        request["include_app_logic"] = include_app_logic
    return json.dumps(request).encode("utf-8")


def test_build_request_carries_include_app_logic_defaulting_off() -> None:
    request = build_request(db_path="core.accdb", backend="dao", allowed_dirs=[], tables=None, queries=None)
    assert request["include_app_logic"] is False
    opted_in = build_request(
        db_path="core.accdb",
        backend="dao",
        allowed_dirs=[],
        tables=None,
        queries=None,
        include_app_logic=True,
    )
    assert opted_in["include_app_logic"] is True


def test_decode_request_defaults_include_app_logic_off_and_validates_a_boolean() -> None:
    assert decode_request(_request())["include_app_logic"] is False
    assert decode_request(_request(True))["include_app_logic"] is True
    assert decode_request(_request(False))["include_app_logic"] is False


@pytest.mark.parametrize("malformed", ["yes", 1, 0, None, [], {}])
def test_decode_request_rejects_a_non_boolean_include_app_logic(malformed: object) -> None:
    with pytest.raises(AccessMcpError, match="include_app_logic"):
        decode_request(_request(malformed))


def test_run_request_passes_include_app_logic_through_to_the_inspection_service(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class _FakeDatabase:
        def open_database(self, _path: str) -> object:
            from ms_access_mcp.application.database import Ok

            return Ok(data={})

        def close_database(self) -> None:
            return None

    class _FakeDocument:
        status = "complete"

        def to_dict(self) -> dict[str, object]:
            return {"schema": "access.documentation.evidence/1"}

    def _fake_inspect(_database: object, **kwargs: object) -> _FakeDocument:
        seen.update(kwargs)
        return _FakeDocument()

    monkeypatch.setattr(documentation_worker, "DatabaseUseCases", lambda **_kwargs: _FakeDatabase())
    monkeypatch.setattr(documentation_worker, "inspect_open_database", _fake_inspect)
    # ``include_app_logic`` now also triggers the armed Tier 2 read; stub the reader so
    # this preservation test never instantiates Access.
    monkeypatch.setattr(documentation_worker, "read_tier2_content", lambda *_a, **_k: object())
    monkeypatch.setattr(documentation_worker, "apply_app_logic_content", lambda document, _content: document)

    documentation_worker._run_request(
        {
            "db_path": "core.accdb",
            "backend": "dao",
            "allowed_dirs": [],
            "tables": None,
            "queries": None,
            "include_query_sql": False,
            "include_app_logic": True,
        }
    )

    assert seen["include_app_logic"] is True
    assert seen["include_query_sql"] is False


# ---------------------------------------------------------------------------
# T7: Tier 2 source opt-in request field and reader invocation
# ---------------------------------------------------------------------------


def _source_request(include_app_logic_source: object = _MISSING) -> bytes:
    request: dict[str, object] = {
        "db_path": "core.accdb",
        "backend": "dao",
        "allowed_dirs": [],
        "tables": None,
        "queries": None,
    }
    if include_app_logic_source is not _MISSING:
        request["include_app_logic_source"] = include_app_logic_source
    return json.dumps(request).encode("utf-8")


def test_build_request_carries_include_app_logic_source_defaulting_off() -> None:
    request = build_request(db_path="core.accdb", backend="dao", allowed_dirs=[], tables=None, queries=None)
    assert request["include_app_logic_source"] is False
    opted_in = build_request(
        db_path="core.accdb",
        backend="dao",
        allowed_dirs=[],
        tables=None,
        queries=None,
        include_app_logic=True,
        include_app_logic_source=True,
    )
    assert opted_in["include_app_logic_source"] is True


def test_decode_request_defaults_include_app_logic_source_off_and_validates_a_boolean() -> None:
    assert decode_request(_source_request())["include_app_logic_source"] is False
    assert decode_request(_source_request(True))["include_app_logic_source"] is True
    assert decode_request(_source_request(False))["include_app_logic_source"] is False


@pytest.mark.parametrize("malformed", ["yes", 1, 0, None, [], {}])
def test_decode_request_rejects_a_non_boolean_include_app_logic_source(malformed: object) -> None:
    with pytest.raises(AccessMcpError, match="include_app_logic_source"):
        decode_request(_source_request(malformed))


def test_run_request_invokes_the_tier_two_reader_with_the_source_opt_in(monkeypatch) -> None:
    from ms_access_mcp.application.database import Ok
    from ms_access_mcp.application.documentation import (
        Coverage,
        EvidenceDoc,
        RunMetadata,
        SourceEvidence,
    )

    seen: dict[str, object] = {}
    document = EvidenceDoc(
        run=RunMetadata("run-1", "1.0", [], []),
        source=SourceEvidence("core.accdb", "accdb", "DAO", "offline", "closed"),
        coverage=Coverage(),
    )

    class _FakeDatabase:
        def open_database(self, _path: str) -> object:
            return Ok(data={})

        def close_database(self) -> None:
            return None

    class _FakeContent:
        warnings: list[str] = []

    def _fake_reader(path: str, *, include_source: bool = False) -> _FakeContent:
        seen["reader"] = (path, include_source)
        return _FakeContent()

    def _fake_apply(target: object, content: object) -> object:
        seen["applied"] = (target, content)
        return target

    monkeypatch.setattr(documentation_worker, "DatabaseUseCases", lambda **_kwargs: _FakeDatabase())
    monkeypatch.setattr(documentation_worker, "inspect_open_database", lambda *_a, **_k: document)
    monkeypatch.setattr(documentation_worker, "read_tier2_content", _fake_reader)
    monkeypatch.setattr(documentation_worker, "apply_app_logic_content", _fake_apply)

    documentation_worker._run_request(
        {
            "db_path": "core.accdb",
            "backend": "dao",
            "allowed_dirs": [],
            "tables": None,
            "queries": None,
            "include_query_sql": False,
            "include_app_logic": True,
            "include_app_logic_source": True,
        }
    )

    assert seen["reader"] == ("core.accdb", True)
    assert "applied" in seen


def test_run_request_never_invokes_the_tier_two_reader_when_app_logic_is_off(monkeypatch) -> None:
    """PRESERVE: default-off requests never instantiate Access, so no reader call happens."""
    from ms_access_mcp.application.database import Ok
    from ms_access_mcp.application.documentation import (
        Coverage,
        EvidenceDoc,
        RunMetadata,
        SourceEvidence,
    )

    called: list[str] = []
    document = EvidenceDoc(
        run=RunMetadata("run-1", "1.0", [], []),
        source=SourceEvidence("core.accdb", "accdb", "DAO", "offline", "closed"),
        coverage=Coverage(),
    )

    class _FakeDatabase:
        def open_database(self, _path: str) -> object:
            return Ok(data={})

        def close_database(self) -> None:
            return None

    monkeypatch.setattr(documentation_worker, "DatabaseUseCases", lambda **_kwargs: _FakeDatabase())
    monkeypatch.setattr(documentation_worker, "inspect_open_database", lambda *_a, **_k: document)
    monkeypatch.setattr(documentation_worker, "read_tier2_content", lambda *_a, **_k: called.append("reader"))

    documentation_worker._run_request(
        {
            "db_path": "core.accdb",
            "backend": "dao",
            "allowed_dirs": [],
            "tables": None,
            "queries": None,
            "include_query_sql": False,
            "include_app_logic": False,
        }
    )

    assert called == []


# ---------------------------------------------------------------------------
# 4. WP4: Tier 2 fallback observed through the public worker protocol
# ---------------------------------------------------------------------------


TIER2_PID = 51_000


def _provenance(kind: str, name: str, prop: str) -> FactProvenance:
    return FactProvenance(kind, name, prop)


def _passive_document() -> EvidenceDoc:
    """A v1 document (+ Tier 1 container) that a Tier 2 failure must never erase."""
    return EvidenceDoc(
        run=RunMetadata("run-1", "1.0", ["Orders"], []),
        source=SourceEvidence("core.accdb", "accdb", "DAO", "offline", "closed"),
        coverage=Coverage(),
        tables=[
            TableEvidence(
                name="Orders",
                fields=[
                    FieldEvidence(
                        name="OrderID",
                        type=FactStatus.observed("Long", _provenance("field", "OrderID", "type")),
                        size=FactStatus.observed(4, _provenance("field", "OrderID", "size")),
                        required=FactStatus.observed(True, _provenance("field", "OrderID", "required")),
                        allow_zero_length=FactStatus.observed(False, _provenance("field", "OrderID", "allow_zero_length")),
                        default_value=FactStatus.observed(None, _provenance("field", "OrderID", "default_value")),
                    )
                ],
                primary_key=FactStatus.observed(["OrderID"], _provenance("table", "Orders", "primary_key")),
                indexes=[],
            )
        ],
        app_logic=AppLogicEvidence(
            containers=[
                ContainerInventoryEvidence(
                    "form",
                    "Forms",
                    "DAO.Containers",
                    FactStatus.observed([{"name": "frmMain"}], _provenance("form", "Forms", "objects")),
                )
            ]
        ),
    )


class _FakeDatabase:
    """Records the worker's own open/close so the teardown is observable."""

    def __init__(self) -> None:
        self.closed = False

    def open_database(self, _path: str) -> object:
        from ms_access_mcp.application.database import Ok

        return Ok(data={})

    def close_database(self) -> None:
        self.closed = True


class _FakeJob:
    """A fake kill-on-close Job handle."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _ReadbackMismatchSession:
    """A session that cannot be armed: ForceDisable does not read back."""

    def __init__(self) -> None:
        self.opened = False

    def application_version(self) -> str:
        return "16.0"

    def set_automation_security(self, _level: int) -> None:
        return None

    def read_automation_security(self) -> int:
        return 1

    def open_current_database(self, _path: str) -> None:
        self.opened = True


class _ObservedTier2Session:
    """A fake COM boundary with exactly one observed form export."""

    def __init__(self) -> None:
        self.opened_path: str | None = None
        self.closed = False
        self.quit_called = False

    def application_version(self) -> str:
        return "16.0"

    def set_automation_security(self, _level: int) -> None:
        return None

    def read_automation_security(self) -> int:
        return 3

    def access_application_pid(self) -> int:
        return TIER2_PID

    def open_current_database(self, path: str) -> None:
        self.opened_path = path

    def close_current_database(self) -> None:
        self.closed = True

    def quit_application(self) -> None:
        self.quit_called = True

    def current_project_object_names(self, collection: str) -> list[str]:
        return ["frmMain"] if collection == "AllForms" else []

    def save_as_text(self, _object_type: int, _name: str, destination: str) -> None:
        Path(destination).write_bytes("FORM frmMain\r\nEnd\r\n".encode("utf-16-le"))

    def vba_module_count(self) -> int:
        return 0

    def reference_count(self) -> int:
        return 0


class _ComOperationalError(Exception):
    """A duck-typed operational COM failure: one failing HRESULT plus its message."""

    def __init__(self, hresult: int, message: str) -> None:
        super().__init__(hresult, message)
        self.hresult = hresult

    def __str__(self) -> str:
        return str(self.args[1])


class _VbaFailingSession(_ObservedTier2Session):
    """A Tier 2 session whose VBA project read fails with ``error``."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self._error = error

    def vba_module_count(self) -> int:
        raise self._error


def _run_protocol(
    monkeypatch: pytest.MonkeyPatch, request: dict[str, Any]
) -> tuple[int, list[AccessIdentity], dict[str, Any]]:
    """Drive the child entry point over in-memory streams (no spawn, no Access)."""
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode("utf-8"))))
    stdout = _RecordingStream()
    # ``main`` writes its single response to ``sys.stdout.buffer`` (a binary stream), and
    # the identity announcement targets the raw process stdout (``sys.__stdout__``); in a
    # real child both are the same protocol stream.
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=stdout))
    monkeypatch.setattr(sys, "__stdout__", SimpleNamespace(buffer=stdout))
    exit_code = documentation_worker.main([])
    identities, response = decode_response_with_identities(stdout.getvalue())
    return exit_code, identities, response


def _armed_request(source: Path) -> dict[str, Any]:
    """A request that opts into the armed Tier 2 read for ``source``."""
    return build_request(
        db_path=str(source),
        backend="dao",
        allowed_dirs=[],
        tables=None,
        queries=None,
        include_app_logic=True,
    )


def _scratch_dirs(root: Path) -> list[str]:
    return sorted(path.name for path in root.iterdir() if path.name.startswith("ms_access_doc_v2_"))


def _patch_worker_database(monkeypatch: pytest.MonkeyPatch, database: _FakeDatabase) -> None:
    monkeypatch.setattr(documentation_worker, "DatabaseUseCases", lambda **_kwargs: database)
    monkeypatch.setattr(documentation_worker, "inspect_open_database", lambda *_args, **_kwargs: _passive_document())


def test_worker_reports_ok_partial_and_keeps_every_passive_fact_when_tier_two_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S1-S3: the real reader's fallback reaches the protocol as partial evidence."""

    def failing_copy(_source: object, _destination: object) -> None:
        raise OSError("the disposable copy could not be written: PWD=hunter2secret;")

    monkeypatch.setenv(documentation_app_logic.SCRATCH_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(documentation_app_logic, "shutil", SimpleNamespace(copy2=failing_copy, rmtree=shutil.rmtree))
    source = tmp_path / "core.accdb"
    source.write_bytes(b"not-a-real-access-file")
    original = source.read_bytes()
    database = _FakeDatabase()
    _patch_worker_database(monkeypatch, database)

    exit_code, identities, response = _run_protocol(monkeypatch, _armed_request(source))

    assert exit_code == 0
    assert response["ok"] is True
    evidence = response["evidence"]
    assert evidence["status"] == "partial"
    # v1 + Tier 1 facts are retained in full.
    assert [table["name"] for table in evidence["data"]["tables"]] == ["Orders"]
    assert evidence["data"]["tables"][0]["fields"][0]["name"] == "OrderID"
    containers = evidence["data"]["app_logic"]["containers"]
    assert containers[0]["objects"]["state"] == "observed"
    assert containers[0]["objects"]["value"] == [{"name": "frmMain"}]
    # Every Tier 2 family and the reference family are explicit failed facts.
    content = evidence["data"]["app_logic"]["content"]
    assert [(item["category"], item["name"]) for item in content] == [
        ("form", "<all objects>"),
        ("report", "<all objects>"),
        ("macro", "<all objects>"),
        ("vba_module", "<all objects>"),
    ]
    assert all(item["content"]["state"] == "failed" for item in content)
    reference = evidence["data"]["app_logic"]["references"][0]
    assert reference["name"] == "<all objects>"
    assert all(reference[prop]["state"] == "failed" for prop in ("path", "guid", "major", "minor", "built_in"))
    # Sanitized reasons and a visible warning, never a silent degradation.
    assert evidence["warnings"]
    serialized = json.dumps(evidence)
    assert "hunter2secret" not in serialized
    # No owned Access instance was ever announced, and the original is untouched.
    assert identities == []
    assert source.read_bytes() == original
    assert database.closed is True
    assert _scratch_dirs(tmp_path) == []


def test_worker_keeps_a_containment_failure_a_hard_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S2: an unarmable inspection is never reported as partial success."""

    session = _ReadbackMismatchSession()
    monkeypatch.setenv(documentation_app_logic.SCRATCH_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(documentation_app_logic, "_default_launcher", lambda: session)
    source = tmp_path / "core.accdb"
    source.write_bytes(b"not-a-real-access-file")
    database = _FakeDatabase()
    _patch_worker_database(monkeypatch, database)

    exit_code, _identities, response = _run_protocol(monkeypatch, _armed_request(source))

    assert exit_code == 0
    assert response["ok"] is False
    # S2 strict diagnostics: the envelope carries the class, not the raised message.
    assert response["error"] == "ContainmentError"
    assert "evidence" not in response
    assert session.opened is False
    assert database.closed is True
    assert _scratch_dirs(tmp_path) == []


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_worker_propagates_an_interrupt_instead_of_reporting_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt: type[BaseException]
) -> None:
    """S2/S3: KeyboardInterrupt/SystemExit never become partial success in the protocol."""

    def interrupted_copy(_source: object, _destination: object) -> None:
        raise interrupt

    monkeypatch.setenv(documentation_app_logic.SCRATCH_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(documentation_app_logic, "shutil", SimpleNamespace(copy2=interrupted_copy, rmtree=shutil.rmtree))
    source = tmp_path / "core.accdb"
    source.write_bytes(b"not-a-real-access-file")
    database = _FakeDatabase()
    _patch_worker_database(monkeypatch, database)

    exit_code, _identities, response = _run_protocol(monkeypatch, _armed_request(source))

    assert exit_code == 0
    assert response["ok"] is False
    assert response["error"] == interrupt.__name__
    assert "evidence" not in response
    assert _scratch_dirs(tmp_path) == []


def test_worker_success_path_stays_complete_with_observed_tier_two_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRESERVE: the successful armed read is unchanged by the WP4 fallback."""

    session = _ObservedTier2Session()
    job = _FakeJob()
    monkeypatch.setenv(documentation_app_logic.SCRATCH_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(documentation_app_logic, "_default_launcher", lambda: session)
    monkeypatch.setattr(
        documentation_app_logic,
        "read_process_identity",
        lambda pid: AccessProcessIdentity(pid=pid, creation=CREATION, image="MSACCESS.EXE"),
    )
    monkeypatch.setattr(documentation_app_logic, "_assign_access_to_job", lambda _pid: job)
    source = tmp_path / "core.accdb"
    source.write_bytes(b"not-a-real-access-file")
    original = source.read_bytes()
    database = _FakeDatabase()
    _patch_worker_database(monkeypatch, database)

    exit_code, identities, response = _run_protocol(monkeypatch, _armed_request(source))

    assert exit_code == 0
    assert response["ok"] is True, response
    evidence = response["evidence"]
    assert evidence["status"] == "complete"
    assert identities == [AccessIdentity(pid=TIER2_PID, creation=CREATION, image="MSACCESS.EXE")]
    content = evidence["data"]["app_logic"]["content"]
    assert [(item["category"], item["name"], item["content"]["state"]) for item in content] == [
        ("form", "frmMain", "observed"),
    ]
    assert evidence["warnings"] == []
    # Only the disposable copy is opened, and both teardown paths ran.
    assert session.opened_path is not None
    assert session.opened_path != str(source)
    assert session.closed is True and session.quit_called is True and job.closed is True
    assert source.read_bytes() == original
    assert database.closed is True
    assert _scratch_dirs(tmp_path) == []


def _patch_armed_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, session: Any) -> Any:
    """Wire the production Tier 2 reader to ``session`` for one armed protocol run."""
    monkeypatch.setenv(documentation_app_logic.SCRATCH_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(documentation_app_logic, "_default_launcher", lambda: session)
    monkeypatch.setattr(
        documentation_app_logic,
        "read_process_identity",
        lambda pid: AccessProcessIdentity(pid=pid, creation=CREATION, image="MSACCESS.EXE"),
    )
    monkeypatch.setattr(documentation_app_logic, "_assign_access_to_job", lambda _pid: _FakeJob())
    return session


def test_worker_degrades_an_operational_com_copy_failure_as_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S1: a COM-shaped operational failure keeps the documented partial degradation."""

    def failing_copy(_source: object, _destination: object) -> None:
        raise _ComOperationalError(0x80004005, "the disposable copy COM call failed")

    monkeypatch.setenv(documentation_app_logic.SCRATCH_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(documentation_app_logic, "shutil", SimpleNamespace(copy2=failing_copy, rmtree=shutil.rmtree))
    source = tmp_path / "core.accdb"
    source.write_bytes(b"not-a-real-access-file")
    original = source.read_bytes()
    database = _FakeDatabase()
    _patch_worker_database(monkeypatch, database)

    exit_code, identities, response = _run_protocol(monkeypatch, _armed_request(source))

    assert exit_code == 0
    assert response["ok"] is True
    evidence = response["evidence"]
    assert evidence["status"] == "partial"
    content = evidence["data"]["app_logic"]["content"]
    assert all(item["content"]["state"] == "failed" for item in content)
    # S2 strict diagnostics: every degraded reason names the failure class only.
    assert {item["content"]["reason"] for item in content} == {
        "Tier 2 app-logic content was not read: the disposable copy of the source "
        "could not be created: _ComOperationalError"
    }
    assert identities == []
    assert source.read_bytes() == original
    assert database.closed is True
    assert _scratch_dirs(tmp_path) == []


def test_worker_reports_a_programming_defect_as_a_hard_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S1: an unexpected defect is a sanitized hard error, never partial evidence."""

    def failing_copy(_source: object, _destination: object) -> None:
        raise ValueError("documentation reader defect: PWD=hunter2secret;")

    monkeypatch.setenv(documentation_app_logic.SCRATCH_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(documentation_app_logic, "shutil", SimpleNamespace(copy2=failing_copy, rmtree=shutil.rmtree))
    source = tmp_path / "core.accdb"
    source.write_bytes(b"not-a-real-access-file")
    original = source.read_bytes()
    database = _FakeDatabase()
    _patch_worker_database(monkeypatch, database)

    exit_code, identities, response = _run_protocol(monkeypatch, _armed_request(source))

    assert exit_code == 0
    assert response["ok"] is False
    # S2 strict diagnostics: the defect's text is suppressed, only its class survives.
    assert response["error"] == "ValueError"
    assert "hunter2secret" not in response["error"]
    assert "evidence" not in response
    assert identities == []
    assert source.read_bytes() == original
    assert database.closed is True
    assert _scratch_dirs(tmp_path) == []


_VBA_FAILURES = [
    # The recognized trust refusal is authored prerequisite text rendered from the
    # constant, so it survives the strict policy. Unknown COM detail is exception text and
    # is therefore class-only (S2/S3): the state, not the text, carries the classification.
    pytest.param(VbaTrustRefusedError(VBA_TRUST_PREREQUISITE), "unavailable", "Trust access", id="trust-refusal"),
    pytest.param(
        VbaProjectUnavailableError("the VBA project could not be read (unknown COM failure 0x8000FFFF)"),
        "failed",
        "VbaProjectUnavailableError",
        id="unknown-com-failure",
    ),
]


@pytest.mark.parametrize(("error", "state", "expected_text"), _VBA_FAILURES)
def test_worker_reports_the_vba_family_by_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException, state: str, expected_text: str
) -> None:
    """S2: only a recognized trust refusal is ``unavailable``; unknowns stay ``failed``."""
    session = _patch_armed_session(monkeypatch, tmp_path, _VbaFailingSession(error))
    source = tmp_path / "core.accdb"
    source.write_bytes(b"not-a-real-access-file")
    database = _FakeDatabase()
    _patch_worker_database(monkeypatch, database)

    exit_code, _identities, response = _run_protocol(monkeypatch, _armed_request(source))

    assert exit_code == 0
    assert response["ok"] is True, response
    evidence = response["evidence"]
    assert evidence["status"] == "partial"
    vba = next(item for item in evidence["data"]["app_logic"]["content"] if item["category"] == "vba_module")
    assert vba["content"]["state"] == state
    assert expected_text in (vba["content"]["reason"] or "")
    if state == "failed":
        assert VBA_TRUST_PREREQUISITE not in (vba["content"]["reason"] or "")
        assert "0x8000FFFF" not in (vba["content"]["reason"] or "")
    # The observed form export and the teardown survive the VBA classification.
    form = next(item for item in evidence["data"]["app_logic"]["content"] if item["category"] == "form")
    assert form["content"]["state"] == "observed"
    assert session.closed is True and session.quit_called is True
    assert database.closed is True
    assert _scratch_dirs(tmp_path) == []


# --- Strict class-only diagnostics (odd/tasks/documentation-strict-error-diagnostics.md) ---

_STRICT_FAKE_USER = "fakeuser123"
_STRICT_PATH = rf"C:\Users\{_STRICT_FAKE_USER}\AppData\Local\Temp\ms_access_doc_v2_x\source.accdb"


def test_worker_catch_all_reports_the_exception_class_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S2/S5: the child catch-all exposes the class, never a credential-bearing text."""

    def failing_copy(_source: object, _destination: object) -> None:
        raise RuntimeError(f"copy failed for {_STRICT_PATH};PWD=hunter2secret")

    monkeypatch.setenv(documentation_app_logic.SCRATCH_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(documentation_app_logic, "shutil", SimpleNamespace(copy2=failing_copy, rmtree=shutil.rmtree))
    source = tmp_path / "core.accdb"
    source.write_bytes(b"not-a-real-access-file")
    original = source.read_bytes()
    database = _FakeDatabase()
    _patch_worker_database(monkeypatch, database)

    exit_code, identities, response = _run_protocol(monkeypatch, _armed_request(source))

    assert exit_code == 0
    assert response == {"ok": False, "error": "RuntimeError"}
    assert "hunter2secret" not in json.dumps(response)
    assert _STRICT_FAKE_USER not in json.dumps(response)
    assert identities == []
    assert source.read_bytes() == original
    assert database.closed is True
    assert _scratch_dirs(tmp_path) == []


def test_worker_catch_all_keeps_only_the_numeric_os_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S2/S5: an OS failure at the catch-all keeps its class and numeric errno only."""
    error = OSError(2, f"request read failed for {_STRICT_PATH}", None, None, _STRICT_PATH)

    def failing_decode(_payload: bytes) -> Any:
        raise error

    monkeypatch.setattr(documentation_worker, "decode_request", failing_decode)

    exit_code, identities, response = _run_protocol(monkeypatch, _armed_request(tmp_path / "core.accdb"))

    assert exit_code == 0
    assert response == {"ok": False, "error": "FileNotFoundError: [Errno 2]"}
    assert _STRICT_FAKE_USER not in json.dumps(response)
    assert identities == []
