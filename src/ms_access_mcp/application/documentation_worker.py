"""Owned child worker for isolated, deadline-bounded documentation inspection.

The parent documentation facade (``application/documentation_runtime.py``) never opens
the Access source. It spawns this module as a fresh Python process and hands it a small
structured JSON request over stdin. This child is the only place that validates the
source path, resolves credentials, opens the database, and performs passive extraction:

* DAO opens ``DAO.DBEngine.120`` on the child's own STA thread (``DatabaseUseCases`` ->
  ``DaoAdapter`` -> ``ComDispatcher``); the ODBC path opens its own engine-level
  read-only connection. Neither launches ``Access.Application`` nor executes a query,
  macro, or VBA. No live parent handle is shared across the process boundary.
* The request carries only path/backend/allowed-dir/filter policy plus the
  ``include_query_sql`` output opt-in (default ``False``). Credentials are never
  placed in argv or a dedicated environment variable: the child resolves them through
  the existing server credential resolver (``credentials.resolve_password``), exactly as
  the ordinary server/CLI open does, and the allowed-directory guard runs inside the
  child through the same ``DatabaseUseCases`` open path.
* The response is one JSON object written to stdout and flushed once. A known
  application failure is reported as ``{"ok": false, "error": <scrubbed message>}`` with
  exit status 0 so the parent can surface the exact reason; a crash or unparseable
  stdout is a non-zero exit the parent maps to a generic failure.

The bounded-IPC constants live in ``documentation_limits`` and are shared with the
parent supervisor, so both sides agree on the framing and byte budgets.
"""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from ..errors import AccessMcpError
from .database import DatabaseUseCases, Err
from .documentation import redact_connection_secrets
from .documentation_inspect import inspect_open_database
from .documentation_limits import REQUEST_MAX_BYTES, ensure_evidence_within_limit

_ALLOWED_BACKENDS = frozenset({"auto", "dao", "odbc"})
_RESPONSE_ENCODING = "utf-8"


def build_request(
    *,
    db_path: str,
    backend: str,
    allowed_dirs: list[Path],
    tables: list[str] | None,
    queries: list[str] | None,
    include_query_sql: bool = False,
) -> dict[str, Any]:
    """Build the structured request handed to the child (no credentials, no executable)."""
    normalized_backend = str(backend).strip().casefold()
    if normalized_backend not in _ALLOWED_BACKENDS:
        msg = f"Documentation inspection backend must be one of {sorted(_ALLOWED_BACKENDS)}"
        raise AccessMcpError(msg)
    if not isinstance(db_path, str) or not db_path:
        msg = "Documentation inspection requires a non-empty database path"
        raise AccessMcpError(msg)
    return {
        "db_path": db_path,
        "backend": normalized_backend,
        "allowed_dirs": [str(directory) for directory in allowed_dirs],
        "tables": None if tables is None else [str(name) for name in tables],
        "queries": None if queries is None else [str(name) for name in queries],
        "include_query_sql": bool(include_query_sql),
    }


def encode_request(request: dict[str, Any]) -> bytes:
    """Serialize a request to canonical bytes, rejecting one above the IPC request cap."""
    encoded = json.dumps(request, ensure_ascii=True, separators=(",", ":")).encode(_RESPONSE_ENCODING)
    if len(encoded) > REQUEST_MAX_BYTES:
        msg = (
            f"Documentation inspection request exceeds the allowed {REQUEST_MAX_BYTES} byte "
            "IPC request limit; the request was rejected before any worker was started"
        )
        raise AccessMcpError(msg)
    return encoded


