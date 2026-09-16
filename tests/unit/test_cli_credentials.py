"""Tests for credentials CLI subcommand — RED then GREEN for Step 3.

Cases:
1. credentials set --db <path> with mocked getpass twice → global file created.
2. Mismatched getpass entries → exit 2, no file written.
3. Empty password → exit 2.
4. Forbidden chars ;, {, } → exit 2.
5. Existing entry → overwrite requires confirm; --yes skips it.
6. credentials set --db <path> --local → local file created.
7. credentials list → prints paths and source, never prints passwords.
8. credentials remove --db <path> → entry gone; remove on missing is no-op exit 0.
"""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ms_access_mcp import cli

# =============================================================================
# Helpers
# =============================================================================


def _global_credentials_path() -> Path:
    """Return the platform-appropriate global credentials file path."""
    if os.name == "nt":
        base = Path(os.environ["USERPROFILE"])
    else:
        base = Path.home()
    return base / ".ms-access-mcp" / "credentials.json"


def _local_credentials_path(db_path: str | Path) -> Path:
    """Return the local credentials file path for a given db."""
    return Path(db_path).resolve().parent / ".ms-access-mcp.json"


# =============================================================================
# Case 1: credentials set — happy path (global)
# =============================================================================


class TestCredentialsSetGlobal:
    def test_set_creates_global_credentials_file(self, monkeypatch, tmp_path):
        """credentials set --db <path> with matching getpass twice creates global file."""
        db_path = str(tmp_path / "db.accdb")
        global_file = _global_credentials_path()
        global_file.parent.mkdir(parents=True, exist_ok=True)

        # Isolate: use tmp USERPROFILE
        fake_userprofile = tmp_path / "home"
        fake_userprofile.mkdir()

        with patch.dict(os.environ, {"USERPROFILE": str(fake_userprofile)}):
            with patch("getpass.getpass", side_effect=["pw_one", "pw_one"]):
                with patch.object(sys, "argv", [
                    "ms-access-mcp", "credentials", "set",
                    "--db", db_path,
                ]):
                    exit_code = cli.main()

        assert exit_code == 0, f"expected exit 0, got {exit_code}"
        expected_path = fake_userprofile / ".ms-access-mcp" / "credentials.json"
        assert expected_path.exists(), f"expected {expected_path} to exist"
        data = json.loads(expected_path.read_text(encoding="utf-8"))
        assert "credentials" in data
        assert list(data["credentials"].values())[0] == "pw_one"


# =============================================================================
# Case 2: mismatch → exit 2
# =============================================================================


class TestCredentialsSetMismatch:
    def test_mismatch_exits_2_no_file(self, monkeypatch, tmp_path):
        """Mismatched getpass entries → exit 2, no file written."""
        db_path = str(tmp_path / "db.accdb")
        global_file = _global_credentials_path()
        global_file.parent.mkdir(parents=True, exist_ok=True)

        fake_userprofile = tmp_path / "home"
        fake_userprofile.mkdir()

        with patch.dict(os.environ, {"USERPROFILE": str(fake_userprofile)}):
            with patch("getpass.getpass", side_effect=["pw_one", "pw_two"]):
                with patch.object(sys, "argv", [
                    "ms-access-mcp", "credentials", "set",
                    "--db", db_path,
                ]):
                    exit_code = cli.main()

        assert exit_code == 2, f"expected exit 2, got {exit_code}"
        expected_path = fake_userprofile / ".ms-access-mcp" / "credentials.json"
        assert not expected_path.exists(), "file should not be created on mismatch"


# =============================================================================
# Case 3: empty password → exit 2
# =============================================================================


class TestCredentialsSetEmpty:
    def test_empty_password_exits_2(self, monkeypatch, tmp_path):
        """Empty password → exit 2."""
        db_path = str(tmp_path / "db.accdb")

        fake_userprofile = tmp_path / "home"
        fake_userprofile.mkdir()

        with patch.dict(os.environ, {"USERPROFILE": str(fake_userprofile)}):
            with patch("getpass.getpass", side_effect=["", ""]):
                with patch.object(sys, "argv", [
                    "ms-access-mcp", "credentials", "set",
                    "--db", db_path,
                ]):
                    exit_code = cli.main()

        assert exit_code == 2, f"expected exit 2, got {exit_code}"


