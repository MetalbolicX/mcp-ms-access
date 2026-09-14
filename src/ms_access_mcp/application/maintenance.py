"""Maintenance use cases — backup, dev copy, compact/repair, diagnose, recover.

Constructor-injected backends, immutable result envelopes.
All destructive ops gated via safety.require_acknowledgment + write policy.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from typing import Any, Literal

from ..errors import Err, Ok, Result
from ..errors import err as _err
from ..errors import ok as _ok
from ..safety import WritePolicy
from ._adapter_lifecycle import AdapterLifecycle
from .protocols import MaintenanceAdapterProtocol

__all__ = ["MaintenanceUseCases", "kill_access_processes"]


def kill_access_processes(*, timeout: int = 30) -> tuple[list[str], list[str]]:
    """Enumerate MSACCESS.EXE PIDs via tasklist and kill each by PID.

    Never emits /IM. Returns (killed_pids, errors). Skips the kill entirely
    when enumeration yields no PIDs, including enumeration failure.
    """
    pids: list[str] = []
    errors: list[str] = []

    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        lines = result.stdout.strip().splitlines()
        for line in lines:
            if "MSACCESS.EXE" in line:
                parts = line.split(",")
                if len(parts) >= 2:
                    pids.append(parts[1].strip('"'))
    except Exception as e:
        errors.append(f"Failed to list Access processes: {e}")

    if not pids:
        return pids, errors

    try:
        subprocess.run(
            ["taskkill", "/F"]
            + [arg for pid in pids for arg in ("/PID", pid)],
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except Exception as e:
        errors.append(f"Failed to kill Access processes: {e}")

    return pids, errors


class MaintenanceUseCases(AdapterLifecycle[MaintenanceAdapterProtocol]):
    """Constructor-injected maintenance operations.

    No globals, no container. Each instance holds a reference to the
    active backend adapter and write policy.
    """

    _backend = "auto"
    _capabilities_name = "MAINTENANCE_CAPS"

    DEFAULT_RETENTION = 5

    def __init__(
        self,
        adapter: MaintenanceAdapterProtocol | None = None,
        readonly: bool = True,
        db_path: str | None = None,
    ) -> None:
        self._adapter = adapter
        self._policy = WritePolicy(readonly=readonly)
        self._db_path = db_path

    def _confirm_gate(self, op: str, confirm: bool) -> Err | None:
        if ack := self._policy.require_acknowledgment(op, confirm):
            return _err(ack)
        return None

    def _call_adapter(self, fn: Any, *args: Any, **kwargs: Any) -> Ok | Err:
        if err := self._ensure_adapter():
            return err
        try:
            result = fn(*args, **kwargs)
            return _ok(data=result)
        except Exception as e:
            return _err(str(e))

    def _is_disposable_path(self, path: str) -> bool:
        temp_dir = tempfile.gettempdir()
        if path.startswith(temp_dir):
            return True
        disposable_marker = os.path.join(temp_dir, "ms_access_disposable")
        if os.path.exists(disposable_marker):
            marker_dir = os.path.dirname(disposable_marker)
            if path.startswith(marker_dir):
                return True
        return False

    def _preflight_backup(self, path: str) -> Ok | Err:
        if not os.path.exists(path):
            return _err(f"Cannot backup {path}: source not found")

        backup_dir = os.path.join(tempfile.gettempdir(), "ms_access_backups")
        os.makedirs(backup_dir, exist_ok=True)

        basename = os.path.basename(path)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        backup_file = os.path.join(backup_dir, f"{timestamp}_{basename}")

        try:
            shutil.copy2(path, backup_file)
        except Exception as e:
            return _err(f"Cannot backup {path}: {e}")

        sha256 = hashlib.sha256()
        with open(backup_file, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)  # type: ignore[arg-type]

        manifest = {
            "source_path": path,
            "backup_path": backup_file,
            "sha256": sha256.hexdigest(),
            "size_bytes": os.path.getsize(backup_file),
            "created_at": datetime.now(UTC).isoformat(),
            "kind": "backup",
        }
        manifest_path = backup_file + ".manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        return _ok(data={"backup_path": backup_file, "manifest_path": manifest_path})

    # -------------------------------------------------------------------------
    # Public Use Cases
    # -------------------------------------------------------------------------

    def backup(self, path: str, *, retention: int | None = None) -> Result:
        if not os.path.exists(path):
            return _err(f"Source not found: {path}")

        backup_dir = os.path.join(tempfile.gettempdir(), "ms_access_backups")
        os.makedirs(backup_dir, exist_ok=True)

        basename = os.path.basename(path)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        backup_file = os.path.join(backup_dir, f"{timestamp}_{basename}")

        try:
            shutil.copy2(path, backup_file)
        except Exception as e:
            return _err(f"Cannot backup {path}: {e}")

        sha256 = hashlib.sha256()
        with open(backup_file, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)  # type: ignore[arg-type]

        manifest = {
            "source_path": path,
            "backup_path": backup_file,
            "sha256": sha256.hexdigest(),
            "size_bytes": os.path.getsize(backup_file),
            "created_at": datetime.now(UTC).isoformat(),
            "kind": "backup",
        }
        manifest_path = backup_file + ".manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        retention_count = retention or self.DEFAULT_RETENTION
        pattern = os.path.join(backup_dir, f"*_{basename}")
        existing_backups = glob.glob(pattern)
        existing_with_manifest = [
            bp for bp in existing_backups
            if os.path.exists(bp + ".manifest.json")
        ]
        existing_with_manifest.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        kept = len(existing_with_manifest)
        pruned = 0
        if len(existing_with_manifest) > retention_count:
            to_remove = existing_with_manifest[retention_count:]
            for old_backup in to_remove:
                try:
                    os.remove(old_backup)
                    manifest_to_remove = old_backup + ".manifest.json"
                    if os.path.exists(manifest_to_remove):
                        os.remove(manifest_to_remove)
                    pruned += 1
                except Exception:
                    pass
            kept = retention_count

        return _ok(
            data={
                "backup_path": backup_file,
                "manifest_path": manifest_path,
                "manifest": manifest,
                "kept": kept,
                "pruned": pruned,
            }
        )

    def create_dev_copy(self, source: str | None = None) -> Result:
        if source is None:
            if self._db_path is None:
                return _err(
                    "No source path provided and not connected to a database"
                )
            source = self._db_path

        if not os.path.exists(source):
            return _err(f"Source not found: {source}")

        dev_dir = os.path.join(tempfile.gettempdir(), "ms_access_dev")
        os.makedirs(dev_dir, exist_ok=True)

        source_md5 = hashlib.md5(source.encode()).hexdigest()[:8]
        basename = os.path.basename(source)
        name, ext = os.path.splitext(basename)
        dev_name = f"{name}_dev{ext}"
        dev_path = os.path.join(dev_dir, source_md5, dev_name)

        manifest_path = os.path.join(dev_dir, f"{source_md5}.json")

        if os.path.exists(manifest_path):
            return _err("Dev copy already active")

        os.makedirs(os.path.dirname(dev_path), exist_ok=True)

        try:
            shutil.copy2(source, dev_path)
        except Exception as e:
            return _err(f"Cannot create dev copy: {e}")

        manifest = {
            "production_path": source,
            "dev_path": dev_path,
            "created_at": datetime.now(UTC).isoformat(),
            "db_size_bytes": os.path.getsize(dev_path),
            "deployed_at": None,
        }

        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        return _ok(
            data={
                "dev_path": dev_path,
                "manifest_path": manifest_path,
                "production_path": source,
            }
        )

    def deploy_dev_copy(
        self,
        dev_path: str,
        target: str,
        *,
        confirm: bool,
        preflight_backup: bool = True,
    ) -> Result:
        if err := self._confirm_gate("deploy_dev_copy", confirm):
            return err

        if not os.path.exists(dev_path):
            return _err(f"Dev copy not found: {dev_path}")

        bak_path: str | None = None

        if preflight_backup and not self._is_disposable_path(target):
            backup_result = self._preflight_backup(target)
            if isinstance(backup_result, Err):
                return backup_result
            bak_path = backup_result.data.get("backup_path")

        try:
            shutil.copy2(dev_path, target)
        except Exception as e:
            return _err(f"Cannot deploy dev copy: {e}")

        dev_dir = os.path.join(tempfile.gettempdir(), "ms_access_dev")

        for mf in glob.glob(os.path.join(dev_dir, "*.json")):
            try:
                with open(mf) as f:
                    manifest = json.load(f)
                if manifest.get("dev_path") == dev_path:
                    os.remove(mf)
                    break
            except Exception:
                continue

        return _ok(
            data={
                "target": target,
                "bak_path": bak_path,
                "deployed_at": datetime.now(UTC).isoformat(),
            }
        )

    def dev_copy_status(self) -> Result:
        dev_dir = os.path.join(tempfile.gettempdir(), "ms_access_dev")

        manifest_files = glob.glob(os.path.join(dev_dir, "*.json"))
        if not manifest_files:
            return _ok(data={"active": False})

        for manifest_path in manifest_files:
            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)
                return _ok(data={**manifest, "active": True, "manifest_path": manifest_path})
            except Exception:
                continue

        return _ok(data={"active": False})

    def discard_dev_copy(self, *, confirm: bool = False) -> Result:
        """Discard the active dev copy without deploying to production.

        Deletes the dev copy file and removes its manifest.
        Does NOT modify the production database.
        """
        if err := self._confirm_gate("discard_dev_copy", confirm):
            return err

        dev_dir = os.path.join(tempfile.gettempdir(), "ms_access_dev")
        manifest_files = glob.glob(os.path.join(dev_dir, "*.json"))

        if not manifest_files:
            return _err("No active dev copy found")

        for manifest_path in manifest_files:
            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)
                dev_path = manifest.get("dev_path")
                if dev_path and os.path.exists(dev_path):
                    os.remove(dev_path)
                os.remove(manifest_path)
                return _ok(
                    data={
                        "dev_path": dev_path,
                        "manifest_path": manifest_path,
                        "discarded_at": datetime.now(UTC).isoformat(),
                    }
                )
            except Exception:
                continue

        return _err("No active dev copy found")

    def compact_repair(
        self,
        path: str,
        mode: Literal["compact", "repair", "compact_and_repair"],
        *,
        confirm: bool,
    ) -> Result:
        if mode not in ("compact", "repair", "compact_and_repair"):
            return _err(
                f"Invalid mode: {mode}; must be one of compact|repair|compact_and_repair"
            )

        if err := self._confirm_gate("compact_repair", confirm):
            return err

        if not self._is_disposable_path(path):
            backup_result = self._preflight_backup(path)
            if isinstance(backup_result, Err):
                return backup_result

        if err := self._ensure_adapter():
            return err
        assert self._adapter is not None  # noqa: S101 - _ensure_adapter establishes the application invariant

        try:
            result = self._adapter.compact_repair(path, mode)
            return _ok(
                data={
                    "path": path,
                    "mode": mode,
                    "output_path": None,
                    "stats": result if isinstance(result, dict) else None,
                }
            )
        except Exception as e:
            return _err(str(e))

    def diagnose_environment(self) -> Result:
        from .. import doctor

        checks = doctor.run_checks(deep=False)
        return _ok(
            data=[
                {
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "details": c.details,
                }
                for c in checks
            ]
        )

    def recover_access(self, *, confirm: bool) -> Result:
        if err := self._confirm_gate("recover_access", confirm):
            return err

        if sys.platform != "win32":
            return _err("recover_access is only available on Windows")

        killed_pids, errors = kill_access_processes()

        reconnected = False
        assert self._adapter is not None  # noqa: S101 - recover requires an initialized adapter
        if hasattr(self._adapter, "recover"):
            try:
                reconnected = self._adapter.recover()
            except Exception as e:
                errors.append(f"Adapter recover failed: {e}")

        return _ok(
            data={
                "killed_pids": killed_pids,
                "reconnected": reconnected,
                "errors": errors if errors else None,
            }
        )
