"""Fixed resource bounds for documentation workflows (S7 standard profile).

Units are UTF-8 bytes. The evidence and report caps count the canonical JSON
serialization of the evidence/report *object* -- the ``data`` member the CLI and MCP
tools emit -- never the outer CLI envelope or the JSON-RPC transport envelope. Canonical
JSON uses ``json.dumps`` defaults, so a non-ASCII character counts as its ``\\uXXXX``
escape bytes. The Markdown cap counts the UTF-8 bytes of the report Markdown string.

These are application-level caps. MCP receives already-decoded JSON, so these caps do
not bound the JSON-RPC payload before decoding, and no server-global transport limit is
claimed. Acceptance is inclusive: a value whose size equals a cap is accepted; a larger
value is rejected with a safe ``AccessMcpError`` and no truncation, no partial artifact,
and no destination or temporary-file side effect.

Selected profile (S7): 32 MiB evidence, 16 MiB Markdown, 16 MiB report JSON, and a
120-second whole-inspection deadline enforced by the owned
``application/documentation_runtime.py`` supervisor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import AccessMcpError

MIB = 1024 * 1024

EVIDENCE_MAX_BYTES = 32 * MIB
MARKDOWN_MAX_BYTES = 16 * MIB
REPORT_JSON_MAX_BYTES = 16 * MIB

# The 120-second whole-inspection deadline (S7) covers the owned worker's process
# startup, source open, passive extraction, and evidence serialization. It is not a
# post-hoc wait after the read returns.
DEADLINE_SECONDS = 120.0

# Bounded cleanup is transparent: after the deadline the owned child is terminated and
# reaped within this grace. The kernel does not release a terminated process or its
# handles instantaneously, so an explicit grace (not an instantaneous-cleanup claim)
# bounds both the reap and the pipe/reader-thread join.
CLEANUP_GRACE_SECONDS = 5.0

# Private bounded-IPC budget. The request and the child's diagnostics are bounded so a
# frontend can never hand an unbounded blob to the child or accumulate unbounded stderr.
REQUEST_MAX_BYTES = MIB
DIAGNOSTIC_MAX_BYTES = 64 * 1024

# The worker wraps the evidence object in a small JSON response envelope. This fixed,
# documented allowance bounds that framing plus the top-level status metadata on top of
# the 32 MiB evidence cap, so a legitimate at-cap document is never mistaken for a
# response flood while a real flood is still rejected.
WORKER_OUTPUT_FRAMING_ALLOWANCE_BYTES = 64 * 1024

# A raw CLI evidence/report file may wrap an exact-cap document in the optional ``Ok``
# envelope (``{"success": true, "data": <doc>, "rowcount": null}``) plus modest
# formatting whitespace. The bounded raw read allows the cap plus this fixed,
# documented allowance; the decoded object's own canonical JSON size is then checked
# against the cap itself, so an arbitrarily oversized wrapper is never silently
# accepted. A raw file above ``cap + allowance`` is rejected before any parse.
CLI_ENVELOPE_ALLOWANCE_BYTES = 1024

_CHUNK_CHARACTERS = 65536


def worker_output_limit() -> int:
    """Byte limit for one bounded worker stdout response (evidence cap + framing)."""
    return EVIDENCE_MAX_BYTES + WORKER_OUTPUT_FRAMING_ALLOWANCE_BYTES


def raw_evidence_file_limit() -> int:
    """Raw byte limit for a CLI evidence file (evidence cap plus envelope allowance)."""
    return EVIDENCE_MAX_BYTES + CLI_ENVELOPE_ALLOWANCE_BYTES


def raw_report_file_limit() -> int:
    """Raw byte limit for a CLI report file (report cap plus envelope allowance)."""
    return REPORT_JSON_MAX_BYTES + CLI_ENVELOPE_ALLOWANCE_BYTES


def json_utf8_size(value: Any, *, limit: int) -> int | None:
    """Return the canonical JSON UTF-8 byte count, or ``None`` once ``limit`` is passed.

    Counting is incremental (``JSONEncoder.iterencode``), so an oversized document is
    not materialized as one serialized string just to be rejected. The serialization
    matches ``json.dumps`` defaults, which is what the CLI/MCP frontends emit.
    """
    total = 0
    for chunk in json.JSONEncoder().iterencode(value):
        total += len(chunk.encode("utf-8"))
        if total > limit:
            return None
    return total


def _over_limit(kind: str, limit: int, unit: str) -> AccessMcpError:
    return AccessMcpError(
        f"Documentation {kind} exceeds the allowed {limit} byte limit ({unit}); "
        "the input was rejected and no truncation is applied"
    )


def ensure_evidence_within_limit(value: Any) -> None:
    """Reject evidence whose canonical JSON exceeds :data:`EVIDENCE_MAX_BYTES`."""
    if json_utf8_size(value, limit=EVIDENCE_MAX_BYTES) is None:
        raise _over_limit("evidence", EVIDENCE_MAX_BYTES, "canonical JSON UTF-8 bytes")


def ensure_report_within_limit(value: Any) -> None:
    """Reject a report object whose canonical JSON exceeds :data:`REPORT_JSON_MAX_BYTES`."""
    if json_utf8_size(value, limit=REPORT_JSON_MAX_BYTES) is None:
        raise _over_limit("report JSON", REPORT_JSON_MAX_BYTES, "canonical JSON UTF-8 bytes")


def ensure_markdown_within_limit(markdown: str) -> None:
    """Reject Markdown whose UTF-8 byte length exceeds :data:`MARKDOWN_MAX_BYTES`.

    The string is encoded in bounded chunks so a very large input is never copied whole
    merely to be rejected.
    """
    total = 0
    for start in range(0, len(markdown), _CHUNK_CHARACTERS):
        total += len(markdown[start : start + _CHUNK_CHARACTERS].encode("utf-8"))
        if total > MARKDOWN_MAX_BYTES:
            raise _over_limit("Markdown", MARKDOWN_MAX_BYTES, "Markdown UTF-8 bytes")


def read_bounded_json_file(path: str | Path, *, limit: int) -> Any:
    """Read and parse a JSON file without an unbounded or stat-only read.

    At most ``limit + 1`` bytes are read exactly once and parsed from that buffer, so a
    file above the raw ingress limit is rejected before any parse and a file that grows
    after the read cannot bypass the check. Exactly ``limit`` bytes is accepted
    (inclusive). ``OSError`` from an unreadable path propagates unchanged.
    """
    location = Path(path)
    with location.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise AccessMcpError(
            f"Documentation input file exceeds the allowed {limit} byte raw ingress "
            f"limit (UTF-8 JSON file bytes): {location}"
        )
    return json.loads(data.decode("utf-8"))


def read_evidence_json_file(path: str | Path) -> Any:
    """Bounded read of a CLI evidence file using the default evidence ingress limit."""
    return read_bounded_json_file(path, limit=raw_evidence_file_limit())


def read_report_json_file(path: str | Path) -> Any:
    """Bounded read of a CLI report file using the default report ingress limit."""
    return read_bounded_json_file(path, limit=raw_report_file_limit())
