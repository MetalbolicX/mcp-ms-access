"""Opt-in runtime proof that a password-protected ``.accdb`` can be inspected read-only.

Why this suite exists
---------------------
The shipped documentation pipeline could resolve a database password
(``credentials.resolve_password``, ``credentials.py:244-279``), reuse it on an engine-level
read-only reopen (``documentation_inspect.py:440-479`` -> ``dispatcher.py:186-200`` /
``odbc.py:75-88``), and redact it from output -- but nothing exercised that path against a
database that *really* is password-protected. The runtime qualification was missing,
alongside the encrypted *reopen* being proven only by unit-level pinning.

This suite closes S2-S5 of ``odd/tasks/encrypted-reopen-and-data-macros.md``: a
disposable, repository-confined encrypted fixture (built by
``tests/helpers/encrypted_db_worker.py``), the shipped positive controls observed in a
new process before any pipeline run, and then the public CLI on both shipped backends
with the fake password supplied the only way a password is ever supplied -- through the
credential store the shipped resolver reads. No source change and no new password
mechanism is involved.

What is qualified
-----------------
* **S2 positive controls, first.** ``build`` creates the file with
  ``CreateDatabase(locale + ';pwd=<canary>')`` and registers the canary with the shipped
  writer (``credentials.write_entry(..., local=True)``, what
  ``credentials set --local`` calls). ``verify`` then observes, in a *new* process, that
  the stored password resolves, that an unprotected DAO open is refused with ACE scode
  ``5003031``, and that a protected read returns the fixture row.
* **S3 success path.** ``documentation inspect --backend odbc``, ``--backend dao`` and
  ``--backend auto`` (which resolves to DAO here) each exit 0 and list the local table and
  its fields; the ODBC primary-key gap the driver genuinely has is preserved.
* **S3 read-only at runtime.** ``probe-write-odbc`` and ``probe-write-dao`` drive the exact
  shipped seams the inspection path opens and attempt an INSERT that must be refused by the
  engine: ODBC native code ``-3035`` (SQLSTATE ``HY000``) on the read-only connection, DAO
  scode ``5003073`` on the reopened handle. No mocks.
* **S4 no credential in output.** The canary and any ``PWD=`` are absent from raw stdout,
  raw stderr, the on-disk evidence JSON, the rendered Markdown, and the fixture inventory.
  The streams are raw because the dialog watcher never redacts them, so a leak under any
  other key cannot be masked into a vacuous pass. The local credential store itself is
  excluded from that sweep: it is the shipped mechanism's own artifact and legitimately
  holds the fake password.
* **S4 fail closed.** A wrong stored password and no stored entry at all each exit
  non-zero with a sanitized error (DAO scode ``5003031`` / ODBC native ``-1905``), no
  success claim, no partial evidence, and no credential echo.
* **S5 unattended + byte-stable.** Every subprocess runs under
  ``tests/helpers/dialog_watcher.py`` with CWD pinned inside the fixture root; the
  per-step dialog census must be all zero. The encrypted database's SHA-256 is identical
  before and after every run.

Run explicitly on Windows with ACE DAO and the Access ODBC driver::

    $env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
    .venv\\Scripts\\python.exe -m pytest tests\\integration\\test_documentation_encrypted_reopen.py -q

Without ``ACCESS_DOCUMENTATION_FIXTURES_TEST=1`` the module skips cleanly. This suite
never deletes a fixture root.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from ms_access_mcp.credentials import remove_entry, resolve_password, write_entry
from tests.helpers.dialog_watcher import WatchResult, assert_no_dialogs, run_watched
from tests.helpers.encrypted_db_worker import (
    ACCESS_ODBC_DRIVER,
    CANARY,
    DB_FILENAME,
    FIXTURE_ROOT,
    INVENTORY_FILENAME,
    LOCAL_TABLE,
)
from tests.integration.conftest import _has_ace_dao

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tests" / "helpers" / "encrypted_db_worker.py"
OPT_IN_ENV = "ACCESS_DOCUMENTATION_FIXTURES_TEST"
ALLOW_DOC_WRITE_ENV = "ACCESS_MCP_ALLOW_DOC_WRITE"

# A fake password that shares no substring with the canary, so a leak of either is
# independently observable. Both are fake; neither is ever printed by this suite.
WRONG_PASSWORD = "not-the-password-1234"

BACKENDS = ("dao", "odbc", "auto")
FAIL_CLOSED_BACKENDS = ("dao", "odbc")
EXPECTED_LOCAL_FIELDS = ["id", "note"]

# PYTHONFAULTHANDLER would inject a process-state dump into the captured stderr, and a
# credential-file override would make the "no stored entry" case depend on ambient state:
# the fixture's password state must come only from the shipped local store.
STRIPPED_ENV = (ALLOW_DOC_WRITE_ENV, "PYTHONFAULTHANDLER", "ACCESS_MCP_CREDENTIALS_FILE")
CLI_TIMEOUT_SECONDS = 300.0
HELPER_TIMEOUT_SECONDS = 300.0

ACE_NOT_A_VALID_PASSWORD_SCODE = 5003031
ACCESS_ODBC_NOT_A_VALID_PASSWORD_CODE = -1905
DAO_READONLY_REFUSAL_SCODE = 5003073
ODBC_READONLY_REFUSAL_CODE = -3035


def _access_odbc_driver_available() -> bool:
    """Return True when the exact driver the shipped adapter resolves is registered."""
    try:
        import pyodbc
    except ImportError:
        return False
    try:
        return ACCESS_ODBC_DRIVER in pyodbc.drivers()
    except Exception:  # noqa: BLE001 - an unreadable driver list is "unavailable"
        return False


pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.skipif(not _has_ace_dao(), reason="Requires ACE DAO (DAO.DBEngine.120)"),
    pytest.mark.skipif(
        not _access_odbc_driver_available(), reason=f"Requires the {ACCESS_ODBC_DRIVER}"
    ),
    pytest.mark.skipif(
        os.environ.get(OPT_IN_ENV) != "1",
        reason=f"Set {OPT_IN_ENV}=1 to run the encrypted-reopen qualification suite",
    ),
    pytest.mark.com_integration,
    pytest.mark.odbc_integration,
]


# --------------------------------------------------------------------------- #
# shared process and parsing helpers
# --------------------------------------------------------------------------- #


def _venv_python() -> str:
    """Return the repository virtualenv interpreter, falling back to the current one."""
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean_env() -> dict[str, str]:
    """Build a deterministic process environment without inherited dump/override switches."""
    return {key: value for key, value in os.environ.items() if key not in STRIPPED_ENV}


def _cli_env(fixture_dir: Path) -> dict[str, str]:
    """Build a deterministic CLI environment scoped to the fixture directory."""
    env = _clean_env()
    env["ACCESS_MCP_ALLOWED_DIRS"] = str(fixture_dir)
    return env


def _run_cli(args: list[str], env: dict[str, str], *, cwd: Path) -> WatchResult:
    """Run one public-CLI invocation under the dialog watcher with its CWD pinned."""
    return run_watched(
        [_venv_python(), "-m", "ms_access_mcp.cli", *args],
        cwd=cwd,
        env=env,
        timeout_seconds=CLI_TIMEOUT_SECONDS,
    )


def _run_helper(args: list[str], *, cwd: Path) -> WatchResult:
    """Run one helper-CLI invocation under the dialog watcher with its CWD pinned."""
    return run_watched(
        [_venv_python(), str(HELPER), *args],
        cwd=cwd,
        env=_clean_env(),
        timeout_seconds=HELPER_TIMEOUT_SECONDS,
    )


def _helper_payload(completed: WatchResult, command: str) -> dict[str, Any]:
    """Parse a helper's single JSON result line and require a clean exit.

    Helper stderr is not required to be empty: pywin32 can print a benign COM-release
    diagnostic. It must never carry a traceback or a credential, which the suite asserts
    separately on every raw stream.
    """
    assert completed.returncode == 0, f"helper {command} exited {completed.returncode}"
    assert "Traceback" not in completed.stderr, f"helper {command}: traceback on stderr"
    return json.loads(completed.stdout)


def _inspect_evidence(completed: WatchResult, label: str) -> dict[str, Any]:
    """Assert a successful inspect run's exit contract and return its evidence payload."""
    assert completed.returncode == 0, f"{label}: exit {completed.returncode}"
    assert "Traceback" not in completed.stderr, f"{label}: traceback on stderr"
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True, f"{label}: envelope reported failure"
    return envelope["data"]


