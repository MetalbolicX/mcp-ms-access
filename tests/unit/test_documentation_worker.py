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

from ms_access_mcp.application.documentation_worker import (
    AccessIdentity,
    decode_response,
    decode_response_with_identities,
    emit_access_identity,
    split_access_identity_frames,
)
from ms_access_mcp.errors import AccessMcpError

FRAME_PREFIX = "@@ACCESS_IDENTITY@@ "
CREATION = 133_456_789_012_345_678


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
