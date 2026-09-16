"""Tests for credentials resolver — Step 1 RED then GREEN.

Cases:
1. resolve_password returns "" when neither file exists.
2. Global file hit: abs path resolves.
3. Normalization matrix: forward-slash, Windows case, relative path.
4. Local shadows global.
5. Malformed JSON → "" (never raises).
6. Valid JSON wrong structure → "".
7. Missing entry → "".
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# The module under test
from ms_access_mcp import credentials
from ms_access_mcp.errors import Err, Ok


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
    return Path(db_path).parent / ".ms-access-mcp.json"


def _write_global_file(path: Path, data: dict) -> None:
    """Write the global credentials file with atomic write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)
    # Set permissions on POSIX
    if os.name != "nt":
        os.chmod(path, 0o600)


def _write_local_file(db_path: str | Path, data: dict) -> None:
    """Write the local credentials file with atomic write."""
    path = _local_credentials_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


# Normalize a db_path the same way credentials.resolve_password does
def _norm(db_path: str) -> str:
    """Normalize a database path to match resolve_password's internal form."""
    return os.path.normcase(os.path.normpath(str(Path(db_path).resolve())))


# =============================================================================
# Case 1: no file exists → ""
# =============================================================================


class TestResolveNoFile:
    def test_returns_empty_string_when_no_file_exists(self, monkeypatch, tmp_path):
        """resolve_password returns '' when neither local nor global file exists."""
        # Isolate by pointing env var to a non-existent location
        fake_global = tmp_path / "does_not_exist.json"
        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(fake_global))

        result = credentials.resolve_password(str(tmp_path / "db.accdb"))
        assert result == ""


# =============================================================================
# Case 2: global file hit
# =============================================================================


class TestGlobalFileHit:
    def test_resolves_password_from_global_file(self, monkeypatch, tmp_path):
        """Global credentials file entry resolves when local file absent."""
        db_path = str(tmp_path / "db.accdb")
        global_file = tmp_path / "global.json"

        _write_global_file(global_file, {
            "version": 1,
            "credentials": {_norm(db_path): "global_pw"},
        })

        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_file))

        result = credentials.resolve_password(db_path)
        assert result == "global_pw"


# =============================================================================
# Case 3: normalization matrix
# =============================================================================


class TestNormalizationMatrix:
    def _write_global_entry(self, path_str: str, password: str, monkeypatch, tmp_path):
        global_file = tmp_path / "global.json"
        # Normalize the path key to match resolve_password's internal form
        norm_path = _norm(path_str)
        _write_global_file(global_file, {
            "version": 1,
            "credentials": {norm_path: password},
        })
        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_file))
        return global_file

    def test_forward_slash_normalized(self, monkeypatch, tmp_path):
        """Entry stored with backslash resolves when queried with forward slashes."""
        db_path = str(tmp_path / "db.accdb")
        self._write_global_entry(db_path, "pw_fw", monkeypatch, tmp_path)
        # Query with forward slash
        db_path_fw = db_path.replace("\\", "/")
        result = credentials.resolve_password(db_path_fw)
        assert result == "pw_fw"

    def test_case_normalized_windows(self, monkeypatch, tmp_path):
        """Entry resolves regardless of Windows drive letter case."""
        db_path_lower = str(tmp_path / "db.accdb")
        self._write_global_entry(db_path_lower, "pw_case", monkeypatch, tmp_path)
        # Query with uppercase drive
        if os.name == "nt" and len(db_path_lower) >= 2 and db_path_lower[1] == ":":
            db_path_upper = db_path_lower[0].upper() + db_path_lower[1:]
            result = credentials.resolve_password(db_path_upper)
            assert result == "pw_case"

    def test_relative_vs_absolute(self, monkeypatch, tmp_path):
        """Relative path query resolves to absolute-path entry when in same dir."""
        global_file = tmp_path / "global.json"
        db_abs = str(tmp_path / "sub" / "db.accdb")
        db_abs_parts = Path(db_abs)
        db_abs_parts.parent.mkdir(parents=True, exist_ok=True)
        # Normalize to match what resolve_password will produce
        _write_global_file(global_file, {
            "version": 1,
            "credentials": {_norm(db_abs): "pw_rel"},
        })
        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_file))

        # Query with relative path
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            rel_path = Path("sub") / "db.accdb"
            result = credentials.resolve_password(str(rel_path))
            assert result == "pw_rel"
        finally:
            os.chdir(old_cwd)