def _report(completed: WatchResult, label: str) -> dict[str, Any]:
    """Assert a render run's exit contract and return its report payload."""
    assert completed.returncode == 0, f"{label}: exit {completed.returncode}"
    assert "Traceback" not in completed.stderr, f"{label}: traceback on stderr"
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True, f"{label}: envelope reported failure"
    return envelope["data"]


def _failed_envelope(completed: WatchResult, label: str) -> dict[str, Any]:
    """Assert a fail-closed run's exit contract and return its error envelope."""
    assert completed.returncode != 0, f"{label}: a fail-closed run exited 0"
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is False, f"{label}: a fail-closed run reported success"
    assert isinstance(envelope.get("error"), str) and envelope["error"], f"{label}: no error"
    assert envelope.get("data") is None, f"{label}: a failed run emitted evidence data"
    return envelope


def _table_named(document: dict[str, Any], name: str) -> dict[str, Any]:
    return next(table for table in document["tables"] if table["name"] == name)


def _assert_secret_free(text: str, label: str) -> None:
    """Assert the canary, the wrong password, and any ``PWD=`` segment are absent."""
    assert CANARY not in text, f"{label}: fake canary reached the output"
    assert WRONG_PASSWORD not in text, f"{label}: the wrong fake password reached the output"
    assert "PWD=" not in text.upper(), f"{label}: a PWD= segment reached the output"


