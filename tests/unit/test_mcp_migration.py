"""Tests for mcp_tools/migration.py — 4-tool registry, param validation, confirm gating."""

from __future__ import annotations

from ms_access_mcp.errors import Err
from ms_access_mcp.mcp_tools import migration
from ms_access_mcp.profiles import PROFILES, load_profile


def _fake_migration_uc(monkeypatch) -> dict:
    """Replace _new_migration with a recorder; any use-case call returns Err('reached uc')."""
    captured: dict = {}

    class _Recorder:
        def __getattr__(self, name: str):
            def _call(*args, **kwargs):
                captured[name] = {"args": args, "kwargs": kwargs}
                return Err("reached uc")

            return _call

    monkeypatch.setattr(migration, "_new_migration", lambda: _Recorder())
    return captured


class TestToolNames:
    """All 4 migration verbatim oracle names are present in _TOOLS."""

    EXPECTED_TOOLS = {
        "extract_schema",
        "upload_schema",
        "transfer_data",
        "get_migration_status",
    }

    def test_all_tools_registered(self):
        """Every expected oracle name appears in _TOOLS."""
        actual = set(migration._TOOLS.keys())
        missing = self.EXPECTED_TOOLS - actual
        extra = actual - self.EXPECTED_TOOLS
        assert not missing, f"Missing tools: {missing}"
        assert not extra, f"Extra tools: {extra}"

    def test_tool_count(self):
        """Exactly 4 tools are registered."""
        assert len(migration._TOOLS) == 4


