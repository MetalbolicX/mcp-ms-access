"""Owned Windows subprocess fixture for a password-protected Access database.

Purpose
-------
Builds a disposable, repository-confined ``.accdb`` that really is password-protected:
it is created with the fake password ``canary-e9c4`` in the DAO ``CreateDatabase`` locale
(``;LANGID=0x0409;CP=1252;COUNTRY=0;pwd=<canary>``, ``dbVersion120``) and holds one local
table with one row. The shipped documentation-inspection path can then be qualified on a
genuine encrypted source instead of an unencrypted stand-in.

The fake password reaches the shipped inspection path the only way a password ever does:
through the server-side credential resolver. ``build`` registers it with the shipped
writer (``credentials.write_entry(..., local=True)`` -- exactly what
``ms-access-mcp credentials set --db <path> --local`` calls, ``cli.py:1009-1091``), and
every later command resolves it with ``credentials.resolve_password``, the same resolver
the documentation worker child uses (``application/documentation_worker.py``
``_run_request`` -> ``DatabaseUseCases.open_database`` ->
``application/database.py:148-180``). The password is never passed on argv, never placed
in the environment, never emitted in a result, and never rendered into a message.

Execution model
---------------
Every command runs as its own short-lived subprocess and performs at most ONE DAO
``DBEngine.120`` dispatch/release cycle (a second engine lifecycle in the same process is
a known hazard). ``Access.Application`` is never started, no DSN or system data source is
created, no registry value is written, and no child process is spawned. Generated state is
confined to a unique ``db/fixtures/encrypted-db-*`` root, and the caller orchestrates the
commands in order; no command ever deletes pre-existing state.

Command API (one JSON object per line on stdout, all booleans/counts/codes/digests)
-----------------------------------------------------------------------------------
``environment``             report Windows/ACE DAO/Access-ODBC-driver availability; no writes.
``build --root``            create ``root``, the password-protected ``encrypted.accdb`` with
                            its local table and row, and the local credential-store entry.
``verify --root``           reopen in a fresh process and report the positive controls: the
                            stored password resolves, an unprotected DAO open is refused, and a
                            protected read returns the local row.
``probe-write-odbc --root`` drive the shipped ODBC inspection seam and report that a write on
                            that connection is refused.
``probe-write-dao --root``  drive the shipped DAO read-only reopen and report that a write on
                            the reopened handle is refused.
``cleanup --root``          guarded removal of exactly one self-created root.

Why the two write probes differ
-------------------------------
``probe-write-odbc`` runs on the fixture database itself. The shipped inspection seam is
``OdbcAdapter.open_readonly_inspection_connection`` (``backends/odbc.py:75-88``), which is
what ``documentation_inspect.open_odbc_inspection_port`` calls, and it opens with
``readonly=True`` so the driver refuses the write. The attempt still runs inside an
explicit transaction that is always rolled back, so even in the defect case where the
driver ignored the read-only access mode the fixture bytes must stay unchanged.

``probe-write-dao`` runs on a byte-identical disposable copy inside the same root. The
shipped DAO seam is ``ComDispatcher.open_dao_database`` (recording target + password)
followed by ``ComDispatcher.reopen_dao_database_read_only``
(``backends/dispatcher.py:154-200``), which is exactly what
``documentation_inspect.open_inspection_port`` calls (``documentation_inspect.py:440-453``).
On a read-only DAO handle ``BeginTrans`` is itself refused (code 5003251), so there is no
transaction to roll back; the copy keeps the fixture itself out of reach of any write,
while a refused write leaves both the copy and the fixture byte-identical.

Exit codes: ``0`` success; ``2`` usage / invalid or missing fixture; ``3`` environment
unsupported (non-Windows or missing ACE DAO); ``4`` a control did not hold; ``1``
unexpected failure.
"""

from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "db" / "fixtures"
ROOT_PREFIX = "encrypted-db-"