# =============================================================================
# Case 4: forbidden chars → exit 2
# =============================================================================


class TestCredentialsSetForbiddenChars:
    def test_semicolon_exits_2(self, monkeypatch, tmp_path):
        """Password containing ; → exit 2."""
        db_path = str(tmp_path / "db.accdb")

        fake_userprofile = tmp_path / "home"
        fake_userprofile.mkdir()

        with patch.dict(os.environ, {"USERPROFILE": str(fake_userprofile)}):
            with patch("getpass.getpass", side_effect=["pw;with;semicolons", "pw;with;semicolons"]):
                with patch.object(sys, "argv", [
                    "ms-access-mcp", "credentials", "set",
                    "--db", db_path,
                ]):
                    exit_code = cli.main()

        assert exit_code == 2, f"expected exit 2, got {exit_code}"

    def test_brace_exits_2(self, monkeypatch, tmp_path):
        """Password containing { or } → exit 2."""
        db_path = str(tmp_path / "db.accdb")

        fake_userprofile = tmp_path / "home"
        fake_userprofile.mkdir()

        with patch.dict(os.environ, {"USERPROFILE": str(fake_userprofile)}):
            with patch("getpass.getpass", side_effect=["pw{brace}", "pw{brace}"]):
                with patch.object(sys, "argv", [
                    "ms-access-mcp", "credentials", "set",
                    "--db", db_path,
                ]):
                    exit_code = cli.main()

        assert exit_code == 2, f"expected exit 2, got {exit_code}"


# =============================================================================
# Case 5: overwrite confirm / --yes
# =============================================================================


class TestCredentialsSetOverwrite:
    def test_existing_entry_requires_confirm(self, monkeypatch, tmp_path):
        """Existing entry → overwrite requires interactive confirm."""
        db_path = str(tmp_path / "db.accdb")
        fake_userprofile = tmp_path / "home"
        fake_userprofile.mkdir()

        global_file = fake_userprofile / ".ms-access-mcp" / "credentials.json"
        global_file.parent.mkdir(parents=True, exist_ok=True)
        # Pre-existing entry
        global_file.write_text(json.dumps({
            "version": 1,
            "credentials": {db_path: "old_pw"},
        }), encoding="utf-8")

        with patch.dict(os.environ, {"USERPROFILE": str(fake_userprofile)}):
            with patch("getpass.getpass", side_effect=["", ""]):
                # Empty stdin → unconfirmed → should exit 2
                with patch("sys.stdin", StringIO("\n")):
                    with patch.object(sys, "argv", [
                        "ms-access-mcp", "credentials", "set",
                        "--db", db_path,
                    ]):
                        exit_code = cli.main()

        assert exit_code == 2, f"expected exit 2 on unconfirmed overwrite, got {exit_code}"

    def test_yes_flag_skips_confirm(self, monkeypatch, tmp_path):
        """--yes flag skips overwrite confirmation."""
        db_path = str(tmp_path / "db.accdb")
        fake_userprofile = tmp_path / "home"
        fake_userprofile.mkdir()

        global_file = fake_userprofile / ".ms-access-mcp" / "credentials.json"
        global_file.parent.mkdir(parents=True, exist_ok=True)
        global_file.write_text(json.dumps({
            "version": 1,
            "credentials": {db_path: "old_pw"},
        }), encoding="utf-8")

        with patch.dict(os.environ, {"USERPROFILE": str(fake_userprofile)}):
            with patch("getpass.getpass", side_effect=["new_pw", "new_pw"]):
                with patch.object(sys, "argv", [
                    "ms-access-mcp", "credentials", "set",
                    "--db", db_path, "--yes",
                ]):
                    exit_code = cli.main()

        assert exit_code == 0, f"expected exit 0 with --yes, got {exit_code}"
        data = json.loads(global_file.read_text(encoding="utf-8"))
        # Keys are normalized to long form in the file
        norm_path = os.path.normcase(os.path.normpath(str(Path(db_path).resolve())))
        assert data["credentials"][norm_path] == "new_pw"


# =============================================================================
# Case 6: --local flag
# =============================================================================


