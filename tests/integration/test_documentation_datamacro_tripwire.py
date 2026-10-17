"""Opt-in runtime proof that the documentation pipeline never fires table data macros.

Why this suite exists
---------------------
The shipped documentation pipeline claims non-execution of executable database content,
but the earlier attempt to arm a table data-macro tripwire was accept-blocked: the client
import format was unknown, so no fixture could prove that a real, live data macro stays
unfired. The format was then established empirically against a real Access build (probe
``db/fixtures/datamacro-probe-0327ea09-9b4a-4c47-ac2f-8027eeaa67fd``; ledger L8): Access's
own ``SaveAsText acTableDataMacro`` round-trip is ground truth, and its UTF-16 LE (BOM),
CRLF-after-declaration, ``CreateRecord``/``SetField`` shape loads on the first attempt.

This suite closes S6/S6a of ``odd/tasks/encrypted-reopen-and-data-macros.md`` on that
proven shape. ``tests/helpers/datamacro_worker.py`` self-builds a disposable,
repository-confined ``tblTripwire`` fixture whose ``AfterInsert``/``AfterUpdate``/
``AfterDelete`` macros each write one distinct row into ``tblTripwireLog``. Each of the
three events is then proven live on its **own byte-copy** before the public pipeline ever
touches the original, so the final "the log is still empty" claim cannot pass vacuously.

What is qualified
-----------------
* **Fixture is armed.** ``build`` creates both tables with DAO, inserts exactly one seed
  row into ``tblTripwire`` *before* the macro is loaded (the log starts empty), loads the
  one three-event XML through ``Application.LoadFromText`` ``acTableDataMacro``, and proves
  all three ``DataMacro`` events with ``CreateRecord``/``SetField`` survive a
  ``SaveAsText`` round-trip.
* **Per-event positive controls first.** One byte-copy per event: a single DAO ``fire``
  then a read-only ``count`` must show exactly one ``tblTripwireLog`` row whose ``Event``
  value is that event. A copy that does not fire fails the run *before* the pipeline runs.
* **Pipeline non-execution.** On the original, ``documentation inspect`` through
  ``--backend dao``, ``--backend odbc`` and ``--backend auto`` each exit 0 and list both
  tables; each evidence file renders, and one rendered report is saved inside the fixture
  root through the shipped write gate. A final read-only ``count`` must still show zero
  log rows and exactly the one seed row, and the original ``.accdb`` SHA-256 must be
  identical before and after the whole run.
* **Unattended and confined.** Every helper and CLI subprocess runs under
  ``tests/helpers/dialog_watcher.py`` with CWD pinned inside the fixture root; every step
  must observe zero dialogs. Generated state stays inside a unique
  ``db/fixtures/datamacro-*`` root, no root is ever deleted, and helper stderr is asserted
  to carry no traceback (the benign pywin32 ``IUnknown`` teardown banner is expected).

A pipeline step that fires a macro or mutates the fixture would be a REAL production
defect; this suite then stops and reports it rather than adjusting the expectation.

Run explicitly on Windows with ACE DAO and ``Access.Application``::

    ACCESS_DOCUMENTATION_FIXTURES_TEST=1 .venv/Scripts/python.exe -m pytest tests/integration/test_documentation_datamacro_tripwire.py -q

Without ``ACCESS_DOCUMENTATION_FIXTURES_TEST=1`` the module skips cleanly, as it does off
Windows, without ACE DAO, or without ``Access.Application``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.datamacro_worker import (
    DB_FILENAME,
    EVENT_KINDS,
    LOG_TABLE,
    ROUNDTRIP_FILENAME,
    TRIPWIRE_TABLE,
    XML_FILENAME,
)
from tests.helpers.dialog_watcher import WatchResult, assert_no_dialogs, run_watched
from tests.integration.conftest import _has_ace_dao

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tests" / "helpers" / "datamacro_worker.py"
OPT_IN_ENV = "ACCESS_DOCUMENTATION_FIXTURES_TEST"
ALLOW_DOC_WRITE_ENV = "ACCESS_MCP_ALLOW_DOC_WRITE"
FIXTURE_PARENT = ROOT / "db" / "fixtures"

BACKENDS = ("dao", "odbc", "auto")

# The write gate is granted per call with ``--allow-doc-write --yes``; the env grant is
# used only for the single save step, mirroring what ``documentation_save`` accepts.
ALLOW_DOC_WRITE = "1"

# PYTHONFAULTHANDLER would inject a process-state dump into captured stderr, and an
# inherited doc-write grant would make the "gate required" expectation ambient state.
STRIPPED_ENV = (ALLOW_DOC_WRITE_ENV, "PYTHONFAULTHANDLER")
CLI_TIMEOUT_SECONDS = 300.0
HELPER_TIMEOUT_SECONDS = 600.0

# One seed row, and one deliberate write per event: insert adds a row, update keeps it,
# delete removes it.
EXPECTED_TRIPWIRE_ROWS_AFTER_FIRE = {"insert": 2, "update": 1, "delete": 0}

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.skipif(not _has_ace_dao(), reason="Requires ACE DAO (DAO.DBEngine.120)"),
    pytest.mark.skipif(
        os.environ.get(OPT_IN_ENV) != "1",
        reason=f"Set {OPT_IN_ENV}=1 to run the table data-macro tripwire qualification suite",
    ),
    pytest.mark.com_integration,
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


def _msaccess_pids() -> set[int]:
    """Return running MSACCESS.EXE process ids so the suite can be checked for leaks."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE", "/NH"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    pids: set[int] = set()
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].upper() == "MSACCESS.EXE" and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _clean_env() -> dict[str, str]:
    """Build a deterministic environment without inherited dump/grant switches."""
    return {key: value for key, value in os.environ.items() if key not in STRIPPED_ENV}


