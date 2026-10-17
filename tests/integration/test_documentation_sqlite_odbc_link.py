"""Opt-in public-CLI qualification of a genuine SQLite-backed Access ODBC link.

Scope
-----
This suite qualifies the *shipped* documentation inspection path against a real
``dbAttachedODBC`` link whose source is a disposable SQLite database. No production
code is changed. The fixture is built and controlled by
``tests/helpers/sqlite_odbc_link_worker.py`` (T1); every public-CLI and helper
invocation runs in its own fresh subprocess under the validated dialog watcher
(``tests/helpers/dialog_watcher.py``), so no COM/ODBC handle is warmed between the DAO
and ODBC inspections and no driver dialog can wait for a human.

What is qualified
-----------------
* Genuine link: the stored ``Attributes`` carry ``dbAttachedODBC`` (``0x20000000``) and
  a deliberate read through the link returns both SQLite rows (``prove-contact``).
* Expected driver dialog (watcher positive control): the deliberate *locked* linked read
  makes the SQLite3 ODBC driver raise its own ``SQLite3 ODBC Driver Connect`` window
  (class ``#32770``) owned by the helper's process tree; the watcher observes exactly
  that one dialog, closes it with ``WM_CLOSE``, and the locked read is then refused with
  DAO ``5003151``. This validates the lock tripwire and proves the watcher detects real
  driver dialogs; a missing dialog fails the control loudly instead of passing vacuously.
* Isolation tripwire under the watcher: an exclusive Windows ``share=0`` file lock on
  ``remote.sqlite`` refuses a plain read while held, and the linked read only succeeds
  once the lock is released (``prove-isolation``). The lock is then held across the
  public ``documentation inspect`` runs (DAO and Access ODBC), a filtered DAO run, and
  the offline renders: every run exits 0, observes **zero** dialogs from its own process
  tree, and therefore performed no source-file open and made no SQLite-driver connect
  attempt through ACE.
* Linked classification and explicit gaps: the linked table is evidence
  ``kind: "linked"`` with empty fields/indexes and an ``unavailable`` primary key whose
  reason is the shipped linked-table gap; its local fields are never read.
* Local control preserved: ``local_items`` keeps its observed DAO fields/PK and its
  ODBC-visible columns.
* Credential sanitation: ACE drops the fake ``PWD`` credential on this build path, the
  full plaintext canary is absent from the stored Connect, and no canary or ``PWD``
  segment ever reaches stdout, stderr, emitted evidence, or the rendered report.
* Offline rendering: the emitted evidence renders without any database access and keeps
  the linked gap visible, deterministically.

Honest scope limits (retained gaps)
-----------------------------------
* The lock plus the zero-dialog observation proves the qualifying run performed no
  *source-file open* and that no SQLite-driver connect attempt surfaced through ACE while
  it ran. It is still not proof against every contact form: a live attempt on an unlocked
  source raises no dialog, and a swallowed attempt would look identical. The claim is
  therefore paired with the shipped classification-before-read unit proofs in
  ``tests/unit/test_odbc_inspection_port.py``.
* The dialog watcher watches only the process tree each step launches, with creation-time
  guards against recycled pids, so it can neither miss nor close an unrelated process's
  window. Its positive control is the expected driver dialog above plus a real in-process
  ``MessageBoxW`` in ``tests/unit/test_dialog_watcher.py``.
* ACE drops the fake ``PWD`` credential on this build path (no stored ``PWD`` segment),
  so a positive runtime redaction of a stored credential is *not* claimed; that sub-gap
  is retained and sanitation stays unit-proven. The full plaintext canary is never
  stored.
* The fail-closed unknown-table-type path is not reachable with this real ACE fixture
  (Access ODBC reports only ``TABLE``/``SYNONYM``); runtime ``SYNONYM`` recognition is
  asserted separately through the driver catalog, and the unknown-type behavior stays
  proven at unit level.
* Source absence or non-recreation is deliberately never used as an isolation oracle.

Run explicitly on Windows with ACE DAO, pywin32, and the SQLite3 ODBC driver::

    $env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
    .venv\\Scripts\\python.exe -m pytest tests\\integration\\test_documentation_sqlite_odbc_link.py -q

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
from tests.helpers.dialog_watcher import (
    WatchResult,
    assert_expected_driver_dialog,
    assert_no_dialogs,
    redact,
    run_watched,
)
from tests.helpers.sqlite_odbc_link_worker import (
    ACCDB_FILENAME,
    CANARY,
    FIXTURE_ROOT,
    INVENTORY_FILENAME,
    LINK_NAME,
    LOCAL_TABLE,
    REMOTE_ROWS,
    REMOTE_TABLE,
    ROOT,
    SQLITE_DRIVER,
    SQLITE_FILENAME,
    ExclusiveFileLock,
    _sqlite_driver_registered,
)
from tests.integration.conftest import _has_ace_dao

HELPER = ROOT / "tests" / "helpers" / "sqlite_odbc_link_worker.py"
OPT_IN_ENV = "ACCESS_DOCUMENTATION_FIXTURES_TEST"
ALLOW_DOC_WRITE_ENV = "ACCESS_MCP_ALLOW_DOC_WRITE"
# PYTHONFAULTHANDLER would inject a different process-state dump into stderr; the
# qualification captures stderr byte-for-byte, so the inherited switch is removed (L7).
STRIPPED_ENV = (ALLOW_DOC_WRITE_ENV, "PYTHONFAULTHANDLER")
CLI_TIMEOUT_SECONDS = 300.0
HELPER_TIMEOUT_SECONDS = 300.0
LINKED_GAP_REASON = _LINKED_TABLE_NOT_INSPECTED_REASON
REMOTE_ROW_VALUES = [list(row) for row in REMOTE_ROWS]

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.skipif(not _has_ace_dao(), reason="Requires ACE DAO (DAO.DBEngine.120)"),
    pytest.mark.skipif(
        not _sqlite_driver_registered(), reason=f"Requires the {SQLITE_DRIVER}"
    ),
    pytest.mark.skipif(
        os.environ.get(OPT_IN_ENV) != "1",
        reason=f"Set {OPT_IN_ENV}=1 to run the SQLite ODBC link qualification suite",
    ),
    pytest.mark.com_integration,
    pytest.mark.odbc_integration,
]


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
    """Run one public-CLI invocation under the dialog watcher with its CWD pinned.

    ``cwd`` is always inside the fixture root so a relative ``Database=`` path can never
    resolve against the repository root and leave a stray file (T4 incident).
    """
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


def _helper_payload(completed: WatchResult) -> dict[str, Any]:
    """Parse the helper's single JSON result line.

    The captured streams are raw, so any message that shows them is formatted through
    ``redact`` and never echoes the credential verbatim.
    """
    assert completed.returncode == 0, redact(completed.stderr)
    assert completed.stderr == "", redact(completed.stderr)
    return json.loads(completed.stdout)


def _inspect_evidence(completed: WatchResult) -> dict[str, Any]:
    """Assert a public inspect run's exit/stdout contract and return its evidence doc.

    The exit status must be 0 and stdout must be one JSON envelope with ``success`` true.
    stderr must carry no traceback; the pywin32 ``IUnknown`` teardown banner is a known
    cosmetic artifact and is the only non-empty stderr this suite tolerates. Failure
    messages format the raw stream through ``redact``.
    """
    assert completed.returncode == 0, redact(completed.stderr)
    assert "Traceback" not in completed.stderr, redact(completed.stderr)
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True, redact(completed.stdout)
    return envelope["data"]


def _report(completed: WatchResult) -> dict[str, Any]:
    """Assert a public render run's exit/stdout contract and return its report payload."""
    assert completed.returncode == 0, redact(completed.stderr)
    assert "Traceback" not in completed.stderr, redact(completed.stderr)
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True, redact(completed.stdout)
    return envelope["data"]