# --------------------------------------------------------------------------- #
# fixture: build -> positive controls -> full run matrix
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def encrypted_build() -> dict[str, Any]:
    """Build the encrypted fixture, its local table row, and its local credential entry.

    The build runs under the dialog watcher in its own process and creates the confined
    root; every later step runs with CWD pinned to that root. The root is retained as
    evidence: this suite never deletes a fixture root.
    """
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_ROOT / f"encrypted-db-{uuid.uuid4().hex[:12]}"
    build_run = _run_helper(["build", "--root", str(root)], cwd=FIXTURE_ROOT)
    return {"root": root, "build_run": build_run, "build": _helper_payload(build_run, "build")}


@pytest.fixture(scope="session")
def encrypted_controls(encrypted_build: dict[str, Any]) -> dict[str, Any]:
    """Observe the encrypted-source positive controls in a new process, before any run.

    ``verify`` re-opens the fixture in its own process, so the refusal of an unprotected
    open and the protected read are runtime observations rather than assumptions. Nothing
    downstream may run until this control set holds.
    """
    root = encrypted_build["root"]
    verify_run = _run_helper(["verify", "--root", str(root)], cwd=root)
    return {"verify_run": verify_run, "verify": _helper_payload(verify_run, "verify")}


@pytest.fixture(scope="session")
def encrypted_runs(encrypted_controls: dict[str, Any]) -> dict[str, Any]:
    """Run the whole public pipeline matrix in dependency order, under the watcher.

    Order matters and is deliberate: (1) the successful inspect/render matrix with the
    correct stored credential, (2) the fail-closed matrix with a wrong password and then
    with no stored entry, (3) the restored credential plus the two engine-level read-only
    write-refusal probes, (4) a final ``verify`` proving the credential survived every
    run. The credential is written and removed with the shipped writer/remover
    (``credentials.write_entry`` / ``remove_entry``, ``local=True``) and resolved by the
    shipped resolver inside each subprocess, never passed on argv.
    """
    root = Path(encrypted_controls["verify"]["root"])
    database = root / DB_FILENAME
    env = _cli_env(root)
    database_before = _sha256(database)

    runs: dict[str, WatchResult] = {}
    evidence: dict[str, Any] = {}
    evidence_paths: dict[str, Path] = {}
    failures: dict[str, Any] = {}
    probes: dict[str, Any] = {}

    def inspect(backend: str) -> WatchResult:
        return _run_cli(
            ["documentation", "inspect", "--db", str(database), "--backend", backend, "--json"],
            env,
            cwd=root,
        )

    # (1) correct stored credential: the public pipeline must succeed on every backend.
    for backend in BACKENDS:
        runs[f"inspect_{backend}"] = inspect(backend)
        evidence[backend] = _inspect_evidence(runs[f"inspect_{backend}"], f"inspect {backend}")
        evidence_paths[backend] = root / f"evidence-{backend}.json"
        evidence_paths[backend].write_text(runs[f"inspect_{backend}"].stdout, encoding="utf-8")
    for backend in BACKENDS:
        runs[f"render_{backend}"] = _run_cli(
            ["documentation", "render", "--evidence", str(evidence_paths[backend]), "--json"],
            env,
            cwd=root,
        )

    # (2) fail-closed matrix: a wrong stored password, then no stored entry at all.
    write_entry(str(database), WRONG_PASSWORD, local=True)
    for backend in FAIL_CLOSED_BACKENDS:
        label = f"wrong_{backend}"
        runs[label] = inspect(backend)
        failures[label] = _failed_envelope(runs[label], label)
    remove_entry(str(database), local=True)
    for backend in FAIL_CLOSED_BACKENDS:
        label = f"missing_{backend}"
        runs[label] = inspect(backend)
        failures[label] = _failed_envelope(runs[label], label)

    # (3) restore the shipped credential, then prove the inspection seams are read-only.
    write_entry(str(database), CANARY, local=True)
    for command in ("probe-write-odbc", "probe-write-dao"):
        runs[command] = _run_helper([command, "--root", str(root)], cwd=root)
        probes[command] = _helper_payload(runs[command], command)

    # (4) the credential is still real and enforced after every run.
    runs["verify_after"] = _run_helper(["verify", "--root", str(root)], cwd=root)
    post_verify = _helper_payload(runs["verify_after"], "verify")

    return {
        "root": root,
        "database": database,
        "database_before": database_before,
        "database_after": _sha256(database),
        "credential_restored": bool(resolve_password(str(database))),
        "inventory_path": root / INVENTORY_FILENAME,
        "runs": runs,
        "evidence": evidence,
        "evidence_paths": evidence_paths,
        "failures": failures,
        "probes": probes,
        "post_verify": post_verify,
    }


