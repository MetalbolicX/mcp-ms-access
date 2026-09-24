"""Automatic session backup — create on write-open, delete on clean close, orphan detect.

Plan 047 / Session Backup: when a write-enabled session opens a database, the
server takes a rolling snapshot (copy + sha256 manifest, kind="auto_session").
On clean close the snapshot is deleted automatically. On crash/kill the snapshot
survives; the next open detects it and reports crash_detected with the backup
path. Read-only sessions take no snapshot.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime

from ..errors import Err, Ok, Result

# ---------------------------------------------------------------------------
# Internal helpers (shared with maintenance.backup but isolated for auto-session)
# ---------------------------------------------------------------------------

_AUTO_SESSION_DIR = os.path.join(tempfile.gettempdir(), "ms_access_backups", "autosession")
_FILENAME_PREFIX = "autosession"


def _snapshot_path(db_path: str, pid: int) -> tuple[str, str]:
    """Return (backup_path, manifest_path) for the auto-session snapshot.

    Filename: autosession_<pid>_<timestamp>_<basename>
    This never collides with or counts against manual-backup retention.
    """
    os.makedirs(_AUTO_SESSION_DIR, exist_ok=True)
    basename = os.path.basename(db_path)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{_FILENAME_PREFIX}_{pid}_{timestamp}_{basename}"
    backup_path = os.path.join(_AUTO_SESSION_DIR, filename)
    manifest_path = backup_path + ".manifest.json"
    return backup_path, manifest_path


def _write_manifest(manifest_path: str, source_path: str, backup_path: str) -> None:
    """Write the manifest file for an auto-session snapshot."""
    sha256 = hashlib.sha256()
    with open(backup_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            sha256.update(chunk)

    manifest = {
        "source_path": source_path,
        "backup_path": backup_path,
        "sha256": sha256.hexdigest(),
        "size_bytes": os.path.getsize(backup_path),
        "created_at": datetime.now(UTC).isoformat(),
        "kind": "auto_session",
        "session_pid": os.getpid(),
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_session_backup(db_path: str) -> Result:
    """Create an automatic pre-session snapshot of the database.

    Reuses the maintenance backup copy/hash/manifest machinery.
    Filename is prefixed with autosession_<pid>_ so it is isolated per-session
    and does not count against manual-backup retention.

    Returns:
        Ok with {"backup_path": ..., "manifest_path": ...} on success.
        Err on failure (backup not created — non-fatal, proceed without it).
    """
    if not os.path.exists(db_path):
        return Err(f"Cannot backup {db_path}: source not found")

    backup_path, manifest_path = _snapshot_path(db_path, os.getpid())

    try:
        shutil.copy2(db_path, backup_path)
    except Exception as e:
        return Err(f"Cannot create session backup: {e}")

    try:
        _write_manifest(manifest_path, db_path, backup_path)
    except Exception as e:
        # Best-effort: delete partial snapshot if manifest write fails
        try:
            os.remove(backup_path)
        except Exception:
            pass
        return Err(f"Cannot write session backup manifest: {e}")

    return Ok(data={"backup_path": backup_path, "manifest_path": manifest_path})


def cleanup_session_backup(db_path: str, pid: int | None = None) -> None:
    """Delete the auto-session snapshot (and manifest) for this session.

    Restricted to this database's basename — one process may hold snapshots
    for several databases. Best-effort: never raises. Idempotent — safe to
    call in finally blocks.
    """
    target_pid = pid if pid is not None else os.getpid()
    basename = os.path.basename(db_path)
    pattern = os.path.join(_AUTO_SESSION_DIR, f"{_FILENAME_PREFIX}_{target_pid}_*_{basename}")
    for backup_path in glob.glob(pattern):
        manifest_path = backup_path + ".manifest.json"
        try:
            os.remove(backup_path)
        except Exception:
            pass
        try:
            if os.path.exists(manifest_path):
                os.remove(manifest_path)
        except Exception:
            pass


def detect_orphaned_backup(db_path: str) -> dict | None:
    """Detect a surviving auto-session snapshot for this database.

    Returns:
        The manifest dict if a snapshot exists whose session_pid is no longer
        alive (crashed session). Returns None if no orphan found.

    The heuristic: any surviving auto-session snapshot for this DB whose
    session_pid is dead (or simply: any surviving auto-session snapshot =
    crashed session, since clean close deletes it).
    """
    basename = os.path.basename(db_path)
    pattern = os.path.join(_AUTO_SESSION_DIR, f"{_FILENAME_PREFIX}_*_{basename}")
    candidates = glob.glob(pattern)
    if not candidates:
        return None

    # Filter to those whose manifest belongs to this source path
    for backup_path in candidates:
        manifest_path = backup_path + ".manifest.json"
        if not os.path.exists(manifest_path):
            continue
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except Exception:
            continue

        if manifest.get("source_path") != db_path:
            continue
        if manifest.get("kind") != "auto_session":
            continue

        # Check if the session PID is still alive
        session_pid = manifest.get("session_pid")
        if session_pid is None:
            # Old orphan from before session_pid field — treat as orphan
            return manifest

        try:
            # On Windows, check if process exists
            import subprocess

            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {session_pid}", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
            output = result.stdout.decode("utf-8", errors="replace").strip()
            if str(session_pid) in output:
                # Process still alive — not an orphan
                continue
        except Exception:
            pass

        return manifest

    return None
