"""VBA project access classification tests (S2).

Every VBE/VBProjects boundary here is fake: no ``Access.Application`` is created, no COM
call runs, and pywin32 is never imported. The fake failures are duck-typed COM failures
(a direct ``hresult``, a status only in ``args[0]``, and a nested ``excepinfo`` status) so
the classification sees exactly the shapes pywin32 produces. A positively recognized
trust refusal (``0x800A17B4``) may be reported as unavailable with its prerequisite; an
unknown COM status, a corrupt/untrusted failure, a localized message alone, and an empty
project list must surface as real failures and never as an invented trust diagnosis.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ms_access_mcp.application.documentation_app_logic import FAMILY_SCOPE_NAME, AppLogicReader
from ms_access_mcp.application.process_identity import AccessProcessIdentity
from ms_access_mcp.backends.access.app_logic_session import (
    VBA_TRUST_PREREQUISITE,
    ComAccessSession,
    VbaTrustRefusedError,
)
from ms_access_mcp.backends.access.vba import (
    VbaProjectUnavailableError,
    failing_hresults,
    get_vb_project,
)

TRUST_HRESULT = 0x800A17B4
SIGNED_TRUST_HRESULT = TRUST_HRESULT - 0x1_0000_0000
DISP_E_EXCEPTION = 0x80020009
UNKNOWN_COM_HRESULT = 0x8000FFFF
LOCALIZED_TRUST_TEXT = "Programmatic access to Visual Basic Project is denied."

PID = 4242
CREATION = 133_456_789_012_345_678


class _ComError(Exception):
    """A duck-typed pywin32 ``com_error``: status, message, ``excepinfo``, ``argerror``."""

    def __init__(self, hresult: int, message: str = "COM failure", scode: int | None = None) -> None:
        excepinfo = (0, None, message, None, None, scode, None) if scode is not None else None
        super().__init__(hresult, message, excepinfo, None)
        self.hresult = hresult
        self.excepinfo = excepinfo


class _ArgsOnlyComError(Exception):
    """A COM failure that exposes its status only through ``args``."""


class _FakeCollection:
    """A minimal COM collection with a ``Count``."""

    def __init__(self, items: list[Any], *, one_based: bool = False) -> None:
        self._items = items
        self._one_based = one_based

    @property
    def Count(self) -> int:  # noqa: N802 - COM shape
        return len(self._items)

    def __getitem__(self, index: int) -> Any:
        return self._items[index - 1] if self._one_based else self._items[index]

    def __call__(self, index: int) -> Any:
        # pywin32 dynamic dispatch exposes a collection's default member as a call.
        return self[index]


class _FakeCodeModule:
    """The one ``CodeModule`` member ``vba_module_source`` reads."""

    CountOfLines = 3

    def Lines(self, _start: int, _count: int) -> str:  # noqa: N802 - COM shape
        return "Sub Main()\nEnd Sub\n"


class _FakeComponent:
    """A standard VBA component."""

    def __init__(self, name: str) -> None:
        self.Name = name
        self.Type = 1
        self.CodeModule = _FakeCodeModule()


class _FakeVBProject:
    """A VBProject whose ``VBComponents`` collection is 1-based."""

    def __init__(self, names: list[str]) -> None:
        self.Name = "VBAProject"
        self.VBComponents = _FakeCollection([_FakeComponent(name) for name in names], one_based=True)


class _FakeProject:
    """``CurrentProject`` with one form and otherwise empty collections."""

    def __init__(self) -> None:
        self._collections = {"AllForms": [SimpleNamespace(Name="frmMain")]}

    def __getattr__(self, collection: str) -> _FakeCollection:
        return _FakeCollection(self._collections.get(collection, []))


class _FailingVBProjects:
    """A ``VBProjects`` collection whose ``Count`` or item read raises.

    This is the second strict-path boundary: ``VBE`` itself is readable, but the project
    enumeration fails. Real hosts raise here too (a locked or corrupt project store), so
    the classification must cover it and never emit the default "unavailable" wording.
    """

    def __init__(self, error: BaseException, *, fail_on_item: bool = False) -> None:
        self._error = error
        self._fail_on_item = fail_on_item
        self._items = [SimpleNamespace(Name="VBAProject")]

    @property
    def Count(self) -> int:  # noqa: N802 - COM shape
        if self._fail_on_item:
            return len(self._items)
        raise self._error

    def __call__(self, index: int) -> Any:
        # pywin32 dynamic dispatch exposes a collection's default member as a call.
        if self._fail_on_item:
            raise self._error
        return self._items[index - 1]


class _FakeApplication:
    """A fake ``Access.Application`` whose VBE read fails or yields one project."""

    def __init__(
        self,
        *,
        vbe_error: BaseException | None = None,
        project: Any = None,
        vbprojects_error: BaseException | None = None,
        vbprojects_error_on_item: bool = False,
    ) -> None:
        self.Version = "16.0"
        self.AutomationSecurity = 1
        self.CurrentProject = _FakeProject()
        self.References = _FakeCollection([])
        self._vbe_error = vbe_error
        self._vb_project = project
        self._vbprojects_error = vbprojects_error
        self._vbprojects_error_on_item = vbprojects_error_on_item
        self.vbe_reads = 0
        self.opened_paths: list[str] = []
        self.closed = False
        self.quit_called = False

    @property
    def VBE(self) -> Any:  # noqa: N802 - COM shape
        self.vbe_reads += 1
        if self._vbe_error is not None:
            raise self._vbe_error
        if self._vbprojects_error is not None:
            return SimpleNamespace(
                VBProjects=_FailingVBProjects(
                    self._vbprojects_error, fail_on_item=self._vbprojects_error_on_item
                )
            )
        projects = [] if self._vb_project is None else [self._vb_project]
        return SimpleNamespace(VBProjects=_FakeCollection(projects, one_based=True))

    def OpenCurrentDatabase(self, path: str) -> None:  # noqa: N802 - COM shape
        self.opened_paths.append(path)

    def CloseCurrentDatabase(self) -> None:  # noqa: N802 - COM shape
        self.closed = True

    def Quit(self) -> None:  # noqa: N802 - COM shape
        self.quit_called = True

    def SaveAsText(self, _object_type: int, _name: str, destination: str) -> None:  # noqa: N802 - COM shape
        Path(destination).write_bytes("FORM frmMain\r\nEnd\r\n".encode("utf-16-le"))


_TRUST_REFUSALS = [
    pytest.param(lambda: _ComError(TRUST_HRESULT), id="direct-unsigned"),
    pytest.param(lambda: _ComError(SIGNED_TRUST_HRESULT), id="direct-signed"),
    pytest.param(lambda: _ArgsOnlyComError(SIGNED_TRUST_HRESULT, "trust denied"), id="args0-signed"),
    pytest.param(lambda: _ArgsOnlyComError(TRUST_HRESULT, "trust denied"), id="args0-unsigned"),
    pytest.param(lambda: _ComError(DISP_E_EXCEPTION, LOCALIZED_TRUST_TEXT, TRUST_HRESULT), id="nested-unsigned"),
    pytest.param(
        lambda: _ComError(DISP_E_EXCEPTION, LOCALIZED_TRUST_TEXT, SIGNED_TRUST_HRESULT), id="nested-signed"
    ),
]

_UNKNOWN_FAILURES = [
    pytest.param(lambda: _ComError(UNKNOWN_COM_HRESULT), id="direct-unknown"),
    pytest.param(lambda: _ComError(DISP_E_EXCEPTION, "catastrophic failure", UNKNOWN_COM_HRESULT), id="nested-unknown"),
    pytest.param(lambda: RuntimeError("the disposable copy is corrupt"), id="plain-runtime-error"),
    pytest.param(
        lambda: _ComError(0x80004005, LOCALIZED_TRUST_TEXT), id="localized-message-without-the-code"
    ),
]


@pytest.mark.parametrize("make_error", _TRUST_REFUSALS)
def test_strict_get_vb_project_recognizes_the_trust_refusal(make_error: Any) -> None:
    """S2: the documented 0x800A17B4 refusal is the only positive trust signal."""
    error = make_error()

    with pytest.raises(VbaTrustRefusedError) as raised:
        get_vb_project(_FakeApplication(vbe_error=error), strict=True)

    assert raised.value.__cause__ is error
    assert VBA_TRUST_PREREQUISITE in str(raised.value)


@pytest.mark.parametrize("make_error", _UNKNOWN_FAILURES)
def test_strict_get_vb_project_never_diagnoses_trust_for_an_unknown_failure(make_error: Any) -> None:
    """S2: corruption, unknown COM status and message text are never a trust refusal."""
    error = make_error()

    with pytest.raises(VbaProjectUnavailableError) as raised:
        get_vb_project(_FakeApplication(vbe_error=error), strict=True)

    assert raised.value.__cause__ is error
    assert VBA_TRUST_PREREQUISITE not in str(raised.value)


def test_strict_get_vb_project_rejects_an_empty_project_list() -> None:
    """S2: no readable project is a real failure, never an invented trust diagnosis."""
    with pytest.raises(VbaProjectUnavailableError) as raised:
        get_vb_project(_FakeApplication(project=None), strict=True)

    assert "no VBA project" in str(raised.value)
    assert VBA_TRUST_PREREQUISITE not in str(raised.value)


def test_default_get_vb_project_still_swallows_and_logs_the_vbe_failure(caplog: pytest.LogCaptureFixture) -> None:
    """PRESERVE: the shared helper's default callers keep the documented swallow."""
    with caplog.at_level(logging.DEBUG):
        assert get_vb_project(_FakeApplication(vbe_error=_ComError(TRUST_HRESULT))) is None

    debug_messages = [record.message for record in caplog.records if record.levelno == logging.DEBUG]
    assert any("vb project" in message.lower() for message in debug_messages)