# --------------------------------------------------------------------------- #
# S2: positive controls must be observed before the pipeline runs
# --------------------------------------------------------------------------- #


def test_encrypted_source_positive_controls_hold_in_a_new_process(
    encrypted_build: dict[str, Any], encrypted_controls: dict[str, Any]
) -> None:
    """The fixture really is encrypted and the shipped credential path really works.

    This is the non-vacuous precondition asserted first: the build registered the fake
    password with the shipped writer, the stored password resolves, an unprotected DAO
    open is refused with ACE's ``Not a valid password`` scode, and a protected read
    returns the local row. Without these, proving redaction or read-only reopening on this
    fixture would be meaningless, so a missing control fails loudly.
    """
    assert_no_dialogs(encrypted_build["build_run"], "helper build")
    assert_no_dialogs(encrypted_controls["verify_run"], "helper verify (positive controls)")

    build = encrypted_build["build"]
    assert build["ok"] is True, "the fixture build reported a failed control"
    assert build["credential_store_written"] is True, "no shipped credential entry was written"
    assert build["row_count"] == 1
    assert len(build["database_sha256"]) == 64

    verify = encrypted_controls["verify"]
    assert verify["ok"] is True, "the encrypted-source positive controls did not hold"
    checks = verify["checks"]
    assert checks["password_resolves"] is True, "the stored password did not resolve"
    assert checks["unprotected_open_refused"] is True, "an unprotected open was not refused"
    assert checks["protected_read_returns_local_row"] is True, "the protected read returned no row"
    assert verify["unprotected_open_error_code"] == ACE_NOT_A_VALID_PASSWORD_SCODE
    assert verify["protected_row_count"] == 1
    assert verify["protected_read_failed"] is False