# =============================================================================
# Case 4: local shadows global
# =============================================================================


class TestLocalShadowsGlobal:
    def test_local_entry_shadows_global_for_same_db(self, monkeypatch, tmp_path):
        """Local credentials file entry takes precedence over global entry."""
        db_path = str(tmp_path / "db.accdb")
        global_file = tmp_path / "global.json"
        local_file = _local_credentials_path(db_path)

        _write_global_file(global_file, {
            "version": 1,
            "credentials": {_norm(db_path): "global_pw"},
        })
        _write_local_file(db_path, {
            "version": 1,
            "credentials": {_norm(db_path): "local_pw"},
        })

        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_file))

        result = credentials.resolve_password(db_path)
        assert result == "local_pw"


# =============================================================================
# Case 5: malformed JSON → ""
# =============================================================================


class TestMalformedJson:
    def test_malformed_json_returns_empty_string(self, monkeypatch, tmp_path):
        """Malformed JSON in credentials file returns '' without raising."""
        db_path = str(tmp_path / "db.accdb")
        global_file = tmp_path / "global.json"
        global_file.parent.mkdir(parents=True, exist_ok=True)
        global_file.write_text("{not valid json", encoding="utf-8")

        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_file))

        result = credentials.resolve_password(db_path)
        assert result == ""


# =============================================================================
# Case 6: wrong structure → ""
# =============================================================================


class TestWrongStructure:
    def test_list_root_returns_empty(self, monkeypatch, tmp_path):
        """Valid JSON that is a list (not a dict) returns ''."""
        db_path = str(tmp_path / "db.accdb")
        global_file = tmp_path / "global.json"
        _write_global_file(global_file, [{"path": db_path, "pw": "x"}])

        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_file))

        result = credentials.resolve_password(db_path)
        assert result == ""

    def test_dict_without_credentials_key_returns_empty(self, monkeypatch, tmp_path):
        """Valid JSON dict without 'credentials' key returns ''."""
        db_path = str(tmp_path / "db.accdb")
        global_file = tmp_path / "global.json"
        _write_global_file(global_file, {"version": 1, "other": "data"})

        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_file))

        result = credentials.resolve_password(db_path)
        assert result == ""


# =============================================================================
# Case 7: missing entry → ""
# =============================================================================


class TestMissingEntry:
    def test_existing_file_but_no_entry_returns_empty(self, monkeypatch, tmp_path):
        """Credentials file exists but has no entry for this db → ''."""
        db_path = str(tmp_path / "db.accdb")
        global_file = tmp_path / "global.json"
        _write_global_file(global_file, {
            "version": 1,
            "credentials": {_norm(str(tmp_path / "other.accdb")): "other_pw"},
        })

        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_file))

        result = credentials.resolve_password(db_path)
        assert result == ""


# =============================================================================
# Case 8: wire-through — password passed to adapter.connect
# Case 9: no entry → adapter.connect called with empty password
# =============================================================================