def decode_request(data: bytes) -> dict[str, Any]:
    """Decode and validate a bounded structured request; reject malformed content."""
    if len(data) > REQUEST_MAX_BYTES:
        msg = (
            f"Documentation inspection request exceeds the allowed {REQUEST_MAX_BYTES} byte "
            "IPC request limit"
        )
        raise AccessMcpError(msg)
    try:
        request = json.loads(data.decode(_RESPONSE_ENCODING))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        msg = "Documentation worker received a malformed request"
        raise AccessMcpError(msg) from error
    if not isinstance(request, dict):
        msg = "Documentation worker request must be a JSON object"
        raise AccessMcpError(msg)
    if not isinstance(request.get("db_path"), str) or not request.get("db_path"):
        msg = "Documentation worker request requires a non-empty db_path"
        raise AccessMcpError(msg)
    if request.get("backend") not in _ALLOWED_BACKENDS:
        msg = f"Documentation worker request backend must be one of {sorted(_ALLOWED_BACKENDS)}"
        raise AccessMcpError(msg)
    if not isinstance(request.get("allowed_dirs"), list):
        msg = "Documentation worker request requires an allowed_dirs list"
        raise AccessMcpError(msg)
    for key in ("tables", "queries"):
        value = request.get(key)
        if value is not None and (not isinstance(value, list) or any(not isinstance(name, str) for name in value)):
            msg = f"Documentation worker request field {key!r} must be a list of strings or null"
            raise AccessMcpError(msg)
    # Strict bool validation with a default: an absent key is ``False``; any present
    # non-bool (including 0/1) is a malformed request rather than a truthy coercion.
    include_query_sql = request.get("include_query_sql", False)
    if not isinstance(include_query_sql, bool):
        msg = "Documentation worker request field 'include_query_sql' must be a boolean"
        raise AccessMcpError(msg)
    request["include_query_sql"] = include_query_sql
    return request


def encode_response(payload: dict[str, Any]) -> bytes:
    """Serialize one complete worker response object."""
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(_RESPONSE_ENCODING)


def decode_response(data: bytes) -> dict[str, Any]:
    """Decode one complete worker response; reject empty, malformed, or unknown shapes."""
    if not data:
        msg = "Documentation worker returned an empty response"
        raise AccessMcpError(msg)
    try:
        payload = json.loads(data.decode(_RESPONSE_ENCODING))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        msg = "Documentation worker returned a malformed response"
        raise AccessMcpError(msg) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        msg = "Documentation worker response must be an object with a boolean 'ok'"
        raise AccessMcpError(msg)
    return payload


def _read_stdin_request(stream: Any) -> bytes:
    """Read at most the request cap plus one byte from a binary stream."""
    return stream.read(REQUEST_MAX_BYTES + 1)


def _run_request(request: dict[str, Any]) -> dict[str, Any]:
    """Open the source, extract passively, and return the final capped evidence payload."""
    allowed_dirs = [Path(directory) for directory in request["allowed_dirs"]]
    db_path = str(request["db_path"])
    database = DatabaseUseCases(
        backend=str(request["backend"]),
        readonly=True,
        allowed_dirs=allowed_dirs,
    )
    # ``open_database`` resolves credentials through the existing server credential
    # resolver (``credentials.resolve_password``) and enforces the same allowed-directory
    # policy ordinary server/CLI opens use; there is no child bypass. The read-only policy
    # flag is set so no documentation path can write.
    try:
        opened = database.open_database(db_path)
        if isinstance(opened, Err):
            raise AccessMcpError(opened.message)
        document = inspect_open_database(
            database,
            tables=request.get("tables"),
            queries=request.get("queries"),
            include_query_sql=request.get("include_query_sql", False),
        )
    finally:
        database.close_database()
    payload = document.to_dict()
    payload["status"] = document.status
    # Reject the final emitted payload here too, so an over-cap document is a clean
    # application error rather than a truncated or oversized response.
    ensure_evidence_within_limit(payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    """Child entry point: read one bounded request, emit one bounded response."""
    del argv  # the child interface is stdin/stdout only; it accepts no caller arguments
    try:
        request_bytes = _read_stdin_request(sys.stdin.buffer)
        request = decode_request(request_bytes)
        with redirect_stdout(sys.stderr):
            payload = _run_request(request)
        response = {"ok": True, "evidence": payload}
    except BaseException as error:  # noqa: BLE001 - any child failure becomes a safe Err
        message = redact_connection_secrets(str(error).strip() or type(error).__name__)
        response = {"ok": False, "error": message or "Documentation inspection failed"}
    try:
        sys.stdout.buffer.write(encode_response(response))
        sys.stdout.buffer.flush()
    except Exception:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
