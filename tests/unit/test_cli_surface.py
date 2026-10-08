"""Characterization test: pin the complete CLI subcommand surface.

This test programmatically captures the full parser surface (subcommand names,
family membership, count floor) so any structural change to build_parser()
is immediately detected. It must FAIL if any subcommand is added, removed,
or renamed — proving the pin is live.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from ms_access_mcp.cli import build_parser


class TestCLISurface:
    """Pin the complete CLI subcommand surface."""

    def test_top_level_subcommand_count_floor(self) -> None:
        """Assert the total top-level subcommand count is above the floor."""
        parser = build_parser()
        main_subparsers = next(
            a for a in parser._actions
            if getattr(a, "dest", None) == "command"
        )
        top_level_names = sorted(main_subparsers.choices.keys())
        # Plan 061 expects ~90 subparsers across all levels; use 20 as a floor for top-level
        assert len(top_level_names) >= 20, (
            f"Expected ≥20 top-level subcommands, got {len(top_level_names)}: "
            f"{top_level_names}"
        )

    def test_top_level_subcommand_names(self) -> None:
        """Capture the exact sorted list of top-level subcommand names."""
        parser = build_parser()
        main_subparsers = next(
            a for a in parser._actions
            if getattr(a, "dest", None) == "command"
        )
        top_level_names = sorted(main_subparsers.choices.keys())
        # Snapshot — update manually only when the surface is intentionally changed
        EXPECTED = sorted([
            "automation",
            "configure",
            "credentials",
            "db",
            "ddl",
            "delete",
            "doctor",
            "export",
            "indexes",
            "insert",
            "links",
            "maintenance",
            "mcp",
            "props",
            "queries",
            "query",
            "relationships",
            "schema",
            "stats",
            "tables",
            "update",
            "versioning",
        ])
        assert top_level_names == EXPECTED, (
            f"Top-level subcommand set changed.\n"
            f"  Got ({len(top_level_names)}): {top_level_names}\n"
            f"  Expected ({len(EXPECTED)}): {EXPECTED}\n"
            f"  Diff: added={set(top_level_names)-set(EXPECTED)}, "
            f"removed={set(EXPECTED)-set(top_level_names)}"
        )

    def test_family_membership(self) -> None:
        """Pin per-family subcommand membership.

        Families are extracted from the inline blocks in build_parser.
        This test proves each builder must reproduce the same membership.
        """
        parser = build_parser()
        main_subparsers = next(
            a for a in parser._actions
            if getattr(a, "dest", None) == "command"
        )
        choices = main_subparsers.choices

        # Family: db
        assert "db" in choices
        db_sub = choices["db"]
        db_actions = next(
            a for a in db_sub._actions
            if getattr(a, "dest", None) == "db_action"
        )
        assert sorted(db_actions.choices.keys()) == ["active", "close", "list", "open"]

        # Family: credentials (credentials_action contains: set, list, remove, profile)
        assert "credentials" in choices
        cred_sub = choices["credentials"]
        cred_actions = next(
            a for a in cred_sub._actions
            if getattr(a, "dest", None) == "credentials_action"
        )
        assert sorted(cred_actions.choices.keys()) == ["list", "profile", "remove", "set"]

        # Family: credentials > profile (nested under credentials_action's "profile" parser)
        cred_profile_parser = cred_actions.choices["profile"]
        cred_profile_actions = next(
            a for a in cred_profile_parser._actions
            if getattr(a, "dest", None) == "credentials_profile_action"
        )
        assert sorted(cred_profile_actions.choices.keys()) == ["list", "remove", "set"]

        # Family: configure
        assert "configure" in choices
        cfg_sub = choices["configure"]
        cfg_actions = next(
            a for a in cfg_sub._actions
            if getattr(a, "dest", None) == "configure_action"
        )
        assert sorted(cfg_actions.choices.keys()) == ["opencode"]

        # Family: props
        assert "props" in choices
        props_sub = choices["props"]
        props_actions = next(
            a for a in props_sub._actions
            if getattr(a, "dest", None) == "props_action"
        )
        assert sorted(props_actions.choices.keys()) == ["get", "set"]

        # Family: queries
        assert "queries" in choices
        q_sub = choices["queries"]
        q_actions = next(
            a for a in q_sub._actions
            if getattr(a, "dest", None) == "queries_action"
        )
        assert sorted(q_actions.choices.keys()) == ["create", "delete", "list", "set"]

        # Family: ddl
        assert "ddl" in choices
        ddl_sub = choices["ddl"]
        ddl_actions = next(
            a for a in ddl_sub._actions
            if getattr(a, "dest", None) == "ddl_action"
        )
        assert sorted(ddl_actions.choices.keys()) == [
            "alter",
            "create-index",
            "create-table",
            "drop-index",
            "drop-table",
            "relationship",
        ]

        # Family: links
        assert "links" in choices
        links_sub = choices["links"]
        links_actions = next(
            a for a in links_sub._actions
            if getattr(a, "dest", None) == "links_action"
        )
        assert sorted(links_actions.choices.keys()) == ["list"]

        # Family: automation > forms
        assert "automation" in choices
        auto_sub = choices["automation"]
        auto_actions = next(
            a for a in auto_sub._actions
            if getattr(a, "dest", None) == "automation_action"
        )
        assert sorted(auto_actions.choices.keys()) == ["forms", "macros", "reports", "vba"]

        # Access forms via its parser (not via automation_action choices directly)
        forms_parser = auto_actions.choices["forms"]
        forms_sub_actions = next(
            a for a in forms_parser._actions
            if getattr(a, "dest", None) == "forms_action"
        )
        assert sorted(forms_sub_actions.choices.keys()) == [
            "add-control",
            "controls",
            "create",
            "delete",
            "exists",
            "export",
            "import",
            "list",
            "properties",
            "remove-control",
            "rename",
            "sections",
        ]

        # Family: automation > reports
        reports_parser = auto_actions.choices["reports"]
        reports_sub_actions = next(
            a for a in reports_parser._actions
            if getattr(a, "dest", None) == "reports_action"
        )
        assert sorted(reports_sub_actions.choices.keys()) == [
            "add-control",
            "controls",
            "create",
            "delete",
            "exists",
            "export",
            "import",
            "list",
            "properties",
            "remove-control",
            "rename",
            "sections",
        ]

        # Family: automation > macros
        macros_parser = auto_actions.choices["macros"]
        macros_sub_actions = next(
            a for a in macros_parser._actions
            if getattr(a, "dest", None) == "macros_action"
        )
        assert sorted(macros_sub_actions.choices.keys()) == [
            "create",
            "delete",
            "exists",
            "list",
            "properties",
            "rename",
            "run",
        ]

        # Family: automation > vba
        vba_parser = auto_actions.choices["vba"]
        vba_sub_actions = next(
            a for a in vba_parser._actions
            if getattr(a, "dest", None) == "vba_action"
        )
        assert sorted(vba_sub_actions.choices.keys()) == [
            "add-procedure",
            "compile",
            "create-module",
            "delete-module",
            "get-code",
            "get-procedure",
            "list-procedures",
            "module-exists",
            "modules",
            "rename-module",
            "replace-procedure",
            "save",
            "set-code",
        ]

        # Family: versioning
        assert "versioning" in choices
        ver_sub = choices["versioning"]
        ver_actions = next(
            a for a in ver_sub._actions
            if getattr(a, "dest", None) == "versioning_action"
        )
        assert sorted(ver_actions.choices.keys()) == [
            "compare",
            "export-all",
            "export-object",
            "export-schema-ddl",
            "import-all",
            "import-object",
        ]

        # Family: maintenance
        assert "maintenance" in choices
        maint_sub = choices["maintenance"]
        maint_actions = next(
            a for a in maint_sub._actions
            if getattr(a, "dest", None) == "maintenance_action"
        )
        assert sorted(maint_actions.choices.keys()) == [
            "backup",
            "compact-repair",
            "dev-copy-create",
            "dev-copy-deploy",
            "dev-copy-status",
            "diagnose",
            "recover",
        ]

    def test_doctor_parser_flags(self) -> None:
        """Pin doctor family flags."""
        parser = build_parser()
        main_subparsers = next(
            a for a in parser._actions
            if getattr(a, "dest", None) == "command"
        )
        doctor = main_subparsers.choices["doctor"]
        flags = {a.option_strings[0] if a.option_strings else a.dest
                 for a in doctor._actions}
        assert "--json" in flags
        assert "--deep" in flags

    def test_mcp_parser_flags(self) -> None:
        """Pin mcp family flags."""
        parser = build_parser()
        main_subparsers = next(
            a for a in parser._actions
            if getattr(a, "dest", None) == "command"
        )
        mcp = main_subparsers.choices["mcp"]
        flags = {a.option_strings[0] if a.option_strings else a.dest
                 for a in mcp._actions}
        assert "--profile" in flags

    def test_configure_opencode_flags(self) -> None:
        """Pin configure > opencode family flags."""
        parser = build_parser()
        main_subparsers = next(
            a for a in parser._actions
            if getattr(a, "dest", None) == "command"
        )
        cfg = main_subparsers.choices["configure"]
        opencode = next(
            a for a in cfg._actions
            if getattr(a, "dest", None) == "configure_action"
        ).choices["opencode"]
        flags = {a.option_strings[0] if a.option_strings else a.dest
                 for a in opencode._actions}
        assert "--global" in flags
        assert "--remove" in flags
        assert "--force" in flags
        assert "--profile" in flags
        assert "--exe-path" in flags
        assert "--python-mode" in flags

    def test_credentials_set_flags(self) -> None:
        """Pin credentials > set flags."""
        parser = build_parser()
        main_subparsers = next(
            a for a in parser._actions
            if getattr(a, "dest", None) == "command"
        )
        cred = main_subparsers.choices["credentials"]
        cred_action = next(
            a for a in cred._actions
            if getattr(a, "dest", None) == "credentials_action"
        )
        cred_set = cred_action.choices["set"]
        flags = {a.option_strings[0] if a.option_strings else a.dest
                 for a in cred_set._actions}
        assert "--db" in flags
        assert "--local" in flags
        assert "--yes" in flags


class TestMcpProfileHelpContract:
    """Pin the mcp --profile help to the single source of truth (PROFILES)."""

    def test_mcp_profile_help_valid_values_match_profiles(self) -> None:
        """The 'Valid values:' segment must list exactly set(PROFILES)."""
        import re

        from ms_access_mcp.profiles import PROFILES

        parser = build_parser()
        main_subparsers = next(
            a for a in parser._actions if getattr(a, "dest", None) == "command"
        )
        mcp_parser = main_subparsers.choices["mcp"]
        profile_action = next(
            a
            for a in mcp_parser._actions
            if "--profile" in getattr(a, "option_strings", [])
        )
        help_text = profile_action.help or ""
        match = re.search(r"Valid values: (.*?)\. Default:", help_text)
        assert match is not None, f"'Valid values:' segment missing: {help_text!r}"
        listed = {token.strip() for token in match.group(1).split(",")}
        assert listed == set(PROFILES), (
            f"Help lists {sorted(listed)} but PROFILES is {sorted(PROFILES)}"
        )
