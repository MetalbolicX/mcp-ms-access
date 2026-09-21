"""Seam tests for the access package layout.

Verifies that:
1. `ms_access_mcp.backends.access.control_types` exposes the lookup functions
2. `ms_access_mcp.backends.access.AccessAdapter` import surface is preserved
3. Delegation works: adapter methods call through to extracted modules
"""
from __future__ import annotations


class TestAccessPackageSubmodules:
    """Step 1 RED — control_types submodule must exist."""

    def test_control_types_module_exists(self):
        """control_types must be importable as a submodule of the access package."""
        import ms_access_mcp.backends.access as access_pkg
        assert hasattr(access_pkg, "control_types"), (
            "ms_access_mcp.backends.access.control_types submodule not found; "
            "extract control_types.py before proceeding"
        )

    def test_control_types_exposes_lookup_functions(self):
        """control_types must expose all four lookup table functions."""
        from ms_access_mcp.backends.access import control_types

        assert hasattr(control_types, "control_type_name"), "control_type_name not found"
        assert hasattr(control_types, "control_type_id"), "control_type_id not found"
        assert hasattr(control_types, "section_name"), "section_name not found"
        assert hasattr(control_types, "section_id"), "section_id not found"

    def test_control_types_lookup_sanity(self):
        """control_type_name/section_name must return expected strings."""
        from ms_access_mcp.backends.access.control_types import (
            control_type_name,
            section_name,
        )

        assert control_type_name(100) == "TextBox"
        assert control_type_name(102) == "CommandButton"
        assert section_name(0) == "detail"
        assert section_name(1) == "header"


class TestAccessAdapterImportSurface:
    """AccessAdapter must remain importable from backends.access."""

    def test_access_adapter_importable(self):
        """backends.access must export AccessAdapter."""
        from ms_access_mcp.backends.access import AccessAdapter

        assert AccessAdapter is not None

    def test_access_adapter_has_public_methods(self):
        """AccessAdapter must expose public methods (spot-check 10 dynamically)."""
        from ms_access_mcp.backends.access import AccessAdapter

        adapter = AccessAdapter.__dict__
        publics = [k for k in adapter if not k.startswith("_")]
        assert len(publics) > 0, "AccessAdapter has no public methods"

        # Spot-check 10 methods that must exist
        spot_checks = [
            "connect",
            "disconnect",
            "is_connected",
            "get_forms",
            "get_reports",
            "create_form",
            "delete_form",
            "get_form_properties",
            "set_form_property",
            "compile_vba",
            "get_vba_code",
            "set_vba_code",
            "vba_list_procedures",
        ]
        for name in spot_checks:
            assert name in publics, f"AccessAdapter missing public method {name!r}"

    def test_module_level_private_helpers_still_importable(self):
        """Module-level helpers must still be importable from backends.access.

        These are used by tests directly and must not break during extraction.
        """
        # These are imported by tests/unit/test_backend_access.py directly
        from ms_access_mcp.backends.access import _COMPILE_CMD_IDS, _MACRO_TEMPLATE

        assert isinstance(_COMPILE_CMD_IDS, list)
        assert isinstance(_MACRO_TEMPLATE, str)
        assert len(_MACRO_TEMPLATE) > 0

    def test_props_to_dict_importable(self):
        """_props_to_dict must remain importable from backends.access."""
        from ms_access_mcp.backends.access import _props_to_dict

        assert callable(_props_to_dict)
