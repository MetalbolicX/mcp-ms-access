"""Owned Windows subprocess fixture for a password-protected Access link.

Purpose
-------
Builds a disposable, repository-confined fixture and validates the T5/S4a
stored-credential path: a genuine Access link (``dbAttachedTable``, ``0x40000000``)
whose stored ``TableDef.Connect`` really carries a fake credential, so the public
documentation pipeline's runtime redaction is exercised against a non-vacuous stored
secret rather than an already-dropped one.

* ``backend.accdb`` — created with the fake password ``canary-7f3a`` in the DAO
  ``CreateDatabase`` locale (``;LANGID=0x0409;CP=1252;COUNTRY=0;pwd=<canary>``,
  ``dbVersion120``) and holding one ``secret_items`` row. Its password is real: an
  unprotected DAO open is refused (observed DAO ``5003031``).
* ``frontend.accdb`` — a disposable ACE database with a local control table
  ``local_items`` and a link ``link_items`` (``SourceTableName = secret_items``)
  whose ``Connect`` is ``;PWD=<canary>;DATABASE=<backend absolute path>``. ACE stores it
  verbatim and normalizes the scheme to ``MS Access``.

Execution model
---------------
Every command runs as its own short-lived subprocess and performs at most ONE DAO
``DBEngine.120`` dispatch/release cycle (a second engine lifecycle in the same process
is a known hazard for link work). ``Access.Application`` is never started, no user
database is opened, no DSN or system data source is created, and no child process is
spawned. Generated state is confined to a unique ``db/fixtures/protected-link-*`` root
and the caller orchestrates commands in order.

Command API (one JSON object per line on stdout)
------------------------------------------------
``environment``             report Windows/ACE DAO availability; no writes.
``build-backend --root``    create ``root`` and the password-protected ``backend.accdb``.
``build-frontend --root``   create ``frontend.accdb`` with the local control and the link.
``verify --root``           reopen the front-end in a fresh process and report the stored
                            credential booleans, the linked read, and the backend's
                            refusal of an unprotected open.
``cleanup --root``          guarded removal of exactly one self-created root.

Exit codes: ``0`` success; ``2`` usage / invalid or missing fixture; ``3`` environment
unsupported (non-Windows or missing ACE DAO); ``4`` a control did not hold; ``1``
unexpected failure.

Reporting rule: only booleans, counts, hex attribute masks, and connection-string *key
names* are emitted. The raw ``Connect`` value and the canary never reach stdout, and
failure messages are redacted.
"""

from __future__ import annotations

import argparse
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
ROOT_PREFIX = "protected-link-"

BACKEND_FILENAME = "backend.accdb"
FRONTEND_FILENAME = "frontend.accdb"
INVENTORY_FILENAME = "inventory.json"
INVENTORY_SCHEMA = "protected-link-fixture/1"

BACKEND_TABLE = "secret_items"
BACKEND_ROW: tuple[int, str] = (1, "secret row")
LOCAL_TABLE = "local_items"
LINK_NAME = "link_items"

CANARY = "canary-7f3a"
LOCALE = ";LANGID=0x0409;CP=1252;COUNTRY=0"
CREATE_VERSION_ACE = 128  # dbVersion120
DAO_FAIL_ON_ERROR = 128
DAO_DB_OPEN_SNAPSHOT = 4

DB_ATTACHED_TABLE = 0x40000000
DB_ATTACHED_ODBC = 0x20000000
SYSTEM_OR_HIDDEN = 0x80000000

CREATE_LOCAL_SQL = (
    "CREATE TABLE [local_items] ([id] LONG NOT NULL, [note] TEXT(50), "
    "CONSTRAINT [PK_local_items] PRIMARY KEY ([id]))"
)
INSERT_LOCAL_SQL = "INSERT INTO [local_items] ([id], [note]) VALUES (1, 'local control row')"
CREATE_BACKEND_SQL = (
    "CREATE TABLE [secret_items] ([id] LONG NOT NULL, [note] TEXT(50), "
    "CONSTRAINT [PK_secret_items] PRIMARY KEY ([id]))"
)
INSERT_BACKEND_SQL = "INSERT INTO [secret_items] ([id], [note]) VALUES (1, 'secret row')"

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_UNSUPPORTED = 3
EXIT_CHECK_FAILED = 4

_PWD_RE = re.compile(r"(?i)\bPWD=[^;]*")
_KEY_RE = re.compile(r"([^;=]+)=")


class WorkerError(Exception):
    """Reportable usage or fixture failure (exit code 2)."""


class UnsupportedError(WorkerError):
    """The environment cannot run this qualification (exit code 3)."""


