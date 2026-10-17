"""Owned Windows subprocess fixture for a genuine SQLite -> Access ODBC link.

Purpose
-------
Builds a disposable, repository-confined fixture and validates the controls the
SQLite-linked-table qualification (``odd/tasks/sqlite-odbc-link-qualification.md``,
T1/S1-S6) depends on:

* a real SQLite database with exactly two ``remote_items`` rows;
* a disposable ACE ``.accdb`` with a local ``local_items`` control table and a
  genuine DSN-less ODBC link to ``remote_items`` through the installed x64
  ``SQLite3 ODBC Driver``;
* proof that ACE itself marks the link ``dbAttachedODBC`` (``0x20000000``) and that a
  deliberate read through the link contacts SQLite and returns both rows;
* an isolation tripwire: an exclusive Windows file-sharing lock (``CreateFileW`` with
  share mode 0) on the SQLite file must refuse the deliberate linked read, and the
  same read must succeed immediately after the lock is released (positive control). The
  deliberate locked read makes the driver raise its own ``SQLite3 ODBC Driver Connect``
  dialog, so the caller runs this command under ``tests/helpers/dialog_watcher.py``: the
  watcher observes exactly that one expected dialog, closes it with ``WM_CLOSE``, and the
  read is then refused. That single dialog is also the watcher's own positive control, and
  the same watcher must observe zero dialogs for any caller that claims no connect
  attempt;
* the raw Access ODBC ``SQLTables`` type for each user table, so runtime ``SYNONYM``
  (linked) versus ``TABLE`` (local) recognition is observable independently of the
  documentation inspection path;
* the observed stored-credential behavior for the fake ``PWD=canary-7f3a`` credential
  under ``dbAttachSavePWD``.

Execution model
---------------
Every command runs as its own short-lived subprocess. All COM is initialized and
released on this process's own single thread (``CoInitialize``/``CoUninitialize``);
``Access.Application`` is never started and no user database is opened. Generated
state is confined to a unique ``db/fixtures/sqlite-odbc-*`` root and no child process
is ever spawned (no self-orchestration). The caller orchestrates commands in order.

Command API (one JSON object per line on stdout)
------------------------------------------------
``environment``            report Windows/ACE/driver availability; no writes.
``build --root <dir>``     create ``remote.sqlite`` + ``linked.accdb`` under ``<dir>``.
                           ``--database-style absolute`` (default) stores the absolute
                           SQLite path; ``relative`` stores ``Database=remote.sqlite``,
                           which the driver resolves against the process CWD and which is
                           short enough for ACE to persist the trailing ``PWD`` segment
                           (T5/S4a stored-credential path; every step that resolves the
                           link must then run with CWD pinned to the fixture root).
``prove-stored-credential --root``
                           reopen the built front-end in a fresh process and positively
                           assert that the stored ``Connect`` contains the fake credential
                           and that a read through the link returns the two rows. Emits
                           booleans and connection-string key names only, never a raw
                           ``Connect`` value or the canary.
``prove-contact --root``   prove the link is genuine and contacts SQLite (2 rows).
``prove-isolation --root`` validate the exclusive-lock tripwire with its positive
                           control under ``--isolation exclusive-file-lock``; the locked
                           linked read raises the driver's expected connect dialog, so
                           run it under ``tests/helpers/dialog_watcher.py``.
``catalog-types --root``   report each user table's raw Access ODBC ``SQLTables`` type
                           (``TABLE``/``SYNONYM``/other) without any DAO session.
``cleanup --root``         guarded removal of exactly one self-created root.

Exit codes: ``0`` success; ``2`` usage / invalid or missing fixture; ``3`` environment
unsupported (non-Windows, missing ACE DAO or SQLite ODBC driver); ``4`` a genuineness
or tripwire control did not hold; ``1`` unexpected failure.

Failures are emitted as ``{"ok": false, ...}`` with the canary and any ``PWD=`` segment
redacted. Raw connection strings are never emitted, so downstream qualification logs
cannot capture a credential or a full ``Connect`` value.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "db" / "fixtures"
ROOT_PREFIX = "sqlite-odbc-"

SQLITE_FILENAME = "remote.sqlite"
ACCDB_FILENAME = "linked.accdb"
INVENTORY_FILENAME = "inventory.json"
INVENTORY_SCHEMA = "sqlite-odbc-fixture/1"

REMOTE_TABLE = "remote_items"
REMOTE_ROWS: tuple[tuple[int, str], ...] = ((1, "alpha"), (2, "beta"))
LOCAL_TABLE = "local_items"
LINK_NAME = "remote_items_link"
CANARY = "canary-7f3a"

SQLITE_DRIVER = "SQLite3 ODBC Driver"
ACCESS_ODBC_DRIVER = "Microsoft Access Driver (*.mdb, *.accdb)"
LOCALE = ";LANGID=0x0409;CP=1252;COUNTRY=0"
CREATE_VERSION_ACE = 128  # dbVersion120
DAO_FAIL_ON_ERROR = 128
DAO_DB_OPEN_SNAPSHOT = 4

DB_ATTACHED_ODBC = 0x20000000
DB_ATTACH_SAVE_PWD = 0x00020000
SYSTEM_OR_HIDDEN = 0x80000000

DATABASE_STYLE_ABSOLUTE = "absolute"
DATABASE_STYLE_RELATIVE = "relative"
DATABASE_STYLES = (DATABASE_STYLE_ABSOLUTE, DATABASE_STYLE_RELATIVE)

_DATABASE_VALUE_RE = re.compile(r"(?i)\bDatabase=([^;]*)")
_KEY_RE = re.compile(r"([^;=]+)=")

CREATE_LOCAL_SQL = (
    "CREATE TABLE [local_items] ([id] LONG NOT NULL, [note] TEXT(50), "
    "CONSTRAINT [PK_local_items] PRIMARY KEY ([id]))"
)
INSERT_LOCAL_SQL = "INSERT INTO [local_items] ([id], [note]) VALUES (1, 'local control row')"

ISOLATION_EXCLUSIVE_LOCK = "exclusive-file-lock"
ISOLATION_MODES = (ISOLATION_EXCLUSIVE_LOCK,)

# The only two SQLTables row types a user object may carry in this fixture. Any other
# type is unclassified for the probing purpose and is deliberately not interpreted here.
ODBC_LOCAL_TABLE_TYPE = "TABLE"
ODBC_SYNONYM_TABLE_TYPE = "SYNONYM"

# Limits that must travel with any isolation claim built on this helper. They are
# returned by ``prove-isolation`` so a qualification log cannot overstate the proof.
PROOF_LIMITS: tuple[str, ...] = (
    "The exclusive file-sharing lock refuses every new open of the SQLite file, so a "
    "caller that still succeeds while the lock is held performed no source-file open.",
    "A linked read refused under the lock proves this probe needs source-file access; it "
    "does not by itself prove that no caller attempted some other form of contact.",
    "The deliberate locked linked read makes the SQLite3 ODBC driver raise its own "
    "connect dialog (observed title 'SQLite3 ODBC Driver Connect', class #32770, owned by "
    "this process tree). This command must therefore run under "
    "tests/helpers/dialog_watcher.py, which observes exactly that one expected dialog and "
    "closes it with WM_CLOSE before the read is refused. The stronger claim is the same "
    "watcher observing zero dialogs for a locked public-CLI run: that proves no "
    "SQLite-driver connect attempt surfaced through ACE during inspection, not that every "
    "conceivable contact form is excluded.",
    "Source absence is deliberately not used as an isolation oracle: a failed read after "
    "a missing or non-recreated source proves nothing about attempted contact.",
    "On the absolute-path build the stored Connect holds no PWD segment: ACE persists "
    "only the first 254 characters of the driver's completed connection string, so the "
    "credential and the trailing keys fall past the cut. That is truncation, not "
    "credential filtering; the relative-database build fits under the cut and retains "
    "the PWD segment verbatim, which is the positive stored-credential path used by "
    "``prove-stored-credential`` (T5/S4a).",
)

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_UNSUPPORTED = 3
EXIT_CHECK_FAILED = 4

_PWD_RE = re.compile(r"(?i)\bPWD=[^;]*")
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class WorkerError(Exception):
    """Reportable usage or fixture failure (exit code 2)."""


class UnsupportedError(WorkerError):
    """The environment cannot run this qualification (exit code 3)."""


class CheckFailedError(WorkerError):
    """A genuineness or tripwire control did not hold (exit code 4)."""


# --------------------------------------------------------------------------- #
# output and confinement
# --------------------------------------------------------------------------- #


def _redact(text: str) -> str:
    """Remove the fake canary and any ``PWD=`` segment from a message."""
    return _PWD_RE.sub("PWD=<redacted>", text.replace(CANARY, "<canary>"))


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
    """Return a confinement-checked ``db/fixtures/sqlite-odbc-*`` root."""
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


def _require_built(root: Path) -> dict[str, Any]:
    """Load the build inventory and require both fixture files to exist."""
    inventory_path = root / INVENTORY_FILENAME
    if not inventory_path.is_file():
        raise WorkerError(f"fixture inventory missing; run build first: {INVENTORY_FILENAME}")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    for key in ("sqlite", "accdb"):
        if not (root / inventory[key]["filename"]).is_file():
            raise WorkerError(f"fixture file missing; run build first: {inventory[key]['filename']}")
    return inventory


# --------------------------------------------------------------------------- #
# environment
# --------------------------------------------------------------------------- #


def _probe_ace_dao() -> tuple[bool, str | None]:
    """Dispatch ACE DAO once and return ``(available, version)``.

    A process must perform at most one DAO session: dispatching and releasing a DAO
    engine and then opening an ODBC-linked table in a second session fails with DAO
    5003151 even though the same steps succeed in a fresh process. Environment probes
    therefore reuse this single session instead of dispatching twice.
    """
    if os.name != "nt":
        return False, None
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return False, None
    pythoncom.CoInitialize()
    try:
        engine = win32com.client.Dispatch("DAO.DBEngine.120")
        version = str(engine.Version)
        del engine
        gc.collect()
        return True, version
    except Exception:
        return False, None
    finally:
        pythoncom.CoUninitialize()


def _sqlite_driver_installed() -> bool:
    """Return True when pyodbc can see the DSN-less SQLite ODBC driver."""
    try:
        import pyodbc
    except ImportError:
        return False
    return any(driver.strip() == SQLITE_DRIVER for driver in pyodbc.drivers())


def _sqlite_driver_path() -> str | None:
    """Return the registered x64 driver DLL path from the ODBC installer registry."""
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\ODBC\ODBCINST.INI\{SQLITE_DRIVER}") as key:
                return str(winreg.QueryValueEx(key, "Driver")[0])
        except OSError:
            continue
    return None


def _sqlite_driver_registered() -> bool:
    """Return True when the x64 driver is registered with the ODBC installer.

    Used instead of ``pyodbc.drivers()`` before any DAO work: enumerating drivers
    through pyodbc in the same process makes a later ACE ODBC link creation fail
    with DAO 5003151, so driver availability is read from the installer registry.
    """
    return _sqlite_driver_path() is not None


def _unsupported_reasons(dao_available: bool) -> list[str]:
    """Return every reason this environment cannot run the qualification."""
    reasons: list[str] = []
    if os.name != "nt":
        reasons.append("not Windows")
    if not dao_available:
        reasons.append("ACE DAO (DAO.DBEngine.120) unavailable")
    if not _sqlite_driver_installed():
        reasons.append(f"ODBC driver unavailable: {SQLITE_DRIVER}")
    return reasons


def cmd_environment() -> dict[str, Any]:
    """Report the qualification environment without writing anything."""
    dao_available, dao_version = _probe_ace_dao()
    reasons = _unsupported_reasons(dao_available)
    return {
        "ok": not reasons,
        "command": "environment",
        "python_executable": sys.executable,
        "python_64bit": sys.maxsize > 2**32,
        "platform": os.name,
        "ace_dao_available": dao_available,
        "ace_dao_version": dao_version,
        "sqlite_driver_installed": _sqlite_driver_installed(),
        "sqlite_driver_path": _sqlite_driver_path(),
        "sqlite_driver_name": SQLITE_DRIVER,
        "unsupported_reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# SQLite and ACE fixture construction
# --------------------------------------------------------------------------- #


def _write_sqlite(path: Path) -> None:
    """Create the two-row ``remote_items`` SQLite database."""
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            "CREATE TABLE remote_items (id INTEGER PRIMARY KEY, label TEXT NOT NULL)"
        )
        connection.executemany("INSERT INTO remote_items (id, label) VALUES (?, ?)", REMOTE_ROWS)
        connection.commit()
    finally:
        connection.close()


def _connect_string(sqlite_path: Path, database_style: str = DATABASE_STYLE_ABSOLUTE) -> str:
    """Return the DSN-less ODBC connect string carrying the fake canary credential.

    With ``Database=<absolute path>`` the driver's completed string is long enough that
    ACE's 254-character persistence cut drops the credential. With
    ``Database=remote.sqlite`` (relative) the completed string fits and ACE stores the
    ``PWD`` segment verbatim; the driver then resolves the value against the process CWD.
    """
    database_value = SQLITE_FILENAME if database_style == DATABASE_STYLE_RELATIVE else str(sqlite_path)
    return f"ODBC;Driver={{{SQLITE_DRIVER}}};Database={database_value};PWD={CANARY};"


def _dao_engine() -> Any:
    """Dispatch ACE DAO on this thread; the caller must close it via :func:`_release_dao`."""
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


def _open_accdb_readonly(engine: Any, accdb: Path) -> Any:
    """Open the disposable accdb read-only; inspection never mutates the fixture."""
    return engine.OpenDatabase(str(accdb), False, True)


def _close_object(obj: Any) -> None:
    """Close a DAO object if it exposes ``Close`` and then drop the reference."""
    try:
        if obj is not None:
            obj.Close()
    finally:
        obj = None
        gc.collect()


def _build_fixture(root: Path, database_style: str = DATABASE_STYLE_ABSOLUTE) -> dict[str, Any]:
    """Create the SQLite source and the ACE database with a genuine ODBC link."""
    sqlite_path = root / SQLITE_FILENAME
    accdb_path = root / ACCDB_FILENAME
    _write_sqlite(sqlite_path)
    engine = _dao_engine()
    try:
        database = engine.CreateDatabase(str(accdb_path), LOCALE, CREATE_VERSION_ACE)
        try:
            database.Execute(CREATE_LOCAL_SQL, DAO_FAIL_ON_ERROR)
            database.Execute(INSERT_LOCAL_SQL, DAO_FAIL_ON_ERROR)
            tabledef = database.CreateTableDef(LINK_NAME)
            tabledef.SourceTableName = REMOTE_TABLE
            tabledef.Connect = _connect_string(sqlite_path, database_style)
            # ACE rejects an Attributes assignment that already contains dbAttachedODBC
            # before Append (DAO 5003001 "Invalid argument"). The dbAttachedODBC bit is
            # inferred from the "ODBC;" Connect prefix; only dbAttachSavePWD may be set
            # here, and only before Append (setting it afterwards fails the same way).
            tabledef.Attributes = DB_ATTACH_SAVE_PWD
            database.TableDefs.Append(tabledef)
        finally:
            _close_object(database)
    finally:
        _release_dao(engine)
    inventory = {
        "schema": INVENTORY_SCHEMA,
        "database_style": database_style,
        "sqlite": {"filename": SQLITE_FILENAME, "sha256": _sha256(sqlite_path)},
        "accdb": {"filename": ACCDB_FILENAME, "sha256": _sha256(accdb_path)},
        "remote_rows": [{"id": row[0], "label": row[1]} for row in REMOTE_ROWS],
    }
    (root / INVENTORY_FILENAME).write_text(
        json.dumps(inventory, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return inventory


def _build_preconditions() -> None:
    """Refuse an unsupported environment without opening a DAO session here.

    The check deliberately avoids dispatching DAO: the single DAO session for this
    process must be created by :func:`_build_fixture` itself, because a prior
    dispatch/release cycle in the same process breaks ODBC link creation.
    """
    if os.name != "nt":
        raise UnsupportedError("not Windows")
    if not _sqlite_driver_registered():
        raise UnsupportedError(f"ODBC driver unavailable: {SQLITE_DRIVER}")


def cmd_build(root: Path, database_style: str = DATABASE_STYLE_ABSOLUTE) -> dict[str, Any]:
    """Build a fresh fixture in a new, dedicated, confined root.

    For the relative-database style the caller must pin CWD to the fixture root because
    ACE resolves the relative ``Database=`` value while appending the link; that CWD can
    only exist once the root does, so an *empty* pre-created root is accepted for this
    style only. Every other build still refuses an existing root.
    """
    if database_style not in DATABASE_STYLES:
        raise WorkerError(f"unsupported database style: {database_style}")
    precreated_empty = (
        database_style == DATABASE_STYLE_RELATIVE
        and root.is_dir()
        and not any(root.iterdir())
    )
    if root.exists() and not precreated_empty:
        raise WorkerError(f"root already exists; never overwrite fixtures: {root}")
    _build_preconditions()
    root.mkdir(parents=True, exist_ok=True)
    inventory = _build_fixture(root, database_style)
    return {
        "ok": True,
        "command": "build",
        "database_style": database_style,
        "root": str(root),
        "root_relative": str(root.relative_to(ROOT)),
        "sqlite": {"filename": SQLITE_FILENAME, "sha256": inventory["sqlite"]["sha256"]},
        "accdb": {"filename": ACCDB_FILENAME, "sha256": inventory["accdb"]["sha256"]},
        "remote_row_count": len(REMOTE_ROWS),
    }


# --------------------------------------------------------------------------- #
# reading the built fixture
# --------------------------------------------------------------------------- #


def _iter_user_tabledefs(database: Any) -> list[tuple[str, int, Any]]:
    """Return (name, attributes, tabledef) for every non-system user table."""
    tables: list[tuple[str, int, Any]] = []
    for tabledef in database.TableDefs:
        name = str(tabledef.Name)
        if name.startswith("MSys") or name.startswith("~"):
            continue
        attributes = int(tabledef.Attributes)
        if attributes & SYSTEM_OR_HIDDEN:
            continue
        tables.append((name, attributes, tabledef))
    return tables


def _field_names(tabledef: Any) -> list[str]:
    """Return the local field names of a tabledef (never called on the ODBC link)."""
    return [str(field.Name) for field in tabledef.Fields]


def _read_rows(database: Any, table: str) -> list[tuple[int, str]]:
    """Read every row of a two-column table through a snapshot recordset."""
    recordset = database.OpenRecordset(table, DAO_DB_OPEN_SNAPSHOT)
    try:
        rows: list[tuple[int, str]] = []
        while not recordset.EOF:
            rows.append((int(recordset.Fields(0).Value), str(recordset.Fields(1).Value)))
            recordset.MoveNext()
        return rows
    finally:
        recordset.Close()


def _com_error_code(exc: BaseException) -> int | None:
    """Return the DAO/COM scode from a pywintypes com_error, or None."""
    args = getattr(exc, "args", ())
    if len(args) >= 3 and isinstance(args[2], tuple) and len(args[2]) >= 5:
        return int(args[2][4])
    return None


def _direct_odbc_probe(sqlite_path: Path) -> dict[str, Any]:
    """Read the two rows through the ODBC driver directly (driver, not ACE)."""
    try:
        import pyodbc
    except ImportError as exc:
        raise UnsupportedError(f"pyodbc unavailable: {exc}") from exc
    connect_string = f"DRIVER={{{SQLITE_DRIVER}}};DATABASE={sqlite_path};"
    connection = pyodbc.connect(connect_string, timeout=15)
    try:
        cursor = connection.cursor()
        cursor.execute(f"SELECT id, label FROM {REMOTE_TABLE} ORDER BY id")
        rows = [(int(row[0]), str(row[1])) for row in cursor.fetchall()]
        dbms_version = str(connection.getinfo(pyodbc.SQL_DBMS_VER))
    finally:
        connection.close()
    return {"row_count": len(rows), "rows": rows, "dbms_version": _redact(dbms_version)}


def cmd_prove_contact(root: Path) -> dict[str, Any]:
    """Prove the ACE ODBC link is genuine and contacts SQLite for both rows."""
    inventory = _require_built(root)
    sqlite_path = root / inventory["sqlite"]["filename"]
    accdb_path = root / inventory["accdb"]["filename"]
    sqlite_before = _sha256(sqlite_path)
    accdb_before = _sha256(accdb_path)
    engine = _dao_engine()
    try:
        database = _open_accdb_readonly(engine, accdb_path)
        try:
            tables = _iter_user_tabledefs(database)
            names = [name for name, _, _ in tables]
            local = next((entry for entry in tables if entry[0] == LOCAL_TABLE), None)
            link = next((entry for entry in tables if entry[0] == LINK_NAME), None)
            local_fields = _field_names(local[2]) if local is not None else []
            local_rows = _read_rows(database, LOCAL_TABLE) if local is not None else []
            link_attributes = link[1] if link is not None else 0
            connect = str(link[2].Connect) if link is not None else ""
            source_table = str(link[2].SourceTableName) if link is not None else ""
            linked_rows = _read_rows(database, LINK_NAME) if link is not None else []
        finally:
            _close_object(database)
    finally:
        _release_dao(engine)
    sqlite_after = _sha256(sqlite_path)
    accdb_after = _sha256(accdb_path)
    # The direct driver probe runs after the DAO session closes: pyodbc and ACE share
    # the process ODBC driver manager, and mixing their ordering can break ACE's ODBC
    # path (see ``_sqlite_driver_registered``).
    direct = _direct_odbc_probe(sqlite_path)
    checks = {
        "local_table_present": local is not None,
        "local_table_is_not_attached": local is not None and not (local[1] & (DB_ATTACHED_ODBC | 0x40000000)),
        "link_present": link is not None,
        "link_attributes_odbc_bit": bool(link_attributes & DB_ATTACHED_ODBC),
        "link_source_table_matches": source_table == REMOTE_TABLE,
        "link_read_returns_two_rows": linked_rows == list(REMOTE_ROWS),
        "direct_odbc_read_matches_linked_read": direct["rows"] == linked_rows,
        "sqlite_bytes_unchanged": sqlite_after == inventory["sqlite"]["sha256"] == sqlite_before,
        "accdb_bytes_unchanged": accdb_after == inventory["accdb"]["sha256"] == accdb_before,
    }
    return {
        "ok": all(checks.values()),
        "command": "prove-contact",
        "root": str(root),
        "checks": checks,
        "local_inventory": names,
        "local_table": {
            "name": LOCAL_TABLE,
            "attributes": local[1] if local is not None else None,
            "fields": local_fields,
            "row_count": len(local_rows),
        },
        "link_metadata": {
            "name": LINK_NAME,
            "attributes": link_attributes,
            "attributes_hex": hex(link_attributes & 0xFFFFFFFF),
            "db_attached_odbc_bit": bool(link_attributes & DB_ATTACHED_ODBC),
            "db_attach_save_pwd_bit": bool(link_attributes & DB_ATTACH_SAVE_PWD),
            "source_table": source_table,
            "connect_scheme": connect.split(";", 1)[0] if connect else "",
            "connect_driver_matches": f"{{{SQLITE_DRIVER}}}" in connect,
            "connect_database_matches_source": str(sqlite_path).casefold() in connect.casefold(),
            "pwd_segment_present": ";PWD=" in connect,
            "canary_retained_in_stored_connect": CANARY in connect,
        },
        "linked_read": {"row_count": len(linked_rows), "rows": [list(row) for row in linked_rows]},
        "direct_odbc": direct,
        "proof_limits": list(PROOF_LIMITS),
    }


def _connect_key_names(connect: str) -> list[str]:
    """Return only the left-hand key names of a connection string, never values."""
    return sorted({match.group(1).strip().casefold() for match in _KEY_RE.finditer(connect)})


def _database_value(connect: str) -> str:
    """Return the raw ``Database=`` value (non-credential metadata) or an empty string."""
    match = _DATABASE_VALUE_RE.search(connect)
    return match.group(1) if match else ""


def cmd_prove_stored_credential(root: Path) -> dict[str, Any]:
    """Positively assert the built link's stored ``Connect`` retains the fake credential.

    This is the T5/S4a positive control: a genuine, non-vacuous runtime proof needs a
    link whose stored metadata really carries a credential before redaction can be
    claimed. The command emits only booleans, counts, and connection-string key names;
    the raw ``Connect`` value and the canary are never written to stdout. It performs a
    linked read, so with a relative ``Database=`` value the caller must pin CWD to the
    fixture root.
    """
    inventory = _require_built(root)
    sqlite_path = root / inventory["sqlite"]["filename"]
    accdb_path = root / inventory["accdb"]["filename"]
    sqlite_before = _sha256(sqlite_path)
    accdb_before = _sha256(accdb_path)
    engine = _dao_engine()
    try:
        database = _open_accdb_readonly(engine, accdb_path)
        try:
            tables = _iter_user_tabledefs(database)
            link = next((entry for entry in tables if entry[0] == LINK_NAME), None)
            connect = str(link[2].Connect) if link is not None else ""
            attributes = link[1] if link is not None else 0
            linked_rows = _read_rows(database, LINK_NAME) if link is not None else []
        finally:
            _close_object(database)
    finally:
        _release_dao(engine)
    sqlite_after = _sha256(sqlite_path)
    accdb_after = _sha256(accdb_path)
    database_value = _database_value(connect)
    checks = {
        "link_present": link is not None,
        "link_attributes_odbc_bit": bool(attributes & DB_ATTACHED_ODBC),
        "link_attributes_save_pwd_bit": bool(attributes & DB_ATTACH_SAVE_PWD),
        "stored_connect_has_pwd_segment": ";PWD=" in connect.upper(),
        "stored_connect_contains_canary": CANARY in connect,
        "linked_read_returns_expected_rows": linked_rows == list(REMOTE_ROWS),
        "sqlite_bytes_unchanged": sqlite_after == inventory["sqlite"]["sha256"] == sqlite_before,
        "accdb_bytes_unchanged": accdb_after == inventory["accdb"]["sha256"] == accdb_before,
    }
    return {
        "ok": all(checks.values()),
        "command": "prove-stored-credential",
        "root": str(root),
        "checks": checks,
        "database_style": inventory.get("database_style", DATABASE_STYLE_ABSOLUTE),
        "connect_scheme": connect.split(";", 1)[0] if connect else "",
        "connect_keys": _connect_key_names(connect),
        "connect_length": len(connect),
        "stored_database_value_is_relative": bool(database_value) and database_value == SQLITE_FILENAME,
        "linked_read": {"row_count": len(linked_rows), "rows": [list(row) for row in linked_rows]},
        "link_attributes_hex": hex(attributes & 0xFFFFFFFF),
        "proof_limits": list(PROOF_LIMITS),
    }


# --------------------------------------------------------------------------- #
# isolation tripwire
# --------------------------------------------------------------------------- #


def _open_exclusive_handle(path: Path) -> int:
    """Open ``path`` with share mode 0 so no other handle can open it."""
    if os.name != "nt":
        raise UnsupportedError("exclusive file-sharing lock requires Windows")
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(str(path), 0x80000000, 0, None, 3, 0, None)
    if handle == _INVALID_HANDLE:
        raise WorkerError(f"exclusive lock on {path.name} failed (WinError {ctypes.get_last_error()})")
    return int(handle)


def _close_handle(handle: int) -> None:
    """Close a Win32 handle returned by :func:`_open_exclusive_handle`."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    kernel32.CloseHandle(ctypes.c_void_p(handle))