DB_FILENAME = "encrypted.accdb"
WRITE_PROBE_COPY_FILENAME = "write-probe-copy.accdb"
INVENTORY_FILENAME = "inventory.json"
INVENTORY_SCHEMA = "encrypted-db-fixture/1"
LOCAL_CREDENTIAL_FILENAME = ".ms-access-mcp.json"

LOCAL_TABLE = "local_items"
LOCAL_ROW: tuple[int, str] = (1, "encrypted control row")

# Fake password only. It must satisfy the CLI's ODBC connection-string safety rule
# (``cli.py:1009-1013`` forbids ``;``, ``{`` and ``}``) so the shipped writer accepts it.
CANARY = "canary-e9c4"
FORBIDDEN_IN_PASSWORD = frozenset({";", "{", "}"})
assert not (FORBIDDEN_IN_PASSWORD & set(CANARY)), "the fake password must satisfy the CLI rule"

LOCALE = ";LANGID=0x0409;CP=1252;COUNTRY=0"
CREATE_VERSION_ACE = 128  # dbVersion120
DAO_FAIL_ON_ERROR = 128
DAO_DB_OPEN_SNAPSHOT = 4

# The exact driver name the shipped adapter and CLI resolve (``backends/odbc.py:51-56``).
ACCESS_ODBC_DRIVER = "Microsoft Access Driver (*.mdb, *.accdb)"

CREATE_LOCAL_SQL = (
    f"CREATE TABLE [{LOCAL_TABLE}] ([id] LONG NOT NULL, [note] TEXT(50), "
    f"CONSTRAINT [PK_{LOCAL_TABLE}] PRIMARY KEY ([id]))"
)
INSERT_LOCAL_SQL = f"INSERT INTO [{LOCAL_TABLE}] ([id], [note]) VALUES (1, 'encrypted control row')"
WRITE_PROBE_SQL = f"INSERT INTO [{LOCAL_TABLE}] ([id], [note]) VALUES (99, 'read-only probe row')"

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_UNSUPPORTED = 3
EXIT_CHECK_FAILED = 4

_PWD_RE = re.compile(r"(?i)\bPWD=[^;]*")
_NATIVE_CODE_RE = re.compile(r"\((-?\d+)\)")


class WorkerError(Exception):
    """Reportable usage or fixture failure (exit code 2)."""


class UnsupportedError(WorkerError):
    """The environment cannot run this qualification (exit code 3)."""


# --------------------------------------------------------------------------- #
# output, digests and confinement
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
    """Return a confinement-checked ``db/fixtures/encrypted-db-*`` root."""
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


def _inventory_path(root: Path) -> Path:
    return root / INVENTORY_FILENAME


