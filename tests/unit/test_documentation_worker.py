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

import pytest

from ms_access_mcp.application import documentation_worker
from ms_access_mcp.application.documentation_worker import (
    AccessIdentity,
    build_request,
    decode_request,
    decode_response,
    decode_response_with_identities,
    emit_access_identity,
    split_access_identity_frames,
)
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