class TestWireThrough:
    """Test that open_database wires the resolved password to adapter.connect."""

    def test_wire_password_to_connect(self, monkeypatch, tmp_path):
        """When a credentials entry exists, connect is called with password=."""
        from ms_access_mcp.application import database
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends import BackendSelector

        db_path = str(tmp_path / "db.accdb")
        global_file = tmp_path / "global.json"

        # Write the credentials entry
        _write_global_file(global_file, {
            "version": 1,
            "credentials": {_norm(db_path): "wire_pw"},
        })
        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_file))

        # Bypass is_path_allowed for the non-existent .accdb
        monkeypatch.setattr(database, "is_path_allowed", lambda path, dirs: True)

        captured_connect_calls: list[tuple] = []

        class FakeAdapter:
            def connect(self, db_path: str, password: str = "") -> None:
                captured_connect_calls.append((db_path, password))

        # Patch BackendSelector.get_adapter to return our fake adapter
        monkeypatch.setattr(
            BackendSelector,
            "get_adapter",
            lambda **kwargs: FakeAdapter(),
        )

        db = DatabaseUseCases(allowed_dirs=[Path(tmp_path)])
        result = db.open_database(db_path)

        assert isinstance(result, Ok), f"expected Ok, got {result}"
        assert len(captured_connect_calls) == 1
        call_db_path, call_password = captured_connect_calls[0]
        assert call_db_path == db_path
        assert call_password == "wire_pw"

    def test_no_entry_pass_empty_password(self, monkeypatch, tmp_path):
        """When no credentials entry exists, connect is called with password=""."""
        from ms_access_mcp.application import database
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends import BackendSelector

        db_path = str(tmp_path / "db.accdb")

        # Ensure no credentials file
        monkeypatch.delenv("ACCESS_MCP_CREDENTIALS_FILE", raising=False)

        # Bypass is_path_allowed for the non-existent .accdb
        monkeypatch.setattr(database, "is_path_allowed", lambda path, dirs: True)

        captured_connect_calls: list[tuple] = []

        class FakeAdapter:
            def connect(self, db_path: str, password: str = "") -> None:
                captured_connect_calls.append((db_path, password))

        monkeypatch.setattr(
            BackendSelector,
            "get_adapter",
            lambda **kwargs: FakeAdapter(),
        )

        db = DatabaseUseCases(allowed_dirs=[Path(tmp_path)])
        result = db.open_database(db_path)

        assert isinstance(result, Ok), f"expected Ok, got {result}"
        assert len(captured_connect_calls) == 1
        call_db_path, call_password = captured_connect_calls[0]
        assert call_db_path == db_path
        assert call_password == ""


# =============================================================================
# Case 10: error-leak guard — password scrubbed from error messages
# =============================================================================


class TestErrorLeakGuard:
    def test_password_not_in_error_message(self, monkeypatch, tmp_path):
        """Resolved password must not appear in the error message returned to LLM."""
        from ms_access_mcp.application import database
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends import BackendSelector

        db_path = str(tmp_path / "db.accdb")
        global_file = tmp_path / "global.json"

        _write_global_file(global_file, {
            "version": 1,
            "credentials": {_norm(db_path): "hunter2"},
        })
        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_file))

        # Bypass is_path_allowed for the non-existent .accdb
        monkeypatch.setattr(database, "is_path_allowed", lambda path, dirs: True)

        class LeakingAdapter:
            """Adapter that raises an exception containing the password."""

            def connect(self, db_path: str, password: str = "") -> None:
                raise Exception(f"foo;PWD={password};bar")

        monkeypatch.setattr(
            BackendSelector,
            "get_adapter",
            lambda **kwargs: LeakingAdapter(),
        )

        db = DatabaseUseCases(allowed_dirs=[Path(tmp_path)])
        result = db.open_database(db_path)

        assert isinstance(result, Err), f"expected Err, got {result}"
        msg = result.message
        assert "hunter2" not in msg, f"password leaked into error: {msg}"
        assert "***" in msg, f"password should be scrubbed to ***: {msg}"


# =============================================================================
# Case 11: params guard — password not passed via MCP tool params
# =============================================================================


class TestParamsGuard:
    def test_password_not_leaked_via_tool_params(self, monkeypatch, tmp_path):
        """Password in MCP params must not appear in connection result."""
        from ms_access_mcp.application import database
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.backends import BackendSelector

        db_path = str(tmp_path / "db.accdb")
        global_file = tmp_path / "global.json"

        _write_global_file(global_file, {
            "version": 1,
            "credentials": {_norm(db_path): "hunter2"},
        })
        monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_file))

        # Bypass is_path_allowed for the non-existent .accdb
        monkeypatch.setattr(database, "is_path_allowed", lambda path, dirs: True)

        # Use a stub that raises so we can inspect the call args
        connect_called_with: dict = {}

        class FakeAdapter:
            def connect(self, db_path: str, password: str = "") -> None:
                connect_called_with["db_path"] = db_path
                connect_called_with["password"] = password
                raise Exception("injected")

        monkeypatch.setattr(
            BackendSelector,
            "get_adapter",
            lambda **kwargs: FakeAdapter(),
        )

        db = DatabaseUseCases(allowed_dirs=[Path(tmp_path)])
        result = db.open_database(db_path)

        # The connect was called with the resolved password from credentials file,
        # NOT from params. The params dict in MCP tools is for other settings,
        # not for the password (which would be a security leak).
        assert connect_called_with.get("password") == "hunter2"
        assert "hunter2" not in str(result)
