"""Tests for opencode_config.py — RED phase: tests describe expected behaviour."""

from pathlib import Path

import pytest

from ms_access_mcp.opencode_config import (
    Err,
    McpEntry,
    Ok,
    Result,
    _ValidationError,
    entry_from,
    load,
    merge_entry,
    register,
    remove,
    timestamped_backup,
    write_atomic,
)

# =============================================================================
# Helpers
# =============================================================================


def fake_validatorraiser(_path: Path) -> None:
    """Validator that always raises — simulates opencode debug config failure."""
    raise RuntimeError("opencode debug config failed")


# =============================================================================
# S1 — Fresh registration: absent opencode.json created with $schema + mcp.ms-access
# =============================================================================


class TestS1FreshRegistration:
    def test_fresh_creates_file_with_schema_and_entry(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        entry = McpEntry(command=("python", "-m", "ms_access_mcp.cli"), profile="core")

        result = register(target, entry)

        assert isinstance(result, Ok), f"expected Ok, got {result!r}"
        assert target.exists(), "opencode.json should be created"
        import json

        with target.open() as f:
            data = json.load(f)
        assert "$schema" in data, "fresh file must contain $schema"
        assert "mcp" in data, "fresh file must contain mcp key"
        assert "ms-access" in data["mcp"], "fresh file must contain mcp.ms-access"
        # S9: no secrets
        for key in data:
            assert key not in ("env", "headers", "token", "key", "password", "secret"), (
                f"secret key {key!r} must not appear in fresh config"
            )
        for key in data.get("mcp", {}).get("ms-access", {}):
            assert key not in ("env", "headers", "token", "key", "password", "secret"), (
                f"secret key {key!r} must not appear in mcp.ms-access"
            )
        # Only $schema and mcp
        assert set(data.keys()) == {"$schema", "mcp"}, (
            f"fresh file must contain ONLY $schema and mcp, got {set(data.keys())}"
        )


# =============================================================================
# S2 — Preserve unrelated config: existing mcp.engram + permission survive merge
# =============================================================================


class TestS2PreserveUnrelated:
    def test_existing_mcp_engram_and_permission_preserved(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        import json

        original = {
            "$schema": "https://opencode.ai/opencode.schema.json",
            "mcp": {
                "engram": {
                    "command": ("npx", "engram"),
                    "profile": "default",
                },
            },
            "permission": {"read": True, "write": False},
        }
        target.write_text(json.dumps(original, indent=2))

        entry = McpEntry(command=("python", "-m", "ms_access_mcp.cli"), profile="core")
        result = register(target, entry)

        assert isinstance(result, Ok), f"expected Ok, got {result!r}"
        with target.open() as f:
            data = json.load(f)
        assert "mcp" in data, "mcp key must survive"
        assert "engram" in data["mcp"], "mcp.engram must survive merge"
        assert "permission" in data, "permission key must survive merge"
        assert data["permission"]["read"] is True
        assert "ms-access" in data["mcp"], "ms-access must be added"


# =============================================================================
# S3 — Conflict detection: differing mcp.ms-access.command aborts without force
# =============================================================================


class TestS3ConflictDetection:
    def test_conflicting_entry_aborts_without_force(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        import json

        original = {
            "$schema": "https://opencode.ai/opencode.schema.json",
            "mcp": {
                "ms-access": {
                    "command": ("python", "old-path", "mcp"),
                    "profile": "core",
                    "enabled": True,
                    "timeout": 30000,
                },
            },
        }
        target.write_text(json.dumps(original))

        entry = McpEntry(
            command=("python", "-m", "ms_access_mcp.cli"), profile="core"
        )
        result = register(target, entry, force=False)

        assert isinstance(result, Err), f"expected Err for conflict, got {result!r}"
        assert "conflict" in result.message.lower() or "differs" in result.message.lower(), (
            f"error message must mention conflict, got {result.message!r}"
        )
        # File unchanged (JSON deserialises tuples→lists, so compare normalised)
        with target.open() as f:
            saved = json.load(f)
        saved_cmd = saved["mcp"]["ms-access"]["command"]
        orig_cmd = list(original["mcp"]["ms-access"]["command"])
        assert saved_cmd == orig_cmd, "original file must be unchanged after conflict abort"

    def test_force_overwrites_after_backup(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        import json

        original = {
            "$schema": "https://opencode.ai/opencode.schema.json",
            "mcp": {
                "ms-access": {
                    "command": ("python", "old-path", "mcp"),
                    "profile": "core",
                    "enabled": True,
                    "timeout": 30000,
                },
            },
        }
        target.write_text(json.dumps(original))

        entry = McpEntry(
            command=("python", "-m", "ms_access_mcp.cli"), profile="core"
        )
        result = register(target, entry, force=True)

        assert isinstance(result, Ok), f"expected Ok with force, got {result!r}"
        with target.open() as f:
            saved = json.load(f)
        assert (
            saved["mcp"]["ms-access"]["command"]
            == list(entry.command)
        ), "file should be updated with force"


# =============================================================================
# S4 — JSONC isolation: sibling opencode.jsonc bytes and mtime unchanged
# =============================================================================


class TestS4JsoncIsolation:
    def test_jsonc_mtime_unchanged_after_registration(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        jsonc_path = tmp_path / "opencode.jsonc"

        import time

        jsonc_content = '{"mcp": {"engram": {"command": ["npx"]}}}'
        jsonc_path.write_text(jsonc_content)
        jsonc_mtime_before = jsonc_path.stat().st_mtime
        time.sleep(0.05)  # ensure clock ticks on Windows

        entry = McpEntry(command=("python", "-m", "ms_access_mcp.cli"), profile="core")
        result = register(target, entry)

        assert isinstance(result, Ok), f"expected Ok, got {result!r}"
        jsonc_mtime_after = jsonc_path.stat().st_mtime
        assert (
            jsonc_mtime_before == jsonc_mtime_after
        ), ".jsonc mtime must not change"
        assert (
            jsonc_path.read_text() == jsonc_content
        ), ".jsonc content must not change"


# =============================================================================
# S6 — Rollback: failed validation restores backup or deletes new file
# =============================================================================


class TestS6Rollback:
    def test_failed_validation_restores_backup(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        import json

        original = {
            "$schema": "https://opencode.ai/opencode.schema.json",
            "mcp": {
                "ms-access": {
                    "command": ("python", "old", "mcp"),
                    "profile": "core",
                    "enabled": True,
                    "timeout": 30000,
                },
            },
        }
        target.write_text(json.dumps(original))

        entry = McpEntry(
            command=("python", "-m", "ms_access_mcp.cli"), profile="core"
        )

        # Validator that raises → should trigger rollback
        result = register(
            target, entry, force=True, validator=fake_validatorraiser
        )

        assert isinstance(result, Err), f"expected Err after rollback, got {result!r}"
        # Target must be restored to original content
        # (JSON deserialises tuples→lists, so compare normalised)
        with target.open() as f:
            restored = json.load(f)
        restored_cmd = restored["mcp"]["ms-access"]["command"]
        orig_cmd = list(original["mcp"]["ms-access"]["command"])
        assert restored_cmd == orig_cmd, (
            "original content must be restored after failed validation"
        )


# =============================================================================
# Threat: injected failing validator → rollback restores original bytes
# =============================================================================


class TestThreatValidatorRollback:
    def test_failing_validator_raises_and_restores_original(self, tmp_path: Path) -> None:
        """Injected failing validator: backup restored, original bytes intact."""
        target = tmp_path / "opencode.json"
        import json

        original = {
            "$schema": "https://opencode.ai/opencode.schema.json",
            "mcp": {
                "ms-access": {
                    "command": ("python", "original", "mcp"),
                    "profile": "core",
                    "enabled": True,
                    "timeout": 30000,
                },
            },
        }
        target.write_text(json.dumps(original))
        original_bytes = target.read_bytes()

        entry = McpEntry(
            command=("python", "-m", "ms_access_mcp.cli"), profile="core"
        )
        result = register(
            target, entry, force=True, validator=fake_validatorraiser
        )

        assert isinstance(result, Err), f"expected Err, got {result!r}"
        # Bytes must be exactly restored
        assert target.read_bytes() == original_bytes, (
            "target file bytes must exactly match original after rollback"
        )
        # A backup should exist (pointing to where the original was)
        assert result.backup_path is not None, "Err must carry backup_path after rollback"


# =============================================================================
# S9 — No secrets: written config has no credentials/tokens/keys
# =============================================================================


class TestS9NoSecrets:
    def test_written_config_has_no_secret_keys(self, tmp_path: Path) -> None:
        """S9: registration output contains only path/flags, no credentials."""
        target = tmp_path / "opencode.json"
        entry = McpEntry(command=("python", "-m", "ms_access_mcp.cli"), profile="core")

        result = register(target, entry)

        assert isinstance(result, Ok), f"expected Ok, got {result!r}"
        import json

        with target.open() as f:
            data = json.load(f)

        all_keys: list[str] = []
        # Top-level
        all_keys.extend(data.keys())
        # mcp.*
        for mcp_key, mcp_val in data.get("mcp", {}).items():
            all_keys.append(f"mcp.{mcp_key}")
            if isinstance(mcp_val, dict):
                all_keys.extend(f"mcp.{mcp_key}.{k}" for k in mcp_val)

        forbidden = {"env", "headers", "token", "key", "password", "secret"}
        found = forbidden & set(all_keys)
        assert not found, f"forbidden secret keys found: {found}"


# =============================================================================
# S7 — Remove: deletes only mcp.ms-access, preserves everything else
# =============================================================================


class TestS7RemoveEntry:
    def test_remove_deletes_only_ms_access(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        import json

        original = {
            "$schema": "https://opencode.ai/opencode.schema.json",
            "mcp": {
                "ms-access": {
                    "command": ("python", "-m", "ms_access_mcp.cli"),
                    "profile": "core",
                    "enabled": True,
                    "timeout": 30000,
                },
                "engram": {
                    "command": ("npx", "engram"),
                    "profile": "default",
                },
            },
            "permission": {"read": True},
        }
        target.write_text(json.dumps(original))

        result = remove(target)

        assert isinstance(result, Ok), f"expected Ok, got {result!r}"
        with target.open() as f:
            data = json.load(f)
        assert (
            "ms-access" not in data.get("mcp", {})
        ), "mcp.ms-access must be removed"
        assert (
            "engram" in data["mcp"]
        ), "mcp.engram must survive removal"
        assert (
            "permission" in data
        ), "permission key must survive removal"

    def test_remove_missing_key_is_noop(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        import json

        target.write_text(json.dumps({"$schema": "x", "mcp": {}}))

        result = remove(target)

        assert isinstance(result, Ok), f"missing mcp.ms-access should be no-op Ok, got {result!r}"

    def test_remove_missing_file_is_err(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"

        result = remove(target)

        assert isinstance(result, Err), f"missing file should be Err, got {result!r}"


# =============================================================================
# Pure-function unit tests — load, merge_entry, entry_from, timestamped_backup
# =============================================================================


class TestLoad:
    def test_load_parses_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        import json

        path.write_text(json.dumps({"$schema": "x", "mcp": {}}))

        result = load(path)

        assert result == {"$schema": "x", "mcp": {}}

    def test_load_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.json"

        result = load(path)

        assert result == {}


class TestMergeEntry:
    def test_merge_adds_entry_under_key(self) -> None:
        config = {"$schema": "x"}
        entry = McpEntry(command=("python", "-m", "ms"), profile="core")

        result = merge_entry(config, "mcp.ms-access", entry)

        assert "mcp" in result
        assert "ms-access" in result["mcp"]

    def test_merge_preserves_existing_siblings(self) -> None:
        config = {"$schema": "x", "mcp": {"engram": {"command": ("npx",)}}}
        entry = McpEntry(command=("python", "-m", "ms"), profile="core")

        result = merge_entry(config, "mcp.ms-access", entry)

        assert "engram" in result["mcp"]
        assert result["mcp"]["engram"]["command"] == ["npx"]

    def test_merge_overwrites_existing_key(self) -> None:
        config = {
            "mcp": {"ms-access": {"command": ("old",), "profile": "core"}}
        }
        entry = McpEntry(command=("new",), profile="core")

        result = merge_entry(config, "mcp.ms-access", entry)

        # Command stored as list (JSON round-trip compatibility)
        assert result["mcp"]["ms-access"]["command"] == ["new"]


class TestEntryFrom:
    def test_entry_from_produces_correct_shape(self) -> None:
        exe = Path("python")
        entry = entry_from(exe, profile="core")

        assert isinstance(entry, McpEntry)
        assert entry.command == (str(exe), "mcp", "--profile", "core")
        assert entry.profile == "core"
        assert entry.enabled is True
        assert entry.timeout == 30000


class TestTimestampedBackup:
    def test_backup_creates_sibling_file(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        import json

        target.write_text(json.dumps({"x": 1}))

        backup = timestamped_backup(target)

        assert backup.exists(), "backup file must be created"
        assert backup.name.startswith("opencode.json.bak-"), (
            f"backup name must start with opencode.json.bak-, got {backup.name!r}"
        )
        assert backup.read_text() == json.dumps({"x": 1})

    def test_backup_nonexistent_raises(self, tmp_path: Path) -> None:
        target = tmp_path / "nonexistent.json"

        with pytest.raises(FileNotFoundError):
            timestamped_backup(target)


class TestWriteAtomic:
    def test_write_atomic_creates_file(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        config = {"$schema": "x", "mcp": {}}

        result = write_atomic(target, config, backup=False)

        assert result == target
        assert target.exists()
        import json

        assert json.loads(target.read_text()) == config

    def test_write_atomic_with_backup_creates_backup(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        import json

        target.write_text(json.dumps({"original": True}))
        config = {"updated": True}

        result = write_atomic(target, config, backup=True)

        assert result == target
        # Original content must be in backup
        backups = sorted(tmp_path.glob("opencode.json.bak-*"))
        assert len(backups) == 1, f"expected 1 backup, got {backups}"
        assert json.loads(backups[0].read_text()) == {"original": True}

    def test_write_atomic_validator_failure_restores(self, tmp_path: Path) -> None:
        target = tmp_path / "opencode.json"
        import json

        target.write_text(json.dumps({"original": True}))
        original_bytes = target.read_bytes()
        config = {"updated": True}

        with pytest.raises(_ValidationError, match="opencode debug config failed"):
            write_atomic(target, config, backup=True, validator=fake_validatorraiser)

        # Restored
        assert target.read_bytes() == original_bytes


class TestResultType:
    def test_result_is_frozen_dataclass(self) -> None:
        ok = Ok(backup_path=Path("x"), message="ok")
        err = Err(message="fail", backup_path=Path("y"))

        assert isinstance(ok, Ok)
        assert isinstance(err, Err)
        # Frozen: assignment should fail
        with pytest.raises(Exception):  # FrozenInstanceError
            ok.ok = False  # type: ignore[misc]

    def test_ok_has_expected_fields(self) -> None:
        p = Path("backup.json")
        r: Result = Ok(ok=True, backup_path=p, message="success")
        assert r.ok is True
        assert r.backup_path == p
        assert r.message == "success"

    def test_err_has_expected_fields(self) -> None:
        p = Path("backup.json")
        r: Result = Err(ok=False, backup_path=p, message="conflict")
        assert r.ok is False
        assert r.backup_path == p
        assert r.message == "conflict"
