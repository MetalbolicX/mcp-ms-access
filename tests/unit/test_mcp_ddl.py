"""Tests for mcp_tools/ddl.py — DDL + admin profile tool registry, confirm gating."""

from __future__ import annotations

from ms_access_mcp.mcp_tools import ddl
from ms_access_mcp.profiles import PROFILES, load_profile


class TestToolNames:
    """All 16 DDL/admin verbatim oracle names are present in _TOOLS."""

    EXPECTED_TOOLS = {
        # 7 DDL tools (use cases already at database.py:573-782)
        "create_table",
        "alter_table",
        "delete_table",
        "create_index",
        "drop_index",
        "create_relationship",
        "delete_relationship",
        # 4 linked-table tools (DAO backend at dao.py:713,735,753)
        "create_linked_table",
        "refresh_linked_table",
        "unlink_table",
        "upsert_linked_table",
        # 5 admin tools
        "create_access_database",
        "get_database_properties",
        "set_database_property",
        "get_object_metadata",
        "is_connected",
        # Script execution (plan 025)
        "execute_sql_script",
    }

    def test_all_tools_registered(self):
        """Every expected oracle name appears in _TOOLS."""
        actual = set(ddl._TOOLS.keys())
        missing = self.EXPECTED_TOOLS - actual
        extra = actual - self.EXPECTED_TOOLS
        assert not missing, f"Missing tools: {missing}"
        assert not extra, f"Extra tools: {extra}"


class TestConfirmGating:
    """Write tools require confirm=True or return isError=True."""

    WRITE_TOOLS = {
        "create_table",
        "alter_table",
        "delete_table",
        "create_index",
        "drop_index",
        "create_relationship",
        "delete_relationship",
        "set_database_property",
        "create_linked_table",
        "refresh_linked_table",
        "unlink_table",
        "upsert_linked_table",
        "create_access_database",
        "execute_sql_script",
    }

    def _call_without_confirm(self, name: str):
        """Call the tool function with no confirm arg; return the result."""
        fn, _desc = ddl._TOOLS[name]
        # Tools always have (path, params, confirm) signature
        return fn(path="dummy.mdb", params={}, confirm=False)

    def test_write_tools_reject_without_confirm(self):
        """Each write tool returns isError=True when confirm=False."""
        for name in self.WRITE_TOOLS:
            result = self._call_without_confirm(name)
            assert result.isError is True, f"{name} should reject without confirm"


class TestConnectRefPassthrough:
    """connect_ref is forwarded to the use case for linked-table tools (step 5)."""

    def test_create_linked_table_passes_connect_ref(self, monkeypatch):
        """create_linked_table with connect_ref forwards it to uc.create_linked_table."""
        captured_args = {}

        class FakeDB:
            def create_linked_table(self, **kwargs):
                captured_args.update(kwargs)
                from ms_access_mcp.errors import Err

                return Err("not implemented")

        monkeypatch.setattr(ddl, "_new_db", lambda readonly=True: FakeDB())
        fn, _desc = ddl._TOOLS["create_linked_table"]
        fn(path="dummy.mdb", params={
            "name": "remote_tbl",
            "source_table": "tbl",
            "connect_ref": "prod_dw",
        }, confirm=True)
        assert captured_args.get("connect_ref") == "prod_dw", \
            f"connect_ref not forwarded: {captured_args}"

    def test_refresh_linked_table_passes_connect_ref(self, monkeypatch):
        """refresh_linked_table with connect_ref forwards it to uc.refresh_linked_table."""
        captured_args = {}

        class FakeDB:
            def refresh_linked_table(self, **kwargs):
                captured_args.update(kwargs)
                from ms_access_mcp.errors import Err

                return Err("not implemented")

        monkeypatch.setattr(ddl, "_new_db", lambda readonly=True: FakeDB())
        fn, _desc = ddl._TOOLS["refresh_linked_table"]
        fn(path="dummy.mdb", params={
            "name": "remote_tbl",
            "connect_ref": "prod_dw",
        }, confirm=True)
        assert captured_args.get("connect_ref") == "prod_dw", \
            f"connect_ref not forwarded: {captured_args}"

    def test_upsert_linked_table_passes_connect_ref(self, monkeypatch):
        """upsert_linked_table with connect_ref forwards it to uc.upsert_linked_table."""
        captured_args = {}

        class FakeDB:
            def upsert_linked_table(self, **kwargs):
                captured_args.update(kwargs)
                from ms_access_mcp.errors import Err

                return Err("not implemented")

        monkeypatch.setattr(ddl, "_new_db", lambda readonly=True: FakeDB())
        fn, _desc = ddl._TOOLS["upsert_linked_table"]
        fn(path="dummy.mdb", params={
            "name": "remote_tbl",
            "source_table": "tbl",
            "connect_ref": "prod_dw",
        }, confirm=True)
        assert captured_args.get("connect_ref") == "prod_dw", \
            f"connect_ref not forwarded: {captured_args}"

    def test_create_linked_table_literal_still_works(self, monkeypatch):
        """create_linked_table with literal connect_string still forwards it."""
        captured_args = {}

        class FakeDB:
            def create_linked_table(self, **kwargs):
                captured_args.update(kwargs)
                from ms_access_mcp.errors import Err

                return Err("not implemented")

        monkeypatch.setattr(ddl, "_new_db", lambda readonly=True: FakeDB())
        fn, _desc = ddl._TOOLS["create_linked_table"]
        fn(path="dummy.mdb", params={
            "name": "remote_tbl",
            "source_table": "tbl",
            "connect_string": "Driver={ODBC Driver 18};PWD=hunter2;",
        }, confirm=True)
        assert captured_args.get("connect_string") == "Driver={ODBC Driver 18};PWD=hunter2;", \
            f"connect_string not forwarded: {captured_args}"


class TestProfileWiring:
    """The ddl profile is properly registered in profiles.py."""

    def test_ddl_in_profiles(self):
        """PROFILES frozenset contains 'ddl'."""
        assert "ddl" in PROFILES

    def test_load_profile_ddl_returns_ddl_module(self):
        """load_profile('ddl') returns the ddl module."""
        modules = load_profile("ddl")
        assert "ms_access_mcp.mcp_tools.ddl" in modules

    def test_full_profile_includes_ddl(self):
        """The 'full' profile includes all ddl tools."""
        modules = load_profile("full")
        assert "ms_access_mcp.mcp_tools.ddl" in modules
