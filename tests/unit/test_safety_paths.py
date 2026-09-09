"""Tests for safety.py path guard — TDD RED phase."""

from pathlib import Path


class TestIsPathAllowed:
    """Tests for is_path_allowed function."""

    def test_single_allowed_dir_inside(self, tmp_path):
        """Single allowed dir: path inside is allowed."""
        from ms_access_mcp.safety import is_path_allowed
        allowed = [tmp_path / "dbs"]
        allowed[0].mkdir()
        target = allowed[0] / "db1.accdb"
        target.touch()
        assert is_path_allowed(str(target), allowed) is True

    def test_single_allowed_dir_outside(self, tmp_path):
        """Single allowed dir: path outside is rejected."""
        from ms_access_mcp.safety import is_path_allowed
        allowed = [tmp_path / "dbs"]
        allowed[0].mkdir()
        target = tmp_path / "outside.accdb"
        target.touch()
        assert is_path_allowed(str(target), allowed) is False

    def test_multi_dir_regression_oracle_bug(self, tmp_path):
        """Multi-dir regression: allowed=[a,b], path under b must be allowed.

        The oracle bug: return True was inside the for loop, but try/except
        wrapped the whole loop. Only the first allowed dir was ever checked.
        """
        from ms_access_mcp.safety import is_path_allowed
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        allowed = [dir_a, dir_b]
        target = dir_b / "db.accdb"
        target.touch()
        assert is_path_allowed(str(target), allowed) is True

    def test_traversal_blocked(self, tmp_path):
        """allowed=[a], a\\..\\..\\other\\file resolved must be rejected."""
        from ms_access_mcp.safety import is_path_allowed
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        target = allowed_dir / ".." / ".." / "other" / "file.accdb"
        allowed = [allowed_dir]
        assert is_path_allowed(str(target), allowed) is False

    def test_network_path_rejected(self, tmp_path):
        """Network path \\\\server\\share\\x.accdb is rejected by default."""
        from ms_access_mcp.safety import is_path_allowed
        allowed = [tmp_path / "dbs"]
        allowed[0].mkdir()
        network = Path("//server/share/db.accdb")
        assert is_path_allowed(str(network), allowed) is False

    def test_nonexistent_path_rejected(self, tmp_path):
        """Nonexistent path is rejected with no exception."""
        from ms_access_mcp.safety import is_path_allowed
        allowed = [tmp_path / "dbs"]
        allowed[0].mkdir()
        nonexistent = tmp_path / "dbs" / "nonexistent.accdb"
        assert is_path_allowed(str(nonexistent), allowed) is False

    def test_case_insensitive_windows(self, tmp_path):
        """D:\\DBS vs d:\\dbs both allowed on Windows."""
        from ms_access_mcp.safety import is_path_allowed
        allowed_dir = tmp_path / "DBS"
        allowed_dir.mkdir()
        allowed_lower = [Path(str(allowed_dir).lower())]
        target_lower = allowed_dir / "db.accdb"
        target_lower.touch()
        assert is_path_allowed(str(target_lower), allowed_lower) is True


class TestWritePolicy:
    """Tests for WritePolicy dataclass."""

    def test_write_policy_default_readonly(self):
        """WritePolicy is readonly by default."""
        from ms_access_mcp.safety import WritePolicy
        policy = WritePolicy()
        assert policy.readonly is True

    def test_allow_write_gate(self):
        """allow_write() permits write when readonly=False."""
        from ms_access_mcp.safety import WritePolicy
        policy = WritePolicy(readonly=False)
        assert policy.allow_write() is True

    def test_allow_write_blocked_when_readonly(self):
        """allow_write() returns False when readonly=True."""
        from ms_access_mcp.safety import WritePolicy
        policy = WritePolicy(readonly=True)
        assert policy.allow_write() is False

    def test_require_acknowledgment_rejects_destructive(self):
        """require_acknowledgment returns error for destructive op without confirm."""
        from ms_access_mcp.safety import WritePolicy
        policy = WritePolicy()
        error = policy.require_acknowledgment("DROP TABLE")
        assert error is not None
        assert "DROP TABLE" in str(error)

    def test_require_acknowledgment_accepts_with_confirm(self):
        """require_acknowledgment returns None when confirm=True."""
        from ms_access_mcp.safety import WritePolicy
        policy = WritePolicy()
        error = policy.require_acknowledgment("DROP TABLE", confirm=True)
        assert error is None
