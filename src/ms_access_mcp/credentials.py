"""Server-side credential resolver for password-protected Access databases.

Resolution order:
  1. Local:  ``<db_dir>/.ms-access-mcp.json``
  2. Global: ``~/.ms-access-mcp/credentials.json`` (platform-appropriate)

File format
------------
``{"version": 1, "credentials": {"<normalized absolute path>": "<password>"}}``

The global file location can be overridden via ``ACCESS_MCP_CREDENTIALS_FILE``.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import TypedDict

__all__ = ["resolve_password", "write_entry", "remove_entry", "list_entries"]


# =============================================================================
# Types
# =============================================================================


class _CredentialsDict(TypedDict):
    """Structure of a credentials JSON file."""

    version: int
    credentials: dict[str, str]


# =============================================================================
# Module-level cache: (file_path, mtime) -> parsed data
# =============================================================================

_cache: dict[tuple[Path, float], _CredentialsDict | None] = {}


# =============================================================================
# Path helpers
# =============================================================================


def _global_credentials_path() -> Path:
    """Return the platform-appropriate global credentials file path.

    Returns:
        ``%USERPROFILE%\\.ms-access-mcp\\credentials.json`` on Windows,
        ``~/.ms-access-mcp/credentials.json`` on POSIX.

    Raises:
        KeyError: if ``USERPROFILE`` (Windows) or ``HOME`` (POSIX) is not set.
    """
    if os.name == "nt":
        base = Path(os.environ["USERPROFILE"])
    else:
        base = Path.home()
    return base / ".ms-access-mcp" / "credentials.json"


def _local_credentials_path(db_path: str | Path) -> Path:
    """Return the local credentials file path for a given database.

    Args:
        db_path: Path to the Access database (.accdb or .mdb).

    Returns:
        ``<db_dir>/.ms-access-mcp.json``.
    """
    return Path(db_path).resolve().parent / ".ms-access-mcp.json"


def _normalize_path(db_path: str) -> str:
    """Normalize a database path for consistent lookup.

    Args:
        db_path: The database path as passed by the caller.

    Returns:
        The normalized absolute path, using ``os.path.normcase`` so that
        Windows drive-letter case and forward/back-slash differences do not
        cause a cache miss.
    """
    return os.path.normcase(os.path.normpath(str(Path(db_path).resolve())))


# =============================================================================
# File read helpers
# =============================================================================


def _read_credentials_file(path: Path) -> _CredentialsDict | None:
    """Read and validate a credentials JSON file.

    Args:
        path: Path to the credentials file.

    Returns:
        The parsed credentials dict, or ``None`` if the file does not exist
        or is malformed. Never raises.
    """
    if not path.exists():
        return None

    try:
        mtime = path.stat().st_mtime
        cache_key = (path, mtime)
        if cache_key in _cache:
            return _cache[cache_key]
    except OSError:
        return None

    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError):
        _cache[(path, mtime)] = None
        return None

    if not isinstance(raw, dict):
        _cache[(path, mtime)] = None
        return None
    if "credentials" not in raw:
        _cache[(path, mtime)] = None
        return None

    data: _CredentialsDict = {"version": raw.get("version", 1), "credentials": raw["credentials"]}
    _cache[cache_key] = data
    return data


# =============================================================================
# Public API
# =============================================================================


def resolve_password(db_path: str) -> str:
    """Resolve the stored password for a database, if any.

    Args:
        db_path: Absolute or relative path to the Access database.

    Returns:
        The stored password, or ``""`` when no entry exists or the file is
        malformed. Never raises. Never logs the password.

    Resolution order
    ----------------
    1. ``<db_dir>/.ms-access-mcp.json`` (local, highest priority)
    2. Global credentials file (lowest priority)

    The global file path can be overridden by setting the
    ``ACCESS_MCP_CREDENTIALS_FILE`` environment variable.
    """
    normalized = _normalize_path(db_path)

    # 1. Check local file first
    local_path = _local_credentials_path(normalized)
    local_data = _read_credentials_file(local_path)
    if local_data is not None:
        return local_data["credentials"].get(normalized, "")

    # 2. Check global file
    env_override = os.environ.get("ACCESS_MCP_CREDENTIALS_FILE", "")
    global_path = Path(env_override) if env_override else _global_credentials_path()
    global_data = _read_credentials_file(global_path)
    if global_data is not None:
        return global_data["credentials"].get(normalized, "")

    return ""


def write_entry(db_path: str, password: str, *, local: bool = False) -> None:
    """Write (or update) a credentials entry.

    Args:
        db_path: Path to the Access database.
        password: The password to store.
        local: If True, write to ``<db_dir>/.ms-access-mcp.json``;
               otherwise write to the global credentials file.

    Raises:
        OSError: If the directory cannot be created or the file cannot be
                 written atomically.
    """
    target = _local_credentials_path(db_path) if local else _global_credentials_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    normalized = _normalize_path(db_path)

    # Read existing data or start fresh
    existing = _read_credentials_file(target)
    if existing is None:
        data: _CredentialsDict = {"version": 1, "credentials": {}}
    else:
        data = existing

    data["credentials"][normalized] = password

    # Atomic write: write to a .tmp file in the same directory, then replace
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        dir=str(target.parent),
        suffix=".tmp",
        prefix=".ms-access-mcp-",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path_str, str(target))

        # Set POSIX permissions to 0o600 (owner read/write only)
        if os.name != "nt":
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)

    except BaseException:
        # Clean up the temp file on failure
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def remove_entry(db_path: str, *, local: bool = False) -> bool:
    """Remove a credentials entry.

    Args:
        db_path: Path to the Access database.
        local: If True, operate on ``<db_dir>/.ms-access-mcp.json``;
               otherwise operate on the global credentials file.

    Returns:
        True if an entry was removed, False if no entry existed.
    """
    target = _local_credentials_path(db_path) if local else _global_credentials_path()
    existing = _read_credentials_file(target)
    if existing is None:
        return False

    normalized = _normalize_path(db_path)
    if normalized not in existing["credentials"]:
        return False

    del existing["credentials"][normalized]

    if not existing["credentials"]:
        # Remove the file entirely when empty
        try:
            os.unlink(target)
        except OSError:
            pass
    else:
        # Rewrite atomically
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=str(target.parent),
            suffix=".tmp",
            prefix=".ms-access-mcp-",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(existing, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path_str, str(target))
            if os.name != "nt":
                os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        except BaseException:
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
            raise

    return True


def list_entries() -> list[tuple[Path, str]]:
    """List all credentials entries from both local and global files.

    Returns:
        A list of ``(path, source)`` tuples, where ``source`` is the string
        ``"global"`` or ``"local"``. The paths are the raw keys stored in the
        JSON files (not resolved/normalized). Passwords are never returned.
        Malformed files are silently skipped.
    """
    results: list[tuple[Path, str]] = []

    env_override = os.environ.get("ACCESS_MCP_CREDENTIALS_FILE", "")
    global_path = Path(env_override) if env_override else _global_credentials_path()
    global_data = _read_credentials_file(global_path)
    if global_data is not None:
        for db_path in global_data["credentials"]:
            results.append((Path(db_path), "global"))

    # Check for local files alongside any .accdb/.mdb in the global credentials
    # by scanning directory of global path for local files.
    # NOTE: we don't auto-discover local files for all dbs on the filesystem;
    # we only list entries that are actually stored. The local file path is
    # derived from the global file's sibling scan approach: we look for
    # <db_dir>/.ms-access-mcp.json next to each path in the global file.
    if global_data is not None:
        seen_roots = set()
        for db_path in global_data["credentials"]:
            p = Path(db_path)
            if p.parent in seen_roots:
                continue
            seen_roots.add(p.parent)
            local_path = p.parent / ".ms-access-mcp.json"
            if local_path.exists() and local_path.resolve() != global_path.resolve():
                local_data = _read_credentials_file(local_path)
                if local_data is not None:
                    for local_db in local_data["credentials"]:
                        results.append((Path(local_db), "local"))

    return results