def test_strict_get_vb_project_does_not_log_the_default_unavailable_wording(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S3 H3: strict mode raises instead of logging the default "unavailable" DEBUG line."""
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(VbaProjectUnavailableError):
            get_vb_project(_FakeApplication(vbe_error=_ComError(UNKNOWN_COM_HRESULT)), strict=True)

    debug_messages = [record.message for record in caplog.records if record.levelno == logging.DEBUG]
    assert debug_messages == []


@pytest.mark.parametrize(
    ("fail_on_item", "make_error", "expected", "expected_text"),
    [
        pytest.param(False, lambda: _ComError(TRUST_HRESULT), VbaTrustRefusedError, "Trust access", id="count-trust"),
        pytest.param(
            False, lambda: _ComError(UNKNOWN_COM_HRESULT), VbaProjectUnavailableError, "0x8000FFFF", id="count-unknown"
        ),
        pytest.param(True, lambda: _ComError(TRUST_HRESULT), VbaTrustRefusedError, "Trust access", id="item-trust"),
        pytest.param(
            True, lambda: _ComError(UNKNOWN_COM_HRESULT), VbaProjectUnavailableError, "0x8000FFFF", id="item-unknown"
        ),
    ],
)
def test_strict_get_vb_project_classifies_a_vbprojects_enumeration_failure(
    caplog: pytest.LogCaptureFixture,
    fail_on_item: bool,
    make_error: Any,
    expected: type[Exception],
    expected_text: str,
) -> None:
    """S3 H3: the strict enumeration-failure branch is classified and never swallowed."""
    error = make_error()
    app = _FakeApplication(vbprojects_error=error, vbprojects_error_on_item=fail_on_item)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(expected) as raised:
            get_vb_project(app, strict=True)

    assert raised.value.__cause__ is error
    assert expected_text in str(raised.value)
    if expected is VbaProjectUnavailableError:
        assert VBA_TRUST_PREREQUISITE not in str(raised.value)
    assert [record.message for record in caplog.records if record.levelno == logging.DEBUG] == []


def test_default_get_vb_project_keeps_the_enumeration_failure_visible() -> None:
    """PRESERVE: the non-strict enumeration branch still re-raises the original error."""
    error = RuntimeError("the project store is corrupt")

    with pytest.raises(RuntimeError) as raised:
        get_vb_project(_FakeApplication(vbprojects_error=error))

    assert raised.value is error


class _BoolHresultError(Exception):
    """A defect whose ``hresult`` attribute is a bool, which is never an HRESULT."""

    hresult = True


def test_failing_hresults_reads_a_plain_high_bit_int_argument() -> None:
    """S3 H4: the documented duck typing accepts a plain ``int`` ``args[0]``.

    A high-bit (severity-1) status is a failing HRESULT whether it is spelled unsigned or
    signed, and the classification is deliberately never tightened to require an
    ``hresult`` attribute. A severity-0 status is not a failure and a ``bool`` is never
    an HRESULT.
    """
    assert failing_hresults(_ArgsOnlyComError(0x80004005, "operational")) == (0x80004005,)
    assert failing_hresults(_ArgsOnlyComError(-2147467259, "operational")) == (0x80004005,)
    assert failing_hresults(_ArgsOnlyComError(1, "success status")) == ()
    assert failing_hresults(_BoolHresultError(True)) == ()


def test_default_get_vb_project_returns_the_first_project() -> None:
    """PRESERVE: the shared helper's default success path is unchanged."""
    project = _FakeVBProject(["modMain"])

    assert get_vb_project(_FakeApplication(project=project)) is project


def test_default_get_vb_project_still_returns_none_for_an_empty_project_list() -> None:
    """PRESERVE: an empty project list stays ``None`` for every legacy caller."""
    assert get_vb_project(_FakeApplication(project=None)) is None


def test_com_session_reports_a_trust_refusal_as_the_trust_error() -> None:
    """S2: the documentation session reads strictly, so trust is positively classified."""
    session = ComAccessSession(_FakeApplication(vbe_error=_ComError(TRUST_HRESULT)))

    with pytest.raises(VbaTrustRefusedError, match="Trust access to the VBA project object model"):
        session.vba_module_count()


def test_com_session_reports_an_unknown_failure_as_a_real_failure() -> None:
    """S2: an unknown COM failure propagates as a failure, not a trust refusal."""
    session = ComAccessSession(
        _FakeApplication(vbe_error=_ComError(DISP_E_EXCEPTION, "catastrophic", UNKNOWN_COM_HRESULT))
    )

    with pytest.raises(VbaProjectUnavailableError) as raised:
        session.vba_module_count()

    assert not isinstance(raised.value, VbaTrustRefusedError)
    assert "0x8000FFFF" in str(raised.value)
    assert VBA_TRUST_PREREQUISITE not in str(raised.value)


def test_com_session_caches_the_successful_project() -> None:
    """PRESERVE: a cached successful project is read once and serves every member."""
    app = _FakeApplication(project=_FakeVBProject(["modMain"]))
    session = ComAccessSession(app)

    assert session.vba_module_count() == 1
    assert session.vba_module_name(1) == "modMain"
    assert session.vba_module_type(1) == "Standard"
    assert session.vba_module_source(1) == "Sub Main()\nEnd Sub\n"
    assert app.vbe_reads == 1


class _ReaderSession(ComAccessSession):
    """The production session over a fake application, with the owned-PID read faked."""

    def access_application_pid(self) -> int:
        return PID


def _reader(session: _ReaderSession, scratch_root: Path) -> AppLogicReader:
    return AppLogicReader(
        launcher=lambda: session,
        identity_reader=lambda pid: AccessProcessIdentity(pid=pid, creation=CREATION, image="MSACCESS.EXE"),
        emit_identity=lambda _pid, _creation, _image: None,
        assign_job=lambda _pid: SimpleNamespace(close=lambda: None),
        scratch_root=scratch_root,
    )


_READER_VBA_CASES = [
    pytest.param(_ComError(TRUST_HRESULT), "unavailable", VBA_TRUST_PREREQUISITE, id="recognized-trust"),
    pytest.param(_ComError(UNKNOWN_COM_HRESULT), "failed", "0x8000FFFF", id="unknown-com"),
    pytest.param(
        _ComError(DISP_E_EXCEPTION, "catastrophic", UNKNOWN_COM_HRESULT), "failed", "0x8000FFFF", id="nested-unknown"
    ),
    pytest.param(None, "failed", "no VBA project", id="no-project"),
]


@pytest.mark.parametrize(("vbe_error", "state", "expected_text"), _READER_VBA_CASES)
def test_reader_reports_the_vba_family_from_the_production_session(
    tmp_path: Path, vbe_error: BaseException | None, state: str, expected_text: str
) -> None:
    """S2: only a recognized refusal is unavailable; every other failure is ``failed``."""
    source = tmp_path / "source.accdb"
    source.write_bytes(b"not-a-real-access-file")
    original = source.read_bytes()
    app = _FakeApplication(vbe_error=vbe_error, project=None)

    content = _reader(_ReaderSession(app), tmp_path).read(str(source))

    vba = next(item for item in content.content if item.category == "vba_module")
    assert vba.name == FAMILY_SCOPE_NAME
    assert vba.content.state == state
    assert expected_text in (vba.content.reason or "")
    if state == "failed":
        assert VBA_TRUST_PREREQUISITE not in (vba.content.reason or "")
    # The other families travel the same real session and stay observed: no over-degradation.
    form = next(item for item in content.content if item.category == "form")
    assert form.name == "frmMain"
    assert form.content.state == "observed"
    # Teardown, copy discipline and the untouched original are unchanged by classification.
    assert app.opened_paths and app.opened_paths[0] != str(source)
    assert app.closed is True
    assert app.quit_called is True
    assert source.read_bytes() == original
