"""Unit tests for the Access format sniffer."""

from ms_access_mcp.backends.format_detector import (
    _ACE_MAGIC,
    _HEADER_SIZE,
    _JET3_VERSION_BYTE,
    _JET4_VERSION_BYTE,
    _JET_MAGIC,
    _VERSION_OFFSET,
    AccessFormat,
    _classify,
    _read_header,
    detect_access_format,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def header(magic: bytes, version_byte: int) -> bytes:
    """Build a 32-byte Jet/ACE header blob.

    Parameters
    ----------
    magic:
        ASCII bytes written at offset 4 (e.g. b'Standard Jet DB').
    version_byte:
        Integer written at offset 0x14 (0 for JET3, 1 for JET4).
    """
    base = b"\x00\x01\x00\x00"
    family_field = magic.ljust(16, b"\x00")[:16]
    padding_before_version = b"\x00" * (_VERSION_OFFSET - len(base) - len(family_field))
    version = bytes([version_byte])
    remaining = b"\x00" * (
        _HEADER_SIZE - len(base) - len(family_field) - len(padding_before_version) - 1
    )
    return base + family_field + padding_before_version + version + remaining


# ---------------------------------------------------------------------------
# Tests — public API
# ---------------------------------------------------------------------------


class TestDetectAccessFormat:
    def test_jet3_header_returns_jet3(self, tmp_path):
        db_path = tmp_path / "test.mdb"
        db_path.write_bytes(header(_JET_MAGIC, _JET3_VERSION_BYTE))
        assert detect_access_format(db_path) == AccessFormat.JET3

    def test_jet4_header_returns_jet4(self, tmp_path):
        db_path = tmp_path / "test.mdb"
        db_path.write_bytes(header(_JET_MAGIC, _JET4_VERSION_BYTE))
        assert detect_access_format(db_path) == AccessFormat.JET4

    def test_ace_header_returns_ace(self, tmp_path):
        db_path = tmp_path / "test.accdb"
        db_path.write_bytes(header(_ACE_MAGIC, 0x00))
        assert detect_access_format(db_path) == AccessFormat.ACE

    def test_garbage_bytes_returns_unknown(self, tmp_path):
        db_path = tmp_path / "unknown.bin"
        db_path.write_bytes(b"\xff\xfe\xfd\xfc" + b"garbage payload data here!!!")
        assert detect_access_format(db_path) == AccessFormat.UNKNOWN

    def test_truncated_file_returns_unknown(self, tmp_path):
        db_path = tmp_path / "truncated.mdb"
        db_path.write_bytes(b"\x00\x01\x00\x00" + b"truncated")
        assert detect_access_format(db_path) == AccessFormat.UNKNOWN

    def test_empty_file_returns_unknown(self, tmp_path):
        db_path = tmp_path / "empty.mdb"
        db_path.write_bytes(b"")
        assert detect_access_format(db_path) == AccessFormat.UNKNOWN

    def test_nonexistent_path_returns_unknown(self, tmp_path):
        nonexistent = tmp_path / "does_not_exist.mdb"
        assert detect_access_format(nonexistent) == AccessFormat.UNKNOWN

    def test_str_path_returns_jet4(self, tmp_path):
        db_path = tmp_path / "test.mdb"
        db_path.write_bytes(header(_JET_MAGIC, _JET4_VERSION_BYTE))
        # Pass as plain str, not Path
        assert detect_access_format(str(db_path)) == AccessFormat.JET4


# ---------------------------------------------------------------------------
# Tests — internal helpers
# ---------------------------------------------------------------------------


class TestClassify:
    def test_wrong_magic_prefix_returns_unknown(self):
        bad = b"\x01\x00\x00\x00" + b"Standard Jet DB".ljust(16, b"\x00") + b"\x00" * 12
        assert _classify(bad) == AccessFormat.UNKNOWN

    def test_unknown_family_returns_unknown(self):
        blob = b"\x00\x01\x00\x00" + b"Unknown Access DB  ".ljust(16, b"\x00") + b"\x00" * 12
        assert _classify(blob) == AccessFormat.UNKNOWN

    def test_truncated_blob_returns_unknown(self):
        assert _classify(b"\x00\x01\x00\x00" + b"Standard Jet DB".ljust(16)) == AccessFormat.UNKNOWN

    def test_empty_blob_returns_unknown(self):
        assert _classify(b"") == AccessFormat.UNKNOWN


class TestReadHeader:
    def test_reads_exact_bytes(self, tmp_path):
        db_path = tmp_path / "probe.bin"
        content = b"x" * 64
        db_path.write_bytes(content)
        result = _read_header(db_path)
        assert result is not None
        assert len(result) == _HEADER_SIZE
        assert result == content[:_HEADER_SIZE]

    def test_missing_file_returns_none(self, tmp_path):
        missing = tmp_path / "no_such_file.bin"
        assert _read_header(missing) is None