class ExclusiveFileLock:
    """Context manager holding a Win32 share-mode-0 handle on one file."""

    def __init__(self, path: Path) -> None:
        """Record the file to lock; no handle is opened until ``acquire``."""
        self._path = path
        self._handle: int | None = None

    def acquire(self) -> None:
        """Acquire the exclusive handle."""
        self._handle = _open_exclusive_handle(self._path)

    def release(self) -> None:
        """Release the exclusive handle if held."""
        if self._handle is not None:
            _close_handle(self._handle)
            self._handle = None

    def __enter__(self) -> ExclusiveFileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release()


def _plain_read_allowed(path: Path) -> bool:
    """Return True when a plain reader can open the file now."""
    try:
        with path.open("rb") as handle:
            handle.read(16)
    except OSError:
        return False
    return True


def cmd_prove_isolation(root: Path, isolation: str) -> dict[str, Any]:
    """Validate the exclusive-lock tripwire and its unlocked positive control."""
    if isolation != ISOLATION_EXCLUSIVE_LOCK:
        raise WorkerError(f"unsupported isolation mode: {isolation}")
    inventory = _require_built(root)
    sqlite_path = root / inventory["sqlite"]["filename"]
    accdb_path = root / inventory["accdb"]["filename"]
    sqlite_before = _sha256(sqlite_path)
    plain_read_before_lock = _plain_read_allowed(sqlite_path)
    engine = _dao_engine()
    refusal: dict[str, Any] = {"refused": False, "error_code": None, "error_message": "", "row_count": 0}
    post_release: dict[str, Any] = {"row_count": 0, "error": ""}
    locked_plain_read_allowed = True
    link_present = False
    try:
        database = _open_accdb_readonly(engine, accdb_path)
        try:
            link_present = any(name == LINK_NAME for name, _, _ in _iter_user_tabledefs(database))
            with ExclusiveFileLock(sqlite_path):
                locked_plain_read_allowed = _plain_read_allowed(sqlite_path)
                try:
                    rows = _read_rows(database, LINK_NAME)
                    refusal["row_count"] = len(rows)
                except Exception as exc:
                    refusal = {
                        "refused": True,
                        "error_code": _com_error_code(exc),
                        "error_message": _redact(str(exc)),
                        "row_count": 0,
                    }
            try:
                rows = _read_rows(database, LINK_NAME)
                post_release = {"row_count": len(rows), "rows": [list(row) for row in rows], "error": ""}
            except Exception as exc:
                post_release = {"row_count": 0, "rows": [], "error": _redact(str(exc))}
        finally:
            _close_object(database)
    finally:
        _release_dao(engine)
    sqlite_after = _sha256(sqlite_path)
    plain_read_after_release = _plain_read_allowed(sqlite_path)
    checks = {
        "link_present": link_present,
        "positive_control_plain_read_before_lock": plain_read_before_lock,
        "lock_refuses_plain_read": not locked_plain_read_allowed,
        "lock_refuses_linked_read": refusal["refused"],
        "positive_control_linked_read_after_release": post_release["row_count"] == len(REMOTE_ROWS),
        "plain_read_allowed_after_release": plain_read_after_release,
        "sqlite_bytes_unchanged": sqlite_after == inventory["sqlite"]["sha256"] == sqlite_before,
    }
    return {
        "ok": all(checks.values()),
        "command": "prove-isolation",
        "root": str(root),
        "isolation": isolation,
        "checks": checks,
        "locked_linked_read_refusal": refusal,
        "post_release_linked_read": post_release,
        "sqlite_sha256_before": sqlite_before,
        "sqlite_sha256_after": sqlite_after,
        "proof_limits": list(PROOF_LIMITS),
    }