# --------------------------------------------------------------------------- #
# S3: the public pipeline succeeds on the encrypted source
# --------------------------------------------------------------------------- #


def test_encrypted_inspection_succeeds_and_lists_the_local_table(encrypted_runs: dict[str, Any]) -> None:
    """Every shipped backend inspects the encrypted file and lists the local table.

    ``dao`` and ``odbc`` are the two shipped seams; ``auto`` resolves to DAO on this
    Windows/ACE host, which the recorded backend identity proves instead of assuming.
    Access ODBC genuinely cannot read a primary key (no ``SQLPrimaryKeys``), so that table
    keeps the shipped ``unavailable`` gap fact rather than a fabricated key.
    """
    for backend in BACKENDS:
        run = encrypted_runs["runs"][f"inspect_{backend}"]
        assert_no_dialogs(run, f"inspect --backend {backend}")
        assert run.returncode == 0, f"inspect --backend {backend}: exit {run.returncode}"

    identities = {
        backend: encrypted_runs["evidence"][backend]["source"]["backend_identity"]
        for backend in BACKENDS
    }
    assert identities["dao"] == "DAO"
    assert identities["odbc"] == "ODBC"
    assert identities["auto"] == identities["dao"], "auto did not resolve to the DAO backend"

    for backend in BACKENDS:
        payload = encrypted_runs["evidence"][backend]
        document = payload["data"]
        table = _table_named(document, LOCAL_TABLE)
        assert table["kind"] == "local", f"{backend}: the local table was misclassified"
        assert [field["name"] for field in table["fields"]] == EXPECTED_LOCAL_FIELDS, (
            f"{backend}: unexpected local field list"
        )
        assert LOCAL_TABLE in payload["coverage"]["tables"]["inspected"], f"{backend}: not inspected"
        assert LOCAL_TABLE not in payload["coverage"]["tables"]["failed"], f"{backend}: marked failed"
        if backend == "odbc":
            assert table["primary_key"]["state"] == "unavailable", f"{backend}: fabricated key state"
            assert "SQLPrimaryKeys" in table["primary_key"]["reason"], f"{backend}: missing key reason"
        else:
            assert table["primary_key"]["state"] == "observed", f"{backend}: local key not observed"
            assert table["primary_key"]["value"] == ["id"], f"{backend}: unexpected local key"


# --------------------------------------------------------------------------- #
# S3: engine-level read-only, proven at runtime on the shipped seams
# --------------------------------------------------------------------------- #


