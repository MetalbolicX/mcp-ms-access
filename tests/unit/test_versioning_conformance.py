"""Protocol-driven conformance checks for the production AccessAdapter."""

from __future__ import annotations

import inspect

from ms_access_mcp.application.protocols import VersioningAdapterProtocol
from ms_access_mcp.backends import access
from ms_access_mcp.backends.access import AccessAdapter

_KIND_METHODS = {
    "export_form_to_text": ["form_name"],
    "import_form_from_text": ["form_name", "form_data"],
    "export_report_to_text": ["report_name"],
    "import_report_from_text": ["report_name", "report_data"],
    "export_macro_to_text": ["macro_name"],
    "import_macro_from_text": ["macro_name", "text_data"],
    "export_query_to_text": ["query_name"],
    "import_query_from_text": ["query_name", "text_data"],
    "export_module_to_text": ["module_name"],
    "import_module_from_text": ["module_name", "code"],
}


def test_access_adapter_conforms_to_versioning_protocol(monkeypatch):
    """Every protocol and per-kind operation is explicitly exposed with its signature."""
    dispatcher = object()
    monkeypatch.setattr(access, "ComDispatcher", lambda: dispatcher)
    adapter = AccessAdapter("fake.accdb")

    protocol_methods = {
        name: value
        for name, value in vars(VersioningAdapterProtocol).items()
        if callable(value) and not name.startswith("_")
    }
    assert protocol_methods
    for name, protocol_method in protocol_methods.items():
        assert hasattr(adapter, name), f"Missing protocol method: {name}"
        assert list(inspect.signature(getattr(adapter, name)).parameters) == list(
            inspect.signature(protocol_method).parameters
        )[1:], f"Signature mismatch for {name}"

    for name, parameters in _KIND_METHODS.items():
        method = getattr(adapter, name, None)
        assert callable(method), f"Missing kind method: {name}"
        assert list(inspect.signature(method).parameters) == parameters, f"Signature mismatch for {name}"
