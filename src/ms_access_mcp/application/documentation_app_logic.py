"""Tier 2 instantiation-level app-logic content reader (T7).

Tier 1 app-logic inventory (DAO ``Containers`` plus startup database properties) is
engine-level and never instantiates Access. Tier 2 facts -- ``SaveAsText`` form/report/
macro exports, the VBA module catalogue, and project references -- are only reachable
from a live ``Access.Application``, so this module is the one disciplined reader that
instantiates it. It is deliberately **pure-orchestrated**: the COM boundary is an
injected :class:`AccessLauncher`/:class:`AccessSession`, the owned-process identity is an
injected reader (production: :func:`ms_access_mcp.application.process_identity.read_process_identity`),
the ownership announcement is an injected sink (production:
:func:`ms_access_mcp.application.documentation_worker.emit_access_identity`), and the
Job Object assignment is injected as well. Unit tests therefore fake every COM call and
no live Access is ever started.

Safety contract (v2 normative, contract items 2-6):

* the original is **never** opened by ``Access.Application``: the source is copied into
  one unique disposable directory under an overridable scratch root and only that copy is
  handed to ``OpenCurrentDatabase``; the directory is removed in a ``finally``;
* ``AutomationSecurity=3`` is set and read back **before** the open; a mismatch aborts
  without opening anything;
* ``Visible=False``/``UserControl=False`` are set at dispatch; the unsupported runtime is
  detected from ``Application.Version`` **before** the armed open;
* the owned Access identity is captured and announced before the open, and the Job
  Object ``kill-on-close`` handle is held until after the graceful close, so the worker's
  death reaps Access through the handle close (the T2 PID kill net remains the primary
  supervisor path);
* per-family read failures degrade to ``failed``/``unavailable`` facts without erasing
  the others; a positively recognized VBProject trust refusal (HRESULT ``0x800A17B4``)
  degrades the VBA family to ``unavailable`` and names the operator prerequisite, while
  corruption, an unknown COM status, or a missing project stays ``failed`` and is never
  described as a trust refusal;
* an *operational* failure of this **optional** Tier 2 stage (scratch directory, disposable
  copy, owned dispatch, armed open, or a whole content read) -- a filesystem/OS error or a
  COM-shaped error carrying a failing HRESULT -- degrades to ``failed`` family/reference
  facts plus one warning, so a caller keeps the v1 and Tier 1 facts it already collected as
  explicit ``partial`` evidence. An unexpected programmer defect (``ValueError``,
  ``TypeError``, ``AttributeError``, a ``RuntimeError`` without a failing HRESULT) is a hard
  error and is never reported as partial evidence. Containment steps do **not** degrade:
  ForceDisable set/readback, owned-identity capture (including an invalid PID
  read), the identity announcement, and the identity re-read guarding the Job Object
  assignment raise :class:`ContainmentError`, and ``KeyboardInterrupt``/``SystemExit``
  are never converted into partial evidence;
* raw source text is opt-in (R1): the catalogue always carries a content hash and byte
  size, and the untrusted export text is embedded only when ``include_source=True``.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..backends.access.app_logic_session import (  # noqa: F401 - re-exported seam contract
    VBA_TRUST_PREREQUISITE,
    VbaTrustRefusedError,
)
from ..backends.access.vba import failing_hresults
from ..errors import AccessMcpError
from .documentation import (
    ACCESS_APPLICATION_METHOD,
    REFERENCE_PROPERTIES,
    AppLogicEvidence,
    ContentObjectEvidence,
    EvidenceDoc,
    FactProvenance,
    FactStatus,
    ReferenceEvidence,
    redact_connection_secrets,
)
from .process_identity import AccessProcessIdentity, read_process_identity

# Scratch location override for the disposable copy. The default is the system temp
# directory; operators on this host prefer the D: scratch convention.
SCRATCH_DIR_ENV = "ACCESS_MCP_DOC_SCRATCH_DIR"
_SCRATCH_PREFIX = "ms_access_doc_v2_"

# Access DoCmd constants used by SaveAsText. These are the qualified values from the T4
# gate; the wrong 4/8 mix-up is a known recording defect (L14) and must never be used.
SAVE_AS_TEXT_FORM = 2
SAVE_AS_TEXT_REPORT = 3
SAVE_AS_TEXT_MACRO = 4

# Force-disable automation security (``msoAutomationSecurityForceDisable``).
AUTOMATION_SECURITY_FORCE_DISABLE = 3

# The measured, supported runtime is Access 16.0 on this host (contract 1). Any other
# major version reports app-logic facts unavailable and is never attempted.
SUPPORTED_ACCESS_MAJOR = "16"

# A whole-family fact that could not be read at all uses this scope name, so the
# unavailable/failed state is never attributed to a fabricated object.
FAMILY_SCOPE_NAME = "<all objects>"

# D4: the operator prerequisite named when VBProject trust is refused. The VBProject
# object model is only reachable when the Trust Center option is enabled; the reader
# never changes a Trust Center setting. (Constant and exception live in the backends
# session module and are re-exported here as the reader's public contract.)

# (category, CurrentProject collection, SaveAsText object type) in deterministic order.
_SAVE_AS_TEXT_COLLECTIONS: tuple[tuple[str, str, int], ...] = (
    ("form", "AllForms", SAVE_AS_TEXT_FORM),
    ("report", "AllReports", SAVE_AS_TEXT_REPORT),
    ("macro", "AllMacros", SAVE_AS_TEXT_MACRO),
)

# Whole-family iteration order for the unsupported-runtime degradation.
_FAMILY_CATEGORIES: tuple[str, ...] = ("form", "report", "macro", "vba_module")


class JobHandle(Protocol):
    """A held Job Object handle; closing it reaps any process still assigned to it."""

    def close(self) -> None: ...


class ContainmentError(AccessMcpError):
    """A containment step failed, so the read is a hard error and never partial evidence.

    Force-disable set/readback, owned-identity capture (including an invalid PID read),
    the owned-identity announcement, and the identity re-read guarding the Job Object
    assignment are containment: an uncertain ownership or armed state is reported as a
    safe error instead of being degraded into partial app-logic facts. Interrupts
    (``KeyboardInterrupt``/``SystemExit``) are never converted to this type.
    """


class AccessSession(Protocol):
    """The read-only COM surface the reader drives (fully faked in unit tests)."""

    def application_version(self) -> str: ...

    def set_automation_security(self, level: int) -> None: ...

    def read_automation_security(self) -> int: ...

    def access_application_pid(self) -> int: ...

    def open_current_database(self, path: str) -> None: ...

    def close_current_database(self) -> None: ...

    def quit_application(self) -> None: ...

    def current_project_object_names(self, collection: str) -> list[str]: ...

    def save_as_text(self, object_type: int, name: str, destination: str) -> None: ...

    def vba_module_count(self) -> int: ...

    def vba_module_name(self, index: int) -> str: ...

    def vba_module_type(self, index: int) -> str: ...

    def vba_procedures(self, index: int) -> list[str]: ...

    def vba_module_source(self, index: int) -> str: ...

    def reference_count(self) -> int: ...

    def reference_item(self, index: int) -> Any: ...

    def reference_name(self, _index: int, item: Any) -> str: ...

    def reference_path(self, _index: int, item: Any) -> str: ...

    def reference_guid(self, _index: int, item: Any) -> str: ...

    def reference_major(self, _index: int, item: Any) -> int: ...

    def reference_minor(self, _index: int, item: Any) -> int: ...

    def reference_built_in(self, _index: int, item: Any) -> bool: ...


@runtime_checkable
class AccessLauncher(Protocol):
    """Create one owned ``Access.Application`` and return its read-only session."""

    def launch(self) -> AccessSession: ...


@dataclass
class AppLogicContent:
    """Tier 2 facts and reader warnings, ready to merge into an :class:`EvidenceDoc`."""

    content: list[ContentObjectEvidence] = field(default_factory=list)
    references: list[ReferenceEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _reason(error: Exception) -> str:
    if isinstance(error, OSError) and (error.filename is not None or error.filename2 is not None):
        return redact_connection_secrets(_path_free_os_reason(error))
    return redact_connection_secrets(str(error).strip() or type(error).__name__)


def _path_free_os_reason(error: OSError) -> str:
    """Render a path-bearing OS error without its ``filename``/``filename2``.

    ``str(OSError)`` appends the offending paths, which on Windows carry the user name and
    the scratch layout into shareable evidence. The error code and ``strerror`` already say
    what went wrong, so only those are kept.
    """
    winerror = getattr(error, "winerror", None)
    if winerror is not None:
        code = f"[WinError {winerror}] "
    elif error.errno is not None:
        code = f"[Errno {error.errno}] "
    else:
        code = ""
    return f"{code}{error.strerror or ''}".strip() or type(error).__name__


def _is_operational_failure(error: Exception) -> bool:
    """Whether a whole-stage failure is operational (degradable) rather than a defect.

    Operational failures are filesystem/OS errors, the positively recognized VBA trust
    refusal, and COM-shaped errors that carry a failing HRESULT (``hresult``, ``args[0]``,
    or a nested ``excepinfo`` status). Anything else -- ``ValueError``, ``TypeError``,
    ``AttributeError``, a bare ``RuntimeError``, or any ``int``/``bool`` argument that is
    not a failing HRESULT -- is an unexpected programmer defect, so it stays a hard error
    instead of masquerading as successful partial evidence. The predicate is duck-typed:
    the application layer never imports pywin32/Win32.
    """
    if isinstance(error, (OSError, VbaTrustRefusedError)):
        return True
    return bool(failing_hresults(error))


def _major(version: str) -> str:
    return str(version).strip().split(".", 1)[0]


def _default_scratch_root() -> str:
    override = os.environ.get(SCRATCH_DIR_ENV, "").strip()
    return override or tempfile.gettempdir()


def _default_emit_identity(pid: int, creation: int, image: str) -> None:
    # Imported lazily: the worker imports this module, so a module-level import would be
    # circular, and the production sink writes to the protocol stdout.
    from .documentation_worker import emit_access_identity

    emit_access_identity(pid, creation, image)


class AppLogicReader:
    """Orchestrate one offline-copy, ForceDisable, catalogue-only Tier 2 read.

    Every collaborator is injectable so unit tests fake the COM boundary; only the
    disposable-copy lifecycle touches the real filesystem.
    """

    def __init__(
        self,
        *,
        launcher: Callable[[], AccessSession] | AccessLauncher | None = None,
        identity_reader: Callable[[int], AccessProcessIdentity | None] | None = None,
        emit_identity: Callable[[int, int, str], None] | None = None,
        assign_job: Callable[[int], JobHandle | None] | None = None,
        scratch_root: str | Path | None = None,
    ) -> None:
        self._launcher: Callable[[], AccessSession]
        if launcher is None:
            self._launcher = _default_launcher
        elif isinstance(launcher, AccessLauncher):
            self._launcher = launcher.launch
        else:
            self._launcher = launcher
        self._identity_reader = identity_reader or read_process_identity
        self._emit_identity = emit_identity or _default_emit_identity
        self._assign_job = assign_job or _assign_access_to_job
        self._scratch_root = Path(str(scratch_root)) if scratch_root is not None else Path(_default_scratch_root())

    def read(self, source_path: str, *, include_source: bool = False) -> AppLogicContent:
        """Read the Tier 2 catalogue from one disposable copy of ``source_path``.

        This stage is optional, so its *operational* failures -- a filesystem/OS error or a
        COM-shaped error carrying a failing HRESULT at the scratch directory, the
        disposable copy, the owned dispatch, the armed open, or a whole content read --
        degrade to :func:`_failed_content`: every Tier 2 family and reference is reported
        ``failed`` with a sanitized reason plus one warning, and the caller's already
        collected v1/Tier 1 facts survive as ``partial`` evidence. An unexpected
        programmer defect is re-raised as a hard error; it never becomes partial evidence.
        Containment failures raise :class:`ContainmentError`, and interrupts are never
        caught here. The per-family best-effort catches inside a content read are a
        separate, unchanged contract: one failed family is a fact, not a whole-stage exit.

        The scratch directory is disposable, so its removal is best-effort: a removal
        that fails, or that silently leaves the directory behind, adds exactly one
        sanitized warning (S2 A4) instead of raising out of the ``finally`` and masking
        the inspection's own result. A successful removal adds no warning, so a
        successful run's content stays byte-identical.
        """
        try:
            workdir = Path(tempfile.mkdtemp(prefix=_SCRATCH_PREFIX, dir=str(self._scratch_root)))
        except OSError as error:
            return _failed_content(_stage_reason("the disposable scratch directory could not be created", error))
        try:
            content = self._read_copy(source_path, workdir, include_source)
        except BaseException:
            # Teardown still runs on a raised containment/defect/interrupt error. There is
            # no content to carry a residue warning in that case, and the error keeps
            # propagating untouched.
            _remove_scratch(workdir)
            raise
        residue_warning = _remove_scratch(workdir)
        if residue_warning is not None:
            content.warnings.append(residue_warning)
        return content

    def _read_copy(self, source_path: str, workdir: Path, include_source: bool) -> AppLogicContent:
        """Copy the source into ``workdir`` and read the armed copy, degrading ordinary failures."""
        copy_path = workdir / Path(source_path).name
        try:
            shutil.copy2(source_path, copy_path)
        except Exception as error:  # noqa: BLE001 - an operational copy failure degrades
            if not _is_operational_failure(error):
                raise
            return _failed_content(_stage_reason("the disposable copy of the source could not be created", error))
        try:
            return self._read_armed(str(copy_path), workdir, include_source)
        except ContainmentError:
            # An uncertain armed state or ownership is a hard error, never partial.
            raise
        except Exception as error:  # noqa: BLE001 - an operational stage failure degrades
            if not _is_operational_failure(error):
                raise
            return _failed_content(_stage_reason("the Tier 2 inspection failed", error))

    def _read_armed(self, copy_path: str, workdir: Path, include_source: bool) -> AppLogicContent:
        warnings: list[str] = []
        session = self._launcher()
        job: JobHandle | None = None
        try:
            version = session.application_version()
            if _major(version) != SUPPORTED_ACCESS_MAJOR:
                return _unsupported_runtime_content(version)
            self._arm_automation_security(session)
            identity = self._capture_identity(session)
            # Announce the owned instance before the armed open so the supervisor can
            # reap Access even when this worker is killed at the whole-inspection deadline.
            self._announce_identity(identity)
            job, job_failure = self._assign_containment_job(identity)
            if job is None:
                warnings.append(
                    "Job Object assignment for the owned Access process failed; the "
                    "inspection proceeds with the supervisor PID-scoped kill net only"
                )
                if job_failure is not None:
                    warnings.append(job_failure)
            session.open_current_database(copy_path)
            content = self._read_content(session, workdir, include_source)
        finally:
            # Graceful close first, then release the kill-on-close Job handle so a
            # surviving Access instance is reaped when this worker dies.
            with contextlib.suppress(Exception):
                session.close_current_database()
            with contextlib.suppress(Exception):
                session.quit_application()
            if job is not None:
                with contextlib.suppress(Exception):
                    job.close()
        content.warnings[:0] = warnings
        return content

    def _assign_verified_job(
        self, identity: AccessProcessIdentity
    ) -> tuple[JobHandle | None, str | None]:
        """Assign the captured PID to the job only while its live identity still matches.

        The PID is re-read through the same production identity reader immediately before
        the assignment call, so a recycled PID is never handed to ``OpenProcess``. Any
        mismatch or unreadable re-read degrades exactly like a failed assignment -- the
        supervisor PID-scoped kill net remains the containment -- and the reason is
        returned so the reader can record it.
        """
        live = self._identity_reader(identity.pid)
        if live is None:
            return None, (
                f"Job Object assignment for owned Access PID {identity.pid} was skipped "
                "because the live process identity is unreadable at assignment time; the "
                "supervisor PID-scoped kill net remains the only containment"
            )
        if live.creation != identity.creation or live.image.casefold() != identity.image.casefold():
            return None, (
                f"Job Object assignment for owned Access PID {identity.pid} was skipped "
                f"because the live process identity changed since capture (creation="
                f"{live.creation}, image={live.image!r} vs creation={identity.creation}, "
                f"image={identity.image!r}); the supervisor PID-scoped kill net remains "
                "the only containment"
            )
        return self._assign_job(identity.pid), None

    def _arm_automation_security(self, session: AccessSession) -> None:
        """Force-disable automation security and verify the readback before any open.

        Both the write and the readback are containment: if the armed state is unknown the
        read is a hard error, because partial evidence would imply a contained open.
        """
        try:
            session.set_automation_security(AUTOMATION_SECURITY_FORCE_DISABLE)
            readback = session.read_automation_security()
        except Exception as error:
            msg = (
                f"Documentation app-logic reader could not set AutomationSecurity="
                f"{AUTOMATION_SECURITY_FORCE_DISABLE} and read it back before opening any "
                f"source: {_reason(error)}"
            )
            raise ContainmentError(msg) from error
        if readback != AUTOMATION_SECURITY_FORCE_DISABLE:
            msg = (
                f"Documentation app-logic reader set AutomationSecurity="
                f"{AUTOMATION_SECURITY_FORCE_DISABLE} but read back {readback!r}; the "
                "inspection aborted without opening any source"
            )
            raise ContainmentError(msg)

    def _assign_containment_job(
        self, identity: AccessProcessIdentity
    ) -> tuple[JobHandle | None, str | None]:
        """Verify the live identity and assign the Job Object, failing hard on uncertainty.

        A *reported* assignment failure still degrades to the supervisor PID-scoped kill
        net with a warning (contract 6); an unexpected error while verifying or assigning
        is containment and aborts before the open.
        """
        try:
            return self._assign_verified_job(identity)
        except Exception as error:
            msg = (
                "Documentation app-logic reader could not verify the owned Access process "
                f"identity for Job Object assignment ({_reason(error)}); the inspection "
                "aborted without opening any source"
            )
            raise ContainmentError(msg) from error

    def _capture_identity(self, session: AccessSession) -> AccessProcessIdentity:
        try:
            pid = session.access_application_pid()
            identity = self._identity_reader(pid)
        except Exception as error:
            msg = (
                "Documentation app-logic reader could not capture the owned Access process "
                f"identity ({_reason(error)}); the inspection aborted without opening any "
                "source"
            )
            raise ContainmentError(msg) from error
        if identity is None:
            msg = (
                "Documentation app-logic reader could not capture the owned Access process "
                "identity; the inspection aborted without opening any source"
            )
            raise ContainmentError(msg)
        return identity

    def _announce_identity(self, identity: AccessProcessIdentity) -> None:
        """Announce the owned identity before the open; a lost announcement is fatal."""
        try:
            self._emit_identity(identity.pid, identity.creation, identity.image)
        except Exception as error:
            msg = (
                "Documentation app-logic reader could not announce the owned Access process "
                f"identity to the supervisor ({_reason(error)}); no source was opened"
            )
            raise ContainmentError(msg) from error

    def _read_content(self, session: AccessSession, workdir: Path, include_source: bool) -> AppLogicContent:
        content: list[ContentObjectEvidence] = []
        for category, collection, object_type in _SAVE_AS_TEXT_COLLECTIONS:
            content.extend(self._read_export_family(session, workdir, category, collection, object_type, include_source))
        content.extend(self._read_vba_family(session, include_source))
        return AppLogicContent(content=content, references=self._read_references(session), warnings=[])

    def _read_export_family(
        self,
        session: AccessSession,
        workdir: Path,
        category: str,
        collection: str,
        object_type: int,
        include_source: bool,
    ) -> list[ContentObjectEvidence]:
        try:
            names = session.current_project_object_names(collection)
        except Exception as error:  # noqa: BLE001 - one family failure never erases the rest
            return [_family_fact(category, FactStatus.failed(_reason(error), _family_provenance(category)))]
        entries: list[ContentObjectEvidence] = []
        for index, name in enumerate(names):
            provenance = FactProvenance(category, name, "content")
            try:
                destination = workdir / f"{category}-{index}.txt"
                session.save_as_text(object_type, name, str(destination))
                raw = destination.read_bytes()
                text = raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
                encoded = text.encode("utf-8")
                value: dict[str, Any] = {
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                    "bytes": len(encoded),
                }
                if include_source:
                    value["source"] = text
                fact: FactStatus[Any] = FactStatus.observed(value, provenance)
            except Exception as error:  # noqa: BLE001 - a failed export is its own fact
                fact = FactStatus.failed(_reason(error), provenance)
            entries.append(ContentObjectEvidence(category, name, ACCESS_APPLICATION_METHOD, fact))
        return entries

    def _read_vba_family(self, session: AccessSession, include_source: bool) -> list[ContentObjectEvidence]:
        try:
            count = session.vba_module_count()
        except VbaTrustRefusedError:
            return [_family_fact("vba_module", FactStatus.unavailable(VBA_TRUST_PREREQUISITE, _family_provenance("vba_module")))]
        except Exception as error:  # noqa: BLE001 - VBA failure never erases other families
            return [_family_fact("vba_module", FactStatus.failed(_reason(error), _family_provenance("vba_module")))]
        entries: list[ContentObjectEvidence] = []
        for index in range(1, count + 1):
            try:
                name = session.vba_module_name(index)
            except Exception as error:  # noqa: BLE001 - one unreadable module is one fact
                entries.append(_failed_module_fact(index, error))
                continue
            provenance = FactProvenance("vba_module", name, "content")
            try:
                value: dict[str, Any] = {
                    "type": session.vba_module_type(index),
                    "procedures": session.vba_procedures(index),
                }
                # Agreement 9: a content hash per source object, always. The VBA
                # module source has no SaveAsText file, so the digest is over the
                # UTF-8 encoding of the CodeModule text (same scheme as exports).
                module_text = session.vba_module_source(index)
                encoded = module_text.encode("utf-8")
                value["sha256"] = hashlib.sha256(encoded).hexdigest()
                value["bytes"] = len(encoded)
                if include_source:
                    value["source"] = module_text
                fact: FactStatus[Any] = FactStatus.observed(value, provenance)
            except Exception as error:  # noqa: BLE001 - a failed module read is its own fact
                fact = FactStatus.failed(_reason(error), provenance)
            entries.append(ContentObjectEvidence("vba_module", name, ACCESS_APPLICATION_METHOD, fact))
        return entries

    def _read_references(self, session: AccessSession) -> list[ReferenceEvidence]:
        try:
            count = session.reference_count()
        except Exception as error:  # noqa: BLE001 - the whole reference family is one fact
            return [_failed_reference(FAMILY_SCOPE_NAME, error)]
        entries: list[ReferenceEvidence] = []
        for index in range(1, count + 1):
            try:
                item = session.reference_item(index)
                name = session.reference_name(index, item)
            except Exception as error:  # noqa: BLE001 - one unreadable item never erases the rest
                entries.append(_failed_reference(f"reference[{index}]", error))
                continue
            entries.append(
                _reference_evidence(
                    session,
                    index,
                    item,
                    name,
                    {
                        "path": session.reference_path,
                        "guid": session.reference_guid,
                        "major": session.reference_major,
                        "minor": session.reference_minor,
                        "built_in": session.reference_built_in,
                    },
                )
            )
        return entries


def _family_provenance(category: str) -> FactProvenance:
    return FactProvenance(category, FAMILY_SCOPE_NAME, "content")


def _family_fact(category: str, fact: FactStatus[Any]) -> ContentObjectEvidence:
    return ContentObjectEvidence(category, FAMILY_SCOPE_NAME, ACCESS_APPLICATION_METHOD, fact)


def _failed_module_fact(index: int, error: Exception) -> ContentObjectEvidence:
    name = f"vba_module[{index}]"
    return ContentObjectEvidence(
        "vba_module",
        name,
        ACCESS_APPLICATION_METHOD,
        FactStatus.failed(_reason(error), FactProvenance("vba_module", name, "content")),
    )


def _failed_reference(name: str, error: Exception) -> ReferenceEvidence:
    reason = _reason(error)
    facts = [FactStatus.failed(reason, FactProvenance("reference", name, prop)) for prop in REFERENCE_PROPERTIES]
    return ReferenceEvidence(name, ACCESS_APPLICATION_METHOD, *facts)


def _reference_evidence(
    session: AccessSession,
    index: int,
    item: Any,
    name: str,
    readers: dict[str, Callable[[int, Any], Any]],
) -> ReferenceEvidence:
    facts = []
    for prop in REFERENCE_PROPERTIES:
        provenance = FactProvenance("reference", name, prop)
        try:
            facts.append(FactStatus.observed(readers[prop](index, item), provenance))
        except Exception as error:  # noqa: BLE001 - each reference property is its own fact
            facts.append(FactStatus.failed(_reason(error), provenance))
    return ReferenceEvidence(name, ACCESS_APPLICATION_METHOD, *facts)


def _stage_reason(stage: str, error: Exception) -> str:
    """Compose one sanitized degradation reason from a stage label and its error."""
    return redact_connection_secrets(f"{stage}: {_reason(error)}")


def _remove_scratch(workdir: Path) -> str | None:
    """Remove the disposable scratch directory, never raising from the caller's ``finally``.

    Teardown is best-effort (S2 A4): the directory is disposable, so a removal that fails
    -- or that reports success while the directory is still there -- must not mask the
    inspection's own result with a teardown error. Instead it returns one sanitized
    warning naming the error class and its redacted reason. A removed directory, and an
    already absent one, return ``None`` so a successful run stays byte-identical.
    """
    failures: list[Exception] = []

    def record(_operation: Any, _path: Any, exc_info: tuple[type[BaseException], BaseException, Any]) -> None:
        # ``rmtree``'s ``onerror`` hands over ``sys.exc_info()``, not the exception alone.
        if isinstance(exc_info[1], Exception):
            failures.append(exc_info[1])

    try:
        shutil.rmtree(workdir, onerror=record)
    except Exception as error:  # noqa: BLE001 - teardown never raises
        failures.append(error)
    try:
        present = workdir.exists()
    except Exception as error:  # noqa: BLE001 - an unanswerable probe counts as residue
        failures.append(error)
        present = True
    if not present:
        # The residue itself is the authority: an already removed directory is success.
        return None
    error: Exception | None = next(
        (item for item in failures if not isinstance(item, FileNotFoundError)),
        None,
    )
    if error is None:
        detail = "OSError: the directory is still present after removal"
    else:
        detail = f"{type(error).__name__}: {_reason(error)}"
    return redact_connection_secrets(
        f"The disposable scratch copy directory could not be removed ({detail}); "
        "the residue remains under the documentation scratch root"
    )


def _whole_family_content(
    reason: str, *, unavailable: bool
) -> tuple[list[ContentObjectEvidence], ReferenceEvidence]:
    """One whole-family fact per content family plus one whole-family reference.

    ``unavailable`` is the unsupported-runtime degradation; ``failed`` is the ordinary
    Tier 2 failure degradation. Both reuse :func:`_family_fact`, the family scope name,
    the per-reference property facts, and :class:`FactStatus`, so no schema changes.
    """
    make: Callable[[str, FactProvenance], FactStatus[Any]] = FactStatus.unavailable if unavailable else FactStatus.failed
    content = [_family_fact(category, make(reason, _family_provenance(category))) for category in _FAMILY_CATEGORIES]
    reference = ReferenceEvidence(
        FAMILY_SCOPE_NAME,
        ACCESS_APPLICATION_METHOD,
        *[make(reason, FactProvenance("reference", FAMILY_SCOPE_NAME, prop)) for prop in REFERENCE_PROPERTIES],
    )
    return content, reference


def _failed_content(detail: str) -> AppLogicContent:
    """Degrade an ordinary Tier 2 failure to explicit failed facts plus one warning."""
    reason = f"Tier 2 app-logic content was not read: {detail}"
    content, reference = _whole_family_content(reason, unavailable=False)
    warning = (
        f"Tier 2 app-logic content was not read ({detail}); the v1 and Tier 1 app-logic "
        "facts are retained and every Tier 2 family and reference is reported as failed"
    )
    return AppLogicContent(content=content, references=[reference], warnings=[warning])


def _unsupported_runtime_content(version: str) -> AppLogicContent:
    reason = (
        f"Access runtime version {version!r} is not the supported "
        f"{SUPPORTED_ACCESS_MAJOR}.0 runtime; app-logic facts are unavailable and no "
        "source was opened"
    )
    content, reference = _whole_family_content(reason, unavailable=True)
    return AppLogicContent(content=content, references=[reference], warnings=[])


def apply_app_logic_content(document: EvidenceDoc, content: AppLogicContent) -> EvidenceDoc:
    """Merge Tier 2 facts into a document's additive app-logic category.

    The Tier 1 (DAO) facts already on the document are preserved; the Tier 2 content and
    references are appended, and the reader's warnings are added to the document's.
    """
    existing = document.app_logic
    if existing is None:
        existing = AppLogicEvidence()
    existing.content.extend(content.content)
    existing.references.extend(content.references)
    document.app_logic = existing
    document.warnings.extend(content.warnings)
    return document


def read_tier2_content(source_path: str, *, include_source: bool = False) -> AppLogicContent:
    """Read Tier 2 content with the production reader (offline copy + COM boundary)."""
    return AppLogicReader().read(source_path, include_source=include_source)


# ---------------------------------------------------------------------------
# Production COM boundary (lazy imports; never exercised by unit tests)
# ---------------------------------------------------------------------------


def _default_launcher() -> AccessSession:
    # Win32/COM lives in the backends layer; the architecture guard forbids
    # win32com in application/. Lazy import keeps the module import cheap.
    from ..backends.access.app_logic_session import ComAccessLauncher

    return ComAccessLauncher().launch()


# ---------------------------------------------------------------------------
# Worker-side Job Object containment (T3-proven kill-on-close); optional by design
# ---------------------------------------------------------------------------

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _assign_access_to_job(pid: int) -> JobHandle | None:
    """Assign the owned Access PID to a kill-on-close Job Object, or ``None`` on failure.

    A failure is never fatal by itself: the supervisor's PID-scoped kill net is the
    primary path and the reader records the missing second layer as a warning.
    """
    try:
        import ctypes
    except Exception:  # noqa: BLE001 - a non-Windows host has no Job Object API
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _job_object_extended_limit_information(ctypes)
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job)
            return None
        process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            kernel32.CloseHandle(job)
            return None
        try:
            assigned = bool(kernel32.AssignProcessToJobObject(job, process))
        finally:
            kernel32.CloseHandle(process)
        if not assigned:
            kernel32.CloseHandle(job)
            return None
        return _Win32JobHandle(handle=job, kernel32=kernel32)
    except Exception:  # noqa: BLE001 - containment failure degrades to the PID kill net
        return None


def _job_object_extended_limit_information(ctypes: Any) -> Any:
    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    return _ExtendedLimitInformation()


@dataclass
class _Win32JobHandle:
    handle: int
    kernel32: Any

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.kernel32.CloseHandle(self.handle)
