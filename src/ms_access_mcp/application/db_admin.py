"""Admin use cases — database properties, statistics, and creation."""

from __future__ import annotations

import dataclasses
from typing import Any, Literal, cast

from ..backends import DATA_READ_CAPS, BackendSelector
from ..errors import Result, err, ok, operation_error

_ok = ok
_err = err


def get_database_properties(adapter: Any) -> Result:
    """Get database properties.

    Args:
        adapter: Resolved backend adapter

    Returns:
        Ok with database properties, or Err
    """
    try:
        props = adapter.get_database_properties()
        return _ok(data=props)
    except Exception as e:
        return operation_error(e)


def get_database_statistics(adapter: Any) -> Result:
    """Get database statistics.

    Args:
        adapter: Resolved backend adapter

    Returns:
        Ok with statistics, or Err
    """
    try:
        stats = adapter.get_database_statistics()
        return _ok(data=dataclasses.asdict(stats) if stats is not None else {})
    except Exception as e:
        return operation_error(e)


def set_database_property(
    adapter: Any,
    policy: Any,
    name: str,
    value: Any,
    confirm: bool = False,
) -> Result:
    """Set a database property.

    Args:
        adapter: Resolved backend adapter
        policy: WritePolicy instance
        name: Property name
        value: Property value
        confirm: Must be True to execute

    Returns:
        Ok on success, or Err
    """
    if ack := policy.require_acknowledgment("set_database_property", confirm):
        return _err(ack)
    if not policy.allow_write():
        return _err("Write operations are disabled (readonly mode)")
    try:
        if adapter.set_database_property(name, value):
            return _ok(rowcount=1)
        return _err("Database property write is not supported by this backend")
    except Exception as e:
        return operation_error(e)


def create_access_database(
    policy: Any,
    allowed_dirs: list[Any],
    path: str,
    backend_name: str,
    confirm: bool = False,
    *,
    format: str = "accdb",
) -> Result:
    """Create a blank Access database at the given path.

    Args:
        policy: WritePolicy instance
        allowed_dirs: List of allowed directory paths
        path: Target path for the new .accdb or .mdb file
        backend_name: Backend name to use
        confirm: Must be True to execute
        format: "accdb" (default, ACE/.accdb) or "mdb" (Jet 4 format).

    Returns:
        Ok on success, or Err
    """
    from pathlib import Path

    from ..safety import is_path_allowed

    if ack := policy.require_acknowledgment("create_access_database", confirm):
        return _err(ack)
    if not policy.allow_write():
        return _err("Write operations are disabled (readonly mode)")
    if not is_path_allowed(path, allowed_dirs):
        return _err(f"Output path not allowed: {path}")
    if Path(path).exists():
        return _err(f"Database already exists: {path}")
    try:
        adapter = BackendSelector.get_adapter(
            db_path=path,
            backend=cast(Literal["odbc", "com", "dao", "auto"] | None, backend_name),
            capabilities=DATA_READ_CAPS,
        )
        cast(Any, adapter).create_database(path, format=format)
        return _ok(rowcount=1)
    except Exception as e:
        return operation_error(e, label="create_access_database failed")