class TestCredentialsSetLocal:
    def test_local_flag_writes_local_file(self, monkeypatch, tmp_path):
        """credentials set --db <path> --local writes <db_dir>/.ms-access-mcp.json."""
        db_path = str(tmp_path / "db.accdb")
        fake_userprofile = tmp_path / "home"
        fake_userprofile.mkdir()

        with patch.dict(os.environ, {"USERPROFILE": str(fake_userprofile)}):
            with patch("getpass.getpass", side_effect=["local_pw", "local_pw"]):
                with patch.object(sys, "argv", [
                    "ms-access-mcp", "credentials", "set",
                    "--db", db_path, "--local",
                ]):
                    exit_code = cli.main()

        assert exit_code == 0, f"expected exit 0, got {exit_code}"
        local_file = _local_credentials_path(db_path)
        assert local_file.exists(), f"expected {local_file} to exist"
        data = json.loads(local_file.read_text(encoding="utf-8"))
        assert "credentials" in data


# =============================================================================
# Case 7: credentials list
# =============================================================================


class TestCredentialsList:
    def test_list_prints_paths_never_passwords(self, monkeypatch, tmp_path, capsys):
        """credentials list prints db paths and source, never prints passwords."""
        db_path = str(tmp_path / "db.accdb")
        fake_userprofile = tmp_path / "home"
        fake_userprofile.mkdir()

        global_file = fake_userprofile / ".ms-access-mcp" / "credentials.json"
        global_file.parent.mkdir(parents=True, exist_ok=True)
        global_file.write_text(json.dumps({
            "version": 1,
            "credentials": {db_path: "secret_pw"},
        }), encoding="utf-8")

        with patch.dict(os.environ, {"USERPROFILE": str(fake_userprofile)}):
            with patch.object(sys, "argv", [
                "ms-access-mcp", "credentials", "list",
            ]):
                exit_code = cli.main()

        assert exit_code == 0, f"expected exit 0, got {exit_code}"
        out = capsys.readouterr().out
        err = capsys.readouterr().err
        # Password must not appear in output
        assert "secret_pw" not in out and "secret_pw" not in err, \
            "password must not appear in list output"


# =============================================================================
# Case 8: credentials remove
# =============================================================================


class TestCredentialsRemove:
    def test_remove_deletes_entry(self, monkeypatch, tmp_path):
        """credentials remove --db <path> deletes the entry."""
        db_path = str(tmp_path / "db.accdb")
        fake_userprofile = tmp_path / "home"
        fake_userprofile.mkdir()

        global_file = fake_userprofile / ".ms-access-mcp" / "credentials.json"
        global_file.parent.mkdir(parents=True, exist_ok=True)
        global_file.write_text(json.dumps({
            "version": 1,
            "credentials": {db_path: "removeme_pw"},
        }), encoding="utf-8")

        with patch.dict(os.environ, {"USERPROFILE": str(fake_userprofile)}):
            with patch.object(sys, "argv", [
                "ms-access-mcp", "credentials", "remove",
                "--db", db_path,
            ]):
                exit_code = cli.main()

        assert exit_code == 0, f"expected exit 0, got {exit_code}"
        data = json.loads(global_file.read_text(encoding="utf-8"))
        # Keys are normalized to long form in the file
        norm_path = os.path.normcase(os.path.normpath(str(Path(db_path).resolve())))
        assert norm_path not in data["credentials"]

    def test_remove_missing_entry_is_noop_exit_0(self, monkeypatch, tmp_path):
        """remove on missing entry is a no-op with exit 0."""
        db_path = str(tmp_path / "db.accdb")
        fake_userprofile = tmp_path / "home"
        fake_userprofile.mkdir()

        global_file = fake_userprofile / ".ms-access-mcp" / "credentials.json"
        global_file.parent.mkdir(parents=True, exist_ok=True)
        global_file.write_text(json.dumps({
            "version": 1,
            "credentials": {},
        }), encoding="utf-8")

        with patch.dict(os.environ, {"USERPROFILE": str(fake_userprofile)}):
            with patch.object(sys, "argv", [
                "ms-access-mcp", "credentials", "remove",
                "--db", db_path,
            ]):
                exit_code = cli.main()

        assert exit_code == 0, f"expected exit 0 for no-op remove, got {exit_code}"
