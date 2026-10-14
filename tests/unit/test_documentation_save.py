from __future__ import annotations

import hashlib

import pytest

from ms_access_mcp.application.documentation_save import (
    DocumentationSaveService,
    DocumentationWritePolicy,
)
from ms_access_mcp.errors import AccessMcpError


def _service(allowed: bool = True) -> DocumentationSaveService:
    return DocumentationSaveService(policy=DocumentationWritePolicy(allowed=allowed))


def test_default_policy_denies_write(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    with pytest.raises(AccessMcpError, match="permission"):
        DocumentationSaveService().save("report", str(tmp_path / "report.md"), confirm=True)


def test_permission_denied_even_with_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    with pytest.raises(AccessMcpError, match="permission"):
        _service(False).save("report", str(tmp_path / "report.md"), confirm=True)


def test_confirmation_is_required(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    with pytest.raises(AccessMcpError, match="confirm"):
        _service().save("report", str(tmp_path / "report.md"), confirm=False)


def test_path_guard_rejects_parent_outside_allowed_dirs(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(allowed))
    with pytest.raises(AccessMcpError, match="Output path"):
        _service().save("report", str(outside / "report.md"), confirm=True)


@pytest.mark.parametrize("suffix", [".mdb", ".accdb", ".accde", ".mdw", ".mde", ".ACCDB"])
def test_database_destinations_rejected(tmp_path, monkeypatch, suffix):
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    with pytest.raises(AccessMcpError, match="database"):
        _service().save("report", str(tmp_path / f"output{suffix}"), confirm=True)


def test_existing_destination_requires_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    destination = tmp_path / "report.md"
    destination.write_text("old", encoding="utf-8")
    with pytest.raises(AccessMcpError, match="overwrite"):
        _service().save("report", str(destination), confirm=True)


def test_overwrite_requires_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    destination = tmp_path / "report.md"
    destination.write_text("old", encoding="utf-8")
    with pytest.raises(AccessMcpError, match="fingerprint"):
        _service().save("report", str(destination), confirm=True, overwrite=True)


def test_overwrite_rejects_stale_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    destination = tmp_path / "report.md"
    destination.write_text("changed", encoding="utf-8")
    with pytest.raises(AccessMcpError, match="fingerprint"):
        _service().save("report", str(destination), confirm=True, overwrite=True, expected_fingerprint=hashlib.sha256(b"old").hexdigest())


def test_destination_appearing_after_approval_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    import ms_access_mcp.application.documentation_save as save_module

    destination = tmp_path / "report.md"
    original_temp_file = save_module.tempfile.NamedTemporaryFile

    def create_destination_after_preflight(*args, **kwargs):
        temporary = original_temp_file(*args, **kwargs)
        destination.write_text("appeared", encoding="utf-8")
        return temporary

    monkeypatch.setattr(save_module.tempfile, "NamedTemporaryFile", create_destination_after_preflight)
    with pytest.raises(AccessMcpError, match="appeared"):
        _service().save("report", str(destination), confirm=True)


def test_atomic_write_leaves_no_temporary_file_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    import ms_access_mcp.application.documentation_save as save_module

    def fail_replace(_source, _destination):
        raise OSError("forced failure")

    monkeypatch.setattr(save_module.os, "replace", fail_replace)
    with pytest.raises(AccessMcpError, match="save"):
        _service().save("report", str(tmp_path / "report.md"), confirm=True)
    assert list(tmp_path.glob("*.tmp")) == []
    assert not (tmp_path / "report.md").exists()


def test_success_returns_written_bytes_and_sha256(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_MCP_ALLOWED_DIRS", str(tmp_path))
    content = "# Documentation\n"
    result = _service().save(content, str(tmp_path / "report.md"), confirm=True)
    assert result.output_path == str((tmp_path / "report.md").resolve())
    assert result.bytes_written == len(content.encode("utf-8"))
    assert result.sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == content