def test_encrypted_inspection_connections_refuse_writes_at_runtime(encrypted_runs: dict[str, Any]) -> None:
    """The connections the inspection path opens refuse a write at the engine level.

    ``probe-write-odbc`` drives ``OdbcAdapter.connect`` + ``open_readonly_inspection_connection``
    (``backends/odbc.py:59-88``), the exact call ``open_odbc_inspection_port`` makes
    (``documentation_inspect.py:456-479``); the driver refuses the insert with native code
    ``-3035`` (``Operation must use an updateable query``). ``probe-write-dao`` drives
    ``ComDispatcher.open_dao_database`` + ``reopen_dao_database_read_only``
    (``backends/dispatcher.py:154-200``), the exact call ``open_inspection_port`` makes
    (``documentation_inspect.py:440-453``); the reopened handle refuses the insert with DAO
    scode ``5003073``. Neither probe is mocked, and both record an unchanged row count and
    an unchanged database digest, so a writable inspection connection would fail here
    loudly instead of being absorbed.
    """
    odbc = encrypted_runs["probes"]["probe-write-odbc"]
    assert_no_dialogs(encrypted_runs["runs"]["probe-write-odbc"], "helper probe-write-odbc")
    assert odbc["ok"] is True, "an ODBC read-only control did not hold"
    assert odbc["checks"]["inspection_connection_opened"] is True
    assert odbc["checks"]["write_refused_on_inspection_connection"] is True, "ODBC inspection is writable"
    assert odbc["write_error_code"] == ODBC_READONLY_REFUSAL_CODE
    assert odbc["write_sqlstate"] == "HY000"
    assert odbc["checks"]["unprotected_open_refused"] is True
    assert odbc["unprotected_error_code"] == ACCESS_ODBC_NOT_A_VALID_PASSWORD_CODE
    assert odbc["unprotected_sqlstate"] == "42000"

    dao = encrypted_runs["probes"]["probe-write-dao"]
    assert_no_dialogs(encrypted_runs["runs"]["probe-write-dao"], "helper probe-write-dao")
    assert dao["ok"] is True, "a DAO read-only control did not hold"
    reopened = "readonly_reopen_reused_recorded_target_and_password"
    assert dao["checks"][reopened] is True, "the read-only reopen did not reuse the recorded credential"
    assert dao["checks"]["write_refused_on_reopened_handle"] is True, "DAO inspection is writable"
    assert dao["write_error_code"] == DAO_READONLY_REFUSAL_SCODE
    assert dao["checks"]["probe_copy_bytes_unchanged"] is True

    for name, probe in (("probe-write-odbc", odbc), ("probe-write-dao", dao)):
        assert probe["checks"]["password_resolves"] is True, f"{name}: credential did not resolve"
        assert probe["checks"]["row_count_unchanged"] is True, f"{name}: a write changed the row count"
        assert probe["checks"]["database_bytes_unchanged"] is True, f"{name}: the fixture changed"
        assert probe["row_count_before"] == probe["row_count_after"] == 1, f"{name}: row counts"
        assert probe["error_kind"] == "", f"{name}: a control raised {probe['error_kind']}"


# --------------------------------------------------------------------------- #
# S4 (c) + (e): no credential anywhere, bytes unchanged
# --------------------------------------------------------------------------- #


def test_encrypted_password_never_reaches_documentation_output(encrypted_runs: dict[str, Any]) -> None:
    """The canary and any ``PWD=`` are absent from every observable output.

    Raw stdout and stderr of every step (helper and CLI, success and fail-closed) are
    checked unredacted, as are the on-disk evidence JSON written next to the fixture, the
    rendered Markdown, and the fixture inventory. The database bytes are then identical to
    the pre-run digest, the plaintext canary is not embedded in the encrypted file, and the
    final ``verify`` proves the credential is still enforced after all of it. The shipped
    local credential store is the one artifact that legitimately contains the fake
    password, and it is deliberately excluded from this sweep.
    """
    runs = encrypted_runs["runs"]
    for name, run in runs.items():
        _assert_secret_free(run.stdout, f"encrypted {name} raw stdout")
        _assert_secret_free(run.stderr, f"encrypted {name} raw stderr")
    _assert_secret_free(
        encrypted_runs["inventory_path"].read_text(encoding="utf-8"), "fixture inventory.json"
    )

    for backend in BACKENDS:
        on_disk = encrypted_runs["evidence_paths"][backend].read_text(encoding="utf-8")
        _assert_secret_free(on_disk, f"on-disk evidence-{backend}.json")
        assert json.loads(on_disk) == json.loads(runs[f"inspect_{backend}"].stdout), (
            f"evidence-{backend}.json must carry the inspect run's raw stdout document"
        )
        report = _report(runs[f"render_{backend}"], f"render {backend}")
        markdown = report["markdown"]
        _assert_secret_free(markdown, f"rendered Markdown ({backend})")
        assert f"### {LOCAL_TABLE}" in markdown, f"{backend}: rendered table missing"
        for field in EXPECTED_LOCAL_FIELDS:
            assert f"| {field} |" in markdown, f"{backend}: rendered field {field} missing"

    database_bytes = encrypted_runs["database"].read_bytes()
    assert CANARY.encode() not in database_bytes, "the encrypted file embeds the plaintext password"
    assert encrypted_runs["database_after"] == encrypted_runs["database_before"], (
        "the encrypted database bytes changed"
    )
    assert encrypted_runs["credential_restored"] is True, "the fixture credential was not restored"
    post = encrypted_runs["post_verify"]
    assert post["checks"]["password_resolves"] is True
    assert post["checks"]["unprotected_open_refused"] is True, "the credential stopped being enforced"
    assert post["checks"]["protected_read_returns_local_row"] is True