# --------------------------------------------------------------------------- #
# raw Access ODBC catalog types
# --------------------------------------------------------------------------- #


def _odbc_catalog_table_types(accdb_path: Path) -> dict[str, str]:
    """Return Access ODBC ``SQLTables`` name -> table type for the built database.

    Uses only the Access ODBC driver's catalog call: no DAO session, no table-row read,
    and no linked-source resolution are involved, so a local ``TABLE`` and a linked
    ``SYNONYM`` are distinguishable from the driver's own metadata. The open is
    engine-level read-only. pyodbc is imported here rather than at module import so the
    non-ODBC commands stay importable when pyodbc is absent.
    """
    try:
        import pyodbc
    except ImportError as exc:
        raise UnsupportedError(f"pyodbc unavailable: {exc}") from exc
    connect_string = f"DBQ={accdb_path};DRIVER={{{ACCESS_ODBC_DRIVER}}}"
    try:
        connection = pyodbc.connect(connect_string, readonly=True, timeout=15)
    except Exception as exc:
        raise UnsupportedError(f"Access ODBC catalog open failed: {_redact(str(exc))}") from exc
    try:
        cursor = connection.cursor()
        try:
            cursor.tables()
            return {
                str(row.table_name): str(getattr(row, "table_type", ""))
                for row in cursor.fetchall()
                if getattr(row, "table_name", None)
            }
        finally:
            cursor.close()
    finally:
        connection.close()


