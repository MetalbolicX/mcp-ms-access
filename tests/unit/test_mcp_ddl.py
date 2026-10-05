"""Tests for mcp_tools/ddl.py — DDL + admin profile tool registry, confirm gating."""

from __future__ import annotations

from ms_access_mcp.errors import Err
from ms_access_mcp.mcp_tools import ddl
from ms_access_mcp.profiles import PROFILES, load_profile


def _fake_ddl_uc(monkeypatch) -> dict:
    """Replace _new_db with a recorder; any use-case call returns Err('reached uc')."""
    captured: dict = {}

    class _Recorder:
        def __getattr__(self, name: str):
            def _call(*args, **kwargs):
                captured[name] = {"args": args, "kwargs": kwargs}
                return Err("reached uc")

            return _call

    monkeypatch.setattr(ddl, "_new_db", lambda readonly=True: _Recorder())
    return captured


class TestConnectedPath:
    def test_create_table_dispatches_through_connected_adapter(self, monkeypatch, tmp_path):
        from ms_access_mcp.backends import BackendSelector

        class FakeAdapter:
            connected = False

            def connect(self, path):
                self.connected = True
                self.connected_path = path

            def disconnect(self):
                self.connected = False

            def create_table(self, table_name, columns):
                self.connected_at_dispatch = self.connected
                self.received = (table_name, columns)
                return {"success": True}

        adapter = FakeAdapter()
        monkeypatch.setattr(BackendSelector, "get_adapter", lambda **_kwargs: adapter)
        monkeypatch.setattr(ddl, "allowed_dirs_from_env", lambda: [tmp_path])
        path = str(tmp_path / "db.accdb")
        (tmp_path / "db.accdb").write_bytes(b"fake database")

        result = ddl.create_table(path, params={"table": "T", "columns": {"id": "Long Integer"}}, confirm=True)

        assert result.isError is False
        assert adapter.connected_at_dispatch is True
        assert adapter.connected is False  # per-call session closes after dispatch
        assert adapter.connected_path == path
        assert adapter.received == ("T", [{"name": "id", "type": "Long Integer"}])
        assert '"success": true' in result.content[0].text


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


class TestTypedParamValidation:
    """Linked-table params are pydantic-validated at the boundary.

    The recorder's Err('reached uc') text must NEVER appear — its presence
    means raw junk flowed past the boundary into the use case.
    """

    def test_create_linked_table_rejects_none_name(self, monkeypatch):
        """create_linked_table with name=None gets the typed envelope."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["create_linked_table"]
        result = fn(path="dummy.mdb", params={"name": None, "source_table": "tbl"}, confirm=True)
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "name" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_create_linked_table_rejects_list_connect_string(self, monkeypatch):
        """create_linked_table with connect_string=['evil'] gets the typed envelope."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["create_linked_table"]
        result = fn(
            path="dummy.mdb",
            params={
                "name": "remote_tbl",
                "source_table": "tbl",
                "connect_string": ["evil"],
            },
            confirm=True,
        )
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "connect_string" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_create_linked_table_rejects_int_source_table(self, monkeypatch):
        """create_linked_table with source_table=42 gets the typed envelope."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["create_linked_table"]
        result = fn(path="dummy.mdb", params={"name": "remote_tbl", "source_table": 42}, confirm=True)
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "source_table" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_refresh_linked_table_rejects_none_name(self, monkeypatch):
        """refresh_linked_table with name=None gets the typed envelope."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["refresh_linked_table"]
        result = fn(path="dummy.mdb", params={"name": None}, confirm=True)
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "name" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_refresh_linked_table_rejects_int_connect_string(self, monkeypatch):
        """refresh_linked_table connect_string is str|None — int gets the typed envelope."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["refresh_linked_table"]
        result = fn(path="dummy.mdb", params={"name": "remote_tbl", "connect_string": 99}, confirm=True)
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "connect_string" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_unlink_table_rejects_none_name(self, monkeypatch):
        """unlink_table with name=None gets the typed envelope."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["unlink_table"]
        result = fn(path="dummy.mdb", params={"name": None}, confirm=True)
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "name" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_unlink_table_rejects_list_connection_name(self, monkeypatch):
        """unlink_table with connection_name=['x'] gets the typed envelope."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["unlink_table"]
        result = fn(path="dummy.mdb", params={"name": "remote_tbl", "connection_name": ["x"]}, confirm=True)
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "connection_name" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_upsert_linked_table_rejects_int_connect_ref(self, monkeypatch):
        """upsert_linked_table with connect_ref=77 gets the typed envelope."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["upsert_linked_table"]
        result = fn(
            path="dummy.mdb",
            params={"name": "remote_tbl", "source_table": "tbl", "connect_ref": 77},
            confirm=True,
        )
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "connect_ref" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_upsert_linked_table_rejects_none_name(self, monkeypatch):
        """upsert_linked_table with name=None gets the typed envelope."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["upsert_linked_table"]
        result = fn(path="dummy.mdb", params={"name": None, "source_table": "tbl"}, confirm=True)
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "name" in text
        assert "reached uc" not in text
        assert captured == {}


class TestParsedParamForwarding:
    """Positive controls — valid linked-table params forward with live defaults."""

    def test_create_linked_table_valid_params_forward_defaults(self, monkeypatch):
        """create_linked_table forwards ''/''/'default' defaults for absent keys."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["create_linked_table"]
        fn(path="dummy.mdb", params={"name": "remote_tbl", "source_table": "tbl"}, confirm=True)
        kwargs = captured["create_linked_table"]["kwargs"]
        assert kwargs["name"] == "remote_tbl"
        assert kwargs["source_table"] == "tbl"
        assert kwargs["connect_string"] == ""
        assert kwargs["connect_ref"] == ""
        assert kwargs["connection_name"] == "default"
        assert kwargs["confirm"] is True

    def test_refresh_linked_table_absent_connect_string_forwards_none(self, monkeypatch):
        """refresh_linked_table forwards connect_string=None when the key is absent."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["refresh_linked_table"]
        fn(path="dummy.mdb", params={"name": "remote_tbl"}, confirm=True)
        kwargs = captured["refresh_linked_table"]["kwargs"]
        assert kwargs["name"] == "remote_tbl"
        assert kwargs["connect_string"] is None
        assert kwargs["connection_name"] == "default"

    def test_unlink_table_valid_params_forward_defaults(self, monkeypatch):
        """unlink_table forwards name and the 'default' connection_name."""
        captured = _fake_ddl_uc(monkeypatch)
        fn, _desc = ddl._TOOLS["unlink_table"]
        fn(path="dummy.mdb", params={"name": "remote_tbl"}, confirm=True)
        kwargs = captured["unlink_table"]["kwargs"]
        assert kwargs["name"] == "remote_tbl"
        assert kwargs["connection_name"] == "default"
