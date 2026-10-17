"""Owned Windows subprocess fixture for an armed table data-macro tripwire.

Purpose
-------
Builds a disposable, repository-confined ``.accdb`` whose ``tblTripwire`` table carries
one table data macro per DML event (``AfterInsert`` -> ``"insert"``, ``AfterUpdate`` ->
``"update"``, ``AfterDelete`` -> ``"delete"``), each creating one row in
``tblTripwireLog``. The shipped documentation-inspection pipeline must never fire any of
them: a read-only metadata inspection is not a DML statement, so the log must stay empty
and the source bytes must stay unchanged. This helper arms the tripwire so the pipeline
suite's "nothing executed" claim is non-vacuous rather than an assertion on an inert file.

Why the macro is self-built rather than user-exported
-----------------------------------------------------
An earlier attempt to load a hand-authored ``acTableDataMacro`` payload was
accept-blocked: the client import format was unknown and every candidate was rejected by
``Application.LoadFromText`` (see the blocked-tripwire section of
``docs/specs/documentation-fixtures.md``). The format was then established empirically
against a real Access build (probe
``db/fixtures/datamacro-probe-0327ea09-9b4a-4c47-ac2f-8027eeaa67fd``): Access's own
``SaveAsText acTableDataMacro`` round-trip is the ground truth, its serialization is
UTF-16 LE with a BOM, a CRLF after the ``<?xml ...?>`` declaration, no trailing newline,
and the ``CreateRecord``/``SetField`` shape below is accepted by ``LoadFromText``. This
helper reuses exactly that proven shape.

Execution model
---------------
Every command is its own short-lived subprocess. A command performs at most ONE DAO
``DBEngine.120`` dispatch/release cycle and at most ONE ``Access.Application``
lifecycle. ``build`` runs the DAO phase (``CreateDatabase`` + DDL + the seed row) and
then the Access phase (``LoadFromText`` + ``SaveAsText``) in one process; the Access step
calls ``pythoncom.CoInitialize`` itself after the DAO lifecycle released COM, which is
the ordering the probe proved. ``Access.Application`` is always hidden with
``UserControl = False`` and ``AutomationSecurity = 3`` (force-disabled), and
``CloseCurrentDatabase`` + ``Quit`` run in a ``finally``. If an MSACCESS.EXE process
remains after the lifecycle, only pids that were absent from a baseline taken before the
Access step starts are force-terminated; a pre-existing Access instance is never touched.
No DSN or registry value is written. Generated state is confined to a unique
``db/fixtures/datamacro-*`` root and no command ever deletes pre-existing state.

Command API (one JSON object per line on stdout; booleans, counts and the log-event list)
-----------------------------------------------------------------------------------------
``environment``            report Windows / ACE DAO / Access.Application availability; no writes.
``build --root``           create ``root``, tables ``tblTripwire`` and
                           ``tblTripwireLog``, exactly one seed row in ``tblTripwire``
                           inserted BEFORE the macro load, then load the one three-event
                           data-macro XML and round-trip it with ``SaveAsText``.
                           ``build --root --no-macro`` stops after the seed row: no macro
                           XML is written and no ``Access.Application`` is dispatched, so
                           the fixture is provably inert (negative control).
``fire --event --db``      DAO-only, exactly one corresponding write on ``tblTripwire``
                           (insert / update / delete).
``count --db``             DAO-only row counts for both tables plus the ordered list of
                           ``tblTripwireLog.Event`` values.

Exit codes: ``0`` success; ``2`` usage / invalid or missing fixture; ``3`` environment
unsupported (non-Windows, missing ACE DAO, or missing ``Access.Application``); ``4`` a
control did not hold; ``1`` unexpected failure.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "db" / "fixtures"
ROOT_PREFIX = "datamacro-"

DB_FILENAME = "tripwire.accdb"
XML_FILENAME = "tblTripwire.datamacro.xml"
ROUNDTRIP_FILENAME = "tblTripwire.roundtrip.txt"
INVENTORY_FILENAME = "inventory.json"
INVENTORY_SCHEMA = "datamacro-fixture/1"

TRIPWIRE_TABLE = "tblTripwire"
LOG_TABLE = "tblTripwireLog"
SEED_NOTE = "seed row"

# The three table events this fixture arms, and the distinct log value each writes.
EVENT_KINDS = ("insert", "update", "delete")
EVENT_TO_MACRO_EVENT = {
    "insert": "AfterInsert",
    "update": "AfterUpdate",
    "delete": "AfterDelete",
}

# Fake data only; the fixture writes no real content anywhere.
LOCALE = ";LANGID=0x0409;CP=1252;COUNTRY=0"
CREATE_VERSION_ACE = 128  # dbVersion120
DAO_FAIL_ON_ERROR = 128
DAO_DB_OPEN_SNAPSHOT = 4
DAO_DB_OPEN_READONLY = True
AC_TABLE_DATA_MACRO = 12
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3

CREATE_TRIPWIRE = (
    f"CREATE TABLE [{TRIPWIRE_TABLE}] ([ID] COUNTER NOT NULL, [Note] TEXT(50), "
    f"CONSTRAINT [PK_{TRIPWIRE_TABLE}] PRIMARY KEY ([ID]))"
)
CREATE_LOG = (
    f"CREATE TABLE [{LOG_TABLE}] ([ID] COUNTER NOT NULL, [Event] TEXT(50), "
    f"CONSTRAINT [PK_{LOG_TABLE}] PRIMARY KEY ([ID]))"
)
INSERT_SEED = f"INSERT INTO [{TRIPWIRE_TABLE}] ([Note]) VALUES ('{SEED_NOTE}')"

XML_HEADER = '<?xml version="1.0" encoding="UTF-16" standalone="no"?>'
XML_NAMESPACE = "http://schemas.microsoft.com/office/accessservices/2009/11/application"

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_UNSUPPORTED = 3
EXIT_CHECK_FAILED = 4


class WorkerError(Exception):
    """Reportable usage or fixture failure (exit code 2)."""


class UnsupportedError(WorkerError):
    """The environment cannot run this qualification (exit code 3)."""


# --------------------------------------------------------------------------- #
# output, digests and confinement
# --------------------------------------------------------------------------- #


def _emit(payload: dict[str, Any]) -> None:
    """Write one compact JSON object to stdout."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
    sys.stdout.flush()