def _load_inventory(root: Path) -> dict[str, Any]:
    """Load the build inventory or fail with a usage error."""
    path = _inventory_path(root)
    if not path.is_file():
        raise WorkerError(f"fixture inventory missing; run build first: {INVENTORY_FILENAME}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_inventory(root: Path, inventory: dict[str, Any]) -> None:
    _inventory_path(root).write_text(
        json.dumps(inventory, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _local_credential_path(root: Path) -> Path:
    """Return the fixture-local credential store path the shipped resolver reads."""
    return root / LOCAL_CREDENTIAL_FILENAME


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
        raise UnsupportedError(f"ACE DAO (DAO.DBEngine.120) unavailable: {_redact(str(exc))}") from exc


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


def _com_error_code(exc: BaseException) -> int | None:
    """Return the DAO/COM scode from a pywintypes com_error, or None."""
    args = getattr(exc, "args", ())
    if len(args) >= 3 and isinstance(args[2], tuple) and len(args[2]) >= 5:
        return int(args[2][4])
    return None


def _odbc_sqlstate(exc: BaseException) -> str:
    """Return the SQLSTATE of a pyodbc error, or an empty string."""
    args = getattr(exc, "args", ())
    return str(args[0]) if args else ""


def _odbc_error_code(exc: BaseException) -> int | None:
    """Return the driver's native error code parsed from a pyodbc message, or None.

    Only the numeric code is ever returned, so no driver message text (which can embed a
    connection string) is propagated into a result.
    """
    args = getattr(exc, "args", ())
    message = str(args[1]) if len(args) >= 2 else str(exc)
    match = _NATIVE_CODE_RE.search(message)
    return int(match.group(1)) if match else None


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
            reasons.append(_redact(str(exc)))
        else:
            try:
                dao_available = True
                dao_version = str(engine.Version)
            finally:
                _release_dao(engine)
    driver_available = False
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover - pyodbc is a runtime dependency
        reasons.append(f"pyodbc unavailable: {exc}")
    else:
        driver_available = ACCESS_ODBC_DRIVER in pyodbc.drivers()
        if not driver_available:
            reasons.append(f"Access ODBC driver unavailable: {ACCESS_ODBC_DRIVER}")
    return {
        "ok": not reasons,
        "command": "environment",
        "python_executable": sys.executable,
        "platform": os.name,
        "ace_dao_available": dao_available,
        "ace_dao_version": dao_version,
        "access_odbc_driver_available": driver_available,
        "unsupported_reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# fixture construction
# --------------------------------------------------------------------------- #


def cmd_build(root: Path) -> dict[str, Any]:
    """Create the protected database, its local row, and the local credential entry."""
    if root.exists():
        raise WorkerError(f"root already exists; never overwrite fixtures: {root}")
    if os.name != "nt":
        raise UnsupportedError("not Windows")
    root.mkdir(parents=True)
    database_path = root / DB_FILENAME
    engine = _dao_engine()
    try:
        database = engine.CreateDatabase(
            str(database_path), LOCALE + f";pwd={CANARY}", CREATE_VERSION_ACE
        )
        try:
            database.Execute(CREATE_LOCAL_SQL, DAO_FAIL_ON_ERROR)
            database.Execute(INSERT_LOCAL_SQL, DAO_FAIL_ON_ERROR)
        finally:
            _close_object(database)
    finally:
        _release_dao(engine)

    # The shipped writer ``credentials set --local`` calls; the password is stored where
    # the shipped resolver (and therefore the documentation worker child) looks for it.
    from ms_access_mcp.credentials import write_entry

    write_entry(str(database_path), CANARY, local=True)

    inventory = {
        "schema": INVENTORY_SCHEMA,
        "database": {"filename": DB_FILENAME, "sha256": _sha256(database_path)},
        "local_table": LOCAL_TABLE,
        "row_count": 1,
        "credential_store": {"filename": LOCAL_CREDENTIAL_FILENAME, "scope": "local"},
    }
    _write_inventory(root, inventory)
    return {
        "ok": True,
        "command": "build",
        "root": str(root),
        "root_relative": str(root.relative_to(ROOT)),
        "database_sha256": inventory["database"]["sha256"],
        "credential_store_written": _local_credential_path(root).is_file(),
        "row_count": inventory["row_count"],
    }


# --------------------------------------------------------------------------- #
# positive controls
# --------------------------------------------------------------------------- #


def cmd_verify(root: Path) -> dict[str, Any]:
    """Report the encrypted-source positive controls in this fresh process.

    The stored password must resolve, an unprotected DAO open of the encrypted file must
    be refused with the ACE ``Not a valid password`` scode (``5003031``), and a protected
    read must return the local row. Nothing is written.
    """
    inventory = _load_inventory(root)
    database_path = root / DB_FILENAME
    if not database_path.is_file():
        raise WorkerError("fixture incomplete; run build first")

    from ms_access_mcp.credentials import resolve_password

    resolved = bool(resolve_password(str(database_path)))
    database_before = _sha256(database_path)

    engine = _dao_engine()
    try:
        try:
            unprotected = engine.OpenDatabase(str(database_path), False, False)
            try:
                unprotected.Close()
            finally:
                unprotected = None
                gc.collect()
            unprotected_refused = False
            unprotected_error_code: int | None = None
        except Exception as exc:  # noqa: BLE001 - refusal is the expected evidence
            unprotected_refused = True
            unprotected_error_code = _com_error_code(exc)

        rows: list[tuple[int, str]] = []
        protected_read_failed = False
        try:
            protected = engine.OpenDatabase(str(database_path), False, True, f";PWD={CANARY}")
            try:
                rows = _read_rows(protected, LOCAL_TABLE)
            finally:
                _close_object(protected)
        except Exception:  # noqa: BLE001 - recorded as a failed control, never swallowed
            protected_read_failed = True
    finally:
        _release_dao(engine)

    checks = {
        "password_resolves": resolved,
        "unprotected_open_refused": unprotected_refused,
        "protected_read_returns_local_row": rows == [LOCAL_ROW],
        "database_bytes_unchanged": _sha256(database_path)
        == inventory["database"]["sha256"]
        == database_before,
    }
    return {
        "ok": all(checks.values()),
        "command": "verify",
        "root": str(root),
        "checks": checks,
        "unprotected_open_error_code": unprotected_error_code,
        "protected_read_failed": protected_read_failed,
        "protected_row_count": len(rows),
        "database_sha256": _sha256(database_path),
    }


# --------------------------------------------------------------------------- #
# runtime read-only proofs
# --------------------------------------------------------------------------- #


def _odbc_connect_string(database_path: Path, password: str | None) -> str:
    """Build the DSN-less connect string the shipped adapter builds (``odbc.py:51-56``)."""
    parts = [f"DBQ={database_path}"]
    if password is not None:
        parts.append(f"PWD={password}")
    parts.append(f"DRIVER={{{ACCESS_ODBC_DRIVER}}}")
    return ";".join(parts)


def _odbc_count(connection: Any) -> int:
    """Return the local table's row count through an open ODBC connection."""
    cursor = connection.cursor()
    try:
        cursor.execute(f"SELECT COUNT(*) FROM [{LOCAL_TABLE}]")
        return int(cursor.fetchone()[0])
    finally:
        cursor.close()


def cmd_probe_write_odbc(root: Path) -> dict[str, Any]:
    """Prove the shipped ODBC inspection connection refuses a write at the engine level.

    ``OdbcAdapter.connect`` records the target and the resolved credential and
    ``open_readonly_inspection_connection`` (``odbc.py:75-88``) is the exact call
    ``open_odbc_inspection_port`` makes (``documentation_inspect.py:456-479``), so a write
    attempt on that handle is a faithful runtime statement about the inspection
    connection. The attempt runs inside an explicit transaction that is always rolled
    back; the local row count and the database digest must be identical afterwards.
    """
    import pyodbc

    from ms_access_mcp.backends.odbc import OdbcAdapter
    from ms_access_mcp.credentials import resolve_password

    inventory = _load_inventory(root)
    database_path = root / DB_FILENAME
    if not database_path.is_file():
        raise WorkerError("fixture incomplete; run build first")

    resolved = resolve_password(str(database_path))
    database_before = _sha256(database_path)

    # Control: without the resolved password the same driver/model refuses the open.
    try:
        control = pyodbc.connect(_odbc_connect_string(database_path, None), timeout=8)
        control.close()
        unprotected_refused = False
        unprotected_error_code: int | None = None
        unprotected_sqlstate = ""
    except Exception as exc:  # noqa: BLE001 - refusal is the expected evidence
        unprotected_refused = True
        unprotected_error_code = _odbc_error_code(exc)
        unprotected_sqlstate = _odbc_sqlstate(exc)

    adapter = OdbcAdapter(str(database_path))
    inspection = None
    opened = False
    write_refused = False
    write_error_code: int | None = None
    write_sqlstate = ""
    rows_before = -1
    rows_after = -1
    error_kind = ""
    try:
        adapter.connect(str(database_path), password=resolved)
        inspection = adapter.open_readonly_inspection_connection()
        opened = True
        rows_before = _odbc_count(inspection)
        try:
            inspection.autocommit = False
            inspection.cursor().execute(WRITE_PROBE_SQL)
            inspection.rollback()
        except Exception as exc:  # noqa: BLE001 - refusal is the expected evidence
            write_refused = True
            write_error_code = _odbc_error_code(exc)
            write_sqlstate = _odbc_sqlstate(exc)
            with contextlib.suppress(Exception):
                inspection.rollback()
        rows_after = _odbc_count(inspection)
    except Exception as exc:  # noqa: BLE001 - reported as a failed control by class name only
        error_kind = type(exc).__name__
    finally:
        if inspection is not None:
            with contextlib.suppress(Exception):
                inspection.close()
        with contextlib.suppress(Exception):
            adapter.disconnect()

    checks = {
        "password_resolves": bool(resolved),
        "unprotected_open_refused": unprotected_refused,
        "inspection_connection_opened": opened,
        "write_refused_on_inspection_connection": write_refused,
        "row_count_unchanged": rows_before == rows_after == inventory["row_count"],
        "database_bytes_unchanged": _sha256(database_path)
        == inventory["database"]["sha256"]
        == database_before,
    }
    return {
        "ok": all(checks.values()),
        "command": "probe-write-odbc",
        "root": str(root),
        "checks": checks,
        "unprotected_error_code": unprotected_error_code,
        "unprotected_sqlstate": unprotected_sqlstate,
        "write_error_code": write_error_code,
        "write_sqlstate": write_sqlstate,
        "row_count_before": rows_before,
        "row_count_after": rows_after,
        "error_kind": error_kind,
        "database_sha256": _sha256(database_path),
    }


def _dao_count_on_current(dispatcher: Any) -> int:
    """STA-thread body: count the local table through the dispatcher's current handle."""
    database = dispatcher.current_db
    recordset = database.OpenRecordset(f"SELECT COUNT(*) AS n FROM [{LOCAL_TABLE}]")
    try:
        return int(recordset.Fields(0).Value)
    finally:
        recordset.Close()


def _dao_write_attempt_on_current(dispatcher: Any) -> dict[str, Any]:
    """STA-thread body: attempt one non-transactional insert on the current handle."""
    database = dispatcher.current_db
    try:
        database.Execute(WRITE_PROBE_SQL, DAO_FAIL_ON_ERROR)
        return {"refused": False, "error_code": None}
    except Exception as exc:  # noqa: BLE001 - refusal is the expected evidence
        return {"refused": True, "error_code": _com_error_code(exc)}


def cmd_probe_write_dao(root: Path) -> dict[str, Any]:
    """Prove the shipped DAO read-only reopen refuses a write at the engine level.

    ``ComDispatcher.open_dao_database`` records the target and password,
    ``reopen_dao_database_read_only`` (``dispatcher.py:186-200``) is the exact call
    ``open_inspection_port`` makes (``documentation_inspect.py:440-453``), and every read
    or write attempt runs through ``dispatcher.call`` so the handle is only touched on its
    owning STA thread. The attempt targets a byte-identical disposable copy inside the
    same root, because a read-only DAO handle refuses ``BeginTrans`` itself and therefore
    offers no transaction to roll back.
    """
    from ms_access_mcp.backends.dispatcher import ComDispatcher
    from ms_access_mcp.credentials import resolve_password

    inventory = _load_inventory(root)
    database_path = root / DB_FILENAME
    if not database_path.is_file():
        raise WorkerError("fixture incomplete; run build first")
    probe_copy = root / WRITE_PROBE_COPY_FILENAME
    if probe_copy.exists():
        raise WorkerError(f"write-probe copy already exists; never overwrite fixtures: {probe_copy.name}")

    database_before = _sha256(database_path)
    shutil.copy2(database_path, probe_copy)
    copy_before = _sha256(probe_copy)
    resolved = resolve_password(str(database_path))

    dispatcher = ComDispatcher()
    reopened = False
    write_refused = False
    write_error_code: int | None = None
    rows_before = -1
    rows_after = -1
    error_kind = ""
    try:
        dispatcher.start()
        dispatcher.open_dao_database(str(probe_copy), password=resolved, read_only=False)
        dispatcher.reopen_dao_database_read_only()
        reopened = True
        rows_before = dispatcher.call(_dao_count_on_current, dispatcher)
        outcome = dispatcher.call(_dao_write_attempt_on_current, dispatcher)
        write_refused = bool(outcome["refused"])
        write_error_code = outcome["error_code"]
        rows_after = dispatcher.call(_dao_count_on_current, dispatcher)
    except Exception as exc:  # noqa: BLE001 - reported as a failed control by class name only
        error_kind = type(exc).__name__
    finally:
        with contextlib.suppress(Exception):
            dispatcher.shutdown()

    checks = {
        "password_resolves": bool(resolved),
        "readonly_reopen_reused_recorded_target_and_password": reopened,
        "write_refused_on_reopened_handle": write_refused,
        "row_count_unchanged": rows_before == rows_after == inventory["row_count"],
        "probe_copy_bytes_unchanged": _sha256(probe_copy) == copy_before,
        "database_bytes_unchanged": _sha256(database_path)
        == inventory["database"]["sha256"]
        == database_before,
    }
    return {
        "ok": all(checks.values()),
        "command": "probe-write-dao",
        "root": str(root),
        "checks": checks,
        "write_error_code": write_error_code,
        "row_count_before": rows_before,
        "row_count_after": rows_after,
        "probe_copy_sha256": _sha256(probe_copy),
        "error_kind": error_kind,
        "database_sha256": _sha256(database_path),
    }


# --------------------------------------------------------------------------- #
# cleanup and entry point
# --------------------------------------------------------------------------- #


def cmd_cleanup(root: Path) -> dict[str, Any]:
    """Remove exactly one self-created ``encrypted-db-*`` root and nothing else."""
    if not root.is_dir():
        raise WorkerError(f"root is not an existing directory: {root}")
    if root.parent.resolve() != FIXTURE_ROOT.resolve():
        raise WorkerError("refusing to remove anything but a direct child of db/fixtures")
    removed = [str(child.relative_to(root)) for child in sorted(root.iterdir())]
    shutil.rmtree(root)
    return {"ok": True, "command": "cleanup", "root": str(root), "removed": removed}


def _parser() -> argparse.ArgumentParser:
    """Build the bounded subcommand parser."""
    parser = argparse.ArgumentParser(description="Encrypted Access database fixture worker.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("environment", help="report ACE DAO/ODBC-driver availability; no writes")

    for name, help_text in (
        ("build", "create the protected database and its local credential entry"),
        ("verify", "assert the encrypted-source positive controls"),
        ("probe-write-odbc", "prove the shipped ODBC inspection connection refuses a write"),
        ("probe-write-dao", "prove the shipped DAO read-only reopen refuses a write"),
        ("cleanup", "remove exactly one self-created root"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--root", required=True)

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
                result = cmd_build(root)
            elif command == "verify":
                result = cmd_verify(root)
            elif command == "probe-write-odbc":
                result = cmd_probe_write_odbc(root)
            elif command == "probe-write-dao":
                result = cmd_probe_write_dao(root)
            else:
                result = cmd_cleanup(root)
    except UnsupportedError as exc:
        _emit({"ok": False, "command": command, "error": _redact(str(exc)), "error_kind": "unsupported"})
        return EXIT_UNSUPPORTED
    except WorkerError as exc:
        _emit({"ok": False, "command": command, "error": _redact(str(exc)), "error_kind": "usage"})
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 — last-resort redacted failure envelope
        _emit({"ok": False, "command": command, "error": _redact(str(exc)), "error_kind": "unexpected"})
        return EXIT_UNEXPECTED
    _emit(result)
    return EXIT_OK if result.get("ok") else EXIT_CHECK_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