def cmd_catalog_types(root: Path) -> dict[str, Any]:
    """Report raw Access ODBC ``SQLTables`` types for the fixture's user tables.

    This is deliberately separate from the documentation inspection: it proves runtime
    ``SYNONYM`` recognition from the driver's own catalog metadata. It also records that
    this fixture contains no unclassified user table type, so the inspection's
    fail-closed unknown-type path is *not* exercised here and stays covered only by the
    unit-level classification-before-read proofs.
    """
    inventory = _require_built(root)
    accdb_path = root / inventory["accdb"]["filename"]
    table_types = _odbc_catalog_table_types(accdb_path)
    user_table_types = {
        name: table_type
        for name, table_type in sorted(table_types.items())
        if not name.startswith("MSys")
    }
    checks = {
        "local_table_is_table": user_table_types.get(LOCAL_TABLE) == ODBC_LOCAL_TABLE_TYPE,
        "link_is_synonym": user_table_types.get(LINK_NAME) == ODBC_SYNONYM_TABLE_TYPE,
        "no_unknown_user_table_type_present": all(
            table_type in (ODBC_LOCAL_TABLE_TYPE, ODBC_SYNONYM_TABLE_TYPE)
            for table_type in user_table_types.values()
        ),
    }
    return {
        "ok": all(checks.values()),
        "command": "catalog-types",
        "root": str(root),
        "checks": checks,
        "user_table_types": user_table_types,
    }