def _table_named(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return next(table for table in payload["tables"] if table["name"] == name)


def _assert_credential_free(text: str, label: str) -> None:
    """Assert the canary and any ``PWD=`` segment are absent, without echoing the text.

    This runs against the watcher's **raw** streams and against on-disk evidence bytes,
    so a leak under a key other than ``PWD=`` cannot be masked into a vacuous pass.
    """
    assert CANARY not in text, f"{label}: fake canary reached the output"
    assert "PWD=" not in text.upper(), f"{label}: a PWD= segment reached the output"


@pytest.fixture(scope="session")
def link_build() -> dict[str, Any]:
    """Build one disposable SQLite -> ACE ODBC-link fixture through the helper CLI.

    The build runs under the dialog watcher so the step is bounded and dialog-free; the
    fixture root does not exist until the helper creates it, so the child CWD is pinned to
    its parent (``db/fixtures``) rather than the fixture root itself.

    The fixture root is intentionally retained as evidence: this suite never deletes a
    pre-existing directory, and no cleanup runs at teardown.
    """
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_ROOT / f"sqlite-odbc-qual-{uuid.uuid4().hex[:12]}"
    completed = _run_helper(["build", "--root", str(root)], cwd=FIXTURE_ROOT)
    payload = _helper_payload(completed)
    assert payload["ok"] is True, payload
    inventory = json.loads((root / INVENTORY_FILENAME).read_text(encoding="utf-8"))
    return {"root": root, "build": payload, "inventory": inventory, "run": completed}


@pytest.fixture(scope="session")
def link_controls(link_build: dict[str, Any]) -> dict[str, Any]:
    """Run the deliberate-contact, isolation, and raw-catalog helper controls once.

    These are the positive controls that must hold *before* source-file isolation is used
    as a tripwire: the link genuinely contacts SQLite, the exclusive lock genuinely
    refuses that contact, and the driver catalog genuinely reports the linked type. Every
    step runs under the dialog watcher; the deliberate locked read is the named positive
    control that raises the driver's own connect dialog.
    """
    root = link_build["root"]
    contact = _run_helper(["prove-contact", "--root", str(root)], cwd=root)
    isolation = _run_helper(["prove-isolation", "--root", str(root)], cwd=root)
    catalog = _run_helper(["catalog-types", "--root", str(root)], cwd=root)
    return {
        "contact_run": contact,
        "isolation_run": isolation,
        "catalog_run": catalog,
        "contact": _helper_payload(contact),
        "isolation": _helper_payload(isolation),
        "catalog": _helper_payload(catalog),
    }


@pytest.fixture(scope="session")
def locked_runs(link_build: dict[str, Any]) -> dict[str, Any]:
    """Run every public CLI inspection/render while the SQLite source file is locked.

    The exclusive ``share=0`` lock is held by this test process and released in ``finally``
    before the source hashes are re-read. Each CLI invocation is a fresh subprocess run
    under the dialog watcher, so no warmed DAO/ODBC handle crosses between the inspections
    and any driver dialog is detected, closed, and reported as a failure. A successful run
    under the lock therefore needed no source-file open and raised no driver connect
    dialog; the lock alone does not prove that no other form of contact was attempted (see
    the module docstring).
    """
    root = link_build["root"]
    sqlite_path = root / SQLITE_FILENAME
    accdb_path = root / ACCDB_FILENAME
    sqlite_before = _sha256(sqlite_path)
    accdb_before = _sha256(accdb_path)
    env = _cli_env(root)
    runs: dict[str, Any] = {}
    with ExclusiveFileLock(sqlite_path):
        try:
            with sqlite_path.open("rb") as handle:
                handle.read(16)
        except OSError:
            plain_read_refused = True
        else:
            plain_read_refused = False
        assert plain_read_refused, "the exclusive file lock did not refuse a plain read"
        runs["dao"] = _run_cli(
            ["documentation", "inspect", "--db", str(accdb_path), "--backend", "dao", "--json"],
            env,
            cwd=root,
        )
        runs["odbc"] = _run_cli(
            ["documentation", "inspect", "--db", str(accdb_path), "--backend", "odbc", "--json"],
            env,
            cwd=root,
        )
        runs["filtered"] = _run_cli(
            [
                "documentation",
                "inspect",
                "--db",
                str(accdb_path),
                "--backend",
                "dao",
                "--tables",
                LOCAL_TABLE,
                "--json",
            ],
            env,
            cwd=root,
        )
        evidence_dao = root / "evidence-dao.json"
        evidence_dao.write_text(runs["dao"].stdout, encoding="utf-8")
        evidence_odbc = root / "evidence-odbc.json"
        evidence_odbc.write_text(runs["odbc"].stdout, encoding="utf-8")
        evidence_paths = {"dao": evidence_dao, "odbc": evidence_odbc}
        runs["render_dao_1"] = _run_cli(
            ["documentation", "render", "--evidence", str(evidence_dao), "--json"], env, cwd=root
        )
        runs["render_dao_2"] = _run_cli(
            ["documentation", "render", "--evidence", str(evidence_dao), "--json"], env, cwd=root
        )
        runs["render_odbc"] = _run_cli(
            ["documentation", "render", "--evidence", str(evidence_odbc), "--json"], env, cwd=root
        )
    return {
        "plain_read_refused": plain_read_refused,
        "runs": runs,
        "evidence_paths": evidence_paths,
        "sqlite_before": sqlite_before,
        "sqlite_after": _sha256(sqlite_path),
        "accdb_before": accdb_before,
        "accdb_after": _sha256(accdb_path),
    }


# --------------------------------------------------------------------------- #
# positive controls: genuine link, validated tripwire, raw catalog types
# --------------------------------------------------------------------------- #


def test_deliberate_contact_proves_a_genuine_odbc_link(
    link_build: dict[str, Any], link_controls: dict[str, Any]
) -> None:
    """The stored bits mark dbAttachedODBC, a deliberate read returns both rows, and the
    build and deliberate-contact helper steps raise no driver dialog."""
    assert_no_dialogs(link_build["run"], "helper build")
    assert_no_dialogs(link_controls["contact_run"], "helper prove-contact")
    contact = link_controls["contact"]
    assert contact["ok"] is True, contact
    assert all(contact["checks"].values()), contact["checks"]
    link = contact["link_metadata"]
    assert link["db_attached_odbc_bit"] is True
    assert link["attributes_hex"] == "0x20020000"
    assert link["db_attach_save_pwd_bit"] is True
    assert link["source_table"] == REMOTE_TABLE
    assert contact["local_table"]["row_count"] == 1
    assert contact["linked_read"]["row_count"] == len(REMOTE_ROWS)
    assert contact["linked_read"]["rows"] == REMOTE_ROW_VALUES
    assert contact["direct_odbc"]["rows"] == REMOTE_ROW_VALUES


def test_exclusive_lock_refuses_linked_reads_then_release_restores_them(
    link_controls: dict[str, Any],
) -> None:
    """The deliberate locked read raises exactly the expected driver dialog, the watcher
    observes and closes it, and the read is refused before release restores it.

    The single expected dialog is the watcher's own positive control: it proves the lock
    was actually challenged by a real driver connect attempt, and it proves the watcher
    detects and closes genuine driver dialogs. If the dialog is not observed, this control
    fails loudly instead of passing vacuously.
    """
    dialog = assert_expected_driver_dialog(
        link_controls["isolation_run"], "helper prove-isolation deliberate locked linked read"
    )
    assert dialog.owner_image == "python.exe"
    assert link_controls["isolation_run"].dismissals[0].posted_wm_close is True
    isolation = link_controls["isolation"]
    assert isolation["ok"] is True, isolation
    assert all(isolation["checks"].values()), isolation["checks"]
    refusal = isolation["locked_linked_read_refusal"]
    assert refusal["refused"] is True
    assert refusal["error_code"] == 5003151
    assert refusal["row_count"] == 0
    assert isolation["post_release_linked_read"]["row_count"] == len(REMOTE_ROWS)
    assert isolation["sqlite_sha256_before"] == isolation["sqlite_sha256_after"]


def test_runtime_catalog_separates_local_table_from_linked_synonym(
    link_controls: dict[str, Any],
) -> None:
    """Runtime SYNONYM recognition is asserted from the driver catalog, not the port.

    This fixture carries no unclassified user table type, so the fail-closed unknown-type
    path is deliberately *not* claimed here; it stays proven by the classification-before-
    read unit tests.
    """
    catalog = link_controls["catalog"]
    assert_no_dialogs(link_controls["catalog_run"], "helper catalog-types")
    assert catalog["ok"] is True, catalog
    assert catalog["checks"] == {
        "local_table_is_table": True,
        "link_is_synonym": True,
        "no_unknown_user_table_type_present": True,
    }
    assert catalog["user_table_types"] == {LOCAL_TABLE: "TABLE", LINK_NAME: "SYNONYM"}


# --------------------------------------------------------------------------- #
# public DAO / ODBC inspection under the source lock
# --------------------------------------------------------------------------- #


def test_dialog_census_per_step_and_zero_for_every_locked_cli_run(
    link_build: dict[str, Any], link_controls: dict[str, Any], locked_runs: dict[str, Any]
) -> None:
    """Every step's observed dialog count is zero except the deliberate locked read.

    This is the S5a unattended guarantee and the per-step census in one place: the
    deliberate ``prove-isolation`` locked read is the *only* step allowed to raise a
    dialog (its expected ``SQLite3 ODBC Driver Connect`` watcher positive control), and
    DAO inspect, ODBC inspect, the filtered DAO subset, and the three offline renders each
    ran while the SQLite source was exclusively locked with **zero** dialogs from their
    own process trees. Any dialog is auto-closed by the watcher and fails with its title,
    class, and owner recorded.
    """
    for name, run in locked_runs["runs"].items():
        assert_no_dialogs(run, f"public CLI {name} under the source lock")
    census = {
        "helper build": len(link_build["run"].dialogs),
        "helper prove-contact": len(link_controls["contact_run"].dialogs),
        "helper prove-isolation (expected dialog)": len(link_controls["isolation_run"].dialogs),
        "helper catalog-types": len(link_controls["catalog_run"].dialogs),
        **{f"cli {name}": len(run.dialogs) for name, run in locked_runs["runs"].items()},
    }
    assert census == {
        "helper build": 0,
        "helper prove-contact": 0,
        "helper prove-isolation (expected dialog)": 1,
        "helper catalog-types": 0,
        "cli dao": 0,
        "cli odbc": 0,
        "cli filtered": 0,
        "cli render_dao_1": 0,
        "cli render_dao_2": 0,
        "cli render_odbc": 0,
    }, census
    assert locked_runs["runs"]["dao"].returncode == 0
    assert locked_runs["runs"]["odbc"].returncode == 0


def test_locked_dao_inspection_needs_no_source_open_and_reports_linked_gaps(
    locked_runs: dict[str, Any],
) -> None:
    """DAO inspection succeeds under the lock and records the linked gap, local intact."""
    assert locked_runs["plain_read_refused"] is True
    evidence = _inspect_evidence(locked_runs["runs"]["dao"])
    assert evidence["source"]["backend_identity"] == "DAO"
    payload = evidence["data"]

    linked = _table_named(payload, LINK_NAME)
    assert linked["kind"] == "linked"
    assert linked["fields"] == []
    assert linked["indexes"] == []
    assert linked["primary_key"]["state"] == "unavailable"
    assert linked["primary_key"]["reason"] == LINKED_GAP_REASON
    assert linked["source_table_name"] == REMOTE_TABLE
    assert evidence["coverage"]["tables"]["unsupported"][LINK_NAME] == LINKED_GAP_REASON
    assert LINK_NAME not in evidence["coverage"]["tables"]["failed"]

    local = _table_named(payload, LOCAL_TABLE)
    assert local["kind"] == "local"
    assert [field["name"] for field in local["fields"]] == ["id", "note"]
    assert local["primary_key"]["state"] == "observed"
    assert local["primary_key"]["value"] == ["id"]
    assert [index["name"] for index in local["indexes"]] == ["PK_local_items"]

    assert locked_runs["sqlite_after"] == locked_runs["sqlite_before"]
    assert locked_runs["accdb_after"] == locked_runs["accdb_before"]


def test_locked_odbc_inspection_recognizes_synonym_gap_without_source_open(
    locked_runs: dict[str, Any],
) -> None:
    """ODBC inspection succeeds under the lock, reports the SYNONYM gap, keeps locals."""
    assert locked_runs["plain_read_refused"] is True
    evidence = _inspect_evidence(locked_runs["runs"]["odbc"])
    assert evidence["source"]["backend_identity"] == "ODBC"
    payload = evidence["data"]

    linked = _table_named(payload, LINK_NAME)
    assert linked["kind"] == "linked"
    assert linked["fields"] == []
    assert linked["indexes"] == []
    # Access ODBC reports the SYNONYM without exposing the link target.
    assert linked["connect"] is None
    assert linked["source_table_name"] is None
    assert linked["primary_key"]["state"] == "unavailable"
    assert linked["primary_key"]["reason"] == LINKED_GAP_REASON
    assert evidence["coverage"]["tables"]["unsupported"][LINK_NAME] == LINKED_GAP_REASON
    assert LINK_NAME not in evidence["coverage"]["tables"]["failed"]

    local = _table_named(payload, LOCAL_TABLE)
    assert [field["name"] for field in local["fields"]] == ["id", "note"]
    assert all(field["type"]["state"] == "observed" for field in local["fields"])
    assert all(field["required"]["state"] == "unavailable" for field in local["fields"])

    assert locked_runs["sqlite_after"] == locked_runs["sqlite_before"]
    assert locked_runs["accdb_after"] == locked_runs["accdb_before"]


def test_filtered_dao_inspection_selects_only_the_local_control(
    locked_runs: dict[str, Any],
) -> None:
    """PRESERVE: an explicit --tables subset selects only that local table.

    The filtered-out linked source is absent (never inspected), and the filtered document
    is complete because the local control alone has no gaps.
    """
    evidence = _inspect_evidence(locked_runs["runs"]["filtered"])
    payload = evidence["data"]
    assert evidence["run"]["scope"]["tables"] == [LOCAL_TABLE]
    assert [table["name"] for table in payload["tables"]] == [LOCAL_TABLE]
    assert evidence["coverage"]["tables"]["unsupported"] == {}
    assert payload["status"] == "complete"


# --------------------------------------------------------------------------- #
# credential sanitation and offline rendering
# --------------------------------------------------------------------------- #


def test_stored_link_credential_is_dropped_and_streams_stay_clean(
    link_controls: dict[str, Any], locked_runs: dict[str, Any]
) -> None:
    """ACE drops the fake link credential on this path; no canary reaches any output.

    ``pwd_segment_present`` records whether ACE retained a stored ``PWD`` segment at all.
    On this build path it does not, so a *positive* runtime redaction of a stored
    credential cannot be exercised here: that sub-gap is retained rather than passed
    vacuously (sanitation itself stays unit-proven at the connection-string seam). What
    is asserted is the observable outcome: the plaintext canary is absent from the stored
    Connect, the emitted connect, every **raw** stdout/stderr stream, the on-disk
    ``evidence-*.json`` bytes, and the rendered Markdown. The streams are raw because the
    watcher never redacts them; a leak under any key other than ``PWD=`` would otherwise
    be masked into a vacuous pass.
    """
    stored = link_controls["contact"]["link_metadata"]
    assert stored["pwd_segment_present"] is False, (
        "ACE retained a stored PWD segment on this environment; the qualification must then "
        "positively assert it is dropped from evidence instead of retaining this sub-gap"
    )
    assert stored["canary_retained_in_stored_connect"] is False

    linked = _table_named(_inspect_evidence(locked_runs["runs"]["dao"])["data"], LINK_NAME)
    connect = linked["connect"]
    assert connect, "DAO local catalogue must expose its sanitized Connect"
    _assert_credential_free(connect, "DAO emitted connect")
    assert f"Driver={{{SQLITE_DRIVER}}}" in connect

    for name in ("dao", "odbc", "filtered", "render_dao_1", "render_odbc"):
        run = locked_runs["runs"][name]
        _assert_credential_free(run.stdout, f"{name} raw stdout")
        _assert_credential_free(run.stderr, f"{name} raw stderr")
    for backend, evidence_path in locked_runs["evidence_paths"].items():
        on_disk = evidence_path.read_text(encoding="utf-8")
        _assert_credential_free(on_disk, f"on-disk evidence-{backend}.json")
        assert json.loads(on_disk) == json.loads(locked_runs["runs"][backend].stdout), (
            f"evidence-{backend}.json must carry the inspect run's raw stdout document"
        )


def test_offline_render_keeps_linked_gaps_without_credentials(
    locked_runs: dict[str, Any],
) -> None:
    """Rendering the DAO evidence stays offline, keeps the gap, and is deterministic."""
    report = _report(locked_runs["runs"]["render_dao_1"])
    markdown = report["markdown"]
    linked_reason = _table_named(_inspect_evidence(locked_runs["runs"]["dao"])["data"], LINK_NAME)[
        "primary_key"
    ]["reason"]
    assert f"### {LINK_NAME}" in markdown
    assert f"Linked table: {LINK_NAME}" in markdown
    assert REMOTE_TABLE in markdown
    assert "_No fields observed_" in markdown
    assert linked_reason in markdown
    _assert_credential_free(markdown, "render_dao_1 markdown")
    assert report["status"] == "partial"
    assert report["coverage"]["tables"]["unsupported"] == 1
    assert locked_runs["runs"]["render_dao_2"].stdout == locked_runs["runs"]["render_dao_1"].stdout


def test_offline_render_of_odbc_synonym_exposes_no_link_target(
    locked_runs: dict[str, Any],
) -> None:
    """The ODBC SYNONYM renders with no target and no credential leakage."""
    report = _report(locked_runs["runs"]["render_odbc"])
    markdown = report["markdown"]
    linked_reason = _table_named(_inspect_evidence(locked_runs["runs"]["odbc"])["data"], LINK_NAME)[
        "primary_key"
    ]["reason"]
    assert f"Linked table: {LINK_NAME}" in markdown
    assert "unknown source" in markdown
    assert linked_reason in markdown
    _assert_credential_free(markdown, "render_odbc markdown")
