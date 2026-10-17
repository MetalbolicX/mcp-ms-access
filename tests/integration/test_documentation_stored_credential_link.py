"""Opt-in runtime proof that a stored link credential never reaches documentation output.

Why this suite exists
---------------------
The shipped documentation pipeline already sanitizes link ``Connect`` metadata, but the
SQLite ODBC qualification could only prove that on a build path where ACE *never stored*
the fake credential at all (its 254-character persistence cut dropped the trailing
``PWD`` segment). Redaction of a credential that was never stored is vacuous. This suite
closes that sub-gap (S4a/T5) with two genuine fixture kinds whose stored ``TableDef.Connect``
really carries the fake ``canary-7f3a`` credential, observed in a **new process before any
pipeline run**:

* **(b) Access link** — a disposable password-protected backend created with the fake
  password in the DAO ``CreateDatabase`` locale (``dbVersion120``) and a front-end whose
  ``dbAttachedTable`` link (``0x40000000``) stores ``;PWD=<canary>;DATABASE=<backend>``
  verbatim (helper ``tests/helpers/protected_link_worker.py``).
* **(a) SQLite ODBC link** — the same helper as the SQLite qualification
  (``tests/helpers/sqlite_odbc_link_worker.py``) built with a **relative**
  ``Database=remote.sqlite``, which keeps the completed driver string under ACE's cut so
  the stored ``Connect`` retains ``PWD`` verbatim. Every step that resolves this link is
  run with CWD pinned to the fixture root, because the driver resolves the relative value
  against the process CWD.

Each fixture's positive controls must hold (stored ``Connect`` contains the canary; the
protected backend refuses an unprotected open; a read through the link returns the source
row) or the run fails loudly instead of passing on a dropped credential.

What is qualified
-----------------
* Every public-CLI subprocess (``documentation inspect --backend dao``,
  ``--backend odbc``, and ``documentation render`` on each emitted evidence) runs under
  the validated dialog watcher (``tests/helpers/dialog_watcher.py``) while the *source*
  file (protected backend / SQLite source) is held under an exclusive ``share=0`` lock.
  Each run must exit 0 and observe **zero** dialogs from its own process tree, so no human
  is ever asked to dismiss a driver dialog and the qualifying run opened no source file.
* The canary and any ``PWD=`` segment are absent from evidence JSON, stdout, stderr, and
  the rendered Markdown; the live linked table stays ``kind: "linked"`` with empty
  fields/indexes and the shipped explicit unavailable gap; non-credential connect metadata
  the backend does expose (``MS Access;DATABASE=...`` for the Access link,
  ``ODBC;Driver={SQLite3 ODBC Driver};Database=remote.sqlite;...`` for the ODBC link) is
  preserved; the local control table keeps its fields; and the source/backend bytes are
  unchanged.
* After the pipeline runs, the protected backend still refuses an unprotected DAO open,
  proving the credential is real and still enforced.

Never print raw ``Connect`` or the canary: the helpers emit booleans and key names only,
and this suite's failure messages name the field, not its value.

Run explicitly on Windows with ACE DAO and (for the ODBC kind) the x64 SQLite3 ODBC driver::

    $env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
    .venv\\Scripts\\python.exe -m pytest tests\\integration\\test_documentation_stored_credential_link.py -q

Without ``ACCESS_DOCUMENTATION_FIXTURES_TEST=1`` the module skips cleanly.
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

from ms_access_mcp.application.documentation_inspect import _LINKED_TABLE_NOT_INSPECTED_REASON
from tests.helpers.dialog_watcher import WatchResult, assert_no_dialogs, run_watched
from tests.helpers.protected_link_worker import (
    BACKEND_FILENAME,
    BACKEND_TABLE,
    CANARY,
    FIXTURE_ROOT,
    FRONTEND_FILENAME,
    LOCAL_TABLE,
)
from tests.helpers.protected_link_worker import (
    LINK_NAME as PROTECTED_LINK_NAME,
)
from tests.helpers.sqlite_odbc_link_worker import (
    ACCDB_FILENAME,
    SQLITE_DRIVER,
    SQLITE_FILENAME,
    ExclusiveFileLock,
    _sqlite_driver_registered,
)
from tests.helpers.sqlite_odbc_link_worker import (
    CANARY as SQLITE_CANARY,
)
from tests.helpers.sqlite_odbc_link_worker import (
    LINK_NAME as SQLITE_LINK_NAME,
)
from tests.integration.conftest import _has_ace_dao

ROOT = Path(__file__).resolve().parents[2]
PROTECTED_HELPER = ROOT / "tests" / "helpers" / "protected_link_worker.py"
SQLITE_HELPER = ROOT / "tests" / "helpers" / "sqlite_odbc_link_worker.py"
OPT_IN_ENV = "ACCESS_DOCUMENTATION_FIXTURES_TEST"
ALLOW_DOC_WRITE_ENV = "ACCESS_MCP_ALLOW_DOC_WRITE"
# PYTHONFAULTHANDLER would inject a different process-state dump into stderr; the run
# captures stderr, so the inherited switch is removed.
STRIPPED_ENV = (ALLOW_DOC_WRITE_ENV, "PYTHONFAULTHANDLER")
CLI_TIMEOUT_SECONDS = 300.0
HELPER_TIMEOUT_SECONDS = 300.0
LINKED_GAP_REASON = _LINKED_TABLE_NOT_INSPECTED_REASON
PROTECTED_SOURCE_ROW = [[1, "secret row"]]

assert CANARY == SQLITE_CANARY, "the two fixtures must share the same fake credential"

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.skipif(not _has_ace_dao(), reason="Requires ACE DAO (DAO.DBEngine.120)"),
    pytest.mark.skipif(
        os.environ.get(OPT_IN_ENV) != "1",
        reason=f"Set {OPT_IN_ENV}=1 to run the stored-credential link qualification suite",
    ),
    pytest.mark.com_integration,
    pytest.mark.odbc_integration,
]

requires_sqlite_driver = pytest.mark.skipif(
    not _sqlite_driver_registered(), reason=f"Requires the {SQLITE_DRIVER}"
)


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
    """Build a deterministic process environment without inherited dump/opt-in switches."""
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


def _run_helper(helper: Path, args: list[str], *, cwd: Path) -> WatchResult:
    """Run one helper-CLI invocation under the dialog watcher with its CWD pinned."""
    return run_watched(
        [_venv_python(), str(helper), *args],
        cwd=cwd,
        env=_clean_env(),
        timeout_seconds=HELPER_TIMEOUT_SECONDS,
    )


def _helper_payload(completed: WatchResult, command: str) -> dict[str, Any]:
    """Parse a helper's single JSON result line and require a clean exit."""
    assert completed.returncode == 0, f"helper {command} exited {completed.returncode}"
    assert completed.stderr == "", f"helper {command} wrote stderr"
    return json.loads(completed.stdout)


