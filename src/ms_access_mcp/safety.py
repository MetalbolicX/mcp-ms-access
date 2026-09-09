"""Safety paths: corrected path guard (oracle bug fix) and write policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def is_path_allowed(path: str | Path, allowed_dirs: list[Path]) -> bool:
    """Return True if *path* is inside one of *allowed_dirs* and safe.

    Fixed oracle bug: per-directory try/except with return True inside the
    loop, and return False only after exhausting all allowed dirs.
    The oracle had `return True` inside the for loop while try/except wrapped
    the whole loop — only the first allowed dir was ever checked.
    """
    p = Path(path).resolve(strict=False)

    # Reject network paths
    if str(p).startswith("\\\\") or str(p).startswith("//"):
        return False

    # Reject nonexistent paths without raising
    if not p.exists():
        return False

    for allowed in allowed_dirs:
        try:
            p.relative_to(Path(allowed).resolve(strict=False))
            return True  # per-directory try/except — the oracle bug fix
        except ValueError:
            continue
    return False


@dataclass
class WritePolicy:
    """Controls whether write operations are permitted."""

    readonly: bool = True

    def allow_write(self) -> bool:
        """Return True if writes are permitted."""
        return not self.readonly

    def require_acknowledgment(self, operation: str, confirm: bool = False) -> str | None:
        """Return an error message if a destructive operation lacks confirmation.

        Returns None if the operation is explicitly acknowledged.
        Destructive operations always require acknowledgment regardless of readonly status.
        """
        if not confirm:
            return f"Operation '{operation}' requires explicit confirmation."
        return None