def _sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_root(raw: str) -> Path:
    """Return a confinement-checked, not-yet-existing ``db/fixtures/datamacro-*`` root."""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    resolved = candidate.resolve()
    fixtures = FIXTURE_ROOT.resolve()
    if resolved == fixtures or not resolved.is_relative_to(fixtures):
        raise WorkerError("root must be a dedicated directory inside db/fixtures")
    if not resolved.name.startswith(ROOT_PREFIX):
        raise WorkerError(f"root directory name must start with {ROOT_PREFIX!r}")
    return resolved


def _resolve_database(raw: str) -> Path:
    """Return a confinement-checked existing database inside a ``datamacro-*`` root."""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    resolved = candidate.resolve()
    parent = resolved.parent
    fixtures = FIXTURE_ROOT.resolve()
    if not parent.is_relative_to(fixtures) or parent == fixtures:
        raise WorkerError("database must live inside a db/fixtures/datamacro-* root")
    if not parent.name.startswith(ROOT_PREFIX):
        raise WorkerError(f"database root directory name must start with {ROOT_PREFIX!r}")
    if not resolved.is_file():
        raise WorkerError(f"database does not exist: {resolved.name}")
    return resolved


# --------------------------------------------------------------------------- #
# DAO lifecycle (exactly one per process) and error codes
# --------------------------------------------------------------------------- #


def _dao_engine() -> Any:
    """Dispatch ACE DAO once on this thread; close it via :func:`_release_dao`."""
    if os.name != "nt":
        raise UnsupportedError("ACE DAO requires Windows")
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise UnsupportedError(f"ACE DAO requires pywin32: {exc}") from exc
    pythoncom.CoInitialize()
    try:
        return win32com.client.Dispatch("DAO.DBEngine.120")
    except Exception as exc:
        pythoncom.CoUninitialize()
        raise UnsupportedError(f"ACE DAO (DAO.DBEngine.120) unavailable: {exc}") from exc


