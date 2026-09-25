"""Windows credential stores must have a protected, restricted DACL."""

import json
import os

import pytest

from ms_access_mcp import credentials

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")


def _assert_owner_only_acl(path):
    import win32security

    descriptor = win32security.GetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    dacl = descriptor.GetSecurityDescriptorDacl()
    owner = descriptor.GetSecurityDescriptorOwner()
    allowed_sids = {
        win32security.ConvertSidToStringSid(owner),
        "S-1-5-18",
        "S-1-5-32-544",
    }

    assert dacl is not None
    for index in range(dacl.GetAceCount()):
        (ace_type, _ace_flags), access_mask, trustee = dacl.GetAce(index)
        assert ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE
        assert win32security.ConvertSidToStringSid(trustee) in allowed_sids
        assert access_mask == 2032127

    control, _revision = descriptor.GetSecurityDescriptorControl()
    assert control & win32security.SE_DACL_PROTECTED


def test_write_entry_hardens_store_acl(tmp_path):
    database = tmp_path / "database.accdb"
    store = tmp_path / ".ms-access-mcp.json"

    credentials.write_entry(str(database), "secret", local=True)

    _assert_owner_only_acl(store)


@pytest.mark.parametrize("writer", ["remove_entry", "write_profile", "remove_profile"])
def test_all_store_writers_harden_acl(writer, monkeypatch, tmp_path):
    global_store = tmp_path / "credentials.json"
    monkeypatch.setenv("ACCESS_MCP_CREDENTIALS_FILE", str(global_store))
    database = tmp_path / "database.accdb"

    if writer == "remove_entry":
        store = tmp_path / ".ms-access-mcp.json"
        # Use the local store's profile section to keep the file after removal.
        store.write_text(
            json.dumps(
                {
                    "version": 2,
                    "credentials": {credentials._normalize_path(str(database)): "secret"},
                    "profiles": {"keep": "Driver={ODBC};"},
                }
            ),
            encoding="utf-8",
        )
        credentials.remove_entry(str(database), local=True)
    else:
        credentials.write_profile("target", "Driver={ODBC};")
        store = global_store
        if writer == "write_profile":
            credentials.write_profile("target", "Driver={ODBC};Server=updated;")
        else:
            credentials.remove_profile("target")

    _assert_owner_only_acl(store)
