"""Tests for automatic session backup (plan 047).

Covers:
- create_session_backup: success path, missing source, manifest written
- cleanup_session_backup: deletes autosession snapshot + manifest, best-effort
- detect_orphaned_backup: no orphan / orphan found / process-still-alive case
- readonly session: no snapshot taken (tested at database.py wiring level)
- orphan never auto-deleted (survives until operator dismisses)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ms_access_mcp.application.session_backup import (
    _AUTO_SESSION_DIR,
    cleanup_session_backup,
    create_session_backup,
    detect_orphaned_backup,
)
from ms_access_mcp.errors import Err, Ok


class TestCreateSessionBackup:
    def test_creates_snapshot_and_manifest(self, tmp_path: Path) -> None:
        db = tmp_path / "test.accdb"
        db.write_bytes(b"FAKE ACCESS DB CONTENT")

        result = create_session_backup(str(db))

        assert isinstance(result, Ok)
        data = result.data
        backup_path = data["backup_path"]
        manifest_path = data["manifest_path"]

        assert os.path.exists(backup_path)
        assert os.path.exists(manifest_path)
        assert backup_path.endswith(".accdb")
        assert "autosession_" in backup_path

        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest["kind"] == "auto_session"
        assert manifest["source_path"] == str(db)
        assert manifest["session_pid"] == os.getpid()
        assert "sha256" in manifest

    def test_missing_source_returns_err(self, tmp_path: Path) -> None:
        result = create_session_backup(str(tmp_path / "nonexistent.accdb"))
        assert isinstance(result, Err)
        assert "not found" in result.message.lower()

    def test_snapshot_contains_source_content(self, tmp_path: Path) -> None:
        db = tmp_path / "test.accdb"
        db.write_bytes(b"ORIGINAL CONTENT")

        result = create_session_backup(str(db))
        assert isinstance(result, Ok)

        backup_path = result.data["backup_path"]
        with open(backup_path, "rb") as f:
            assert f.read() == b"ORIGINAL CONTENT"


class TestCleanupSessionBackup:
    def test_deletes_snapshot_and_manifest(self, tmp_path: Path) -> None:
        db = tmp_path / "test.accdb"
        db.write_bytes(b"CONTENT")
        pid = os.getpid()

        result = create_session_backup(str(db))
        assert isinstance(result, Ok)
        backup_path = result.data["backup_path"]
        manifest_path = result.data["manifest_path"]

        assert os.path.exists(backup_path)
        assert os.path.exists(manifest_path)

        cleanup_session_backup(str(db), pid)

        assert not os.path.exists(backup_path)
        assert not os.path.exists(manifest_path)

    def test_best_effort_never_raises(self, tmp_path: Path) -> None:
        # Calling cleanup with a non-existent PID should not raise
        cleanup_session_backup(str(tmp_path / "fake.accdb"), 99999)

    def test_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "test.accdb"
        db.write_bytes(b"CONTENT")

        result = create_session_backup(str(db))
        assert isinstance(result, Ok)

        cleanup_session_backup(str(db), os.getpid())
        # Second call should not raise even if file already gone
        cleanup_session_backup(str(db), os.getpid())

    def test_cleanup_filters_by_database(self, tmp_path: Path) -> None:
        """Cleanup deletes only this db's snapshot; another db's snapshot for the same PID survives."""
        db_a = tmp_path / "alpha.accdb"
        db_b = tmp_path / "beta.accdb"
        db_a.write_bytes(b"AAA")
        db_b.write_bytes(b"BBB")
        pid = os.getpid()

        result_a = create_session_backup(str(db_a))
        result_b = create_session_backup(str(db_b))
        assert isinstance(result_a, Ok)
        assert isinstance(result_b, Ok)
        backup_a = result_a.data["backup_path"]
        backup_b = result_b.data["backup_path"]

        try:
            cleanup_session_backup(str(db_a), pid)

            assert not os.path.exists(backup_a)
            assert not os.path.exists(backup_a + ".manifest.json")
            assert os.path.exists(backup_b), "db B snapshot must survive cleanup of db A"
            assert os.path.exists(backup_b + ".manifest.json"), "db B manifest must survive cleanup of db A"
        finally:
            cleanup_session_backup(str(db_b), pid)


class TestDetectOrphanedBackup:
    def test_no_orphan_returns_none(self, tmp_path: Path) -> None:
        db = tmp_path / "test.accdb"
        db.write_bytes(b"CONTENT")

        # No snapshot exists
        assert detect_orphaned_backup(str(db)) is None

    def test_orphan_detected_for_dead_session(self, tmp_path: Path) -> None:
        db = tmp_path / "test.accdb"
        db.write_bytes(b"CONTENT")

        # Simulate: write a manifest for a dead PID, using the SAME _AUTO_SESSION_DIR
        dead_pid = 99999  # very unlikely to exist
        backup_filename = f"autosession_{dead_pid}_20260916_test.accdb"
        backup_path = os.path.join(_AUTO_SESSION_DIR, backup_filename)
        manifest_path = backup_path + ".manifest.json"
        manifest = {
            "source_path": str(db),
            "backup_path": backup_path,
            "sha256": "deadbeef",
            "size_bytes": 100,
            "created_at": "2026-09-16T00:00:00+00:00",
            "kind": "auto_session",
            "session_pid": dead_pid,
        }

        os.makedirs(_AUTO_SESSION_DIR, exist_ok=True)
        Path(backup_path).write_bytes(b"DEAD SESSION CONTENT")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        try:
            result = detect_orphaned_backup(str(db))
            assert result is not None
            assert result["session_pid"] == dead_pid
            assert result["source_path"] == str(db)
        finally:
            # Clean up
            try:
                os.remove(backup_path)
            except Exception:
                pass
            try:
                os.remove(manifest_path)
            except Exception:
                pass

    def test_live_session_snapshot_not_reported_as_orphan(self, tmp_path: Path) -> None:
        db = tmp_path / "test.accdb"
        db.write_bytes(b"CONTENT")

        # Create a snapshot for the CURRENT session
        result = create_session_backup(str(db))
        assert isinstance(result, Ok)

        # The current process is alive, so this snapshot is NOT an orphan
        # detect_orphaned_backup checks if session_pid is alive
        orphan = detect_orphaned_backup(str(db))
        # The only snapshot belongs to the current (alive) session
        assert orphan is None or orphan["session_pid"] != os.getpid()

    def test_different_db_not_orphan(self, tmp_path: Path) -> None:
        db1 = tmp_path / "db1.accdb"
        db2 = tmp_path / "db2.accdb"
        db1.write_bytes(b"DB1 CONTENT")
        db2.write_bytes(b"DB2 CONTENT")

        # Snapshot for db1
        result = create_session_backup(str(db1))
        assert isinstance(result, Ok)

        # Asking about db2 should find nothing
        assert detect_orphaned_backup(str(db2)) is None