# --------------------------------------------------------------------------- #
# cleanup and entry point
# --------------------------------------------------------------------------- #


def cmd_cleanup(root: Path) -> dict[str, Any]:
    """Remove exactly one self-created ``sqlite-odbc-*`` root and nothing else."""
    if not root.is_dir():
        raise WorkerError(f"root is not an existing directory: {root}")
    if root.parent.resolve() != FIXTURE_ROOT.resolve():
        raise WorkerError("refusing to remove anything but a direct child of db/fixtures")
    removed = [str(child.relative_to(root)) for child in sorted(root.iterdir())]
    shutil.rmtree(root)
    return {"ok": True, "command": "cleanup", "root": str(root), "removed": removed}


def _parser() -> argparse.ArgumentParser:
    """Build the bounded subcommand parser."""
    parser = argparse.ArgumentParser(description="SQLite -> Access ODBC link fixture worker.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("environment", help="report ACE/driver availability; no writes")

    build = subparsers.add_parser("build", help="create a fresh confined fixture root")
    build.add_argument("--root", required=True)
    build.add_argument(
        "--database-style",
        choices=DATABASE_STYLES,
        default=DATABASE_STYLE_ABSOLUTE,
        help="ODBC Database value: absolute path (default) or relative file name",
    )

    stored = subparsers.add_parser(
        "prove-stored-credential", help="assert the stored Connect retains the fake credential"
    )
    stored.add_argument("--root", required=True)

    contact = subparsers.add_parser("prove-contact", help="prove the link is genuine and live")
    contact.add_argument("--root", required=True)

    isolation = subparsers.add_parser("prove-isolation", help="validate the exclusive-lock tripwire")
    isolation.add_argument("--root", required=True)
    isolation.add_argument("--isolation", choices=ISOLATION_MODES, default=ISOLATION_EXCLUSIVE_LOCK)

    catalog = subparsers.add_parser("catalog-types", help="report raw Access ODBC SQLTables types")
    catalog.add_argument("--root", required=True)

    cleanup = subparsers.add_parser("cleanup", help="remove exactly one self-created root")
    cleanup.add_argument("--root", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one bounded command and print a single JSON result line."""
    args = _parser().parse_args(argv)
    command = args.command
    try:
        if command == "environment":
            result = cmd_environment()
        else:
            root = _resolve_root(args.root)
            if command == "build":
                result = cmd_build(root, args.database_style)
            elif command == "prove-contact":
                result = cmd_prove_contact(root)
            elif command == "prove-stored-credential":
                result = cmd_prove_stored_credential(root)
            elif command == "prove-isolation":
                result = cmd_prove_isolation(root, args.isolation)
            elif command == "catalog-types":
                result = cmd_catalog_types(root)
            else:
                result = cmd_cleanup(root)
        _emit(result)
        if result.get("ok"):
            return EXIT_OK
        raise CheckFailedError("control conditions did not hold")
    except UnsupportedError as exc:
        _emit({"ok": False, "command": command, "error": _redact(str(exc)), "error_kind": "unsupported"})
        return EXIT_UNSUPPORTED
    except CheckFailedError as exc:
        _emit({"ok": False, "command": command, "error": _redact(str(exc)), "error_kind": "check_failed"})
        return EXIT_CHECK_FAILED
    except WorkerError as exc:
        _emit({"ok": False, "command": command, "error": _redact(str(exc)), "error_kind": "usage"})
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 — last-resort redacted failure envelope
        _emit({"ok": False, "command": command, "error": _redact(str(exc)), "error_kind": "unexpected"})
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    raise SystemExit(main())
