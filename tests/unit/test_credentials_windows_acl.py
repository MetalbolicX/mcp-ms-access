"""Windows credential stores must have a protected, restricted DACL."""

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
    allowed_sids = (
        owner,
        win32security.ConvertStringSidToSid("S-1-5-18"),
        win32security.ConvertStringSidToSid("S-1-5-32-544"),
    )

    assert dacl is not None
    for index in range(dacl.GetAceCount()):
        (ace_type, _ace_flags), access_mask, trustee = dacl.GetAce(index)
        assert ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE
        assert trustee in allowed_sids
        assert access_mask > 0

    control, _revision = descriptor.GetSecurityDescriptorControl()
    assert control & win32security.SE_DACL_PROTECTED


def test_write_entry_hardens_store_acl(tmp_path):
    database = tmp_path / "database.accdb"
    store = tmp_path / ".ms-access-mcp.json"

    credentials.write_entry(str(database), "secret", local=True)

    _assert_owner_only_acl(store)
