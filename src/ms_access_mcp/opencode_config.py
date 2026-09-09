"""opencode_config — transactional management of mcp.ms-access in opencode.json.

Pure-function core: merge, conflict detection, atomic write, rollback.
CLI wiring is Unit 2; installer is Unit 3.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

# =============================================================================
# Result Envelope
# =============================================================================


@dataclass(frozen=True)
class Ok:
    """Success result envelope."""

    ok: bool = True
    backup_path: Path | None = None
    message: str = ""


@dataclass(frozen=True)
class Err:
    """Error result envelope."""

    ok: bool = False
    backup_path: Path | None = None
    message: str = ""


Result = Ok | Err


# =============================================================================
# Internal Exception
# =============================================================================


class _ValidationError(Exception):
    """Raised by write_atomic when validator fails and rollback was applied.

    Carries the backup_path so register() can include it in the Err result.
    Plain class (not frozen dataclass) so pytest can re-raise it with
    traceback assigned in its __exit__ protocol.
    """

    def __init__(self, backup_path: Path | None, message: str) -> None:
        self.backup_path = backup_path
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


# =============================================================================
# Data Model
# =============================================================================


@dataclass(frozen=True)
class McpEntry:
    """An mcp.ms-access entry stored in opencode.json."""

    command: tuple[str, ...]
    profile: str = "core"
    enabled: bool = True
    timeout: int = 30000


# =============================================================================
# Pure Functions
# =============================================================================


def load(path: Path) -> dict:
    """Load and parse a JSON config file.

    Returns an empty dict if the file does not exist.
    Raises JSONDecodeError on malformed content.
    """
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)


def merge_entry(config: dict, key: str, entry: McpEntry) -> dict:
    """Return a new config dict with entry merged under key.

    key is a dot-separated path, e.g. "mcp.ms-access".
    Existing siblings under intermediate dicts are preserved.
    """
    result = _deep_copy(config)
    parts = key.split(".")
    current = result
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        current = current[part]
    current[parts[-1]] = _entry_asdict(entry)
    return result


def _entry_asdict(entry: McpEntry) -> dict:
    """Serialize McpEntry to dict, normalising command to list for JSON."""
    d = asdict(entry)
    # command is stored as list in JSON for JSON-round-trip compatibility
    d["command"] = list(d["command"])
    return d


def _deep_copy(obj: dict) -> dict:
    """Recursively copy a dict so merge_entry never mutates originals.

    Tuple values are converted to lists for JSON-round-trip compatibility.
    """
    if not isinstance(obj, dict):
        return obj
    return {k: _deep_copy(v) if isinstance(v, dict) else _normalise(v) for k, v in obj.items()}


def _normalise(value):
    """Normalise tuple to list for JSON compatibility in comparisons."""
    if isinstance(value, tuple):
        return list(value)
    return value


def entry_from(exe_path: Path, profile: str = "core") -> McpEntry:
    """Build an McpEntry from the tool executable path and profile."""
    return McpEntry(
        command=(str(exe_path), "mcp", "--profile", profile),
        profile=profile,
        enabled=True,
        timeout=30000,
    )


def timestamped_backup(path: Path) -> Path:
    """Create a timestamped sibling backup of path.

    Raises FileNotFoundError if path does not exist.
    Backup name: {path.name}.bak-{utc_iso_ts}
    """
    if not path.exists():
        raise FileNotFoundError(f"cannot backup non-existent file: {path}")

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    backup = path.with_name(f"{path.name}.bak-{ts}")
    shutil.copy2(path, backup)
    return backup


def write_atomic(
    path: Path,
    config: dict,
    *,
    backup: bool = True,
    validator: Callable[[Path], None] | None = None,
) -> Path:
    """Write config to path atomically: temp sibling + os.replace.

    If backup=True and path exists, creates a timestamped backup first.
    If validator is provided and raises, restores from backup (or deletes
    the new file if no backup existed) and raises _ValidationError.

    Returns the path on success.
    Raises _ValidationError(backup_path, message) on validation failure after rollback.
    Raises other exceptions on IO errors.
    """
    backup_path: Path | None = None

    if backup and path.exists():
        backup_path = timestamped_backup(path)

    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        # Serialize to temp sibling
        with tmp.open("w") as f:
            json.dump(config, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # Atomic rename
        os.replace(tmp, path)
    except Exception:
        # Clean up temp file on error
        if tmp.exists():
            tmp.unlink()
        raise

    # Post-write validation
    if validator is not None:
        try:
            validator(path)
        except Exception as exc:  # noqa: BLE001
            # Restore from backup or delete new file
            if backup_path is not None and backup_path.exists():
                os.replace(backup_path, path)
            elif path.exists():
                path.unlink()
            raise _ValidationError(backup_path=backup_path, message=str(exc)) from exc

    return path


# =============================================================================
# Register / Remove
# =============================================================================


_OPENCODE_SCHEMA = "https://opencode.ai/opencode.schema.json"


def register(
    target: Path,
    entry: McpEntry,
    *,
    force: bool = False,
    validator: Callable[[Path], None] | None = None,
) -> Result:
    """Register (or update) mcp.ms-access entry in target opencode.json.

    Conflict detection: if mcp.ms-access exists with a different command
    and force=False, returns Err without writing.
    With force=True, overwrites after taking a backup.

    On validation failure, rolls back to the original content and returns
    Err with the backup_path set if a backup was taken.
    """
    backup_exists = target.exists()

    existing = load(target) if backup_exists else {}

    # Deep-equality conflict detection — normalise both sides to tuple
    if "mcp" in existing and "ms-access" in existing.get("mcp", {}):
        existing_entry_dict = existing["mcp"]["ms-access"]
        new_entry_dict = _entry_asdict(entry)
        if (
            _normalise(existing_entry_dict.get("command"))
            != _normalise(new_entry_dict["command"])
            or existing_entry_dict.get("profile") != new_entry_dict["profile"]
            or existing_entry_dict.get("enabled") != new_entry_dict["enabled"]
            or existing_entry_dict.get("timeout") != new_entry_dict["timeout"]
        ):
            if not force:
                return Err(
                    message="conflict: existing mcp.ms-access differs; "
                    "pass force=True to overwrite",
                    backup_path=None,
                )

    # Build merged config — for a fresh file, include $schema
    new_config = merge_entry(existing, "mcp.ms-access", entry)
    if not backup_exists:
        new_config = {"$schema": _OPENCODE_SCHEMA, **new_config}

    try:
        write_atomic(target, new_config, backup=backup_exists, validator=validator)
    except _ValidationError as ve:
        return Err(backup_path=ve.backup_path, message=ve.message)

    return Ok(backup_path=None, message="registered")


def remove(
    target: Path,
    *,
    validator: Callable[[Path], None] | None = None,
) -> Result:
    """Remove mcp.ms-access entry from target opencode.json.

    Returns Ok with no-op if the entry is already absent.
    Returns Err if target does not exist.
    On validation failure, rolls back to the original content.
    """
    if not target.exists():
        return Err(message="target missing", backup_path=None)

    existing = load(target)

    if "mcp" not in existing or "ms-access" not in existing.get("mcp", {}):
        return Ok(message="mcp.ms-access not registered", backup_path=None)

    # Build new config without ms-access (deep copy to avoid mutation)
    new_config = _deep_copy(existing)
    del new_config["mcp"]["ms-access"]

    # If mcp is now empty, remove it too
    if not new_config["mcp"]:
        del new_config["mcp"]

    try:
        write_atomic(target, new_config, backup=True, validator=validator)
    except _ValidationError as ve:
        return Err(backup_path=ve.backup_path, message=ve.message)

    return Ok(backup_path=None, message="removed")
