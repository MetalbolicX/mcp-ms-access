"""Characterization tests for the form/report mirror DoCmd sequences.

Plan 016, Phase 4, Step 12. These tests pin the EXACT observable COM sequence
of the form/report mirror families in ``backends/access.py`` — open verb +
args, live collection accessor, close constant, and the property write —
as of the pre-refactor code. They must pass BEFORE the mirror collapse and
stay green UNCHANGED after it. A failure after the refactor means behavior
changed and the extraction must be reverted, not the test edited.
"""

from typing import Any

import pytest

from ms_access_mcp.backends.access import AccessAdapter


class _FakeDispatcher:
    """Fake dispatcher that invokes _do functions directly for unit testing.

    Same pattern as tests/unit/test_backend_access.py — simulates the
    STA-threaded COM call pattern without requiring win32com.
    """

    def __init__(self, access_app: Any):
        self._started = True
        self._access_app = access_app
        self._access_pid: int | None = None
        self._current_db: Any = None
        self._ado_conn: Any = None
        self._db_path: Any = None

    @property
    def access_app(self):
        return self._access_app

    @property
    def current_db(self):
        return self._current_db

    def start(self):
        self._started = True

    def set_db_path(self, db_path: str) -> None:
        self._db_path = db_path

    def call(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def shutdown(self):
        self._started = False

    def capture_access_pid(self):
        pass


class _TransactionProperty:
    def __init__(self, name, owner):
        self.name = name
        self.owner = owner

    @property
    def Value(self):
        self.owner.events.append(("read", self.name))
        return self.owner.values[self.name]

    @Value.setter
    def Value(self, value):
        self.owner.events.append(("write", self.name, value))
        if value == "FAIL" or (self.owner.fail_restore and value == "old-first"):
            raise RuntimeError("injected property failure")
        self.owner.values[self.name] = value


class _TransactionProperties:
    def __init__(self, owner):
        self.owner = owner

    def __call__(self, name):
        return _TransactionProperty(name, self.owner)


class _TransactionTarget:
    def __init__(self, initial, fail_restore=False):
        self.values = dict(initial)
        self.events = []
        self.fail_restore = fail_restore
        self.Properties = _TransactionProperties(self)


class _TransactionControl(_TransactionTarget):
    Name = "btn1"


class _TransactionLiveObject(_TransactionTarget):
    def __init__(self, fail_restore=False):
        super().__init__({"First": "old-first", "Second": "old-second"}, fail_restore)
        self.control = _TransactionControl(
            {"First": "old-first", "Second": "old-second"}, fail_restore
        )
        self.controls = _FakeControlsColl([self.control])
        self.section = _TransactionTarget(
            {"First": "old-first", "Second": "old-second"}, fail_restore
        )

    @property
    def Controls(self):
        return self.controls

    def Section(self, section_id):
        return self.section


class _FakePropItem:
    """Fake property item exposing .Name and a recording .Value."""

    def __init__(self, name: str, values: dict, writes: list):
        self._name = name
        self._values = values
        self._writes = writes

    @property
    def Name(self):
        return self._name

    @property
    def Value(self):
        return self._values.get(self._name)

    @Value.setter
    def Value(self, val):
        self._values[self._name] = val
        self._writes.append((self._name, val))


class _FakePropertiesColl:
    """Callable + iterable fake Properties collection (COM-style)."""

    def __init__(self, values: dict, writes: list):
        self._values = values
        self._writes = writes

    def __call__(self, name):
        return _FakePropItem(name, self._values, self._writes)

    def __iter__(self):
        for name in list(self._values):
            yield _FakePropItem(name, self._values, self._writes)


class _FakeControl:
    """Fake Access control with Name, Properties and design-view selection."""

    def __init__(self, name: str, initial: dict | None = None):
        self.Name = name
        self.values: dict = dict(initial or {})
        self.writes: list = []
        self.focused = False
        self.InSelection = False

    @property
    def Properties(self):
        return _FakePropertiesColl(self.values, self.writes)

    def SetFocus(self):
        self.focused = True


class _FakeControlsColl:
    """COM-style indexed Controls collection."""

    def __init__(self, controls: list):
        self._controls = controls

    @property
    def Count(self):
        return len(self._controls)

    def __call__(self, i):
        return self._controls[i]


class _FakeSection:
    """Fake report/form section with a recording Properties collection."""

    def __init__(self):
        self.values: dict = {}
        self.writes: list = []

    @property
    def Properties(self):
        return _FakePropertiesColl(self.values, self.writes)


class _FakeLiveObject:
    """Fake open form/report in design view (Controls + Section + Properties)."""

    def __init__(self, control_names=(), control_props: dict | None = None):
        self._controls = [
            _FakeControl(n, (control_props or {}).get(n)) for n in control_names
        ]
        self._sections: dict[int, _FakeSection] = {}
        self.values: dict = {}
        self.writes: list = []

    @property
    def Properties(self):
        return _FakePropertiesColl(self.values, self.writes)

    @property
    def Controls(self):
        return _FakeControlsColl(self._controls)

    def Section(self, section_id: int):
        if section_id not in self._sections:
            self._sections[section_id] = _FakeSection()
        return self._sections[section_id]


class _FakeDoCmd:
    """Recording fake DoCmd: every verb call lands in .calls."""

    def __init__(self):
        self.calls: list = []
        self.created: list = []

    def OpenForm(self, name, view):
        self.calls.append(("OpenForm", name, view))

    def OpenReport(self, name, view):
        self.calls.append(("OpenReport", name, view))

    def Close(self, ac_type, name, save):
        self.calls.append(("Close", ac_type, name, save))

    def RunCommand(self, cmd):
        self.calls.append(("RunCommand", cmd))

    def SelectObject(self, ac_type, name):
        self.calls.append(("SelectObject", ac_type, name))


class _FakeAccessApp:
    """Fake Access application with recording DoCmd and live collections.

    Screen.ActiveForm always raises (no form has focus in the fake), which
    drives the form-side fallback through Forms(name) — the accessor the
    characterization pins. Reports(name) answers directly.
    """

    def __init__(self, live: _FakeLiveObject, live_calls: list):
        self.DoCmd = _FakeDoCmd()
        self._live = live
        self._live_calls = live_calls

    @property
    def Screen(self):
        return self

    @property
    def ActiveForm(self):
        raise RuntimeError("No active form")

    def Forms(self, name):
        self._live_calls.append(("Forms", name))
        return self._live

    def Reports(self, name):
        self._live_calls.append(("Reports", name))
        return self._live

    def CreateControl(self, name, type_int, section):
        # Real COM surface: CreateControl lives on Application, not DoCmd
        # (plan-098 live finding). Recorded into DoCmd.calls so sequence
        # pins keep their original interleaved order.
        self.DoCmd.calls.append(("CreateControl", name, type_int, section))
        ctrl = _FakeControl("")
        self.DoCmd.created.append(ctrl)
        return ctrl


KIND_TOKENS = {
    "form": {"open": "OpenForm", "live": "Forms", "ac": 2},
    "report": {"open": "OpenReport", "live": "Reports", "ac": 3},
}

_SINGLE_PROP = {"form": "set_form_property", "report": "set_report_property"}
_MULTI_PROP = {"form": "set_form_properties", "report": "set_report_properties"}
_GET_CTRL_PROPS = {"form": "get_control_properties", "report": "get_report_control_properties"}
_SET_CTRL_PROP = {"form": "set_control_property", "report": "set_report_control_property"}
_SET_CTRL_PROPS = {"form": "set_control_properties", "report": "set_report_control_properties"}
_ADD_CTRL = {"form": "add_control", "report": "add_report_control"}
_REMOVE_CTRL = {"form": "remove_control", "report": "remove_report_control"}
_SINGLE_SECTION = {"form": "set_form_section_property", "report": "set_report_section_property"}
_MULTI_SECTION = {"form": "set_form_section_properties", "report": "set_report_section_properties"}


def _make_adapter(live: Any):
    """Wire an adapter to a fake dispatcher serving the given live object."""
    adapter = AccessAdapter("test.accdb")
    adapter._connected = True
    live_calls: list = []
    adapter._dispatcher = _FakeDispatcher(_FakeAccessApp(live, live_calls))
    return adapter, live_calls


# -------------------------------------------------------------------------- #
# Family 1: set_{form,report}_property / set_{form,report}_properties
# -------------------------------------------------------------------------- #

class TestSetObjectPropertyMirror:
    """Characterize set_form/set_report_property(ies) DoCmd sequences."""

    @pytest.mark.parametrize("kind", ["form", "report"])
    def test_single_property_success(self, kind):
        tok = KIND_TOKENS[kind]
        live = _FakeLiveObject()
        adapter, live_calls = _make_adapter(live)

        method = getattr(adapter, _SINGLE_PROP[kind])
        result = method("Obj1", "Caption", "Hello")

        assert result is True
        assert adapter._dispatcher.access_app.DoCmd.calls == [
            (tok["open"], "Obj1", 1),
            ("Close", tok["ac"], "Obj1", 1),
        ]
        assert live_calls == [(tok["live"], "Obj1")]
        assert live.writes == [("Caption", "Hello")]

    @pytest.mark.parametrize("kind", ["form", "report"])
    def test_multi_properties_success(self, kind):
        tok = KIND_TOKENS[kind]
        live = _FakeLiveObject()
        adapter, live_calls = _make_adapter(live)

        method = getattr(adapter, _MULTI_PROP[kind])
        result = method("Obj1", {"Caption": "A", "RecordSource": "T"})

        expected = {"Caption": True, "RecordSource": True}
        if kind == "form":
            expected["rolled_back"] = False
        assert result == expected
        expected_calls = [(tok["open"], "Obj1", 1), ("Close", tok["ac"], "Obj1", 1)]
        if kind == "report":
            expected_calls *= 2
        assert adapter._dispatcher.access_app.DoCmd.calls == expected_calls
        assert live_calls == [(tok["live"], "Obj1")] * (1 if kind == "form" else 2)
        assert live.writes == [("Caption", "A"), ("RecordSource", "T")]


# -------------------------------------------------------------------------- #
# Family 2: get/set_{,report_}control_property(ies)
# -------------------------------------------------------------------------- #

class TestControlPropertyMirror:
    """Characterize control-property getter/setter DoCmd sequences."""

    @pytest.mark.parametrize("kind", ["form", "report"])
    def test_get_control_properties(self, kind):
        tok = KIND_TOKENS[kind]
        live = _FakeLiveObject(["btn1"], {"btn1": {"Caption": "Go"}})
        adapter, live_calls = _make_adapter(live)

        method = getattr(adapter, _GET_CTRL_PROPS[kind])
        result = method("Obj1", "btn1")

        assert result == {"Caption": "Go"}
        assert adapter._dispatcher.access_app.DoCmd.calls == [
            (tok["open"], "Obj1", 1),
            ("Close", tok["ac"], "Obj1", 2),
        ]
        assert live_calls == [(tok["live"], "Obj1")]

    @pytest.mark.parametrize("kind", ["form", "report"])
    def test_set_control_property(self, kind):
        tok = KIND_TOKENS[kind]
        live = _FakeLiveObject(["btn1"])
        adapter, live_calls = _make_adapter(live)

        method = getattr(adapter, _SET_CTRL_PROP[kind])
        result = method("Obj1", "btn1", "Caption", "Blue")

        assert result is True
        assert adapter._dispatcher.access_app.DoCmd.calls == [
            (tok["open"], "Obj1", 1),
            ("Close", tok["ac"], "Obj1", 1),
        ]
        assert live_calls == [(tok["live"], "Obj1")]
        assert live._controls[0].writes == [("Caption", "Blue")]

    @pytest.mark.parametrize("kind", ["form", "report"])
    def test_set_control_properties(self, kind):
        tok = KIND_TOKENS[kind]
        live = _FakeLiveObject(["btn1"])
        adapter, live_calls = _make_adapter(live)

        method = getattr(adapter, _SET_CTRL_PROPS[kind])
        result = method("Obj1", "btn1", {"Caption": "A", "Visible": True})

        expected = {"Caption": True, "Visible": True}
        if kind == "form":
            expected["rolled_back"] = False
        assert result == expected
        expected_calls = [(tok["open"], "Obj1", 1), ("Close", tok["ac"], "Obj1", 1)]
        if kind == "report":
            expected_calls *= 2
        assert adapter._dispatcher.access_app.DoCmd.calls == expected_calls
        assert live._controls[0].writes == [("Caption", "A"), ("Visible", True)]


# -------------------------------------------------------------------------- #
# Family 3: {add,remove}_{,report_}control
# -------------------------------------------------------------------------- #

class TestAddRemoveControlMirror:
    """Characterize add/remove control DoCmd sequences."""

    @pytest.mark.parametrize("kind", ["form", "report"])
    def test_add_control(self, kind):
        tok = KIND_TOKENS[kind]
        live = _FakeLiveObject()
        adapter, live_calls = _make_adapter(live)

        method = getattr(adapter, _ADD_CTRL[kind])
        result = method("Obj1", "TextBox", "btnNew", section=0,
                        properties={"Caption": "New"})

        assert result is True
        created = adapter._dispatcher.access_app.DoCmd.created
        assert len(created) == 1
        assert created[0].Name == "btnNew"
        assert created[0].writes == [("Caption", "New")]
        # Plan 035: helper closes exactly once with acSaveYes (was: double-close).
        assert adapter._dispatcher.access_app.DoCmd.calls == [
            (tok["open"], "Obj1", 1),
            ("CreateControl", "Obj1", 109, 0),
            ("Close", tok["ac"], "Obj1", 1),
        ]
        # Plan 035: helper performs one _live_object lookup per design-view
        # invocation regardless of whether work() consumes it; the
        # Application-level CreateControl still runs directly. The single
        # live-call here is harmless.
        assert live_calls == [(tok["live"], "Obj1")]

    @pytest.mark.parametrize("kind", ["form", "report"])
    def test_remove_control_found(self, kind):
        tok = KIND_TOKENS[kind]
        live = _FakeLiveObject(["btn1"])
        adapter, live_calls = _make_adapter(live)

        method = getattr(adapter, _REMOVE_CTRL[kind])
        result = method("Obj1", "btn1")

        assert result is True
        assert live._controls[0].InSelection is True
        # Removal must persist the deleted control (plan-098 live recipe:
        # select window, select control, acCmdDelete=337 — the old pin
        # recorded SetFocus + RunCommand(365), which is HidePane live).
        assert adapter._dispatcher.access_app.DoCmd.calls == [
            (tok["open"], "Obj1", 1),
            ("SelectObject", tok["ac"], "Obj1"),
            ("RunCommand", 337),
            ("Close", tok["ac"], "Obj1", 1),
        ]
        assert live_calls == [(tok["live"], "Obj1")]


# -------------------------------------------------------------------------- #
# Family 4: set_{form,report}_section_property(ies)
# -------------------------------------------------------------------------- #

class TestSetSectionPropertyMirror:
    """Characterize set_{form,report}_section_property(ies) DoCmd sequences."""

    @pytest.mark.parametrize("kind", ["form", "report"])
    def test_single_section_property(self, kind):
        tok = KIND_TOKENS[kind]
        live = _FakeLiveObject()
        adapter, live_calls = _make_adapter(live)

        method = getattr(adapter, _SINGLE_SECTION[kind])
        result = method("Obj1", 0, "Height", "1000")

        assert result is True
        section = live.Section(0)
        assert section.writes == [("Height", "1000")]
        # Plan 035: helper closes exactly once with acSaveYes (was: double-close).
        assert adapter._dispatcher.access_app.DoCmd.calls == [
            (tok["open"], "Obj1", 1),
            ("Close", tok["ac"], "Obj1", 1),
        ]
        assert live_calls == [(tok["live"], "Obj1")]

    @pytest.mark.parametrize("kind", ["form", "report"])
    def test_multi_section_properties(self, kind):
        tok = KIND_TOKENS[kind]
        live = _FakeLiveObject()
        adapter, live_calls = _make_adapter(live)

        method = getattr(adapter, _MULTI_SECTION[kind])
        result = method("Obj1", 0, {"Height": "1000", "Visible": True})

        expected = {"Height": True, "Visible": True}
        if kind == "form":
            expected["rolled_back"] = False
        assert result == expected
        section = live.Section(0)
        assert section.writes == [("Height", "1000"), ("Visible", True)]
        expected_calls = [(tok["open"], "Obj1", 1), ("Close", tok["ac"], "Obj1", 1)]
        if kind == "report":
            expected_calls *= 2
        assert adapter._dispatcher.access_app.DoCmd.calls == expected_calls


@pytest.mark.parametrize("batch_kind", ["properties", "control", "section"])
@pytest.mark.parametrize("fail_restore", [False, True])
def test_batch_property_failure_restores_snapshot(batch_kind, fail_restore, caplog):
    live = _TransactionLiveObject(fail_restore=fail_restore)
    adapter, _ = _make_adapter(live)
    if batch_kind == "properties":
        result = adapter.set_form_properties(
            "Obj1", {"First": "new-first", "Second": "FAIL"}
        )
        target = live
    elif batch_kind == "control":
        result = adapter.set_control_properties(
            "Obj1", "btn1", {"First": "new-first", "Second": "FAIL"}
        )
        target = live.control
    else:
        result = adapter.set_form_section_properties(
            "Obj1", 0, {"First": "new-first", "Second": "FAIL"}
        )
        target = live.section

    assert result == {"First": True, "Second": False, "rolled_back": True}
    assert target.events[:2] == [("read", "First"), ("read", "Second")]
    assert ("write", "First", "old-first") in target.events
    close = [call for call in adapter._dispatcher.access_app.DoCmd.calls if call[0] == "Close"]
    assert close == [("Close", 2, "Obj1", 2 if fail_restore else 1)]
    if fail_restore:
        assert "restore" in caplog.text.lower()


@pytest.mark.parametrize("batch_kind", ["properties", "control", "section"])
def test_batch_property_success_does_not_restore(batch_kind):
    live = _TransactionLiveObject()
    adapter, _ = _make_adapter(live)
    if batch_kind == "properties":
        result = adapter.set_form_properties(
            "Obj1", {"First": "new-first", "Second": "new-second"}
        )
        target = live
    elif batch_kind == "control":
        result = adapter.set_control_properties(
            "Obj1", "btn1", {"First": "new-first", "Second": "new-second"}
        )
        target = live.control
    else:
        result = adapter.set_form_section_properties(
            "Obj1", 0, {"First": "new-first", "Second": "new-second"}
        )
        target = live.section

    assert result == {"First": True, "Second": True, "rolled_back": False}
    assert not any(event[0] == "write" and event[2].startswith("old-") for event in target.events)
