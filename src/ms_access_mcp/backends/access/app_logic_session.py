"""Production Access.Application read session for documentation app-logic reads.

Win32/COM implementation detail, deliberately kept in the backends layer (the
architecture guard forbids win32com in application/). The application-layer
reader (application/documentation_app_logic.py) consumes this through the
``AccessSession`` protocol and injects fakes in unit tests.
"""
from __future__ import annotations

from typing import Any

from .vba import get_vb_project, list_procedures

VBA_TRUST_PREREQUISITE = (
    "VBProject access requires the operator to enable 'Trust access to the VBA project "
    "object model' in the Access Trust Center; the VBA module catalogue is unavailable"
)


class VbaTrustRefusedError(RuntimeError):
    """The host refuses programmatic VBProject access (operator trust setting)."""


_COMPONENT_TYPES = {1: "Standard", 2: "Class", 3: "Form", 100: "Document"}


class ComAccessLauncher:
    """Dispatch one private, invisible Access instance and wrap it as a session."""

    def launch(self) -> Any:
        """Create the Access.Application instance and return its read session."""
        import win32com.client  # type: ignore[import-not-found]

        application = win32com.client.DispatchEx("Access.Application")
        application.Visible = False
        application.UserControl = False
        return ComAccessSession(application)


class ComAccessSession:
    """The production read-only ``Access.Application`` surface (no writes, no design view)."""

    def __init__(self, application: Any) -> None:
        """Wrap an already-dispatched Access application object."""
        self._application = application
        self._project: Any = None

    def application_version(self) -> str:
        """Return the host Access application version string."""
        return str(self._application.Version)

    def set_automation_security(self, level: int) -> None:
        """Set the automation security level (3 = force-disable)."""
        self._application.AutomationSecurity = level

    def read_automation_security(self) -> int:
        """Read back the effective automation security level."""
        return int(self._application.AutomationSecurity)

    def access_application_pid(self) -> int:
        """Return the PID owning the Access main window handle."""
        return _window_process_id(int(self._application.hWndAccessApp))

    def open_current_database(self, path: str) -> None:
        """Open the given database file in this instance."""
        self._application.OpenCurrentDatabase(path)

    def close_current_database(self) -> None:
        """Close the current database without saving."""
        self._application.CloseCurrentDatabase()

    def quit_application(self) -> None:
        """Quit the Access application gracefully."""
        self._application.Quit()

    def current_project_object_names(self, collection: str) -> list[str]:
        """List object names in a CurrentProject collection (1-based)."""
        items = getattr(self._application.CurrentProject, collection)
        return [str(items[index].Name) for index in range(1, int(items.Count) + 1)]

    def save_as_text(self, object_type: int, name: str, destination: str) -> None:
        """Export one object to a text file via SaveAsText."""
        self._application.SaveAsText(object_type, name, destination)

    def _vb_project(self) -> Any:
        if self._project is None:
            self._project = get_vb_project(self._application)
        if self._project is None:
            raise VbaTrustRefusedError(VBA_TRUST_PREREQUISITE)
        return self._project

    def vba_module_count(self) -> int:
        """Return the number of VBA components in the project."""
        return int(self._vb_project().VBComponents.Count)

    def _component(self, index: int) -> Any:
        return self._vb_project().VBComponents(index)

    def vba_module_name(self, index: int) -> str:
        """Return the name of the VBA component at the 1-based index."""
        return str(self._component(index).Name)

    def vba_module_type(self, index: int) -> str:
        """Return the decoded type name of the VBA component."""
        raw = int(self._component(index).Type)
        return _COMPONENT_TYPES.get(raw, str(raw))

    def vba_procedures(self, index: int) -> list[str]:
        """Return procedure names for the VBA component at the index."""
        name = self.vba_module_name(index)
        return [str(item["name"]) for item in list_procedures(self._application, self._vb_project(), name)]

    def vba_module_source(self, index: int) -> str:
        """Return the full source text of the VBA component."""
        module = self._component(index).CodeModule
        return str(module.Lines(1, module.CountOfLines))

    def reference_count(self) -> int:
        """Return the number of application references."""
        return int(self._application.References.Count)

    def reference_item(self, index: int) -> Any:
        """Return the raw reference object at the 1-based index."""
        return self._application.References(index)

    def reference_name(self, _index: int, item: Any) -> str:
        """Return the reference display name."""
        return str(item.Name)

    def reference_path(self, _index: int, item: Any) -> str:
        """Return the reference full path."""
        return str(item.FullPath)

    def reference_guid(self, _index: int, item: Any) -> str:
        """Return the reference GUID string."""
        return str(item.GUID)

    def reference_major(self, _index: int, item: Any) -> int:
        """Return the reference major version."""
        return int(item.Major)

    def reference_minor(self, _index: int, item: Any) -> int:
        """Return the reference minor version."""
        return int(item.Minor)

    def reference_built_in(self, _index: int, item: Any) -> bool:
        """Return whether the reference is built in."""
        return bool(item.BuiltIn)


def _window_process_id(hwnd: int) -> int:
    """Resolve the PID that owns an Access window handle (``hWndAccessApp``)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)