def _cli_env(fixture_dir: Path, *, allow_doc_write: bool = False) -> dict[str, str]:
    """Build a deterministic CLI environment scoped to the fixture directory."""
    env = _clean_env()
    env["ACCESS_MCP_ALLOWED_DIRS"] = str(fixture_dir)
    if allow_doc_write:
        env[ALLOW_DOC_WRITE_ENV] = ALLOW_DOC_WRITE
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
    """Run one helper invocation under the dialog watcher with its CWD pinned."""
    return run_watched(
        [_venv_python(), str(HELPER), *args],
        cwd=cwd,
        env=_clean_env(),
        timeout_seconds=HELPER_TIMEOUT_SECONDS,
    )


def _assert_raw_streams(run: WatchResult, label: str) -> None:
    """Assert the raw captured streams carry no traceback (dialogs are asserted separately)."""
    assert "Traceback" not in run.stderr, f"{label}: traceback on raw stderr"
    assert "Traceback" not in run.stdout, f"{label}: traceback on raw stdout"


def _helper_payload(run: WatchResult, command: str) -> dict[str, Any]:
    """Parse a helper's single JSON result line and require a clean exit.

    Helper stderr is not required to be empty: pywin32 can print a benign COM-release
    diagnostic. It must never carry a traceback, which :func:`_assert_raw_streams` pins.
    """
    assert run.returncode == 0, f"helper {command} exited {run.returncode}"
    _assert_raw_streams(run, f"helper {command}")
    return json.loads(run.stdout)


def _inspect_evidence(run: WatchResult, label: str) -> dict[str, Any]:
    """Assert a successful inspect run's exit contract and return its evidence payload."""
    assert run.returncode == 0, f"{label}: exit {run.returncode}"
    _assert_raw_streams(run, label)
    envelope = json.loads(run.stdout)
    assert envelope["success"] is True, f"{label}: envelope reported failure"
    return envelope["data"]


def _report(run: WatchResult, label: str) -> dict[str, Any]:
    """Assert a render run's exit contract and return its report payload."""
    assert run.returncode == 0, f"{label}: exit {run.returncode}"
    _assert_raw_streams(run, label)
    envelope = json.loads(run.stdout)
    assert envelope["success"] is True, f"{label}: envelope reported failure"
    return envelope["data"]