def _release_dao(engine: Any) -> None:
    """Drop the engine reference and release COM on the owning thread."""
    del engine
    gc.collect()
    import pythoncom

    pythoncom.CoUninitialize()


def _close_object(obj: Any) -> None:
    """Close a DAO object if it exposes ``Close`` and then drop the reference."""
    try:
        if obj is not None:
            obj.Close()
    finally:
        obj = None
        gc.collect()


def _com_error_info(exc: BaseException) -> dict[str, Any]:
    """Return ``{hresult, scode, message}`` for a pywintypes/other error."""
    args = getattr(exc, "args", ())
    hresult = args[0] if args and isinstance(args[0], int) else getattr(exc, "hresult", None)
    message = args[1] if len(args) >= 2 and isinstance(args[1], str) else None
    scode = None
    if len(args) >= 3 and isinstance(args[2], tuple) and len(args[2]) >= 5:
        try:
            scode = int(args[2][4])
        except (TypeError, ValueError):
            scode = None
    if message is None:
        message = str(exc)
    return {"hresult": hresult, "scode": scode, "message": message}


def _scalar(database: Any, sql: str) -> int:
    """Return the first column of the first row of a snapshot query."""
    recordset = database.OpenRecordset(sql, DAO_DB_OPEN_SNAPSHOT)
    try:
        return int(recordset.Fields(0).Value)
    finally:
        recordset.Close()


def _log_events(database: Any) -> list[str]:
    """Return every ``tblTripwireLog.Event`` value in insertion order."""
    recordset = database.OpenRecordset(f"SELECT [Event] FROM [{LOG_TABLE}] ORDER BY [ID]", DAO_DB_OPEN_SNAPSHOT)
    try:
        events: list[str] = []
        while not recordset.EOF:
            events.append(str(recordset.Fields(0).Value))
            recordset.MoveNext()
        return events
    finally:
        recordset.Close()


def _table_names(database: Any) -> list[str]:
    """Return the user table names in a database, sorted."""
    return sorted(str(table.Name) for table in database.TableDefs if not str(table.Name).startswith("MSys"))


# --------------------------------------------------------------------------- #
# Access.Application lifecycle (exactly one per process)
# --------------------------------------------------------------------------- #


