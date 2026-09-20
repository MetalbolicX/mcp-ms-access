"""Access database format sniffer.

WHY: Extension alone cannot distinguish Access formats — .mdb files may be
Jet 3 (Access 95/97), Jet 4 (Access 2000-2003), or even corrupted.  ACE
OLEDB and the ACE ODBC driver refuse Jet 3 files with a cryptic COM exception
("The database you are trying to open is older than...") that is impossible
to act on without knowing what format was expected.  A reliable header sniffer
powers clear diagnostics, correct backend routing, and safe recovery before
any adapter is constructed.
"""

import os
from enum import Enum
from pathlib import Path

# Jet/ACE standard header magic: first 4 bytes of any well-formed Jet or ACE DB.
_STANDARD_HEADER = b"\x00\x01\x00\x00"

# Offset of the format family descriptor (null-padded ASCII, 16 bytes max).
_FAMILY_OFFSET = 4
_FAMILY_LENGTH = 16

# Magic strings at offset 4.
_JET_MAGIC = b"Standard Jet DB"
_ACE_MAGIC = b"Standard ACE DB"

# Offset of the format version byte within the Jet family header.
_VERSION_OFFSET = 0x14

# Version byte values within the Jet family.
_JET3_VERSION_BYTE = 0x00  # Access 95/97.
_JET4_VERSION_BYTE = 0x01  # Access 2000-2003.

# Number of bytes required for a complete sniff.
_HEADER_SIZE = 32


class AccessFormat(Enum):
    """Discriminated format of an Access database file."""

    JET3 = "jet3"
    """Jet 3.x — Access 95 / Access 97."""

    JET4 = "jet4"
    """Jet 4.x — Access 2000 / Access 2002-2003."""

    ACE = "ace"
    """ACE (Access 2007 and later) — .accdb, .mdb via ACE."""

    UNKNOWN = "unknown"
    """Format could not be determined from the header."""


def detect_access_format(path: str | Path) -> AccessFormat:
    """Detect the Access database format from the first 32 bytes of *path*.

    Parameters
    ----------
    path:
        Path to the file to inspect. The file does not need to exist — a
        missing or unreadable file returns ``AccessFormat.UNKNOWN``.

    Returns
    -------
    AccessFormat
        One of ``JET3``, ``JET4``, ``ACE``, or ``UNKNOWN``.

    """
    raw = _read_header(Path(os.fspath(path)))
    if raw is None:
        return AccessFormat.UNKNOWN
    return _classify(raw)


def _read_header(path: Path) -> bytes | None:
    """Read the first _HEADER_SIZE bytes of *path*, or None on any error."""
    try:
        with path.open("rb") as f:
            return f.read(_HEADER_SIZE)
    except OSError:
        return None


def _classify(raw: bytes) -> AccessFormat:
    """Classify a 32-byte header blob as a Jet or ACE format."""
    if len(raw) < _HEADER_SIZE or raw[:4] != _STANDARD_HEADER:
        return AccessFormat.UNKNOWN

    family = raw[_FAMILY_OFFSET : _FAMILY_OFFSET + _FAMILY_LENGTH].rstrip(b"\x00")

    if family == _JET_MAGIC:
        version_byte = raw[_VERSION_OFFSET]
        if version_byte == _JET3_VERSION_BYTE:
            return AccessFormat.JET3
        if version_byte == _JET4_VERSION_BYTE:
            return AccessFormat.JET4
        # Unknown Jet variant — treat as UNKNOWN rather than guessing.
        return AccessFormat.UNKNOWN

    if family == _ACE_MAGIC:
        return AccessFormat.ACE

    return AccessFormat.UNKNOWN