def _inspect_evidence(completed: WatchResult, label: str) -> dict[str, Any]:
    """Assert a public inspect run's exit contract and return its evidence document."""
    assert completed.returncode == 0, f"{label}: exit {completed.returncode}"
    assert "Traceback" not in completed.stderr, f"{label}: traceback on stderr"
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True, f"{label}: envelope reported failure"
    return envelope["data"]


def _report(completed: WatchResult, label: str) -> dict[str, Any]:
    """Assert a public render run's exit contract and return its report payload."""
    assert completed.returncode == 0, f"{label}: exit {completed.returncode}"
    assert "Traceback" not in completed.stderr, f"{label}: traceback on stderr"
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True, f"{label}: envelope reported failure"
    return envelope["data"]


def _table_named(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return next(table for table in payload["tables"] if table["name"] == name)


def _assert_credential_free(text: str, label: str) -> None:
    """Assert the canary and any ``PWD=`` segment are absent, without echoing the text."""
    assert CANARY not in text, f"{label}: fake canary reached the output"
    assert "PWD=" not in text.upper(), f"{label}: a PWD= segment reached the output"


def _assert_linked_gap(payload: dict[str, Any], link_name: str, label: str) -> None:
    """Assert the linked table keeps kind/empty-fields/explicit-gap semantics."""
    linked = _table_named(payload, link_name)
    assert linked["kind"] == "linked", f"{label}: link not classified as linked"
    assert linked["fields"] == [], f"{label}: linked fields were read"
    assert linked["indexes"] == [], f"{label}: linked indexes were read"
    assert linked["primary_key"]["state"] == "unavailable", f"{label}: linked key not unavailable"
    assert linked["primary_key"]["reason"] == LINKED_GAP_REASON, f"{label}: wrong linked gap reason"


def _assert_local_control(payload: dict[str, Any], label: str, backend: str) -> None:
    """Assert the local control table keeps its observed fields and backend key facts.

    DAO exposes the primary key; Access ODBC reports it ``unavailable`` because the driver
    has no ``SQLPrimaryKeys`` and ``SQLStatistics`` does not flag the primary index.
    """
    local = _table_named(payload, LOCAL_TABLE)
    assert local["kind"] == "local", f"{label}: local control misclassified"
    assert [field["name"] for field in local["fields"]] == ["id", "note"], f"{label}: local fields"
    assert [index["name"] for index in local["indexes"]] == ["PK_local_items"], f"{label}: local indexes"
    if backend == "dao":
        assert local["primary_key"]["state"] == "observed", f"{label}: local key not observed"
        assert local["primary_key"]["value"] == ["id"], f"{label}: local key value"
    else:
        assert local["primary_key"]["state"] == "unavailable", f"{label}: unexpected ODBC local key state"
        assert "SQLPrimaryKeys" in local["primary_key"]["reason"], f"{label}: missing ODBC key reason"


def _locked_cli_runs(root: Path, db_path: Path, source_path: Path) -> dict[str, Any]:
    """Run both backend inspections and both renders under an exclusive source lock.

    The ``share=0`` handle is held by this test process while each CLI invocation runs in
    its own dialog-watched subprocess; a run that still exits 0 therefore opened no source
    file and raised no driver dialog. Evidence is written next to the fixture so the
    offline renders read it back from disk.
    """
    env = _cli_env(root)
    runs: dict[str, Any] = {}
    evidence_paths: dict[str, Path] = {}
    with ExclusiveFileLock(source_path):
        try:
            with source_path.open("rb") as handle:
                handle.read(16)
        except OSError:
            plain_read_refused = True
        else:
            plain_read_refused = False
        assert plain_read_refused, "the exclusive file lock did not refuse a plain read"
        for backend in ("dao", "odbc"):
            runs[backend] = _run_cli(
                ["documentation", "inspect", "--db", str(db_path), "--backend", backend, "--json"],
                env,
                cwd=root,
            )
            evidence_path = root / f"evidence-{backend}.json"
            evidence_path.write_text(runs[backend].stdout, encoding="utf-8")
            evidence_paths[backend] = evidence_path
        for backend in ("dao", "odbc"):
            runs[f"render_{backend}"] = _run_cli(
                ["documentation", "render", "--evidence", str(evidence_paths[backend]), "--json"],
                env,
                cwd=root,
            )
    return {
        "plain_read_refused": plain_read_refused,
        "runs": runs,
        "evidence_paths": evidence_paths,
        "db_path": db_path,
        "evidence": {
            backend: _inspect_evidence(runs[backend], f"inspect {backend}")
            for backend in ("dao", "odbc")
        },
    }


# --------------------------------------------------------------------------- #
# (b) Access link fixtures: build, positive controls, locked pipeline
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def protected_build() -> dict[str, Any]:
    """Build the protected backend and its front-end link, each in a fresh process.

    Both helper steps run under the dialog watcher. The backend build creates the confined
    root; the front-end build then runs with CWD pinned to that root. The root is retained
    as evidence: this suite never deletes a fixture root.
    """
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_ROOT / f"protected-link-{uuid.uuid4().hex[:12]}"
    backend_run = _run_helper(PROTECTED_HELPER, ["build-backend", "--root", str(root)], cwd=FIXTURE_ROOT)
    frontend_run = _run_helper(PROTECTED_HELPER, ["build-frontend", "--root", str(root)], cwd=root)
    return {
        "root": root,
        "backend_run": backend_run,
        "frontend_run": frontend_run,
        "backend_build": _helper_payload(backend_run, "build-backend"),
        "frontend_build": _helper_payload(frontend_run, "build-frontend"),
    }


@pytest.fixture(scope="session")
def protected_controls(protected_build: dict[str, Any]) -> dict[str, Any]:
    """Observe the Access-link positive controls in a new process before any pipeline run.

    ``verify`` reopens the front-end read-only, so the stored-credential booleans and the
    linked read are runtime observations, not assumptions: the backend password is proven
    real by its refusal of an unprotected open.
    """
    root = protected_build["root"]
    verify_run = _run_helper(PROTECTED_HELPER, ["verify", "--root", str(root)], cwd=root)
    return {"verify_run": verify_run, "verify": _helper_payload(verify_run, "verify")}


@pytest.fixture(scope="session")
def protected_runs(protected_controls: dict[str, Any]) -> dict[str, Any]:
    """Run the public documentation pipeline on the Access-link fixture under the lock."""
    root_path = Path(protected_controls["verify"]["root"])
    backend = root_path / BACKEND_FILENAME
    frontend = root_path / FRONTEND_FILENAME
    backend_before = _sha256(backend)
    frontend_before = _sha256(frontend)
    result = _locked_cli_runs(root_path, frontend, backend)
    result["backend_before"] = backend_before
    result["backend_after"] = _sha256(backend)
    result["frontend_before"] = frontend_before
    result["frontend_after"] = _sha256(frontend)
    return result


@pytest.fixture(scope="session")
def protected_post_run_verify(protected_runs: dict[str, Any]) -> dict[str, Any]:
    """Re-verify the protected backend after the pipeline runs (unprotected open refused)."""
    root = Path(protected_runs["db_path"]).parent
    verify_run = _run_helper(PROTECTED_HELPER, ["verify", "--root", str(root)], cwd=root)
    assert_no_dialogs(verify_run, "helper verify after the pipeline runs")
    return _helper_payload(verify_run, "verify")


# --------------------------------------------------------------------------- #
# (a) SQLite relative-database link fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def sqlite_relative_build() -> dict[str, Any]:
    """Build the relative-database SQLite ODBC link with CWD pinned to its root.

    ``Database=remote.sqlite`` is resolved by the driver against the process CWD, so the
    root is pre-created empty and every helper step that resolves the link runs with that
    root as CWD. The relative value keeps the completed connection string under ACE's
    254-character cut, which is what retains the stored ``PWD`` segment.
    """
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_ROOT / f"sqlite-odbc-relcred-{uuid.uuid4().hex[:12]}"
    root.mkdir()
    build_run = _run_helper(
        SQLITE_HELPER,
        ["build", "--root", str(root), "--database-style", "relative"],
        cwd=root,
    )
    return {"root": root, "build_run": build_run, "build": _helper_payload(build_run, "build")}


@pytest.fixture(scope="session")
def sqlite_relative_control(sqlite_relative_build: dict[str, Any]) -> dict[str, Any]:
    """Observe the relative-database stored-credential positive control in a new process."""
    root = sqlite_relative_build["root"]
    control_run = _run_helper(
        SQLITE_HELPER, ["prove-stored-credential", "--root", str(root)], cwd=root
    )
    return {"control_run": control_run, "control": _helper_payload(control_run, "prove-stored-credential")}


@pytest.fixture(scope="session")
def sqlite_relative_runs(sqlite_relative_control: dict[str, Any]) -> dict[str, Any]:
    """Run the public documentation pipeline on the relative ODBC fixture under the lock."""
    root = Path(sqlite_relative_control["control"]["root"])
    source = root / SQLITE_FILENAME
    accdb = root / ACCDB_FILENAME
    source_before = _sha256(source)
    accdb_before = _sha256(accdb)
    result = _locked_cli_runs(root, accdb, source)
    result["source_before"] = source_before
    result["source_after"] = _sha256(source)
    result["accdb_before"] = accdb_before
    result["accdb_after"] = _sha256(accdb)
    return result


# --------------------------------------------------------------------------- #
# positive controls must be observed before any pipeline run
# --------------------------------------------------------------------------- #


def test_protected_access_link_stores_the_credential_in_a_new_process(
    protected_build: dict[str, Any], protected_controls: dict[str, Any]
) -> None:
    """The Access link's stored Connect really carries the credential, observed first.

    This is the non-vacuous precondition: a stored ``PWD`` segment containing the canary,
    a real backend password (unprotected open refused), and a linked read that returns the
    protected row. Without these, proving redaction of a stored credential would be
    meaningless, so a missing control fails loudly.
    """
    assert_no_dialogs(protected_build["backend_run"], "helper build-backend")
    assert_no_dialogs(protected_build["frontend_run"], "helper build-frontend")
    assert_no_dialogs(protected_controls["verify_run"], "helper verify (positive controls)")

    verify = protected_controls["verify"]
    assert verify["ok"] is True, "the Access-link positive controls did not hold"
    assert all(verify["checks"].values()), "a stored-credential control returned false"

    link = verify["link"]
    assert link["attributes_hex"] == "0x40000000", "link is not dbAttachedTable"
    assert link["stored_connect_has_canary"] is True, "stored Connect does not contain the canary"
    assert link["pwd_segment_present"] is True, "stored Connect has no PWD segment"
    assert link["connect_scheme"] == "MS Access", "unexpected stored Connect scheme"
    assert "pwd" in link["connect_keys"], "stored Connect has no pwd key"
    assert "database" in link["connect_keys"], "stored Connect has no database key"
    assert link["connect_database_matches_backend"] is True, "stored DATABASE does not match backend"
    assert link["source_table"] == BACKEND_TABLE
    assert verify["linked_read"]["rows"] == PROTECTED_SOURCE_ROW, "linked read did not return the row"
    assert verify["unprotected_open_error_code"] == 5003031, "unprotected open was not refused"


@requires_sqlite_driver
def test_sqlite_relative_link_stores_the_credential_with_pinned_cwd(
    sqlite_relative_build: dict[str, Any], sqlite_relative_control: dict[str, Any]
) -> None:
    """The relative-database ODBC link keeps the stored credential, observed first.

    A relative ``Database=remote.sqlite`` fits under ACE's persistence cut, so the stored
    ``Connect`` retains the ``PWD`` segment verbatim and the driver resolves the source
    against the pinned fixture CWD; the linked read returning both rows proves the
    credential path is live rather than a stored-only artifact.
    """
    assert_no_dialogs(sqlite_relative_build["build_run"], "helper build (relative)")
    assert_no_dialogs(sqlite_relative_control["control_run"], "helper prove-stored-credential")

    control = sqlite_relative_control["control"]
    assert control["ok"] is True, "the SQLite stored-credential positive control did not hold"
    assert all(control["checks"].values()), "a stored-credential control returned false"
    assert control["database_style"] == "relative"
    assert control["stored_database_value_is_relative"] is True
    assert control["connect_scheme"] == "ODBC"
    assert control["link_attributes_hex"] == "0x20020000"
    assert "pwd" in control["connect_keys"], "stored Connect has no pwd key"
    assert "database" in control["connect_keys"], "stored Connect has no database key"
    assert "driver" in control["connect_keys"], "stored Connect has no driver key"
    assert control["linked_read"]["rows"] == [[1, "alpha"], [2, "beta"]]


# --------------------------------------------------------------------------- #
# qualification: locked pipeline runs never emit the stored credential
# --------------------------------------------------------------------------- #


def test_protected_steps_are_dialog_free(
    protected_build: dict[str, Any],
    protected_controls: dict[str, Any],
    protected_runs: dict[str, Any],
) -> None:
    """Every Access-link helper and public-CLI step observed zero dialogs.

    The suite runs unattended. Each step is spawned as its own process under the watcher,
    which detects, records, and closes any dialog owned by that step's process tree and
    fails the step. A non-zero count here means a dialog appeared and was auto-closed, not
    that a human clicked.
    """
    census = {
        "helper protected build-backend": len(protected_build["backend_run"].dialogs),
        "helper protected build-frontend": len(protected_build["frontend_run"].dialogs),
        "helper protected verify": len(protected_controls["verify_run"].dialogs),
        **{f"protected cli {name}": len(run.dialogs) for name, run in protected_runs["runs"].items()},
    }
    for step, count in census.items():
        assert count == 0, f"{step} observed {count} dialog(s) instead of zero"
    for name, run in protected_runs["runs"].items():
        assert run.returncode == 0, f"protected cli {name}: exit {run.returncode}"


@requires_sqlite_driver
def test_sqlite_relative_steps_are_dialog_free(
    sqlite_relative_build: dict[str, Any],
    sqlite_relative_control: dict[str, Any],
    sqlite_relative_runs: dict[str, Any],
) -> None:
    """Every relative-database SQLite helper and public-CLI step observed zero dialogs."""
    census = {
        "helper sqlite relative build": len(sqlite_relative_build["build_run"].dialogs),
        "helper sqlite relative control": len(sqlite_relative_control["control_run"].dialogs),
        **{f"sqlite cli {name}": len(run.dialogs) for name, run in sqlite_relative_runs["runs"].items()},
    }
    for step, count in census.items():
        assert count == 0, f"{step} observed {count} dialog(s) instead of zero"
    for name, run in sqlite_relative_runs["runs"].items():
        assert run.returncode == 0, f"sqlite cli {name}: exit {run.returncode}"


def test_protected_access_link_credential_never_reaches_documentation_output(
    protected_runs: dict[str, Any],
) -> None:
    """The Access-link stored credential is redacted from every observable output.

    With the protected backend locked, DAO and Access-ODBC inspections exit 0 with zero
    dialogs, the link stays a linked gap, the non-credential ``DATABASE`` metadata the
    backend exposes is preserved, the local control is intact, and the canary/PWD segment
    are absent from the raw stdout/stderr streams, the on-disk ``evidence-*.json`` bytes,
    the emitted evidence, and the rendered Markdown. The streams are raw because the
    watcher never redacts them, so a leak under a key other than ``PWD=`` cannot be masked
    into a vacuous pass. Source bytes are unchanged.
    """
    assert protected_runs["plain_read_refused"] is True
    runs = protected_runs["runs"]
    for name, run in runs.items():
        assert_no_dialogs(run, f"protected public CLI {name} under the source lock")

    evidence = protected_runs["evidence"]
    for backend in ("dao", "odbc"):
        data = evidence[backend]["data"]
        _assert_linked_gap(data, PROTECTED_LINK_NAME, f"protected inspect {backend}")
        _assert_local_control(data, f"protected inspect {backend}", backend)
        assert evidence[backend]["coverage"]["tables"]["unsupported"][PROTECTED_LINK_NAME] == LINKED_GAP_REASON
        assert PROTECTED_LINK_NAME not in evidence[backend]["coverage"]["tables"]["failed"]

    dao_link = _table_named(evidence["dao"]["data"], PROTECTED_LINK_NAME)
    connect = dao_link["connect"]
    assert connect is not None, "DAO evidence must expose the sanitized Access-link Connect"
    _assert_credential_free(connect, "protected DAO evidence connect")
    assert connect.startswith("MS Access;"), "unexpected Access-link scheme in evidence"
    assert "DATABASE=" in connect.upper(), "non-credential DATABASE metadata was lost"
    assert str(protected_runs["db_path"].with_name(BACKEND_FILENAME)).casefold() in connect.casefold(), (
        "non-credential DATABASE value was lost"
    )
    assert dao_link["source_table_name"] == BACKEND_TABLE

    # Access ODBC exposes the link as a SYNONYM with no target and no connect metadata.
    odbc_link = _table_named(evidence["odbc"]["data"], PROTECTED_LINK_NAME)
    assert odbc_link["connect"] is None, "Access ODBC unexpectedly exposed connect metadata"
    assert odbc_link["source_table_name"] is None, "Access ODBC unexpectedly exposed the target"

    for name, run in runs.items():
        _assert_credential_free(run.stdout, f"protected {name} raw stdout")
        _assert_credential_free(run.stderr, f"protected {name} raw stderr")
    for backend in ("dao", "odbc"):
        _assert_credential_free(json.dumps(evidence[backend]), f"protected evidence {backend}")
        on_disk = protected_runs["evidence_paths"][backend].read_text(encoding="utf-8")
        _assert_credential_free(on_disk, f"protected on-disk evidence-{backend}.json")
        assert json.loads(on_disk) == json.loads(runs[backend].stdout), (
            f"evidence-{backend}.json must carry the inspect run's raw stdout document"
        )
        report = _report(runs[f"render_{backend}"], f"protected render {backend}")
        _assert_credential_free(report["markdown"], f"protected render {backend} markdown")
        assert f"Linked table: {PROTECTED_LINK_NAME}" in report["markdown"]
        assert LINKED_GAP_REASON in report["markdown"]

    assert protected_runs["backend_after"] == protected_runs["backend_before"]
    assert protected_runs["frontend_after"] == protected_runs["frontend_before"]


@requires_sqlite_driver
def test_sqlite_relative_link_credential_never_reaches_documentation_output(
    sqlite_relative_runs: dict[str, Any],
) -> None:
    """The relative-database ODBC stored credential is redacted from every observable output.

    This is the S4a ODBC-kind proof that was previously impossible with an absolute
    ``Database=`` value: the stored credential is genuine, and it still never reaches
    evidence, stdout, stderr, or the rendered Markdown. Non-credential ``Driver`` and
    ``Database`` facts the DAO backend exposes are preserved; Access ODBC exposes the link
    as a target-less ``SYNONYM``.
    """
    assert sqlite_relative_runs["plain_read_refused"] is True
    runs = sqlite_relative_runs["runs"]
    for name, run in runs.items():
        assert_no_dialogs(run, f"sqlite relative public CLI {name} under the source lock")

    evidence = sqlite_relative_runs["evidence"]
    for backend in ("dao", "odbc"):
        data = evidence[backend]["data"]
        _assert_linked_gap(data, SQLITE_LINK_NAME, f"sqlite inspect {backend}")
        _assert_local_control(data, f"sqlite inspect {backend}", backend)
        assert evidence[backend]["coverage"]["tables"]["unsupported"][SQLITE_LINK_NAME] == LINKED_GAP_REASON

    dao_link = _table_named(evidence["dao"]["data"], SQLITE_LINK_NAME)
    connect = dao_link["connect"]
    assert connect is not None, "DAO evidence must expose the sanitized ODBC-link Connect"
    _assert_credential_free(connect, "sqlite DAO evidence connect")
    assert connect.startswith("ODBC;"), "unexpected ODBC-link scheme in evidence"
    assert f"Driver={{{SQLITE_DRIVER}}}" in connect, "non-credential Driver metadata was lost"
    assert f"Database={SQLITE_FILENAME}" in connect, "non-credential Database metadata was lost"
    assert dao_link["source_table_name"] == "remote_items"

    odbc_link = _table_named(evidence["odbc"]["data"], SQLITE_LINK_NAME)
    assert odbc_link["connect"] is None, "Access ODBC unexpectedly exposed connect metadata"
    assert odbc_link["source_table_name"] is None, "Access ODBC unexpectedly exposed the target"

    for name, run in runs.items():
        _assert_credential_free(run.stdout, f"sqlite {name} raw stdout")
        _assert_credential_free(run.stderr, f"sqlite {name} raw stderr")
    for backend in ("dao", "odbc"):
        _assert_credential_free(json.dumps(evidence[backend]), f"sqlite evidence {backend}")
        on_disk = sqlite_relative_runs["evidence_paths"][backend].read_text(encoding="utf-8")
        _assert_credential_free(on_disk, f"sqlite on-disk evidence-{backend}.json")
        assert json.loads(on_disk) == json.loads(runs[backend].stdout), (
            f"evidence-{backend}.json must carry the inspect run's raw stdout document"
        )
        report = _report(runs[f"render_{backend}"], f"sqlite render {backend}")
        _assert_credential_free(report["markdown"], f"sqlite render {backend} markdown")
        assert f"Linked table: {SQLITE_LINK_NAME}" in report["markdown"]
        assert LINKED_GAP_REASON in report["markdown"]

    assert sqlite_relative_runs["source_after"] == sqlite_relative_runs["source_before"]
    assert sqlite_relative_runs["accdb_after"] == sqlite_relative_runs["accdb_before"]


# --------------------------------------------------------------------------- #
# the credential stays real after the pipeline runs
# --------------------------------------------------------------------------- #


def test_protected_backend_still_refuses_an_unprotected_open_after_the_runs(
    protected_post_run_verify: dict[str, Any],
) -> None:
    """The backend password is still enforced after the documentation pipeline ran.

    A redaction test whose credential had been dropped by the pipeline would be vacuous;
    re-opening the backend without the password must still be refused, and the linked read
    must still return the protected row.
    """
    verify = protected_post_run_verify
    assert verify["ok"] is True, "post-run verification failed"
    assert verify["checks"]["unprotected_open_refused"] is True, "backend no longer refuses an unprotected open"
    assert verify["unprotected_open_error_code"] == 5003031
    assert verify["checks"]["stored_connect_contains_canary"] is True, "stored credential disappeared"
    assert verify["linked_read"]["rows"] == PROTECTED_SOURCE_ROW