def _msaccess_pids() -> set[int]:
    """Return the pids of every running MSACCESS.EXE process."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE", "/NH"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:  # noqa: BLE001 - an unreadable process list means "unknown"
        return set()
    pids: set[int] = set()
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].upper() == "MSACCESS.EXE" and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _terminate_new_msaccess(baseline: set[int]) -> list[int]:
    """Force-terminate only MSACCESS.EXE pids absent from ``baseline``; return them.

    A pre-existing Access instance is never a target, and only pids observed as new after
    the lifecycle are considered, so this can never reach an unrelated user session.
    """
    terminated: list[int] = []
    for pid in sorted(_msaccess_pids() - baseline):
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception:  # noqa: BLE001 - a refused/absent pid needs no handling
            continue
        if completed.returncode == 0:
            terminated.append(pid)
    return terminated


def _access_application() -> Any:
    """Dispatch Access.Application in this process; the caller owns the lifecycle."""
    if os.name != "nt":
        raise UnsupportedError("Access.Application requires Windows")
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise UnsupportedError(f"Access.Application requires pywin32: {exc}") from exc
    pythoncom.CoInitialize()
    try:
        return win32com.client.Dispatch("Access.Application")
    except Exception as exc:
        pythoncom.CoUninitialize()
        raise UnsupportedError(f"Access.Application unavailable: {exc}") from exc


def _release_access(app: Any) -> None:
    """Drop the application reference and release COM on the owning thread."""
    del app
    gc.collect()
    import pythoncom

    pythoncom.CoUninitialize()


def _prepare_access(app: Any) -> None:
    """Force a hidden, non-interactive, macro-disabled automation session."""
    try:
        app.Visible = False
    except Exception:  # noqa: BLE001 - visibility is best-effort hardening
        pass
    try:
        app.UserControl = False
    except Exception:  # noqa: BLE001 - user control is best-effort hardening
        pass
    app.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE


def _shutdown_access(app: Any) -> None:
    """Close the current database and quit, in that order, ignoring shutdown errors."""
    try:
        app.CloseCurrentDatabase()
    except Exception:  # noqa: BLE001 - a failed close still needs the quit attempt
        pass
    try:
        app.Quit()
    except Exception:  # noqa: BLE001 - a failed quit is reported as residue instead
        pass


# --------------------------------------------------------------------------- #
# macro XML (the exact shape proven by the probe)
# --------------------------------------------------------------------------- #


def _data_macro_xml_text() -> str:
    """Return the one-file, three-event data-macro XML in Access's proven text shape.

    Access serializes a table data macro as UTF-16 LE with a BOM, a CRLF after the
    ``<?xml ...?>`` declaration, and no trailing newline (probe ground truth). Each event
    is one ``<DataMacro Event="...">`` whose ``<CreateRecord>`` writes one ``Event`` row.
    """
    macros = [
        f'<DataMacro Event="{EVENT_TO_MACRO_EVENT[value]}"><Statements><CreateRecord>'
        f"<Data><Reference>{LOG_TABLE}</Reference></Data><Statements>"
        f'<Action Name="SetField"><Argument Name="Field">{LOG_TABLE}.Event</Argument>'
        f'<Argument Name="Value">"{value}"</Argument></Action>'
        f"</Statements></CreateRecord></Statements></DataMacro>"
        for value in EVENT_KINDS
    ]
    return XML_HEADER + "\r\n" + f'<DataMacros xmlns="{XML_NAMESPACE}">' + "".join(macros) + "</DataMacros>"


def data_macro_xml_bytes() -> bytes:
    """Return the UTF-16 LE (BOM) bytes the helper writes as the load input."""
    return b"\xff\xfe" + _data_macro_xml_text().encode("utf-16-le")


def _decode_utf16_le(data: bytes) -> str:
    """Decode a UTF-16 LE (optionally BOM-prefixed) text document."""
    return data.decode("utf-16-le")


def _roundtrip_checks(roundtrip_text: str) -> dict[str, Any]:
    """Return the observable facts that prove all three events round-tripped."""
    return {
        "all_events_present": all(
            f'Event="{macro_event}"' in roundtrip_text for macro_event in EVENT_TO_MACRO_EVENT.values()
        ),
        "create_record_count": roundtrip_text.count("<CreateRecord>"),
        "set_field_count": roundtrip_text.count('<Action Name="SetField">'),
        "all_log_values_present": all(f'"{value}"' in roundtrip_text for value in EVENT_KINDS),
        "reference_present": f"<Reference>{LOG_TABLE}</Reference>" in roundtrip_text,
    }


def _roundtrip_ok(checks: dict[str, Any]) -> bool:
    """Return True only when every proven-shape element is present for all three events."""
    return bool(
        checks["all_events_present"]
        and checks["create_record_count"] == len(EVENT_KINDS)
        and checks["set_field_count"] == len(EVENT_KINDS)
        and checks["all_log_values_present"]
        and checks["reference_present"]
    )


# --------------------------------------------------------------------------- #
# environment
# --------------------------------------------------------------------------- #


def cmd_environment() -> dict[str, Any]:
    """Report qualification environment availability without writing anything."""
    reasons: list[str] = []
    dao_available = False
    dao_version: str | None = None
    if os.name != "nt":
        reasons.append("not Windows")
    else:
        try:
            engine = _dao_engine()
        except UnsupportedError as exc:
            reasons.append(str(exc))
        else:
            try:
                dao_available = True
                dao_version = str(engine.Version)
            finally:
                _release_dao(engine)

    access_available = False
    access_version: str | None = None
    baseline = _msaccess_pids()
    app: Any = None
    try:
        app = _access_application()
    except UnsupportedError as exc:
        reasons.append(str(exc))
    else:
        access_available = True
        try:
            access_version = str(app.Version)
        except Exception:  # noqa: BLE001 - version is informational only
            pass
        finally:
            _shutdown_access(app)
            _release_access(app)
            _terminate_new_msaccess(baseline)

    return {
        "ok": dao_available and access_available,
        "command": "environment",
        "python_executable": sys.executable,
        "platform": os.name,
        "windows": os.name == "nt",
        "ace_dao_available": dao_available,
        "ace_dao_version": dao_version,
        "access_application_available": access_available,
        "access_version": access_version,
        "unsupported_reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# fixture construction
# --------------------------------------------------------------------------- #


def _build_tables_and_seed(database_path: Path) -> dict[str, Any]:
    """Create both tables and the single seed row with one DAO engine lifecycle."""
    engine = _dao_engine()
    try:
        database = engine.CreateDatabase(str(database_path), LOCALE, CREATE_VERSION_ACE)
        try:
            database.Execute(CREATE_TRIPWIRE, DAO_FAIL_ON_ERROR)
            database.Execute(CREATE_LOG, DAO_FAIL_ON_ERROR)
            database.Execute(INSERT_SEED, DAO_FAIL_ON_ERROR)
            tables = _table_names(database)
            tripwire_rows = _scalar(database, f"SELECT COUNT(*) FROM [{TRIPWIRE_TABLE}]")
            log_rows = _scalar(database, f"SELECT COUNT(*) FROM [{LOG_TABLE}]")
        finally:
            _close_object(database)
    finally:
        _release_dao(engine)
    return {"tables": tables, "tripwire_rows": tripwire_rows, "log_rows": log_rows}


def _load_and_roundtrip_macro(database_path: Path, xml_path: Path, roundtrip_path: Path) -> dict[str, Any]:
    """Load the data macro and re-export it with one Access.Application lifecycle.

    The baseline for the guarded termination is taken immediately before the Dispatch, so
    only an MSACCESS.EXE process this step started can ever be force-terminated.
    """
    baseline = _msaccess_pids()
    app: Any = None
    load_ok = False
    save_ok = False
    load_error: dict[str, Any] | None = None
    save_error: dict[str, Any] | None = None
    try:
        app = _access_application()
        _prepare_access(app)
        app.OpenCurrentDatabase(str(database_path))
        try:
            app.LoadFromText(AC_TABLE_DATA_MACRO, TRIPWIRE_TABLE, str(xml_path))
            load_ok = True
        except Exception as exc:  # noqa: BLE001 - a load refusal is reported, never retried blindly
            load_error = _com_error_info(exc)
        if load_ok:
            try:
                app.SaveAsText(AC_TABLE_DATA_MACRO, TRIPWIRE_TABLE, str(roundtrip_path))
                save_ok = True
            except Exception as exc:  # noqa: BLE001 - a save refusal is reported evidence
                save_error = _com_error_info(exc)
    except UnsupportedError as exc:
        load_error = {"hresult": None, "scode": None, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - reported as a failed control, never swallowed
        load_error = load_error or _com_error_info(exc)
    finally:
        if app is not None:
            _shutdown_access(app)
            _release_access(app)
        terminated = _terminate_new_msaccess(baseline)
    return {
        "load_ok": load_ok,
        "save_ok": save_ok,
        "load_error": load_error,
        "save_error": save_error,
        "terminated_pids": terminated,
        "msaccess_residue": sorted(_msaccess_pids() - baseline),
    }


def cmd_build(root: Path, *, no_macro: bool = False) -> dict[str, Any]:
    """Create the tripwire fixture: tables, seed row first, then the armed data macro.

    With ``no_macro`` the function stops after the DAO phase: no macro XML is written and
    no Access.Application is dispatched, leaving a fixture that is inert by construction.
    """
    if root.exists():
        raise WorkerError(f"root already exists; never overwrite fixtures: {root}")
    if os.name != "nt":
        raise UnsupportedError("not Windows")
    root.mkdir(parents=True)
    database_path = root / DB_FILENAME
    xml_path = root / XML_FILENAME
    roundtrip_path = root / ROUNDTRIP_FILENAME

    # Seed the tripwire BEFORE any macro exists, so the log genuinely starts empty.
    built = _build_tables_and_seed(database_path)
    if no_macro:
        # Negative-control path: tables and seed only. No macro XML and no Access step, so
        # a deliberate write on this fixture cannot fire anything.
        inventory = {
            "schema": INVENTORY_SCHEMA,
            "database": {"filename": DB_FILENAME, "sha256": _sha256(database_path)},
            "tables": built["tables"],
            "tripwire_rows": built["tripwire_rows"],
            "log_rows": built["log_rows"],
            "events": [],
            "macro_xml": None,
            "roundtrip": None,
            "macro_loaded": False,
        }
        (root / INVENTORY_FILENAME).write_text(
            json.dumps(inventory, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return {
            "ok": bool(
                built["tables"] == [TRIPWIRE_TABLE, LOG_TABLE]
                and built["tripwire_rows"] == 1
                and built["log_rows"] == 0
            ),
            "command": "build",
            "root": str(root),
            "root_relative": str(root.relative_to(ROOT)),
            "database_sha256": inventory["database"]["sha256"],
            "tables": built["tables"],
            "tripwire_rows": built["tripwire_rows"],
            "log_rows": built["log_rows"],
            "macro_loaded": False,
            "macro_xml_bytes": 0,
            "macro_load_ok": None,
            "macro_save_ok": None,
            "macro_load_error": None,
            "macro_save_error": None,
            "roundtrip_checks": {"roundtrip_file_present": False},
            "roundtrip_ok": False,
            "terminated_pids": [],
            "msaccess_residue": [],
        }
    xml_path.write_bytes(data_macro_xml_bytes())
    loaded = _load_and_roundtrip_macro(database_path, xml_path, roundtrip_path)

    checks: dict[str, Any] = {"roundtrip_file_present": roundtrip_path.is_file()}
    if checks["roundtrip_file_present"]:
        checks.update(_roundtrip_checks(_decode_utf16_le(roundtrip_path.read_bytes())))
    ok = bool(
        loaded["load_ok"]
        and loaded["save_ok"]
        and checks["roundtrip_file_present"]
        and _roundtrip_ok(checks)
        and not loaded["msaccess_residue"]
    )

    inventory = {
        "schema": INVENTORY_SCHEMA,
        "database": {"filename": DB_FILENAME, "sha256": _sha256(database_path)},
        "tables": built["tables"],
        "tripwire_rows": built["tripwire_rows"],
        "log_rows": built["log_rows"],
        "events": list(EVENT_KINDS),
        "macro_xml": XML_FILENAME,
        "roundtrip": ROUNDTRIP_FILENAME,
    }
    (root / INVENTORY_FILENAME).write_text(
        json.dumps(inventory, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "ok": ok,
        "command": "build",
        "root": str(root),
        "root_relative": str(root.relative_to(ROOT)),
        "database_sha256": inventory["database"]["sha256"],
        "tables": built["tables"],
        "tripwire_rows": built["tripwire_rows"],
        "log_rows": built["log_rows"],
        "macro_xml_bytes": xml_path.stat().st_size,
        "macro_load_ok": loaded["load_ok"],
        "macro_save_ok": loaded["save_ok"],
        "macro_load_error": loaded["load_error"],
        "macro_save_error": loaded["save_error"],
        "roundtrip_checks": checks,
        "roundtrip_ok": bool(checks["roundtrip_file_present"] and _roundtrip_ok(checks)),
        "terminated_pids": loaded["terminated_pids"],
        "msaccess_residue": loaded["msaccess_residue"],
    }


# --------------------------------------------------------------------------- #
# deliberate fire and read-only count (DAO only)
# --------------------------------------------------------------------------- #


def _fire_sql(event: str) -> str:
    """Return the single DML statement that must fire the requested event macro."""
    if event == "insert":
        return f"INSERT INTO [{TRIPWIRE_TABLE}] ([Note]) VALUES ('fire {event}')"
    if event == "update":
        return f"UPDATE [{TRIPWIRE_TABLE}] SET [Note] = 'fire {event}' WHERE [ID] = 1"
    return f"DELETE FROM [{TRIPWIRE_TABLE}] WHERE [ID] = 1"


def cmd_fire(event: str, database_path: Path) -> dict[str, Any]:
    """Perform exactly one corresponding write on ``tblTripwire`` through DAO."""
    if event not in EVENT_KINDS:
        raise WorkerError(f"event must be one of {EVENT_KINDS}, not {event!r}")
    engine = _dao_engine()
    try:
        database = engine.OpenDatabase(str(database_path), False, False)
        try:
            database.Execute(_fire_sql(event), DAO_FAIL_ON_ERROR)
            tripwire_rows = _scalar(database, f"SELECT COUNT(*) FROM [{TRIPWIRE_TABLE}]")
            log_rows = _scalar(database, f"SELECT COUNT(*) FROM [{LOG_TABLE}]")
        finally:
            _close_object(database)
    finally:
        _release_dao(engine)
    return {
        "ok": True,
        "command": "fire",
        "db": str(database_path),
        "event": event,
        "tripwire_rows": tripwire_rows,
        "log_rows": log_rows,
    }


def cmd_count(database_path: Path) -> dict[str, Any]:
    """Read both table counts and the ordered log events through a read-only DAO open."""
    engine = _dao_engine()
    try:
        database = engine.OpenDatabase(str(database_path), False, DAO_DB_OPEN_READONLY)
        try:
            tripwire_rows = _scalar(database, f"SELECT COUNT(*) FROM [{TRIPWIRE_TABLE}]")
            log_rows = _scalar(database, f"SELECT COUNT(*) FROM [{LOG_TABLE}]")
            events = _log_events(database)
        finally:
            _close_object(database)
    finally:
        _release_dao(engine)
    return {
        "ok": True,
        "command": "count",
        "db": str(database_path),
        "tripwire_rows": tripwire_rows,
        "log_rows": log_rows,
        "log_events": events,
    }


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def _parser() -> argparse.ArgumentParser:
    """Build the bounded subcommand parser."""
    parser = argparse.ArgumentParser(description="Table data-macro tripwire fixture worker.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("environment", help="report ACE DAO / Access.Application availability; no writes")

    build = subparsers.add_parser("build", help="create the tripwire fixture and arm the data macro")
    build.add_argument("--root", required=True)
    build.add_argument(
        "--no-macro",
        action="store_true",
        help="create tables and seed row only, without loading the data macro (negative control)",
    )

    for name, help_text in (
        ("fire", "perform exactly one corresponding write on tblTripwire"),
        ("count", "read both table counts and the ordered log events"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--db", required=True)
        if name == "fire":
            command.add_argument("--event", required=True, choices=list(EVENT_KINDS))

    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one bounded command and print a single JSON result line."""
    args = _parser().parse_args(argv)
    command = args.command
    try:
        if command == "environment":
            result = cmd_environment()
            _emit(result)
            return EXIT_OK if result["ok"] else EXIT_UNSUPPORTED
        if command == "build":
            result = cmd_build(_resolve_root(args.root), no_macro=args.no_macro)
        elif command == "fire":
            result = cmd_fire(args.event, _resolve_database(args.db))
        else:
            result = cmd_count(_resolve_database(args.db))
    except UnsupportedError as exc:
        _emit({"ok": False, "command": command, "error": str(exc), "error_kind": "unsupported"})
        return EXIT_UNSUPPORTED
    except WorkerError as exc:
        _emit({"ok": False, "command": command, "error": str(exc), "error_kind": "usage"})
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - last-resort failure envelope
        _emit({"ok": False, "command": command, "error": str(exc), "error_kind": "unexpected"})
        return EXIT_UNEXPECTED
    _emit(result)
    return EXIT_OK if result.get("ok") else EXIT_CHECK_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