class TestParamValidation:
    """Wrapper-level early returns — no connector work triggered."""

    def test_extract_schema_rejects_empty_params(self):
        """extract_schema with {} returns 'target_type is required'."""
        fn, _desc = migration._TOOLS["extract_schema"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True
        assert "target_type is required" in result.content[0].text

    def test_upload_schema_rejects_empty_params(self):
        """upload_schema with {} returns 'target_type is required'."""
        fn, _desc = migration._TOOLS["upload_schema"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True
        assert "target_type is required" in result.content[0].text

    def test_upload_schema_rejects_missing_connection_string(self):
        """upload_schema with target_type but no connection_string returns error."""
        fn, _desc = migration._TOOLS["upload_schema"]
        result = fn(
            path="dummy.mdb",
            params={"target_type": "sqlite"},
            confirm=False,
        )
        assert result.isError is True
        assert "connection_string is required" in result.content[0].text

    def test_transfer_data_rejects_empty_params(self):
        """transfer_data with {} returns 'target_type is required'."""
        fn, _desc = migration._TOOLS["transfer_data"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True
        assert "target_type is required" in result.content[0].text

    def test_upload_schema_rejects_non_string_target_type(self):
        fn, _desc = migration._TOOLS["upload_schema"]
        result = fn(path="dummy.mdb", params={"target_type": 123, "connection_string": "x"}, confirm=True)
        assert result.isError is True
        assert "target_type" in result.content[0].text

    def test_transfer_data_rejects_non_string_connection_string(self):
        fn, _desc = migration._TOOLS["transfer_data"]
        result = fn(path="dummy.mdb", params={"target_type": "sqlite", "connection_string": ["evil"]}, confirm=True)
        assert result.isError is True
        assert "connection_string" in result.content[0].text

    def test_get_migration_status_rejects_empty_params(self):
        """get_migration_status with {} returns 'job_id is required'."""
        fn, _desc = migration._TOOLS["get_migration_status"]
        result = fn(path="dummy.mdb", params={}, confirm=False)
        assert result.isError is True
        assert "job_id is required" in result.content[0].text

    def test_upload_schema_rejects_missing_connection_ref_and_string(self):
        """upload_schema with neither connection_string nor connection_ref returns error."""
        fn, _desc = migration._TOOLS["upload_schema"]
        result = fn(
            path="dummy.mdb",
            params={"target_type": "sqlite"},
            confirm=True,
        )
        assert result.isError is True
        assert "connection_string is required" in result.content[0].text

    def test_transfer_data_rejects_missing_connection_ref_and_string(self):
        """transfer_data with neither connection_string nor connection_ref returns error."""
        fn, _desc = migration._TOOLS["transfer_data"]
        result = fn(
            path="dummy.mdb",
            params={"target_type": "sqlite"},
            confirm=True,
        )
        assert result.isError is True
        assert "connection_string is required" in result.content[0].text


class TestConfirmGates:
    """confirm gate fires before any connector work — safe, no COM dependency."""

    def test_upload_schema_confirm_false_rejects(self):
        """upload_schema with confirm=False returns isError with 'confirm=True'."""
        fn, _desc = migration._TOOLS["upload_schema"]
        result = fn(
            path="dummy.mdb",
            params={
                "target_type": "sqlite",
                "connection_string": "sqlite:///dummy.db",
                "schema_json": {},
            },
            confirm=False,
        )
        assert result.isError is True
        assert "confirm=True" in result.content[0].text

    def test_transfer_data_confirm_false_rejects(self):
        """transfer_data with confirm=False returns isError with 'confirm=True'."""
        fn, _desc = migration._TOOLS["transfer_data"]
        result = fn(
            path="dummy.mdb",
            params={
                "target_type": "sqlite",
                "connection_string": "sqlite:///dummy.db",
                "schema_json": {},
            },
            confirm=False,
        )
        assert result.isError is True
        assert "confirm=True" in result.content[0].text


class TestGetMigrationStatus:
    """get_migration_status hits the job tracker directly."""

    def test_unknown_job_returns_error(self):
        """Unknown job_id returns isError containing 'not found'."""
        fn, _desc = migration._TOOLS["get_migration_status"]
        result = fn(path="dummy.mdb", params={"job_id": "deadbeef"}, confirm=False)
        assert result.isError is True
        assert "not found" in result.content[0].text


class TestConnectionRefPassthrough:
    """connection_ref is forwarded to the use case (step 5)."""

    def test_upload_schema_passes_connection_ref(self, monkeypatch):
        """upload_schema with connection_ref forwards it to uc.upload_schema."""
        captured_args = {}

        class FakeUC:
            def upload_schema(self, **kwargs):
                captured_args.update(kwargs)
                from ms_access_mcp.errors import Err

                return Err("profile not found")

        monkeypatch.setattr(migration, "_new_migration", lambda: FakeUC())
        fn, _desc = migration._TOOLS["upload_schema"]
        fn(path="dummy.mdb", params={"target_type": "sqlite", "connection_ref": "prod_dw"}, confirm=True)
        assert captured_args.get("connection_ref") == "prod_dw", \
            f"connection_ref not forwarded: {captured_args}"

    def test_transfer_data_passes_connection_ref(self, monkeypatch):
        """transfer_data with connection_ref forwards it to uc.transfer_data."""
        captured_args = {}

        class FakeUC:
            def transfer_data(self, **kwargs):
                captured_args.update(kwargs)
                from ms_access_mcp.errors import Err

                return Err("profile not found")

        monkeypatch.setattr(migration, "_new_migration", lambda: FakeUC())
        fn, _desc = migration._TOOLS["transfer_data"]
        fn(path="dummy.mdb", params={"target_type": "sqlite", "connection_ref": "prod_dw"}, confirm=True)
        assert captured_args.get("connection_ref") == "prod_dw", \
            f"connection_ref not forwarded: {captured_args}"

    def test_upload_schema_literal_still_works(self, monkeypatch):
        """upload_schema with literal connection_string still forwards it."""
        captured_args = {}

        class FakeUC:
            def upload_schema(self, **kwargs):
                captured_args.update(kwargs)
                from ms_access_mcp.errors import Err

                return Err("confirm required")

        monkeypatch.setattr(migration, "_new_migration", lambda: FakeUC())
        fn, _desc = migration._TOOLS["upload_schema"]
        fn(path="dummy.mdb", params={
            "target_type": "sqlite",
            "connection_string": "Driver={ODBC};PWD=hunter2;",
        }, confirm=True)
        assert captured_args.get("connection_string") == "Driver={ODBC};PWD=hunter2;", \
            f"connection_string not forwarded: {captured_args}"


class TestProfileWiring:
    """The migration profile is properly registered in profiles.py."""

    def test_migration_in_profiles(self):
        """PROFILES frozenset contains 'migration'."""
        assert "migration" in PROFILES

    def test_load_profile_migration_returns_migration_module(self):
        """load_profile('migration') returns the migration module."""
        modules = load_profile("migration")
        assert "ms_access_mcp.mcp_tools.migration" in modules

    def test_full_profile_includes_migration(self):
        """The 'full' profile includes migration tools."""
        modules = load_profile("full")
        assert "ms_access_mcp.mcp_tools.migration" in modules


class TestTypedParamValidation:
    """Pydantic param models reject type-confused params at the boundary.

    The recorder's Err('reached uc') text must NEVER appear — its presence
    means raw junk flowed past the boundary into the use case.
    """

    def test_extract_schema_rejects_non_string_target_type(self, monkeypatch):
        """extract_schema with target_type=123 gets the typed envelope."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["extract_schema"]
        result = fn(path="dummy.mdb", params={"target_type": 123})
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "target_type" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_extract_schema_rejects_none_target_type(self, monkeypatch):
        """extract_schema with target_type=None gets the typed envelope, not the business one."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["extract_schema"]
        result = fn(path="dummy.mdb", params={"target_type": None})
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_upload_schema_rejects_list_connection_string(self, monkeypatch):
        """upload_schema with connection_string=['evil'] gets the typed envelope."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["upload_schema"]
        result = fn(
            path="dummy.mdb",
            params={"target_type": "sqlite", "connection_string": ["evil"]},
            confirm=True,
        )
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "connection_string" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_upload_schema_typed_envelope_precedes_business_check(self, monkeypatch):
        """Non-string target_type yields the typed envelope BEFORE 'connection_string is required'."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["upload_schema"]
        result = fn(path="dummy.mdb", params={"target_type": 123}, confirm=True)
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "connection_string is required" not in text
        assert captured == {}

    def test_upload_schema_type_valid_missing_connection_keeps_business_check(self, monkeypatch):
        """Type-valid params missing connection_string still get the business envelope."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["upload_schema"]
        result = fn(path="dummy.mdb", params={"target_type": "sqlite"}, confirm=True)
        assert result.isError is True
        text = result.content[0].text
        assert "connection_string is required" in text
        assert "Invalid params" not in text
        assert captured == {}

    def test_transfer_data_typed_envelope_precedes_business_check(self, monkeypatch):
        """Non-string target_type yields the typed envelope BEFORE 'connection_string is required'."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["transfer_data"]
        result = fn(path="dummy.mdb", params={"target_type": 123}, confirm=True)
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "connection_string is required" not in text
        assert captured == {}

    def test_transfer_data_type_valid_missing_connection_keeps_business_check(self, monkeypatch):
        """Type-valid params missing connection_string still get the business envelope."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["transfer_data"]
        result = fn(path="dummy.mdb", params={"target_type": "sqlite"}, confirm=True)
        assert result.isError is True
        text = result.content[0].text
        assert "connection_string is required" in text
        assert "Invalid params" not in text
        assert captured == {}

    def test_transfer_data_rejects_non_dict_schema_json(self, monkeypatch):
        """transfer_data with schema_json as a list gets the typed envelope."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["transfer_data"]
        result = fn(
            path="dummy.mdb",
            params={
                "target_type": "sqlite",
                "connection_string": "x",
                "schema_json": ["not", "a", "dict"],
            },
            confirm=True,
        )
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "schema_json" in text
        assert "reached uc" not in text
        assert captured == {}

    def test_get_migration_status_rejects_non_string_job_id(self, monkeypatch):
        """get_migration_status with job_id=12345 gets the typed envelope."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["get_migration_status"]
        result = fn(path="dummy.mdb", params={"job_id": 12345})
        assert result.isError is True
        text = result.content[0].text
        assert "Invalid params" in text
        assert "job_id" in text
        assert "reached uc" not in text
        assert captured == {}


class TestParsedParamForwarding:
    """Positive controls — valid params reach the use case with live defaults preserved."""

    def test_upload_schema_minimal_valid_params_forward_defaults(self, monkeypatch):
        """upload_schema forwards empty-string/connection_ref and schema_json={} defaults."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["upload_schema"]
        fn(path="dummy.mdb", params={"target_type": "sqlite", "connection_ref": "dw"}, confirm=True)
        kwargs = captured["upload_schema"]["kwargs"]
        assert kwargs["target_type"] == "sqlite"
        assert kwargs["connection_string"] == ""
        assert kwargs["connection_ref"] == "dw"
        assert kwargs["schema_json"] == {}
        assert kwargs["confirm"] is True

    def test_transfer_data_minimal_valid_params_forward_live_defaults(self, monkeypatch):
        """transfer_data forwards live defaults: schema_json=None, auto/full, overrides=None."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["transfer_data"]
        fn(path="dummy.mdb", params={"target_type": "sqlite", "connection_ref": "dw"}, confirm=True)
        kwargs = captured["transfer_data"]["kwargs"]
        assert kwargs["target_type"] == "sqlite"
        assert kwargs["schema_json"] is None
        assert kwargs["transfer_mode"] == "auto"
        assert kwargs["verification_mode"] == "full"
        assert kwargs["table_overrides"] is None
        assert kwargs["database_path"] == "dummy.mdb"

    def test_get_migration_status_valid_job_id_forwards(self, monkeypatch):
        """get_migration_status forwards a valid job_id positionally."""
        captured = _fake_migration_uc(monkeypatch)
        fn, _desc = migration._TOOLS["get_migration_status"]
        fn(path="dummy.mdb", params={"job_id": "abcd1234"})
        assert captured["get_migration_status"]["args"] == ("abcd1234",)