class CheckFailedError(WorkerError):
    """A control did not hold (exit code 4)."""


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
    """Return a confinement-checked ``db/fixtures/protected-link-*`` root."""
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
        raise WorkerError(f"fixture inventory missing; run build-backend first: {INVENTORY_FILENAME}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_inventory(root: Path, inventory: dict[str, Any]) -> None:
    _inventory_path(root).write_text(
        json.dumps(inventory, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _connect_key_names(connect: str) -> list[str]:
    """Return only the left-hand key names of a connection string, never values."""
    return sorted({match.group(1).strip().casefold() for match in _KEY_RE.finditer(connect)})


# --------------------------------------------------------------------------- #
# DAO lifecycle (exactly one per process)
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


def _find_tabledef(database: Any, name: str) -> Any:
    """Return the named TableDef or None."""
    for tabledef in database.TableDefs:
        if str(tabledef.Name) == name:
            return tabledef
    return None


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
    return {
        "ok": not reasons,
        "command": "environment",
        "python_executable": sys.executable,
        "platform": os.name,
        "ace_dao_available": dao_available,
        "ace_dao_version": dao_version,
        "unsupported_reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# fixture construction
# --------------------------------------------------------------------------- #


def _connect_string(backend: Path) -> str:
    """Return the Access link Connect string carrying the fake credential."""
    return f";PWD={CANARY};DATABASE={backend}"


def cmd_build_backend(root: Path) -> dict[str, Any]:
    """Create the password-protected backend with one row (one DAO session)."""
    if root.exists():
        raise WorkerError(f"root already exists; never overwrite fixtures: {root}")
    if os.name != "nt":
        raise UnsupportedError("not Windows")
    root.mkdir(parents=True)
    backend = root / BACKEND_FILENAME
    engine = _dao_engine()
    try:
        database = engine.CreateDatabase(str(backend), LOCALE + f";pwd={CANARY}", CREATE_VERSION_ACE)
        try:
            database.Execute(CREATE_BACKEND_SQL, DAO_FAIL_ON_ERROR)
            database.Execute(INSERT_BACKEND_SQL, DAO_FAIL_ON_ERROR)
        finally:
            _close_object(database)
    finally:
        _release_dao(engine)
    inventory = {
        "schema": INVENTORY_SCHEMA,
        "backend": {"filename": BACKEND_FILENAME, "sha256": _sha256(backend)},
        "frontend": None,
        "backend_row": {"id": BACKEND_ROW[0], "note": BACKEND_ROW[1]},
    }
    _write_inventory(root, inventory)
    return {
        "ok": True,
        "command": "build-backend",
        "root": str(root),
        "root_relative": str(root.relative_to(ROOT)),
        "backend_sha256": inventory["backend"]["sha256"],
    }


def cmd_build_frontend(root: Path) -> dict[str, Any]:
    """Create the front-end with a local control table and the link (one DAO session)."""
    backend = root / BACKEND_FILENAME
    if not backend.is_file():
        raise WorkerError("build the backend first")
    frontend = root / FRONTEND_FILENAME
    if frontend.exists():
        raise WorkerError(f"front-end already exists; never overwrite fixtures: {frontend.name}")
    engine = _dao_engine()
    appended_attributes = 0
    try:
        database = engine.CreateDatabase(str(frontend), LOCALE, CREATE_VERSION_ACE)
        try:
            database.Execute(CREATE_LOCAL_SQL, DAO_FAIL_ON_ERROR)
            database.Execute(INSERT_LOCAL_SQL, DAO_FAIL_ON_ERROR)
            tabledef = database.CreateTableDef(LINK_NAME)
            tabledef.SourceTableName = BACKEND_TABLE
            tabledef.Connect = _connect_string(backend)
            tabledef.Attributes = 0
            database.TableDefs.Append(tabledef)
            linked = _find_tabledef(database, LINK_NAME)
            appended_attributes = int(linked.Attributes) if linked is not None else 0
        finally:
            _close_object(database)
    finally:
        _release_dao(engine)
    inventory = _load_inventory(root)
    inventory["frontend"] = {"filename": FRONTEND_FILENAME, "sha256": _sha256(frontend)}
    _write_inventory(root, inventory)
    return {
        "ok": True,
        "command": "build-frontend",
        "root": str(root),
        "attributes_after_append_hex": hex(appended_attributes & 0xFFFFFFFF),
        "attributes_db_attached_table": bool(appended_attributes & DB_ATTACHED_TABLE),
        "frontend_sha256": inventory["frontend"]["sha256"],
    }


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #


def cmd_verify(root: Path) -> dict[str, Any]:
    """Report the stored-credential positive controls and the unprotected-open refusal.

    The front-end is reopened read-only in a fresh process: the link's stored
    ``Attributes`` and ``Connect`` booleans, and a read through the link (which uses the
    stored password), are the credential-retention proof. A following unprotected DAO
    open of the backend must be refused, proving the password is real.
    """
    inventory = _load_inventory(root)
    frontend = root / FRONTEND_FILENAME
    backend = root / BACKEND_FILENAME
    if not frontend.is_file() or not backend.is_file():
        raise WorkerError("fixture incomplete; run build-backend and build-frontend first")
    backend_before = _sha256(backend)
    frontend_before = _sha256(frontend)
    engine = _dao_engine()
    try:
        database = engine.OpenDatabase(str(frontend), False, True)
        try:
            link = _find_tabledef(database, LINK_NAME)
            attributes = int(link.Attributes) if link is not None else 0
            connect = str(link.Connect) if link is not None else ""
            source_table = str(link.SourceTableName) if link is not None else ""
            try:
                rows = _read_rows(database, LINK_NAME) if link is not None else []
                read_error: dict[str, Any] = {"failed": False, "error_code": None}
            except Exception as exc:  # noqa: BLE001 - recorded evidence, never swallowed
                rows = []
                read_error = {"failed": True, "error_code": _com_error_code(exc)}
        finally:
            _close_object(database)
        try:
            unprotected = engine.OpenDatabase(str(backend), False, False)
            try:
                unprotected.Close()
            finally:
                unprotected = None
                gc.collect()
            unprotected_refused = False
            unprotected_error_code = None
        except Exception as exc:  # noqa: BLE001 - refusal is the expected evidence
            unprotected_refused = True
            unprotected_error_code = _com_error_code(exc)
    finally:
        _release_dao(engine)
    frontend_bytes = frontend.read_bytes()
    checks = {
        "link_present": link is not None,
        "link_attributes_db_attached_table": bool(attributes & DB_ATTACHED_TABLE),
        "link_attributes_not_odbc": not (attributes & DB_ATTACHED_ODBC),
        "source_table_matches": source_table == BACKEND_TABLE,
        "stored_connect_has_pwd_segment": ";PWD=" in connect.upper(),
        "stored_connect_contains_canary": CANARY in connect,
        "frontend_bytes_contain_canary": CANARY.encode() in frontend_bytes,
        "linked_read_returns_expected_row": rows == [BACKEND_ROW],
        "unprotected_open_refused": unprotected_refused,
        "backend_bytes_unchanged": _sha256(backend) == inventory["backend"]["sha256"] == backend_before,
        "frontend_bytes_unchanged": _sha256(frontend) == inventory["frontend"]["sha256"] == frontend_before,
    }
    return {
        "ok": all(checks.values()),
        "command": "verify",
        "root": str(root),
        "checks": checks,
        "link": {
            "attributes_hex": hex(attributes & 0xFFFFFFFF),
            "attributes_db_attached_table": bool(attributes & DB_ATTACHED_TABLE),
            "connect_scheme": connect.split(";", 1)[0] if connect else "",
            "connect_keys": _connect_key_names(connect),
            "connect_database_matches_backend": str(backend).casefold() in connect.casefold(),
            "pwd_segment_present": ";PWD=" in connect.upper(),
            "stored_connect_has_canary": CANARY in connect,
            "source_table": source_table,
        },
        "linked_read": {"row_count": len(rows), "rows": [list(row) for row in rows], **read_error},
        "unprotected_open_error_code": unprotected_error_code,
        "backend_sha256": _sha256(backend),
        "frontend_sha256": _sha256(frontend),
    }


# --------------------------------------------------------------------------- #
# cleanup and entry point
# --------------------------------------------------------------------------- #


def cmd_cleanup(root: Path) -> dict[str, Any]:
    """Remove exactly one self-created ``protected-link-*`` root and nothing else."""
    if not root.is_dir():
        raise WorkerError(f"root is not an existing directory: {root}")
    if root.parent.resolve() != FIXTURE_ROOT.resolve():
        raise WorkerError("refusing to remove anything but a direct child of db/fixtures")
    removed = [str(child.relative_to(root)) for child in sorted(root.iterdir())]
    shutil.rmtree(root)
    return {"ok": True, "command": "cleanup", "root": str(root), "removed": removed}


def _parser() -> argparse.ArgumentParser:
    """Build the bounded subcommand parser."""
    parser = argparse.ArgumentParser(description="Protected Access link fixture worker.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("environment", help="report ACE DAO availability; no writes")

    backend = subparsers.add_parser("build-backend", help="create the protected backend")
    backend.add_argument("--root", required=True)

    frontend = subparsers.add_parser("build-frontend", help="create the local control + link")
    frontend.add_argument("--root", required=True)

    verify = subparsers.add_parser("verify", help="assert stored credential and refusals")
    verify.add_argument("--root", required=True)

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
            if command == "build-backend":
                result = cmd_build_backend(root)
            elif command == "build-frontend":
                result = cmd_build_frontend(root)
            elif command == "verify":
                result = cmd_verify(root)
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
