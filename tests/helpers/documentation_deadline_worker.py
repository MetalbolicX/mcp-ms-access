"""Owned test-fixture child used only to exercise the documentation supervisor.

This is a private, repository-owned launcher target for ``DocumentationRuntime`` tests.
It is never wired into production: the real worker is
``ms_access_mcp.application.documentation_worker`` and the production launcher is fixed in
``documentation_runtime``. The helper reads the same structured request from stdin as the
real worker and can either emit a controlled response or open a disposable fixture and
block, so a test can prove the supervisor kills/reaps exactly its own child.

Modes:

* ``ok``            read the request, emit a minimal valid evidence response.
* ``malformed``     emit bytes that are not JSON.
* ``oversize``      emit a response larger than a small monkeypatched output cap.
* ``nonzero``       write a canary secret to stderr and exit non-zero.
* ``secret``        report an application error whose text contains a canary secret.
* ``block_before_open``  stall before reading the request (startup/open-setup stall).
* ``block_after_open``   open the requested source read-only, then stall (kill proof).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Fixed canary the supervisor must never reflect back to the caller.
CANARY_SECRET = "canary-secret-9f3c"
_OVERSIZE_BYTES = 1024 * 1024
_STALL_SECONDS = 60


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
    sys.stdout.buffer.flush()


def _minimal_evidence(request: dict[str, object]) -> dict[str, object]:
    from ms_access_mcp.application.documentation import Coverage, EvidenceDoc, RunMetadata, SourceEvidence

    document = EvidenceDoc(
        run=RunMetadata("fixture-stub", "1.0", [], []),
        source=SourceEvidence(str(request.get("db_path", "stub.accdb")), "ACE", "DAO", "offline", "caller-managed"),
        coverage=Coverage(),
    )
    payload = document.to_dict()
    payload["status"] = document.status
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True)
    args = parser.parse_args()

    if args.mode == "block_before_open":
        time.sleep(_STALL_SECONDS)
        return 0

    from ms_access_mcp.application.documentation_limits import REQUEST_MAX_BYTES

    request = json.loads(sys.stdin.buffer.read(REQUEST_MAX_BYTES + 1).decode("utf-8"))

    if args.mode == "ok":
        _emit({"ok": True, "evidence": _minimal_evidence(request)})
        return 0
    if args.mode == "malformed":
        sys.stdout.buffer.write(b"not-a-json-response{")
        sys.stdout.buffer.flush()
        return 0
    if args.mode == "oversize":
        sys.stdout.buffer.write(b"x" * _OVERSIZE_BYTES)
        sys.stdout.buffer.flush()
        return 0
    if args.mode == "nonzero":
        sys.stderr.write(f"PWD={CANARY_SECRET}\n")
        sys.stderr.flush()
        return 2
    if args.mode == "secret":
        _emit({"ok": False, "error": f"connect failed PWD={CANARY_SECRET};DBQ=stub.accdb"})
        return 0
    if args.mode == "block_after_open":
        from ms_access_mcp.application.database import DatabaseUseCases, Err

        database = DatabaseUseCases(
            backend=str(request.get("backend", "dao")),
            readonly=True,
            allowed_dirs=[Path(directory) for directory in request.get("allowed_dirs", [])],
        )
        opened = database.open_database(str(request["db_path"]))
        if isinstance(opened, Err):
            _emit({"ok": False, "error": opened.message})
            return 0
        time.sleep(_STALL_SECONDS)
        return 0
    msg = f"Unknown fixture mode: {args.mode}"
    raise SystemExit(msg)


if __name__ == "__main__":
    raise SystemExit(main())