def _saved(run: WatchResult, label: str) -> dict[str, Any]:
    """Assert a guarded save run's exit contract and return its save payload."""
    assert run.returncode == 0, f"{label}: exit {run.returncode}"
    _assert_raw_streams(run, label)
    envelope = json.loads(run.stdout)
    assert envelope["success"] is True, f"{label}: envelope reported failure"
    return envelope["data"]


def _step_record(run: WatchResult) -> dict[str, Any]:
    """Return the bounded, redacted per-step facts retained as suite evidence."""
    return {
        "returncode": run.returncode,
        "dialogs": len(run.dialogs),
        "dialog_summary": run.dialog_summary(),
        "timed_out": run.timed_out,
        "cap_exceeded": run.cap_exceeded,
        "terminated_pids": list(run.terminated_pids),
        "stdout_bytes": len(run.stdout.encode("utf-8")),
        "stderr_bytes": len(run.stderr.encode("utf-8")),
        "duration_ms": run.duration_ms,
    }


def _table_named(document: dict[str, Any], name: str) -> dict[str, Any]:
    return next(table for table in document["tables"] if table["name"] == name)


# --------------------------------------------------------------------------- #
# fixture: environment -> build -> per-event positive controls on byte-copies
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def datamacro_build() -> dict[str, Any]:
    """Report the environment, then build the armed tripwire fixture in its own process.

    The build runs under the dialog watcher and creates the confined root; every later
    step runs with CWD pinned to that root. The root is retained as evidence: this suite
    never deletes a fixture root.
    """
    FIXTURE_PARENT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_PARENT / f"datamacro-{uuid.uuid4().hex[:12]}"
    environment_run = _run_helper(["environment"], cwd=FIXTURE_PARENT)
    _assert_raw_streams(environment_run, "helper environment")
    environment = json.loads(environment_run.stdout)
    if not environment.get("ok"):
        reasons = "; ".join(environment.get("unsupported_reasons", [])) or "environment unsupported"
        pytest.skip(f"Requires ACE DAO and Access.Application: {reasons}")
    # Root does not exist yet, so the build runs with its CWD pinned to db/fixtures.
    build_run = _run_helper(["build", "--root", str(root)], cwd=FIXTURE_PARENT)
    _assert_raw_streams(build_run, "helper build")
    # Parsed without requiring exit 0 so a failed control keeps its diagnostic payload.
    build = json.loads(build_run.stdout)
    if root.is_dir():
        build_evidence = {
            "schema": "datamacro-tripwire-evidence/1",
            "phase": "build",
            "environment": environment,
            "build": build,
            "steps": [
                {"step": "environment", **_step_record(environment_run)},
                {"step": "build", **_step_record(build_run)},
            ],
        }
        (root / "build-evidence.json").write_text(
            json.dumps(build_evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return {
        "root": root,
        "environment_run": environment_run,
        "environment": environment,
        "build_run": build_run,
        "build": build,
        "database": root / DB_FILENAME,
    }


@pytest.fixture(scope="session")
def datamacro_controls(datamacro_build: dict[str, Any]) -> dict[str, Any]:
    """Prove each event's macro is live on its own byte-copy, before the pipeline runs.

    A copy that does not fire raises here, so the later non-execution claim can never be
    an assertion on an inert fixture. The original is hashed before and after so the
    controls are also proven not to touch it.
    """
    root = Path(datamacro_build["root"])
    database = Path(datamacro_build["database"])
    original_before = _sha256(database)

    controls: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []
    for event in EVENT_KINDS:
        copy_path = root / f"control-{event}.accdb"
        copy_path.write_bytes(database.read_bytes())
        fire_run = _run_helper(["fire", "--event", event, "--db", str(copy_path)], cwd=root)
        fire = _helper_payload(fire_run, f"fire {event}")
        count_run = _run_helper(["count", "--db", str(copy_path)], cwd=root)
        count = _helper_payload(count_run, f"count {event}")
        assert_no_dialogs(fire_run, f"helper fire {event}")
        assert_no_dialogs(count_run, f"helper count {event}")
        assert count["log_rows"] == 1, f"{event}: the deliberate write produced {count['log_rows']} log rows"
        assert count["log_events"] == [event], f"{event}: log events were {count['log_events']!r}"
        assert count["tripwire_rows"] == EXPECTED_TRIPWIRE_ROWS_AFTER_FIRE[event], event
        controls[event] = {"copy": copy_path, "fire": fire, "count": count}
        steps.append({"step": f"fire {event}", **_step_record(fire_run)})
        steps.append({"step": f"count {event}", **_step_record(count_run)})

    original_after = _sha256(database)
    evidence = {
        "schema": "datamacro-tripwire-evidence/1",
        "phase": "positive-controls",
        "database_sha256_before": original_before,
        "database_sha256_after": original_after,
        "events": list(EVENT_KINDS),
        "steps": steps,
    }
    (root / "controls-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return {
        "controls": controls,
        "original_before": original_before,
        "original_after": original_after,
        "steps": steps,
    }


@pytest.fixture(scope="session")
def datamacro_pipeline(
    datamacro_build: dict[str, Any], datamacro_controls: dict[str, Any]
) -> dict[str, Any]:
    """Run the full public pipeline on the ORIGINAL fixture, then read it back.

    Order: inspect through ``dao``, ``odbc`` and ``auto`` (each ``--json``, evidence kept
    on disk inside the root), render each evidence file, and save one rendered report
    inside the root through the shipped write gate (``--allow-doc-write --yes`` with
    ``ACCESS_MCP_ALLOW_DOC_WRITE=1``). A final read-only ``count`` reads the tripwire back
    and the original SHA-256 is measured before and after the whole run.
    """
    root = Path(datamacro_build["root"])
    database = Path(datamacro_build["database"])
    env = _cli_env(root)
    database_before = _sha256(database)
    pids_before = _msaccess_pids()

    runs: dict[str, WatchResult] = {}
    evidence: dict[str, Any] = {}
    evidence_paths: dict[str, Path] = {}
    reports: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []

    for backend in BACKENDS:
        label = f"inspect_{backend}"
        runs[label] = _run_cli(
            ["documentation", "inspect", "--db", str(database), "--backend", backend, "--json"], env, cwd=root
        )
        evidence[backend] = _inspect_evidence(runs[label], f"inspect {backend}")
        evidence_paths[backend] = root / f"evidence-{backend}.json"
        evidence_paths[backend].write_text(runs[label].stdout, encoding="utf-8")
        steps.append({"step": label, **_step_record(runs[label])})

    for backend in BACKENDS:
        label = f"render_{backend}"
        runs[label] = _run_cli(
            ["documentation", "render", "--evidence", str(evidence_paths[backend]), "--kind", "database", "--json"],
            env,
            cwd=root,
        )
        reports[backend] = _report(runs[label], f"render {backend}")
        steps.append({"step": label, **_step_record(runs[label])})

    report_path = root / "report-dao.json"
    report_path.write_text(runs["render_dao"].stdout, encoding="utf-8")
    destination = root / "report-dao.md"
    runs["save"] = _run_cli(
        [
            "documentation",
            "save",
            "--report",
            str(report_path),
            "--output-path",
            str(destination),
            "--allow-doc-write",
            "--yes",
            "--json",
        ],
        _cli_env(root, allow_doc_write=True),
        cwd=root,
    )
    saved = _saved(runs["save"], "save")
    steps.append({"step": "save", **_step_record(runs["save"])})

    runs["count"] = _run_helper(["count", "--db", str(database)], cwd=root)
    final_count = _helper_payload(runs["count"], "count")
    steps.append({"step": "count", **_step_record(runs["count"])})

    database_after = _sha256(database)
    new_pids = _msaccess_pids() - pids_before
    pipeline_evidence = {
        "schema": "datamacro-tripwire-evidence/1",
        "phase": "pipeline",
        "database_sha256_before": database_before,
        "database_sha256_after": database_after,
        "final_tripwire_rows": final_count["tripwire_rows"],
        "final_log_rows": final_count["log_rows"],
        "final_log_events": final_count["log_events"],
        "new_msaccess_pids": sorted(new_pids),
        "steps": steps,
    }
    (root / "pipeline-evidence.json").write_text(
        json.dumps(pipeline_evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return {
        "root": root,
        "database": database,
        "database_before": database_before,
        "database_after": database_after,
        "build_sha256": datamacro_build["build"]["database_sha256"],
        "destination": destination,
        "report": reports["dao"],
        "runs": runs,
        "evidence": evidence,
        "evidence_paths": evidence_paths,
        "reports": reports,
        "saved": saved,
        "final_count": final_count,
        "new_pids": new_pids,
        "steps": steps,
        "pipeline_evidence": pipeline_evidence,
    }


# --------------------------------------------------------------------------- #
# S6a: the fixture is self-built and armed, and the controls are non-vacuous
# --------------------------------------------------------------------------- #


def test_tripwire_fixture_is_self_built_and_armed(datamacro_build: dict[str, Any]) -> None:
    """The helper builds both tables, seeds the tripwire before loading, and arms the macro.

    The seed row is inserted by DAO before the macro exists, so ``tblTripwireLog`` starts
    empty; the three-event XML is then loaded through ``Application.LoadFromText`` and
    re-exported by Access, which must contain all three events with
    ``CreateRecord``/``SetField``. Without this the whole suite would be testing an inert
    table.
    """
    assert_no_dialogs(datamacro_build["environment_run"], "helper environment")
    assert_no_dialogs(datamacro_build["build_run"], "helper build")

    environment = datamacro_build["environment"]
    assert environment["ace_dao_available"] is True
    assert environment["access_application_available"] is True

    build = datamacro_build["build"]
    assert datamacro_build["build_run"].returncode == 0, "the fixture build did not exit 0"
    assert build["ok"] is True, "the tripwire fixture build reported a failed control"
    assert build["tables"] == [TRIPWIRE_TABLE, LOG_TABLE], build["tables"]
    assert build["tripwire_rows"] == 1, "the seed row was not inserted"
    assert build["log_rows"] == 0, "the log was not empty after the seed insert"
    assert build["macro_load_ok"] is True, f"LoadFromText refused the macro: {build['macro_load_error']}"
    assert build["macro_save_ok"] is True, f"SaveAsText refused the macro: {build['macro_save_error']}"
    assert build["roundtrip_ok"] is True, build["roundtrip_checks"]
    checks = build["roundtrip_checks"]
    assert checks["all_events_present"] is True
    assert checks["create_record_count"] == len(EVENT_KINDS)
    assert checks["set_field_count"] == len(EVENT_KINDS)
    assert build["msaccess_residue"] == [], build["msaccess_residue"]
    assert len(build["database_sha256"]) == 64


def test_tripwire_positive_controls_fire_each_event_on_byte_copies(
    datamacro_build: dict[str, Any], datamacro_controls: dict[str, Any]
) -> None:
    """Each event's macro really fires on its own copy, and never on the original.

    A single DAO ``fire`` per copy must produce exactly one ``tblTripwireLog`` row whose
    value is that event, and the original's SHA-256 must be identical before and after the
    three control runs. This is the non-vacuous precondition for the non-execution claim
    in the next test; a copy that does not fire fails here loudly.
    """
    controls = datamacro_controls["controls"]
    assert sorted(controls) == sorted(EVENT_KINDS)
    for event in EVENT_KINDS:
        control = controls[event]
        assert control["fire"]["ok"] is True
        assert control["count"]["ok"] is True
        assert control["count"]["log_rows"] == 1
        assert control["count"]["log_events"] == [event]

    assert datamacro_controls["original_before"] == datamacro_controls["original_after"], (
        "a positive-control byte-copy run mutated the original fixture"
    )
    assert datamacro_controls["original_before"] == datamacro_build["build"]["database_sha256"], (
        "the original fixture changed between build and controls"
    )


# --------------------------------------------------------------------------- #
# S6: the public pipeline inspects the fixture without firing the macros
# --------------------------------------------------------------------------- #


def test_documentation_pipeline_inspects_tripwire_fixture_without_firing_data_macros(
    datamacro_pipeline: dict[str, Any]
) -> None:
    """The full inspect/render/save pipeline exits 0, leaves the log empty and the bytes intact.

    Every shipped backend inspects the armed fixture and lists ``tblTripwire`` and
    ``tblTripwireLog``; every evidence file renders; one rendered report saves inside the
    fixture root. The read-only final ``count`` must still show zero log rows and exactly
    the one seed row, and the original SHA-256 must be unchanged. A fired macro or a
    mutated file would be a real production defect, so it fails here instead of being
    adjusted away.
    """
    runs = datamacro_pipeline["runs"]
    for backend in BACKENDS:
        assert_no_dialogs(runs[f"inspect_{backend}"], f"inspect --backend {backend}")
        assert_no_dialogs(runs[f"render_{backend}"], f"render --backend {backend}")
        assert runs[f"inspect_{backend}"].returncode == 0
        assert runs[f"render_{backend}"].returncode == 0
        payload = datamacro_pipeline["evidence"][backend]
        document = payload["data"]
        assert _table_named(document, TRIPWIRE_TABLE)["kind"] == "local", backend
        assert _table_named(document, LOG_TABLE)["kind"] == "local", backend
        for name in (TRIPWIRE_TABLE, LOG_TABLE):
            assert name in payload["coverage"]["tables"]["inspected"], f"{backend}: {name} not inspected"
            assert name not in payload["coverage"]["tables"]["failed"], f"{backend}: {name} marked failed"

    assert_no_dialogs(runs["save"], "save")
    assert_no_dialogs(runs["count"], "helper count on the original")

    saved = datamacro_pipeline["saved"]
    destination = Path(datamacro_pipeline["destination"])
    assert destination.is_file(), "the guarded save produced no destination"
    assert Path(saved["output_path"]).resolve() == destination.resolve(), "the save wrote elsewhere"
    assert saved["sha256"] == _sha256(destination), "the saved report digest does not match the file"
    assert saved["bytes_written"] == destination.stat().st_size
    assert datamacro_pipeline["report"]["markdown"], "the rendered report carried no Markdown"

    final = datamacro_pipeline["final_count"]
    assert final["log_rows"] == 0, f"a pipeline step fired a data macro: {final['log_events']!r}"
    assert final["log_events"] == [], f"a pipeline step fired a data macro: {final['log_events']!r}"
    assert final["tripwire_rows"] == 1, "the seed row did not survive the pipeline"

    assert datamacro_pipeline["database_before"] == datamacro_pipeline["database_after"], (
        "the pipeline mutated the fixture bytes"
    )
    assert datamacro_pipeline["database_after"] == datamacro_pipeline["build_sha256"], (
        "the fixture changed after the build"
    )
    assert datamacro_pipeline["new_pids"] == set(), "the pipeline left a new MSACCESS.EXE process"


# --------------------------------------------------------------------------- #
# S6 non-vacuity: an unarmed fixture with the same tables stays inert
# --------------------------------------------------------------------------- #


def test_inert_build_without_macro_produces_zero_log_rows_on_fire() -> None:
    """The per-event positive controls discriminate: without the macro the log stays empty.

    A fresh root is built with ``--no-macro`` (tables plus the same seed row, no macro XML
    and no Access step), then written with the exact ``insert`` the positive controls use.
    Zero ``tblTripwireLog`` rows must result, so a single control log row can only come
    from the loaded macro. Without this the non-execution claim could hold on an inert
    fixture and prove nothing.
    """
    FIXTURE_PARENT.mkdir(parents=True, exist_ok=True)
    root = FIXTURE_PARENT / f"datamacro-{uuid.uuid4().hex[:12]}"
    database = root / DB_FILENAME

    build_run = _run_helper(["build", "--root", str(root), "--no-macro"], cwd=FIXTURE_PARENT)
    assert_no_dialogs(build_run, "helper build --no-macro")
    build = _helper_payload(build_run, "build --no-macro")
    assert build["ok"] is True, "the inert build reported a failed control"
    assert build["macro_loaded"] is False
    assert build["tripwire_rows"] == 1, "the inert build did not seed the tripwire"
    assert build["log_rows"] == 0, "the inert build already had log rows"
    assert not (root / XML_FILENAME).exists(), "the inert build wrote a macro XML"
    assert not (root / ROUNDTRIP_FILENAME).exists(), "the inert build ran the Access round-trip"
    assert build["msaccess_residue"] == [], build["msaccess_residue"]

    fire_run = _run_helper(["fire", "--event", "insert", "--db", str(database)], cwd=root)
    assert_no_dialogs(fire_run, "helper fire insert (inert)")
    fire = _helper_payload(fire_run, "fire insert (inert)")
    assert fire["tripwire_rows"] == 2, "the deliberate insert did not change the tripwire"

    count_run = _run_helper(["count", "--db", str(database)], cwd=root)
    assert_no_dialogs(count_run, "helper count (inert)")
    count = _helper_payload(count_run, "count (inert)")
    assert count["log_rows"] == 0, "an inert fixture produced log rows, so the controls are vacuous"
    assert count["log_events"] == []


# --------------------------------------------------------------------------- #
# S7 confinement: out-of-scope roots, databases and events are refused
# --------------------------------------------------------------------------- #


def test_worker_refuses_roots_and_databases_outside_datamacro_fixtures() -> None:
    """The worker refuses every target outside its own fixture roots, and creates nothing.

    One refusal per confinement rule: a root whose name lacks the ``datamacro-`` prefix, a
    build over a root that already exists, a database outside the fixture tree (Northwind,
    whose bytes must not change), and an unknown event. Each must exit 2 with the message
    the worker actually emits, and no refused command may create a directory.
    """
    FIXTURE_PARENT.mkdir(parents=True, exist_ok=True)
    existing_root = FIXTURE_PARENT / f"datamacro-{uuid.uuid4().hex[:12]}"
    existing_root.mkdir()
    entries_before = sorted(path.name for path in FIXTURE_PARENT.iterdir())

    wrong_prefix = FIXTURE_PARENT / "wrong-prefix-root"
    assert not wrong_prefix.exists(), f"the refused root already exists: {wrong_prefix}"

    northwind = ROOT / "db" / "northwind.accdb"
    assert northwind.is_file(), "the Northwind fixture is missing"
    northwind_before = _sha256(northwind)

    refusals = (
        (
            "wrong-prefix root",
            ["build", "--root", str(wrong_prefix)],
            "root directory name must start with 'datamacro-'",
        ),
        (
            "existing root",
            ["build", "--root", str(existing_root)],
            "root already exists; never overwrite fixtures:",
        ),
        (
            "database outside the fixtures",
            ["count", "--db", str(northwind)],
            "database must live inside a db/fixtures/datamacro-* root",
        ),
    )
    for label, argv, message in refusals:
        run = _run_helper(argv, cwd=FIXTURE_PARENT)
        assert run.returncode == 2, f"{label}: exit {run.returncode}, expected 2"
        assert message in run.stdout, f"{label}: {message!r} not in stdout {run.stdout!r}"
        assert_no_dialogs(run, f"helper {label}")

    bogus = _run_helper(["fire", "--event", "bogus"], cwd=FIXTURE_PARENT)
    assert bogus.returncode == 2, f"unknown event: exit {bogus.returncode}, expected 2"
    assert "invalid choice: 'bogus'" in bogus.stderr, f"unknown event: stderr {bogus.stderr!r}"
    assert_no_dialogs(bogus, "helper fire --event bogus")

    assert not wrong_prefix.exists(), "a refused build created its root"
    assert list(existing_root.iterdir()) == [], "a refused build wrote inside the existing root"
    assert sorted(path.name for path in FIXTURE_PARENT.iterdir()) == entries_before, (
        "a refused command created a directory"
    )
    assert _sha256(northwind) == northwind_before, "a refused count mutated Northwind"
