"""Guarded, atomic saving for rendered documentation reports."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..errors import AccessMcpError
from ..safety import allowed_dirs_from_env, is_path_allowed

_DATABASE_SUFFIXES = {".mdb", ".accdb", ".accde", ".mdw", ".mde"}


@dataclass(frozen=True)
class DocumentationWritePolicy:
    """Explicit operator authorization for documentation writes."""

    allowed: bool = False


@dataclass(frozen=True)
class SaveResult:
    """Facts about a successfully saved report."""

    output_path: str
    bytes_written: int
    sha256: str


class DocumentationSaveService:
    """Save Markdown only when operator and per-call safety gates pass."""

    def __init__(
        self,
        policy: DocumentationWritePolicy | bool | None = None,
        *,
        allowed_dirs: list[Path] | None = None,
    ) -> None:
        # A missing policy is intentionally deny-by-default; frontends must wire
        # operator permission explicitly rather than this service reading env.
        self._allowed = policy.allowed if isinstance(policy, DocumentationWritePolicy) else bool(policy)
        self._allowed_dirs = allowed_dirs if allowed_dirs is not None else allowed_dirs_from_env()

    def save(
        self,
        report_markdown: str,
        output_path: str,
        *,
        confirm: bool = False,
        overwrite: bool = False,
        expected_fingerprint: str | None = None,
    ) -> SaveResult:
        """Atomically save a report after authorization and destination checks."""
        if not self._allowed:
            raise AccessMcpError("Documentation save permission denied: operator doc-write is not enabled.")
        if not confirm:
            raise AccessMcpError("Documentation save requires explicit confirm=True.")

        destination = Path(output_path).resolve(strict=False)
        if destination.suffix.casefold() in _DATABASE_SUFFIXES:
            raise AccessMcpError(f"Documentation output path cannot target a database file: {output_path}")
        if not is_path_allowed(destination, self._allowed_dirs, must_exist=False):
            raise AccessMcpError(f"Output path not allowed: {output_path}")

        existed_at_check = destination.exists()
        if existed_at_check:
            if not destination.is_file():
                raise AccessMcpError(f"Documentation output destination is not a file: {output_path}")
            if not overwrite:
                raise AccessMcpError(f"Documentation output already exists; explicit overwrite is required: {output_path}")
            if not expected_fingerprint:
                raise AccessMcpError("Documentation overwrite requires an expected fingerprint.")
            if self._fingerprint(destination) != expected_fingerprint:
                raise AccessMcpError("Documentation overwrite fingerprint is stale or does not match destination.")
        elif overwrite and expected_fingerprint is not None:
            raise AccessMcpError("Documentation overwrite fingerprint supplied for a destination that does not exist.")

        data = report_markdown.encode("utf-8")
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
            ) as temporary:
                temporary_path = temporary.name
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())

            # Recheck immediately before replacement to reject appearance or drift
            # since the caller's approval/preflight check.
            now_exists = destination.exists()
            if not existed_at_check and now_exists:
                raise AccessMcpError(f"Documentation destination appeared before write: {output_path}")
            if existed_at_check:
                if not now_exists or self._fingerprint(destination) != expected_fingerprint:
                    raise AccessMcpError("Documentation overwrite fingerprint changed before write.")
            Path(temporary_path).replace(destination)
            temporary_path = None
        except AccessMcpError:
            raise
        except OSError as exc:
            raise AccessMcpError(f"Documentation save failed: {exc}") from exc
        finally:
            if temporary_path is not None:
                try:
                    Path(temporary_path).unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    # Preserve the original save failure; a best-effort cleanup
                    # cannot safely replace the primary error.
                    pass

        return SaveResult(
            output_path=str(destination),
            bytes_written=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    @staticmethod
    def _fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