# --------------------------------------------------------------------------- #
# S4 (d): fail closed on a wrong or missing password
# --------------------------------------------------------------------------- #


def test_encrypted_wrong_or_missing_password_fails_closed(encrypted_runs: dict[str, Any]) -> None:
    """A wrong stored password and no stored entry both fail closed, sanitized.

    Each run must exit non-zero, report ``success: false`` with no evidence data and no
    partial table list, name the driver's refusal code, and leave stderr traceback-free.
    The credential-echo checks for both fake passwords live in the raw-stream sweep above;
    this test pins the exit and evidence contract that makes those runs safe.
    """
    for label, envelope in encrypted_runs["failures"].items():
        run = encrypted_runs["runs"][label]
        assert_no_dialogs(run, f"{label} (fail-closed)")
        assert run.returncode != 0, f"{label}: a fail-closed run exited 0"
        message = envelope["error"]
        assert LOCAL_TABLE not in run.stdout, f"{label}: partial evidence was emitted"
        assert "Traceback" not in run.stderr, f"{label}: traceback on stderr"
        expected_code = (
            str(ACCESS_ODBC_NOT_A_VALID_PASSWORD_CODE)
            if label.endswith("odbc")
            else str(ACE_NOT_A_VALID_PASSWORD_SCODE)
        )
        assert expected_code in message, f"{label}: the driver refusal code is missing"


# --------------------------------------------------------------------------- #
# S5: unattended, with an explicit per-step dialog census
# --------------------------------------------------------------------------- #


def test_encrypted_every_step_observed_zero_dialogs(
    encrypted_build: dict[str, Any],
    encrypted_controls: dict[str, Any],
    encrypted_runs: dict[str, Any],
) -> None:
    """Every helper and public-CLI step observed zero dialogs and no hang.

    The watcher detects, records, and closes any dialog owned by a step's process tree, so
    a non-zero count means a dialog appeared and was auto-closed, not that a human clicked.
    The census is retained next to the run evidence and reported whole in a failure
    message, so a regression is diagnosable from either place.
    """
    watched = {
        "helper_build": encrypted_build["build_run"],
        "helper_verify_controls": encrypted_controls["verify_run"],
        **encrypted_runs["runs"],
    }
    census = {
        name: {
            "dialogs": len(run.dialogs),
            "returncode": run.returncode,
            "timed_out": run.timed_out,
            "cap_exceeded": run.cap_exceeded,
        }
        for name, run in watched.items()
    }
    (encrypted_runs["root"] / "dialog-census.json").write_text(
        json.dumps(census, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assert census, "no steps were recorded"
    assert all(step["dialogs"] == 0 for step in census.values()), f"dialog census: {census}"
    assert all(not step["timed_out"] and not step["cap_exceeded"] for step in census.values()), (
        f"dialog census: {census}"
    )
