"""Opt-in isolated ACE documentation-pipeline qualification suite (T3 phase 1).

This suite builds the synthetic fixtures once in a fresh ``db/fixtures/e2e-<uuid>``
directory through the public generator CLI, then exercises the documentation pipeline
through the public CLI in subprocesses:

* ``documentation inspect --db <db> --backend dao --json`` (extractor correctness
  against the known-ground-truth fixtures);
* ``documentation render --evidence ... --kind database|er --json``;
* ``documentation save ...`` gating on rendered report copies.

It is deliberately separate from ``test_documentation_fixtures_e2e.py`` (T2), which
only proves construction readback through raw DAO. This suite never executes a saved
query, never uses ``Access.Application``, and never touches ``db/fixtures/documentation``
or ``db/northwind.accdb``.

``test_finding_*`` tests were the phase-1 reproduction of correctness gaps against
``docs/specs/documentation-workflows.md``. T3 phase 2 fixed the update-query category
and linked-table metadata, so those tests now pass; the former field-defaults finding
test is renamed ``test_inspect_core_field_defaults_are_read_from_dao`` because ACE
exposes ``Field.DefaultValue`` and the honest state is ``observed``, not ``unavailable``.

The suite also qualifies the ODBC inspection path (T2): ``--backend odbc`` reads the
same built fixture copy through the Access ODBC driver, and
``test_inspect_core_via_odbc_exposes_odbc_metadata_with_honest_unavailable_gaps``
asserts the ODBC-visible evidence plus every capability the probe proved invisible as
an explicit ``unavailable`` fact with a reason.

Finally it qualifies filtered inspection (T3): the repeatable ``--tables``/``--queries``
flags scope evidence and coverage to the requested subset, a filtered-out object is
absent rather than failed or unavailable, a table subset scopes the relationship
catalogue to relationships with both endpoints selected (recorded as ``excluded`` when
not), filtered evidence still renders a coherent database report, and an unknown
requested name is a hard ``Err`` with exit 1. The same selection contract is exercised
over the ODBC backend (table-only, query-only, combined, unknown-name, and a
filtered-out linked source). The explicit empty-list selection, which the repeatable
CLI flags cannot express, is exercised through the public application inspect interface
and the public MCP tool over a real ODBC port, with a transport-parity assertion against
the CLI ODBC run.

Run explicitly on Windows with ACE DAO and pywin32::

    $env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
    .venv\\Scripts\\python.exe -m pytest tests\\integration\\test_documentation_pipeline_e2e.py -q

Without ``ACCESS_DOCUMENTATION_FIXTURES_TEST=1`` the module skips cleanly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import _has_ace_dao

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts" / "generate_documentation_fixtures.py"
FIXTURE_ROOT = ROOT / "db" / "fixtures"
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "documentation_expected.json"

OPT_IN_ENV = "ACCESS_DOCUMENTATION_FIXTURES_TEST"
ALLOW_DOC_WRITE_ENV = "ACCESS_MCP_ALLOW_DOC_WRITE"
BUILD_TIMEOUT_SECONDS = 600
CLI_TIMEOUT_SECONDS = 300
DB_OPEN_SNAPSHOT = 4  # DAO dbOpenSnapshot
LEGAL_FACT_STATES = {"observed", "unavailable", "failed", "excluded"}

# A diagrammed Mermaid edge as emitted by the renderer: `tChild }o--|| tParent : "name"`.
_EDGE_RE = re.compile(r"^  (t\d+) (\}o|\|\|)--(\|\||o\||\}o) (t\d+) : \"(.*)\"$")

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.skipif(not _has_ace_dao(), reason="Requires ACE DAO (DAO.DBEngine.120)"),
    pytest.mark.skipif(
        os.environ.get(OPT_IN_ENV) != "1",
        reason=f"Set {OPT_IN_ENV}=1 to run the isolated ACE documentation-pipeline qualification suite",
    ),
    pytest.mark.com_integration,
]


def _venv_python() -> str:
    """Return the repository virtualenv interpreter, falling back to the current one."""
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def _cli_env(fixture_dir: Path, *, allow_doc_write: bool = False) -> dict[str, str]:
    """Build a deterministic CLI environment for the session fixture directory."""
    env = {key: value for key, value in os.environ.items() if key != ALLOW_DOC_WRITE_ENV}
    env["ACCESS_MCP_ALLOWED_DIRS"] = str(fixture_dir)
    if allow_doc_write:
        env[ALLOW_DOC_WRITE_ENV] = "1"
    return env


def _run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run one public-CLI invocation in a subprocess and capture its streams."""
    return subprocess.run(
        [_venv_python(), "-m", "ms_access_mcp.cli", *args],
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
        check=False,
        env=env,
        cwd=ROOT,
    )


def _clean(value: Any) -> Any:
    """Strip ACE's trailing NUL padding from a COM string read."""
    return value.rstrip("\x00") if isinstance(value, str) else value


def _iter_collection(collection: Any) -> Iterator[Any]:
    """Yield each item of a DAO COM collection in index order."""
    for index in range(collection.Count):
        yield collection(index)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _msaccess_pids() -> set[int]:
    """Return running MSACCESS.EXE process ids so the pipeline can be checked for leaks."""
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq MSACCESS.EXE", "/NH"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    pids: set[int] = set()
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].upper() == "MSACCESS.EXE" and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _marker_files(root: Path) -> list[str]:
    """Return every ``*marker*`` file under ``root`` as a path relative to it."""
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if "marker" in path.name)


def _load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@contextmanager
def _open_readonly(engine: Any, database: Path) -> Iterator[Any]:
    """Open a database read-only; this suite never mutates or executes a saved query."""
    database_handle = engine.OpenDatabase(str(database), False, True)
    try:
        yield database_handle
    finally:
        database_handle.Close()


def _snapshot_rows(db: Any, table_name: str) -> list[dict[str, Any]]:
    """Read every row of a table through a read-only snapshot recordset."""
    recordset = db.OpenRecordset(table_name, DB_OPEN_SNAPSHOT)
    try:
        field_names = [_clean(recordset.Fields(position).Name) for position in range(recordset.Fields.Count)]
        rows: list[dict[str, Any]] = []
        while not recordset.EOF:
            rows.append({name: recordset.Fields(position).Value for position, name in enumerate(field_names)})
            recordset.MoveNext()
        return rows
    finally:
        recordset.Close()


def _remove_self_created_tree(output_dir: Path, created_fixture_root: bool) -> None:
    """Delete exactly this fixture's ``e2e-<uuid>`` tree and nothing else."""
    assert output_dir.parent == FIXTURE_ROOT
    assert output_dir.name.startswith("e2e-")
    shutil.rmtree(output_dir, ignore_errors=True)
    if created_fixture_root:
        try:
            FIXTURE_ROOT.rmdir()
        except OSError:
            pass


def _payload(core_evidence: dict[str, Any]) -> dict[str, Any]:
    """Return the typed evidence payload nested inside the CLI envelope."""
    return core_evidence["envelope"]["data"]["data"]


def _tables_by_name(core_evidence: dict[str, Any]) -> dict[str, Any]:
    return {table["name"]: table for table in _payload(core_evidence)["tables"]}


def _queries_by_name(core_evidence: dict[str, Any]) -> dict[str, Any]:
    return {query["name"]: query for query in _payload(core_evidence)["queries"]}


def _iter_facts(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield every typed fact in the evidence payload, in document order."""
    for table in payload["tables"]:
        yield table["primary_key"]
        for field in table["fields"]:
            yield from (
                field["type"],
                field["size"],
                field["required"],
                field["allow_zero_length"],
                field["default_value"],
            )
    for query in payload["queries"]:
        yield from (query["sql"], query["type"], query["parameters"])
    for relationship in payload["relationships"]:
        yield relationship["attributes"]


def _mermaid_block(markdown: str) -> str:
    """Extract and structurally validate the single Mermaid fence in a report."""
    parts = markdown.split("```mermaid", 1)
    assert len(parts) == 2, "report must contain one Mermaid fence"
    block, remainder = parts[1].split("```", 1)
    lines = [line for line in block.splitlines() if line.strip()]
    assert lines[0].strip() == "erDiagram"
    open_nodes = [line for line in lines if line.rstrip().endswith("{")]
    close_nodes = [line for line in lines if line.strip() == "}"]
    assert len(open_nodes) == len(close_nodes), "unbalanced Mermaid node blocks"
    return block


@pytest.fixture(scope="session")
def e2e_build() -> Iterator[dict[str, Any]]:
    """Build the fixtures once through the public generator CLI into a fresh directory."""
    output_dir = FIXTURE_ROOT / f"e2e-{uuid.uuid4().hex}"
    created_fixture_root = not FIXTURE_ROOT.exists()
    if created_fixture_root:
        FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            [_venv_python(), str(GENERATOR), "--build", "--output-dir", str(output_dir)],
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_SECONDS,
            check=False,
            cwd=ROOT,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stderr == "", completed.stderr
        inventory = json.loads((output_dir / "inventory.json").read_text(encoding="utf-8"))
        yield {"directory": output_dir, "inventory": inventory, "expected": _load_manifest()}
    finally:
        _remove_self_created_tree(output_dir, created_fixture_root)


@pytest.fixture(scope="session")
def work_dir(e2e_build: dict[str, Any]) -> Path:
    """Scratch directory for evidence/report copies, removed with the e2e tree."""
    path = e2e_build["directory"] / "pipeline-artifacts"
    path.mkdir()
    return path


@pytest.fixture(scope="session")
def dao_engine() -> Iterator[Any]:
    """Dispatch the per-thread DAO engine used for read-only readback and locking."""
    pythoncom = pytest.importorskip("pythoncom")
    win32com_client = pytest.importorskip("win32com.client")
    pythoncom.CoInitialize()
    try:
        yield win32com_client.Dispatch("DAO.DBEngine.120")
    finally:
        pythoncom.CoUninitialize()


@pytest.fixture(scope="session")
def core_evidence(e2e_build: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """Inspect core.accdb once through the public CLI and persist its evidence JSON."""
    directory = e2e_build["directory"]
    completed = _run_cli(
        ["documentation", "inspect", "--db", str(directory / "core.accdb"), "--backend", "dao", "--json"],
        _cli_env(directory),
    )
    assert completed.returncode == 0, completed.stderr
    evidence_path = work_dir / "inspect-core.json"
    evidence_path.write_text(completed.stdout, encoding="utf-8")
    # The COM teardown banner on stderr ("Win32 exception occurred releasing IUnknown")
    # is a pywin32 cleanup artifact: exit status and JSON stdout stay valid.
    return {"envelope": json.loads(completed.stdout), "path": evidence_path, "stderr": completed.stderr}


@pytest.fixture(scope="session")
def core_odbc_evidence(e2e_build: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """Inspect core.accdb once through the public CLI with ``--backend odbc``.

    The ODBC path reads the same built fixture copy as the DAO path through the Access
    ODBC driver. The source is hashed before and after so the inspection's no-write
    behavior is observed, not assumed; no lock file may be left behind.
    """
    directory = e2e_build["directory"]
    core = directory / "core.accdb"
    before = _sha256(core)
    completed = _run_cli(
        ["documentation", "inspect", "--db", str(core), "--backend", "odbc", "--json"],
        _cli_env(directory),
    )
    after = _sha256(core)
    evidence_path = work_dir / "inspect-core-odbc.json"
    evidence_path.write_text(completed.stdout, encoding="utf-8")
    return {
        "completed": completed,
        "envelope": json.loads(completed.stdout) if completed.stdout.strip().startswith("{") else None,
        "path": evidence_path,
        "before": before,
        "after": after,
        "lock_files": sorted(path.name for path in directory.iterdir() if path.name.startswith("core.accdb.")),
    }


@pytest.fixture(scope="session")
def core_filtered_tables(e2e_build: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """Inspect core.accdb with an explicit table subset and render its database report.

    The subset is passed as three repeatable ``--tables`` occurrences, including the
    space-containing table ``Sales Notes``. The source is hashed before and after so the
    filtered open's no-write behavior is observed, and the resulting evidence is rendered
    offline to observe that a filtered document stays coherent.
    """
    directory = e2e_build["directory"]
    core = directory / "core.accdb"
    before = _sha256(core)
    completed = _run_cli(
        [
            "documentation",
            "inspect",
            "--db",
            str(core),
            "--backend",
            "dao",
            "--tables",
            "Customers",
            "--tables",
            "Orders",
            "--tables",
            "Sales Notes",
            "--json",
        ],
        _cli_env(directory),
    )
    after = _sha256(core)
    evidence_path = work_dir / "inspect-core-filtered-tables.json"
    evidence_path.write_text(completed.stdout, encoding="utf-8")
    rendered = _run_cli(
        ["documentation", "render", "--evidence", str(evidence_path), "--kind", "database", "--json"],
        _cli_env(directory),
    )
    (work_dir / "report-filtered-tables.json").write_text(rendered.stdout, encoding="utf-8")
    return {
        "completed": completed,
        "envelope": json.loads(completed.stdout) if completed.stdout.strip().startswith("{") else None,
        "rendered": rendered,
        "path": evidence_path,
        "before": before,
        "after": after,
        "lock_files": sorted(path.name for path in directory.iterdir() if path.name.startswith("core.accdb.")),
    }


@pytest.fixture(scope="session")
def core_filtered_queries(e2e_build: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """Inspect core.accdb with an explicit saved-query subset (repeatable ``--queries``)."""
    directory = e2e_build["directory"]
    completed = _run_cli(
        [
            "documentation",
            "inspect",
            "--db",
            str(directory / "core.accdb"),
            "--backend",
            "dao",
            "--queries",
            "qryOpenOrders",
            "--json",
        ],
        _cli_env(directory),
    )
    evidence_path = work_dir / "inspect-core-filtered-queries.json"
    evidence_path.write_text(completed.stdout, encoding="utf-8")
    return {
        "completed": completed,
        "envelope": json.loads(completed.stdout) if completed.stdout.strip().startswith("{") else None,
        "path": evidence_path,
    }


@pytest.fixture(scope="session")
def core_reports(core_evidence: dict[str, Any], e2e_build: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """Render database and ER reports from the persisted core evidence JSON."""
    env = _cli_env(e2e_build["directory"])
    reports: dict[str, Any] = {}
    for kind in ("database", "er"):
        completed = _run_cli(
            ["documentation", "render", "--evidence", str(core_evidence["path"]), "--kind", kind, "--json"],
            env,
        )
        assert completed.returncode == 0, completed.stderr
        report_path = work_dir / f"report-{kind}.json"
        report_path.write_text(completed.stdout, encoding="utf-8")
        reports[kind] = {"report": json.loads(completed.stdout)["data"], "path": report_path}
    return reports


@pytest.fixture(scope="session")
def linked_isolation(e2e_build: dict[str, Any], dao_engine: Any, work_dir: Path) -> dict[str, Any]:
    """Inspect linked.accdb while sentinel.accdb is held open exclusively.

    Holding the sentinel exclusively elsewhere makes any attempted contact fail loudly,
    so a successful inspection is positive evidence that the local catalogue was used
    alone. The lock is released in ``finally`` before the sentinel is hashed again.
    """
    directory = e2e_build["directory"]
    sentinel = directory / "sentinel.accdb"
    sentinel_before = _sha256(sentinel)
    handle = dao_engine.OpenDatabase(str(sentinel), True, False)
    try:
        completed = _run_cli(
            ["documentation", "inspect", "--db", str(directory / "linked.accdb"), "--backend", "dao", "--json"],
            _cli_env(directory),
        )
    finally:
        handle.Close()
    sentinel_after = _sha256(sentinel)
    evidence_path = work_dir / "inspect-linked.json"
    evidence_path.write_text(completed.stdout, encoding="utf-8")
    envelope = json.loads(completed.stdout) if completed.stdout.strip().startswith("{") else None
    return {
        "completed": completed,
        "envelope": envelope,
        "path": evidence_path,
        "sentinel_before": sentinel_before,
        "sentinel_after": sentinel_after,
    }


# ---------------------------------------------------------------------------
# 1. inspect core: extractor correctness against the synthetic fixtures
# ---------------------------------------------------------------------------


def test_inspect_core_envelope_and_source_identity(core_evidence: dict[str, Any]) -> None:
    """The inspect envelope is a success carrying versioned evidence."""
    envelope = core_evidence["envelope"]
    assert envelope["success"] is True
    assert set(envelope) == {"success", "data", "rowcount"}
    data = envelope["data"]
    assert data["schema"] == "access.documentation.evidence/1"
    assert data["status"] == "partial"
    assert data["run"]["extractor_version"] == "1.0"
    assert data["source"]["backend_identity"] == "DAO"
    # FINDING (cosmetic): the CLI knows the inspected path, yet the evidence records the
    # placeholders below instead of the available source identity/format.
    assert data["source"]["path"] == "unspecified"
    assert data["source"]["format"] == "unknown"
    assert "Traceback" not in core_evidence["stderr"]


def test_inspect_core_covers_exactly_manifest_tables_and_queries(
    core_evidence: dict[str, Any], e2e_build: dict[str, Any]
) -> None:
    """Evidence covers exactly the eight manifest tables and five saved queries."""
    expected = e2e_build["expected"]["databases"]["core.accdb"]
    payload = _payload(core_evidence)
    table_names = [table["name"] for table in payload["tables"]]
    query_names = [query["name"] for query in payload["queries"]]
    assert set(table_names) == set(expected["tables"])
    assert "Sales Notes" in table_names
    assert set(query_names) == set(expected["queries"])
    data = core_evidence["envelope"]["data"]
    assert set(data["coverage"]["tables"]["inspected"]) == set(expected["tables"])
    assert set(data["coverage"]["queries"]["inspected"]) == set(expected["queries"])
    assert data["coverage"]["tables"]["failed"] == {}
    assert data["run"]["scope"]["tables"] == table_names


def test_inspect_core_primary_keys_and_unique_index(core_evidence: dict[str, Any]) -> None:
    """Index-derived primary keys keep composite order; the real unique index survives."""
    tables = _tables_by_name(core_evidence)
    assert tables["OrderLines"]["primary_key"]["state"] == "observed"
    assert tables["OrderLines"]["primary_key"]["value"] == ["OrderID", "LineNo"]
    assert tables["Stock"]["primary_key"]["state"] == "observed"
    assert tables["Stock"]["primary_key"]["value"] == ["ProductID", "WarehouseID"]
    profiles_indexes = {index["name"]: index for index in tables["CustomerProfiles"]["indexes"]}
    assert profiles_indexes["CustomersProfiles"]["columns"] == ["CustomerID"]
    assert profiles_indexes["CustomersProfiles"]["is_unique"] is True
    assert profiles_indexes["CustomersProfiles"]["is_primary"] is False


def test_inspect_core_fact_states_legal_and_capability_gaps_unavailable(
    core_evidence: dict[str, Any],
) -> None:
    """Every fact uses a legal state; the remaining unsupported capability is unavailable.

    Query-parameter metadata is now a supported capability (proved end-to-end by
    ``test_inspect_core_query_parameters_are_read_from_dao``), so
    ``coverage.queries.unsupported`` is empty here. DAO relationship attributes remain
    genuinely unavailable, which is why the document still reports ``partial``.
    """
    payload = _payload(core_evidence)
    assert {fact["state"] for fact in _iter_facts(payload)} <= LEGAL_FACT_STATES
    assert all(relationship["attributes"]["state"] == "unavailable" for relationship in payload["relationships"])
    data = core_evidence["envelope"]["data"]
    assert data["status"] == "partial"
    assert data["coverage"]["queries"]["unsupported"] == {}
    assert data["coverage"]["relationships"]["unsupported"]
    assert data["coverage"]["tables"]["failed"] == {}
    # FINDING (cosmetic): validation rules are not modelled by the evidence schema at all,
    # so they are neither observed nor honestly marked unavailable. Assert none is fabricated.
    assert all("validation" not in field for table in payload["tables"] for field in table["fields"])


def test_inspect_core_query_parameters_are_read_from_dao(
    core_evidence: dict[str, Any], core_reports: dict[str, Any], e2e_build: dict[str, Any]
) -> None:
    """T1: DAO ``QueryDef.Parameters`` reach evidence honestly and render in the catalogue.

    The committed manifest declares the engine-reported bracketed name ``[pCustomerID]``
    with DAO type ``4`` (dbLong) for ``qryOrdersByCustomer`` and no parameters for the
    other four queries. A parameterless query is a known empty (``observed`` empty list),
    which is a different thing from a producer that cannot expose parameters
    (``unavailable``). DAO exposes the parameters collection, so every query here is a
    known empty or a known list, never ``unavailable`` and never a fabricated value.
    """
    expected_queries = e2e_build["expected"]["databases"]["core.accdb"]["queries"]
    queries = _queries_by_name(core_evidence)
    by_customer = queries["qryOrdersByCustomer"]["parameters"]
    assert by_customer["state"] == "observed"
    assert [(item["name"], item["type"]) for item in by_customer["value"]] == [("[pCustomerID]", 4)]
    assert by_customer["value"][0]["type_name"] == "Long Integer"
    assert by_customer["provenance"] == {
        "object_kind": "query",
        "object_name": "qryOrdersByCustomer",
        "property": "parameters",
    }
    # Every query agrees with the committed engine-readback manifest.
    assert expected_queries["qryOrdersByCustomer"]["parameters"] == {"[pCustomerID]": 4}
    assert expected_queries["qryOpenOrders"]["parameters"] == {}
    for name, spec in expected_queries.items():
        parameters = queries[name]["parameters"]
        assert parameters["state"] == "observed", name
        assert {item["name"]: item["type"] for item in parameters["value"]} == spec["parameters"], name
    assert queries["qryOpenOrders"]["parameters"]["value"] == []
    # The database query catalogue renders the declared parameter with its DAO type name.
    markdown = core_reports["database"]["report"]["markdown"]
    assert "- Parameters: [pCustomerID] (Long Integer)" in markdown


def test_inspect_core_field_defaults_are_read_from_dao(
    core_evidence: dict[str, Any],
) -> None:
    """Field defaults are read from DAO ``Field.DefaultValue`` and reported honestly.

    ACE exposes ``Field.DefaultValue`` (proved by raw read-only DAO readback), so a real
    default is an ``observed`` stored expression (``Customers.Status`` = ``"Active"``,
    ``Orders.Status`` = ``"Open"``) and a genuinely defaultless field is ``observed``
    empty (``''``) -- a known empty, never a fabricated ``null`` and never ``unavailable``.
    """
    tables = _tables_by_name(core_evidence)
    status_default = next(field for field in tables["Customers"]["fields"] if field["name"] == "Status")["default_value"]
    assert status_default["state"] == "observed" and status_default["value"] == '"Active"'
    orders_default = next(field for field in tables["Orders"]["fields"] if field["name"] == "Status")["default_value"]
    assert orders_default["state"] == "observed" and orders_default["value"] == '"Open"'
    name_default = next(field for field in tables["Customers"]["fields"] if field["name"] == "Name")["default_value"]
    assert name_default["state"] == "observed" and name_default["value"] == ""


def test_inspect_core_via_odbc_exposes_odbc_metadata_with_honest_unavailable_gaps(
    core_odbc_evidence: dict[str, Any], e2e_build: dict[str, Any]
) -> None:
    """T2: ``--backend odbc`` qualifies the pipeline with honest ODBC-only gaps.

    Probed against the Access ODBC driver on core.accdb, ODBC exposes exactly eight
    user tables (including ``Sales Notes``), SQLColumns type names/sizes (``INTEGER``,
    ``VARCHAR``, ``CURRENCY``), SQLStatistics index names/columns/uniqueness, and the
    three row-returning saved queries as ``VIEW`` rows. It cannot expose the DAO
    Required/AllowZeroLength properties, the primary-index flag (``SQLPrimaryKeys`` is
    unsupported IM001), relationships (``SQLForeignKeys`` is unsupported IM001;
    ``MSysRelationships`` is not readable), saved-query SQL/category, or the target of a
    linked table. The action queries ``qryUpdateOrderStatus``/``qryDeleteDraftOrders`` are
    not ``VIEW`` rows and are therefore invisible to ODBC. Every one of those gaps must
    be an explicit ``unavailable`` fact with a reason, while ODBC-visible facts keep the
    driver's own values (an honest observed value, never the DAO equivalent).
    """
    completed = core_odbc_evidence["completed"]
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    assert core_odbc_evidence["envelope"] is not None
    envelope = core_odbc_evidence["envelope"]
    assert envelope["success"] is True
    evidence = envelope["data"]
    payload = evidence["data"]
    assert evidence["source"]["backend_identity"] == "ODBC"
    assert evidence["status"] == "partial"

    expected_tables = set(e2e_build["expected"]["databases"]["core.accdb"]["tables"])
    assert {table["name"] for table in payload["tables"]} == expected_tables
    assert "Sales Notes" in {table["name"] for table in payload["tables"]}
    assert set(evidence["coverage"]["tables"]["inspected"]) == expected_tables
    assert evidence["coverage"]["tables"]["failed"] == {}
    assert {fact["state"] for fact in _iter_facts(payload)} <= LEGAL_FACT_STATES

    tables = _tables_by_name(core_odbc_evidence)
    customers = {field["name"]: field for field in tables["Customers"]["fields"]}
    assert customers["CustomerID"]["type"]["value"] == "INTEGER"
    assert customers["CustomerID"]["size"]["value"] == 10
    assert customers["Status"]["type"]["value"] == "VARCHAR"
    assert customers["Status"]["size"]["value"] == 20
    # Different fidelity from DAO ``Currency``: the honest ODBC type name is asserted.
    products = {field["name"]: field for field in tables["Products"]["fields"]}
    assert products["UnitPrice"]["type"]["value"] == "CURRENCY"
    notes = {field["name"]: field for field in tables["Sales Notes"]["fields"]}
    assert notes["Résumé"]["type"]["value"] == "VARCHAR"
    assert notes["Résumé"]["size"]["value"] == 50

    # ODBC-visible type/size stay observed; every ODBC-invisible field property is unavailable.
    for table in payload["tables"]:
        assert table["primary_key"]["state"] == "unavailable"
        assert table["primary_key"]["reason"].strip()
        assert evidence["coverage"]["tables"]["unsupported"][table["name"]].strip()
        for field in table["fields"]:
            assert field["type"]["state"] == "observed"
            assert field["size"]["state"] == "observed"
            for prop in ("required", "allow_zero_length", "default_value"):
                assert field[prop]["state"] == "unavailable", (table["name"], field["name"], prop)
                assert field[prop]["reason"].strip()

    # SQLStatistics index facts survive (names/columns/uniqueness, composite order);
    # the primary flag does not, so no index claims to be primary.
    customer_indexes = {index["name"]: index for index in tables["Customers"]["indexes"]}
    assert customer_indexes["PK_Customers"]["columns"] == ["CustomerID"]
    assert customer_indexes["PK_Customers"]["is_unique"] is True
    assert customer_indexes["PK_Customers"]["is_primary"] is False
    order_line_indexes = {index["name"]: index for index in tables["OrderLines"]["indexes"]}
    assert order_line_indexes["PK_OrderLines"]["columns"] == ["OrderID", "LineNo"]
    profile_indexes = {index["name"]: index for index in tables["CustomerProfiles"]["indexes"]}
    assert profile_indexes["CustomersProfiles"]["is_unique"] is True
    assert profile_indexes["CustomersProfiles"]["is_primary"] is False
    assert profile_indexes["relCustomersProfiles"]["is_unique"] is False
    assert all(index["is_primary"] is False for table in payload["tables"] for index in table["indexes"])

    assert payload["relationships"] == []
    assert "SQLForeignKeys" in evidence["coverage"]["relationships"]["unsupported"]["relationships"]
    assert evidence["coverage"]["relationships"]["failed"] == {}

    queries = _queries_by_name(core_odbc_evidence)
    assert set(queries) == {"qryOpenOrders", "qryOrdersByCustomer", "qryOrderSummary"}
    assert "qryUpdateOrderStatus" not in queries and "qryDeleteDraftOrders" not in queries
    for query in queries.values():
        assert query["sql"]["state"] == "unavailable"
        assert query["sql"]["reason"].strip()
        assert query["type"]["state"] == "unavailable"
        assert query["type"]["reason"].strip()
    parameterized = queries["qryOrdersByCustomer"]["parameters"]
    assert parameterized["state"] == "observed"
    assert [(item["name"], item["type"], item["type_name"]) for item in parameterized["value"]] == [
        ("[pCustomerID]", 4, "INTEGER")
    ]
    for name in ("qryOpenOrders", "qryOrderSummary"):
        assert queries[name]["parameters"]["state"] == "observed"
        assert queries[name]["parameters"]["value"] == []
    assert evidence["coverage"]["queries"]["failed"] == {}

    # No observed mutation and no lock residue from the ODBC open.
    assert core_odbc_evidence["after"] == core_odbc_evidence["before"]
    assert core_odbc_evidence["after"] == e2e_build["inventory"]["core.accdb"]["sha256"]
    assert core_odbc_evidence["lock_files"] == []

    # Rendering is offline and accepts ODBC-produced evidence unchanged.
    rendered = _run_cli(
        ["documentation", "render", "--evidence", str(core_odbc_evidence["path"]), "--kind", "database", "--json"],
        _cli_env(e2e_build["directory"]),
    )
    assert rendered.returncode == 0, rendered.stderr
    report = json.loads(rendered.stdout)["data"]
    assert report["schema"] == "access.documentation.report/1"
    assert report["sha256"] == hashlib.sha256(report["markdown"].encode("utf-8")).hexdigest()
    assert "## Data dictionary" in report["markdown"]
    assert "## Saved queries" in report["markdown"]
    assert "- Backend: ODBC" in report["markdown"]
    assert "- Parameters: [pCustomerID] (INTEGER)" in report["markdown"]
    assert _sha256(e2e_build["directory"] / "core.accdb") == core_odbc_evidence["before"]


def test_finding_inspect_core_update_query_is_miscategorised_as_select(
    core_evidence: dict[str, Any],
) -> None:
    """FINDING (blocking qualification): DAO type 48 (dbUpdate) is reported as ``select``.

    ``qryUpdateOrderStatus`` is an action query in the manifest (DAO type 48) and
    ``qryDeleteDraftOrders`` is DAO type 32. The catalogue distinguishes delete but maps
    update onto the default select category, so update and delete are not distinguished.
    """
    queries = _queries_by_name(core_evidence)
    delete_type = queries["qryDeleteDraftOrders"]["type"]
    assert delete_type["state"] == "observed" and delete_type["value"] == "action"
    update_type = queries["qryUpdateOrderStatus"]["type"]
    assert update_type["state"] == "observed" and update_type["value"] == "action", (
        "Fixture qryUpdateOrderStatus is a DAO type 48 (dbUpdate) action query, but the "
        f"extractor reported {update_type!r}; update and delete categories are not distinguished."
    )


# ---------------------------------------------------------------------------
# 2. render database + er from the inspect evidence
# ---------------------------------------------------------------------------


def test_render_database_and_er_reports_from_inspect_evidence(core_reports: dict[str, Any]) -> None:
    """Both report kinds are valid, versioned, and keep the documented section sets."""
    database = core_reports["database"]["report"]
    er = core_reports["er"]["report"]
    for report in (database, er):
        assert report["schema"] == "access.documentation.report/1"
        assert report["status"] == "partial"
        assert report["sha256"] == hashlib.sha256(report["markdown"].encode("utf-8")).hexdigest()
        assert "## Data dictionary" in report["markdown"]
        assert "## Relationship catalogue" in report["markdown"]
        assert "## Mermaid ER diagram" in report["markdown"]
    assert "## Saved queries" in database["markdown"]
    assert "qryUpdateOrderStatus" in database["markdown"]
    assert "## Saved queries" not in er["markdown"]
    assert "Saved queries:" not in er["markdown"]


def test_render_database_is_deterministic(
    core_reports: dict[str, Any], core_evidence: dict[str, Any], e2e_build: dict[str, Any]
) -> None:
    """Identical evidence and options produce identical Markdown and fingerprints."""
    completed = _run_cli(
        ["documentation", "render", "--evidence", str(core_evidence["path"]), "--kind", "database", "--json"],
        _cli_env(e2e_build["directory"]),
    )
    assert completed.returncode == 0, completed.stderr
    again = json.loads(completed.stdout)["data"]
    first = core_reports["database"]["report"]
    assert again["markdown"] == first["markdown"]
    assert again["sha256"] == first["sha256"]


def test_render_mermaid_aliases_preserve_original_names(
    core_reports: dict[str, Any], e2e_build: dict[str, Any]
) -> None:
    """Mermaid uses stable safe aliases while original names stay readable in the report."""
    markdown = core_reports["database"]["report"]["markdown"]
    block = _mermaid_block(markdown)
    expected_tables = sorted(e2e_build["expected"]["databases"]["core.accdb"]["tables"])
    for index, name in enumerate(expected_tables, 1):
        assert f"| t{index} | {name} |" in markdown
    assert sum(1 for line in block.splitlines() if _EDGE_RE.match(line)) == 7
    # Unsafe originals never leak into the diagram; the readable report keeps them.
    assert "Sales Notes" not in block
    assert "R_sum_" in block
    assert "Résumé" not in block
    assert "Résumé" in markdown
    assert "Sales Notes" in markdown


def test_render_mermaid_relationship_cardinality_matches_documented_rules(
    core_reports: dict[str, Any],
) -> None:
    """Every diagrammed edge matches the documented parent-uniqueness cardinality rules.

    Documented rules (``documentation_render``/71152e7): a relationship is diagrammed only
    when its parent columns match an observed primary/unique index; the parent end is
    ``||`` and the child end is ``||`` only for an independent unique/primary child key,
    otherwise ``}o``. Relationship auto-indexes cannot justify child uniqueness.

    FINDING (cosmetic): the nullable ``Orders.BillingCustomerID`` FK renders the same as a
    required FK (``}o--||``). The renderer has no way to express an optional parent because
    relationship attributes are unavailable and field nullability is not consulted; the
    documented rules do not cover nullable FKs.
    """
    markdown = core_reports["database"]["report"]["markdown"]
    block = _mermaid_block(markdown)
    aliases = dict(re.findall(r"^\| (t\d+) \| (.+?) \|$", markdown, re.MULTILINE))
    assert aliases  # the alias table is present after the diagram
    by_name = {name: alias for alias, name in aliases.items()}
    expected_edges = {
        (by_name["CustomerProfiles"], by_name["Customers"], "||", "relCustomersProfiles"),
        (by_name["Orders"], by_name["Customers"], "}o", "CustomersOrders"),
        (by_name["Orders"], by_name["Customers"], "}o", "BillingCustomersOrders"),
        (by_name["OrderLines"], by_name["Orders"], "}o", "OrdersOrderLines"),
        (by_name["Stock"], by_name["Products"], "}o", "ProductsStock"),
        (by_name["Stock"], by_name["Warehouses"], "}o", "WarehousesStock"),
        (by_name["OrderLines"], by_name["Stock"], "}o", "StockOrderLines"),
    }
    actual_edges: set[tuple[str, str, str, str]] = set()
    for line in block.splitlines():
        match = _EDGE_RE.match(line)
        if match:
            child, child_end, parent_end, parent, label = match.groups()
            assert parent_end == "||", line
            actual_edges.add((child, parent, child_end, label))
    assert actual_edges == expected_edges
    # The composite FK keeps its aligned, ordered column pairs in the catalogue.
    assert "ProductID → ProductID; WarehouseID → WarehouseID" in markdown


# ---------------------------------------------------------------------------
# 3. save gating on rendered report copies
# ---------------------------------------------------------------------------


def test_save_gating_contract_on_report_copies(
    e2e_build: dict[str, Any], work_dir: Path, core_reports: dict[str, Any]
) -> None:
    """Permission, confirm, overwrite, fingerprint, and byte-stability of rejections."""
    directory = e2e_build["directory"]
    denied_env = _cli_env(directory)
    granted_env = _cli_env(directory, allow_doc_write=True)
    database_path = core_reports["database"]["path"]
    database_markdown = core_reports["database"]["report"]["markdown"]
    er_path = core_reports["er"]["path"]
    er_markdown = core_reports["er"]["report"]["markdown"]
    destination = work_dir / "saved-report.md"

    def save(report_path: Path, *extra: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return _run_cli(
            [
                "documentation",
                "save",
                "--report",
                str(report_path),
                "--output-path",
                str(destination),
                *extra,
                "--json",
            ],
            env,
        )

    # (a) operator permission is required even with confirmation.
    completed = save(database_path, "--yes", env=denied_env)
    assert completed.returncode == 1
    denied = json.loads(completed.stdout)
    assert denied["success"] is False
    assert "permission" in denied["error"]
    assert not destination.exists()

    # (b) the per-call --yes confirmation is required before any save.
    completed = save(database_path, "--allow-doc-write", env=granted_env)
    assert completed.returncode == 2
    assert completed.stderr == "Error: documentation save requires --yes\n"
    assert not destination.exists()

    # (c) an env grant plus --yes writes exactly the report bytes and reports the fingerprint.
    completed = save(database_path, "--yes", env=granted_env)
    assert completed.returncode == 0, completed.stderr
    saved = json.loads(completed.stdout)["data"]
    written = destination.read_bytes()
    assert written == database_markdown.encode("utf-8")
    assert saved["bytes_written"] == len(database_markdown.encode("utf-8"))
    assert saved["sha256"] == hashlib.sha256(written).hexdigest()
    first_sha = saved["sha256"]

    # (d) an existing destination needs explicit overwrite approval.
    completed = save(database_path, "--allow-doc-write", "--yes", env=denied_env)
    assert completed.returncode == 1
    assert "overwrite" in json.loads(completed.stdout)["error"]
    assert destination.read_bytes() == written

    # (e) overwrite needs the current destination fingerprint.
    completed = save(database_path, "--allow-doc-write", "--yes", "--overwrite", env=denied_env)
    assert completed.returncode == 1
    assert "fingerprint" in json.loads(completed.stdout)["error"]
    assert destination.read_bytes() == written

    # (f) a stale fingerprint is rejected and leaves the destination byte-identical.
    completed = save(
        database_path,
        "--allow-doc-write",
        "--yes",
        "--overwrite",
        "--expected-fingerprint",
        hashlib.sha256(b"stale").hexdigest(),
        env=denied_env,
    )
    assert completed.returncode == 1
    assert "fingerprint" in json.loads(completed.stdout)["error"]
    assert destination.read_bytes() == written

    # (g) a matching fingerprint overwrites with the new report and reports its fingerprint.
    assert er_markdown != database_markdown
    completed = save(
        er_path,
        "--allow-doc-write",
        "--yes",
        "--overwrite",
        "--expected-fingerprint",
        first_sha,
        env=denied_env,
    )
    assert completed.returncode == 0, completed.stderr
    overwritten = json.loads(completed.stdout)["data"]
    assert destination.read_bytes() == er_markdown.encode("utf-8")
    assert overwritten["sha256"] == hashlib.sha256(destination.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 4b. bounded resource ingress at the real default caps (T4a)
# ---------------------------------------------------------------------------


def test_cli_rejects_oversized_evidence_and_report_files_at_the_default_caps(
    e2e_build: dict[str, Any], work_dir: Path, core_reports: dict[str, Any]
) -> None:
    """Default 32 MiB/16 MiB caps reject an oversized raw file before parse, with no output.

    The oversized files start with non-JSON bytes, so observing the raw-ingress size
    error (not a JSON error) proves the bounded read rejected the file before any parse.
    The same session then saves a normal report (PRESERVE) with exact bytes.
    """
    from ms_access_mcp.application.documentation_limits import (
        raw_evidence_file_limit,
        raw_report_file_limit,
    )

    directory = e2e_build["directory"]
    granted_env = _cli_env(directory, allow_doc_write=True)

    oversized_evidence = work_dir / "oversized-evidence.json"
    with oversized_evidence.open("wb") as handle:
        handle.write(b"not-json")
        handle.truncate(raw_evidence_file_limit() + 1)
    rendered = _run_cli(
        ["documentation", "render", "--evidence", str(oversized_evidence), "--json"],
        granted_env,
    )
    assert rendered.returncode == 1
    payload = json.loads(rendered.stdout)
    assert payload["success"] is False
    assert str(raw_evidence_file_limit()) in payload["error"]
    assert "not-json" not in payload["error"]

    oversized_report = work_dir / "oversized-report.json"
    with oversized_report.open("wb") as handle:
        handle.write(b"not-json")
        handle.truncate(raw_report_file_limit() + 1)
    destination = work_dir / "never-written.md"
    saved = _run_cli(
        [
            "documentation",
            "save",
            "--report",
            str(oversized_report),
            "--output-path",
            str(destination),
            "--allow-doc-write",
            "--yes",
            "--json",
        ],
        granted_env,
    )
    assert saved.returncode == 1
    assert json.loads(saved.stdout)["success"] is False
    assert not destination.exists()
    assert list(work_dir.glob("*.tmp")) == []

    # PRESERVE: a normal-size report still saves exactly its Markdown bytes.
    normal = core_reports["database"]
    saved_ok = _run_cli(
        [
            "documentation",
            "save",
            "--report",
            str(normal["path"]),
            "--output-path",
            str(destination),
            "--allow-doc-write",
            "--yes",
            "--json",
        ],
        granted_env,
    )
    assert saved_ok.returncode == 0, saved_ok.stderr
    assert destination.read_bytes() == normal["report"]["markdown"].encode("utf-8")


# ---------------------------------------------------------------------------
# 5. linked isolation via access-observation
# ---------------------------------------------------------------------------


def test_inspect_linked_succeeds_while_sentinel_is_exclusively_locked(
    linked_isolation: dict[str, Any], e2e_build: dict[str, Any]
) -> None:
    """A successful linked inspection under an exclusive sentinel lock proves no contact."""
    completed = linked_isolation["completed"]
    assert completed.returncode == 0, completed.stderr
    assert linked_isolation["envelope"] is not None
    assert linked_isolation["envelope"]["success"] is True
    # Positive evidence of no contact: the sentinel was locked elsewhere for the whole
    # inspection, so any attempted resolution/refresh would have failed; the file bytes
    # are unchanged (hashes prove no observed mutation, not engine-level read-only).
    assert linked_isolation["sentinel_after"] == linked_isolation["sentinel_before"]
    assert linked_isolation["sentinel_after"] == e2e_build["inventory"]["sentinel.accdb"]["sha256"]
    payload = linked_isolation["envelope"]["data"]["data"]
    names = {table["name"] for table in payload["tables"]}
    assert names == {"LinkedCustomers", "LocalNotes"}
    assert {fact["state"] for fact in _iter_facts(payload)} <= LEGAL_FACT_STATES


def test_inspect_linked_via_odbc_does_not_contact_the_linked_source(
    e2e_build: dict[str, Any], dao_engine: Any
) -> None:
    """T1: ODBC classifies the SYNONYM first and never reads its columns/indexes.

    Contract change (observed rationale): the previous port read SQLColumns/SQLStatistics
    for every listed table, so a linked table contacted its source and, when the source
    was unreachable (sentinel held exclusively), the driver returned zero columns and
    the table was recorded as ``failed``. The port now classifies by SQLTables type
    before any column/index call: ``LinkedCustomers`` is a SYNONYM and is never passed to
    SQLColumns or SQLStatistics, so no contact happens and the table is recorded as an
    explicit coverage ``unsupported`` gap with empty fields/indexes and an ``unavailable``
    primary key -- never as a failed read and never as an observed-empty schema. Holding
    the sentinel exclusively makes any attempted contact fail loudly, so a successful
    run is positive non-contact evidence. The sentinel sha256 is compared before and
    after, and the lock is released in a ``finally``. Only the session's self-built
    fixture tree is used.
    """
    directory = e2e_build["directory"]
    sentinel = directory / "sentinel.accdb"
    sentinel_before = _sha256(sentinel)
    handle = dao_engine.OpenDatabase(str(sentinel), True, False)
    try:
        completed = _run_cli(
            [
                "documentation",
                "inspect",
                "--db",
                str(directory / "linked.accdb"),
                "--backend",
                "odbc",
                "--json",
            ],
            _cli_env(directory),
        )
    finally:
        handle.Close()
    sentinel_after = _sha256(sentinel)

    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True
    evidence = envelope["data"]
    payload = evidence["data"]
    assert evidence["source"]["backend_identity"] == "ODBC"

    # The linked SYNONYM is an explicit support gap, not a failure or an observed empty.
    assert "LinkedCustomers" not in evidence["coverage"]["tables"]["failed"]
    assert "LinkedCustomers" in evidence["coverage"]["tables"]["unsupported"]
    linked = next(table for table in payload["tables"] if table["name"] == "LinkedCustomers")
    assert linked["kind"] == "linked"
    assert linked["fields"] == []
    assert linked["indexes"] == []
    assert linked["primary_key"]["state"] == "unavailable"

    # A local table in the same database still reads its real columns normally (PRESERVE).
    local = next(table for table in payload["tables"] if table["name"] == "LocalNotes")
    assert local["fields"], "LocalNotes must expose its real columns"
    assert "LocalNotes" not in evidence["coverage"]["tables"]["failed"]
    assert {fact["state"] for fact in _iter_facts(payload)} <= LEGAL_FACT_STATES

    # No contact: the locked sentinel is untouched and no bytes changed.
    assert sentinel_after == sentinel_before
    assert sentinel_after == e2e_build["inventory"]["sentinel.accdb"]["sha256"]


def test_finding_inspect_linked_local_link_metadata_absent_from_evidence(
    linked_isolation: dict[str, Any],
) -> None:
    """FINDING (blocking qualification): local linked-table metadata never reaches evidence.

    Spec section 3.2 requires a Table subject to record its kind where available, and the
    fixture's ``LinkedCustomers`` is an attached table whose local catalogue exposes
    ``Connect`` (containing ``sentinel.accdb``) and ``SourceTableName``
    (``SentinelCustomers``). The evidence model has no field for any of these, so the report
    cannot show that ``LinkedCustomers`` is a link or where it points.
    """
    payload = linked_isolation["envelope"]["data"]["data"]
    linked = next(table for table in payload["tables"] if table["name"] == "LinkedCustomers")
    assert any(key in linked for key in ("kind", "connect", "source_table", "attributes")), (
        "Linked-table local catalogue metadata (kind/Connect/SourceTableName) must be "
        f"represented in evidence; LinkedCustomers carried only {sorted(linked)}."
    )


# ---------------------------------------------------------------------------
# 6. engine-level read-only enforcement of the inspect open (T12)
# ---------------------------------------------------------------------------


def test_inspect_open_is_engine_read_only_and_refuses_write(
    e2e_build: dict[str, Any], core_evidence: dict[str, Any]
) -> None:
    """T12: the inspect connection is engine-level read-only, not merely behavioral.

    (i) the public ``documentation inspect`` CLI still exits 0 with complete metadata
    readback; (ii) a handle opened through the exact production inspection open seam
    (``documentation_inspect.open_inspection_port`` over ``ComDispatcher``) refuses an
    UPDATE at the engine level; (iii) core.accdb is byte-identical afterwards.
    """
    del core_evidence  # the public inspect fixture must run first
    directory = e2e_build["directory"]
    core = directory / "core.accdb"
    before = _sha256(core)

    # (i) public inspect still reads complete metadata under the read-only open.
    completed = _run_cli(
        ["documentation", "inspect", "--db", str(core), "--backend", "dao", "--json"],
        _cli_env(directory),
    )
    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True
    evidence = envelope["data"]
    payload = evidence["data"]
    assert evidence["coverage"]["tables"]["failed"] == {}
    assert {table["name"] for table in payload["tables"]} == set(
        e2e_build["expected"]["databases"]["core.accdb"]["tables"]
    )

    # (ii) open the same way the service does and prove the engine refuses a write.
    from ms_access_mcp.application.documentation_inspect import open_inspection_port
    from ms_access_mcp.backends.dispatcher import ComDispatcher

    dispatcher = ComDispatcher()
    dispatcher.start()
    recorded: list[tuple[str, str, bool]] = []
    real_open = dispatcher.open_dao_database

    def _recording_open(db_path: str, password: str = "", read_only: bool = False) -> Any:
        recorded.append((db_path, password, read_only))
        return real_open(db_path, password=password, read_only=read_only)

    dispatcher.open_dao_database = _recording_open  # type: ignore[method-assign]
    try:
        dispatcher.open_dao_database(str(core))  # DatabaseUseCases-equivalent first open
        open_inspection_port(dispatcher)
        # Belt: the service's own reopen carried read_only=True.
        assert recorded[-1] == (str(core), "", True)
        handle = dispatcher.current_db
        with pytest.raises(Exception) as excinfo:  # noqa: B017 — DAO raises pywintypes.com_error
            dispatcher.call(lambda: handle.Execute("UPDATE Orders SET Status = 'Tampered'"))
        message = str(excinfo.value)
        assert re.search(r"updateable query", message, re.IGNORECASE), message
        # Pin the DAO engine error code (5003073 = "Operation must use an updateable
        # query."), not just the textual pattern: the excepinfo tuple is
        # (wCode, source, description, helpFile, scode, helpContext).
        excepinfo = excinfo.value.args[2]
        assert isinstance(excepinfo, tuple), excinfo.value
        assert excepinfo[4] == 5003073, excinfo.value
        assert excepinfo[2] == "Operation must use an updateable query.", excinfo.value
    finally:
        dispatcher.close_dao_database()
        dispatcher.shutdown()

    # (iii) the source file is byte-identical.
    assert _sha256(core) == before


def test_odbc_inspection_open_is_engine_read_only_and_refuses_write(
    e2e_build: dict[str, Any], work_dir: Path
) -> None:
    """T2: the ODBC inspection open is engine-level read-only on the installed driver.

    Qualified driver scope: Microsoft Access Driver (*.mdb, *.accdb) (ACEODBC
    ``16.0.20326.20022``, x64). Opened through the production seam
    (``documentation_inspect.open_odbc_inspection_port`` / ``OdbcAdapter``), the port
    reads complete ODBC metadata from a disposable copy, and the production read-only
    opening method refuses one narrow ``UPDATE`` at the engine level (driver error
    ``-3035`` "Operation must use an updateable query."), leaving the copy
    byte-identical with no lock residue. The adapter's own CRUD connection is untouched
    and still usable afterwards, and a separate writable control copy proves the
    ordinary adapter CRUD path still writes. The probe UPDATE is a test control only:
    production never issues an UPDATE as a capability check.
    """
    import pyodbc

    from ms_access_mcp.application.documentation_inspect import (
        DocumentationInspectService,
        open_odbc_inspection_port,
    )
    from ms_access_mcp.backends.odbc import OdbcAdapter

    directory = e2e_build["directory"]
    source = directory / "core.accdb"
    readonly_copy = work_dir / "odbc-readonly.accdb"
    control_copy = work_dir / "odbc-writable-control.accdb"
    shutil.copy2(source, readonly_copy)
    shutil.copy2(source, control_copy)
    readonly_before = _sha256(readonly_copy)

    adapter = OdbcAdapter(str(readonly_copy))
    adapter.connect(str(readonly_copy))
    crud_connection = adapter._conn
    try:
        # (i) the production inspection open reads complete ODBC metadata.
        port = open_odbc_inspection_port(adapter)
        document = DocumentationInspectService().inspect(port, tables=None, queries=None)
        assert document.source.backend_identity == "ODBC"
        assert {table.name for table in document.tables} == set(
            e2e_build["expected"]["databases"]["core.accdb"]["tables"]
        )
        assert document.coverage.tables.failed == {}

        # (ii) the production read-only opening method refuses a narrow UPDATE at the
        # engine level, not merely by policy.
        readonly = adapter.open_readonly_inspection_connection()
        try:
            with pytest.raises(pyodbc.Error) as excinfo:
                readonly.cursor().execute("UPDATE Orders SET Status = 'Tampered'")
            refusal = str(excinfo.value)
            assert "-3035" in refusal, refusal
            assert "updateable query" in refusal.casefold(), refusal
        finally:
            readonly.close()

        # (iii) the adapter's own CRUD connection is untouched and still functional.
        assert adapter._conn is crud_connection
        assert adapter.connected is True
        assert {table.name for table in adapter.get_tables()} == set(
            e2e_build["expected"]["databases"]["core.accdb"]["tables"]
        )
    finally:
        adapter.disconnect()

    # The refused write changed nothing and left no Access lock file behind.
    assert _sha256(readonly_copy) == readonly_before
    assert sorted(path.name for path in work_dir.glob("odbc-readonly*.laccdb")) == []

    # (iv) writable control on a separate copy through the ordinary adapter CRUD path.
    control = OdbcAdapter(str(control_copy))
    control.connect(str(control_copy))
    try:
        written = control.update_data("Orders", {"Status": "Tampered"}, {"OrderID": 1})
    finally:
        control.disconnect()
    assert written.success is True and written.affected == 1
    assert _sha256(control_copy) != readonly_before
    assert sorted(path.name for path in work_dir.glob("odbc-writable-control*.laccdb")) == []


# ---------------------------------------------------------------------------
# 7. filtered inspection (T3): --tables / --queries scoping
# ---------------------------------------------------------------------------


def test_inspect_filtered_tables_returns_exactly_the_requested_subset(
    core_filtered_tables: dict[str, Any], e2e_build: dict[str, Any]
) -> None:
    """T3: ``--tables`` scopes evidence, coverage, relationships, and rendering to a subset.

    The CLI flag is repeatable (``--tables <name>``), so the space-containing
    ``Sales Notes`` is carried as one whole name by one occurrence. An explicit subset
    selects exactly the requested tables: filtered-out tables are absent from the
    document and are neither failed, unsupported, nor fabricated as unavailable (their
    names do not appear in any ``coverage.tables`` bucket). The query scope is unaffected
    by a table filter, and the relationship catalogue is scoped to relationships whose
    parent and child are both selected -- a relationship referencing an excluded table is
    recorded as ``excluded`` with a scope reason instead of being retained, because the
    deterministic renderer resolves both endpoints to diagram nodes and retaining it would
    make the filtered document unrenderable.
    """
    completed = core_filtered_tables["completed"]
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    envelope = core_filtered_tables["envelope"]
    assert envelope is not None and envelope["success"] is True
    evidence = envelope["data"]
    payload = evidence["data"]
    expected = e2e_build["expected"]["databases"]["core.accdb"]
    requested = ["Customers", "Orders", "Sales Notes"]

    assert [table["name"] for table in payload["tables"]] == requested
    assert evidence["run"]["scope"]["tables"] == requested
    assert set(evidence["run"]["scope"]["queries"]) == set(expected["queries"])
    assert evidence["coverage"]["tables"]["inspected"] == requested
    assert evidence["coverage"]["tables"]["failed"] == {}
    assert evidence["coverage"]["tables"]["unsupported"] == {}
    assert evidence["coverage"]["tables"]["excluded"] == {}
    omitted = sorted(set(expected["tables"]) - set(requested))
    assert omitted == ["CustomerProfiles", "OrderLines", "Products", "Stock", "Warehouses"]
    present = {table["name"] for table in payload["tables"]}
    for name in omitted:
        assert name not in present, name
        for bucket in ("inspected", "excluded", "unsupported", "failed"):
            assert name not in evidence["coverage"]["tables"][bucket], (name, bucket)
    assert {fact["state"] for fact in _iter_facts(payload)} <= LEGAL_FACT_STATES

    # A table filter does not narrow the query scope.
    assert {query["name"] for query in payload["queries"]} == set(expected["queries"])
    # The relationship catalogue covers only relationships with both endpoints selected.
    assert sorted(item["name"] for item in payload["relationships"]) == [
        "BillingCustomersOrders",
        "CustomersOrders",
    ]
    excluded_relationships = evidence["coverage"]["relationships"]["excluded"]
    assert sorted(excluded_relationships) == [
        "OrdersOrderLines",
        "ProductsStock",
        "StockOrderLines",
        "WarehousesStock",
        "relCustomersProfiles",
    ]
    assert all("subset" in reason for reason in excluded_relationships.values())
    for relationship in payload["relationships"]:
        assert relationship["parent_table"] in requested and relationship["child_table"] in requested

    # No observed mutation and no lock residue from the filtered open.
    assert core_filtered_tables["after"] == core_filtered_tables["before"]
    assert core_filtered_tables["after"] == e2e_build["inventory"]["core.accdb"]["sha256"]
    assert core_filtered_tables["lock_files"] == []

    # (e) The filtered evidence renders one coherent database report for the subset.
    rendered = core_filtered_tables["rendered"]
    assert rendered.returncode == 0, rendered.stdout
    assert "Traceback" not in rendered.stderr
    report = json.loads(rendered.stdout)["data"]
    markdown = report["markdown"]
    assert report["schema"] == "access.documentation.report/1"
    assert report["status"] == "partial"
    assert report["sha256"] == hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    assert "- Tables: Customers, Orders, Sales Notes" in markdown
    for name in requested:
        assert f"### {name}" in markdown, name
    for name in omitted:
        assert f"### {name}" not in markdown, name
    assert "| CustomersOrders |" in markdown
    assert "| ProductsStock |" not in markdown
    assert any(line.startswith("- Excluded (relationships): relCustomersProfiles") for line in markdown.splitlines())
    block = _mermaid_block(markdown)
    assert "| t1 | Customers |" in markdown
    assert "| t2 | Orders |" in markdown
    assert "| t3 | Sales Notes |" in markdown
    edges = [line for line in block.splitlines() if _EDGE_RE.match(line)]
    assert edges == ['  t2 }o--|| t1 : "BillingCustomersOrders"', '  t2 }o--|| t1 : "CustomersOrders"']


def test_inspect_filtered_queries_returns_exactly_the_requested_query(
    core_filtered_queries: dict[str, Any], e2e_build: dict[str, Any]
) -> None:
    """T3: ``--queries`` selects exactly the requested saved query and nothing else.

    The other four queries are absent from both the evidence and every
    ``coverage.queries`` bucket (never failed or unavailable). A query filter leaves the
    table scope and the relationship catalogue untouched, which is the counterpart of the
    table-filter scoping asserted above.
    """
    completed = core_filtered_queries["completed"]
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    envelope = core_filtered_queries["envelope"]
    assert envelope is not None and envelope["success"] is True
    evidence = envelope["data"]
    payload = evidence["data"]
    expected = e2e_build["expected"]["databases"]["core.accdb"]

    assert [query["name"] for query in payload["queries"]] == ["qryOpenOrders"]
    assert evidence["coverage"]["queries"]["inspected"] == ["qryOpenOrders"]
    assert evidence["coverage"]["queries"]["failed"] == {}
    assert evidence["coverage"]["queries"]["unsupported"] == {}
    assert evidence["coverage"]["queries"]["excluded"] == {}
    omitted = sorted(set(expected["queries"]) - {"qryOpenOrders"})
    assert omitted == [
        "qryDeleteDraftOrders",
        "qryOrderSummary",
        "qryOrdersByCustomer",
        "qryUpdateOrderStatus",
    ]
    for name in omitted:
        assert name not in {query["name"] for query in payload["queries"]}, name
        for bucket in ("inspected", "excluded", "unsupported", "failed"):
            assert name not in evidence["coverage"]["queries"][bucket], (name, bucket)
    assert {fact["state"] for fact in _iter_facts(payload)} <= LEGAL_FACT_STATES

    # A query filter does not narrow the table scope or the relationship catalogue.
    assert {table["name"] for table in payload["tables"]} == set(expected["tables"])
    assert set(evidence["run"]["scope"]["tables"]) == set(expected["tables"])
    assert evidence["run"]["scope"]["queries"] == ["qryOpenOrders"]
    assert set(evidence["coverage"]["tables"]["inspected"]) == set(expected["tables"])
    assert len(payload["relationships"]) == len(expected["relationships"])
    assert evidence["coverage"]["relationships"]["excluded"] == {}


def test_inspect_combined_table_and_query_filters_scope_both_axes(e2e_build: dict[str, Any]) -> None:
    """T3: one invocation can filter tables and queries at the same time.

    ``--tables Orders --tables OrderLines --queries qryOrdersByCustomer`` scopes both
    axes independently: the two requested tables, the single requested query, their own
    coverage ``inspected`` lists, and the relationship catalogue reduced to
    ``OrdersOrderLines`` (the only relationship whose parent and child are both selected).
    """
    completed = _run_cli(
        [
            "documentation",
            "inspect",
            "--db",
            str(e2e_build["directory"] / "core.accdb"),
            "--backend",
            "dao",
            "--tables",
            "Orders",
            "--tables",
            "OrderLines",
            "--queries",
            "qryOrdersByCustomer",
            "--json",
        ],
        _cli_env(e2e_build["directory"]),
    )
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    evidence = json.loads(completed.stdout)["data"]
    payload = evidence["data"]
    assert [table["name"] for table in payload["tables"]] == ["Orders", "OrderLines"]
    assert [query["name"] for query in payload["queries"]] == ["qryOrdersByCustomer"]
    assert evidence["run"]["scope"] == {"tables": ["Orders", "OrderLines"], "queries": ["qryOrdersByCustomer"]}
    assert evidence["coverage"]["tables"]["inspected"] == ["Orders", "OrderLines"]
    assert evidence["coverage"]["queries"]["inspected"] == ["qryOrdersByCustomer"]
    assert [item["name"] for item in payload["relationships"]] == ["OrdersOrderLines"]
    assert {fact["state"] for fact in _iter_facts(payload)} <= LEGAL_FACT_STATES


def test_inspect_unknown_requested_name_is_a_hard_error(e2e_build: dict[str, Any]) -> None:
    """T3: an unknown requested name is a hard ``Err`` (exit 1), not an honest-empty document.

    Spec section 3.4 maps invalid input to ``Err(message)`` with CLI exit 1 (2 is reserved
    for usage/invocation errors), so both an unknown table and an unknown query must fail
    with a named error envelope and no evidence payload, while leaving the source bytes
    untouched and no lock residue behind.
    """
    directory = e2e_build["directory"]
    core = directory / "core.accdb"
    before = _sha256(core)
    table_run = _run_cli(
        ["documentation", "inspect", "--db", str(core), "--backend", "dao", "--tables", "NoSuchTable", "--json"],
        _cli_env(directory),
    )
    query_run = _run_cli(
        ["documentation", "inspect", "--db", str(core), "--backend", "dao", "--queries", "NoSuchQuery", "--json"],
        _cli_env(directory),
    )
    after = _sha256(core)
    for completed, expected_error in (
        (table_run, "Unknown requested table name(s): NoSuchTable"),
        (query_run, "Unknown requested query name(s): NoSuchQuery"),
    ):
        assert completed.returncode == 1
        assert "Traceback" not in completed.stderr
        envelope = json.loads(completed.stdout)
        assert envelope["success"] is False
        assert envelope["error"] == expected_error
        assert "data" not in envelope
    assert after == before == e2e_build["inventory"]["core.accdb"]["sha256"]
    assert sorted(path.name for path in directory.iterdir() if path.name.startswith("core.accdb.")) == []


# ---------------------------------------------------------------------------
# 7b. filtered inspection over ODBC (T3): same selection contract, ODBC gaps
# ---------------------------------------------------------------------------

# ODBC exposes only row-returning saved queries as SQLTables ``VIEW`` rows. These are the
# fixture's complete ODBC-visible query catalogue; the action queries are not VIEW rows
# and are never visible to ODBC, so no test may claim their names are query evidence.
_ODBC_VISIBLE_QUERIES = ["qryOpenOrders", "qryOrdersByCustomer", "qryOrderSummary"]
_ODBC_ACTION_QUERIES = ["qryUpdateOrderStatus", "qryDeleteDraftOrders"]
_ODBC_PK_GAP = "SQLPrimaryKeys"
_ODBC_RELATIONSHIP_GAP = "SQLForeignKeys"


def _lock_files(directory: Path, stem: str) -> list[str]:
    """Return the names of any Access lock files left beside a fixture database."""
    return sorted(path.name for path in directory.iterdir() if path.name.startswith(f"{stem}."))


def _render_database_report(completed_stdout: str, evidence_path: Path, directory: Path) -> dict[str, Any]:
    """Persist an evidence envelope and render it offline to a database report."""
    evidence_path.write_text(completed_stdout, encoding="utf-8")
    rendered = _run_cli(
        ["documentation", "render", "--evidence", str(evidence_path), "--kind", "database", "--json"],
        _cli_env(directory),
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "Traceback" not in rendered.stderr
    return json.loads(rendered.stdout)["data"]


def test_inspect_filtered_tables_via_odbc_returns_exactly_the_requested_subset(
    e2e_build: dict[str, Any], work_dir: Path
) -> None:
    """T3: ``--tables`` scopes ODBC evidence, coverage, and rendering in request order.

    ODBC exposes the same eight user tables as DAO, so an explicit subset selects exactly
    the requested tables in the order given (a deliberately non-alphabetical order
    including the space-containing ``Sales Notes``). The query axis is untouched: every
    ODBC-visible row-returning query (a ``VIEW`` row) is still reported. A filtered-out
    table leaves no trace in any ``coverage.tables`` bucket. ODBC must not fabricate the
    capabilities it lacks: relationships stay an explicit coverage ``unsupported`` gap,
    each selected table's primary key is ``unavailable`` with the SQLPrimaryKeys reason,
    and each query's SQL/type remain ``unavailable`` while its declared parameters stay
    ``observed``. The source copy is byte-identical with no lock residue, and the filtered
    evidence renders one coherent database report for the subset.
    """
    directory = e2e_build["directory"]
    core = directory / "core.accdb"
    before = _sha256(core)
    requested = ["Orders", "Sales Notes", "Customers"]
    completed = _run_cli(
        [
            "documentation", "inspect", "--db", str(core), "--backend", "odbc",
            "--tables", "Orders", "--tables", "Sales Notes", "--tables", "Customers", "--json",
        ],
        _cli_env(directory),
    )
    after = _sha256(core)
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True
    evidence = envelope["data"]
    payload = evidence["data"]
    expected = e2e_build["expected"]["databases"]["core.accdb"]

    assert evidence["source"]["backend_identity"] == "ODBC"
    # Exactly the requested tables in request order; the query axis is unchanged.
    assert [table["name"] for table in payload["tables"]] == requested
    assert evidence["run"]["scope"] == {"tables": requested, "queries": _ODBC_VISIBLE_QUERIES}
    assert evidence["coverage"]["tables"]["inspected"] == requested
    assert evidence["coverage"]["tables"]["failed"] == {}
    assert evidence["coverage"]["tables"]["excluded"] == {}
    assert sorted(evidence["coverage"]["tables"]["unsupported"]) == sorted(requested)
    omitted = sorted(set(expected["tables"]) - set(requested))
    for name in omitted:
        assert name not in {table["name"] for table in payload["tables"]}, name
        for bucket in ("inspected", "excluded", "unsupported", "failed"):
            assert name not in evidence["coverage"]["tables"][bucket], (name, bucket)
    assert {fact["state"] for fact in _iter_facts(payload)} <= LEGAL_FACT_STATES

    # Honest ODBC gaps: unavailable PK per selected table, no fabricated relationships,
    # and query SQL/type unavailable while declared parameters stay observed.
    for table in payload["tables"]:
        assert table["primary_key"]["state"] == "unavailable"
        assert _ODBC_PK_GAP in table["primary_key"]["reason"]
        assert _ODBC_PK_GAP in evidence["coverage"]["tables"]["unsupported"][table["name"]]
        for field in table["fields"]:
            assert field["type"]["state"] == "observed" and field["size"]["state"] == "observed"
            for prop in ("required", "allow_zero_length", "default_value"):
                assert field[prop]["state"] == "unavailable", (table["name"], field["name"], prop)
                assert field[prop]["reason"].strip()
    assert payload["relationships"] == []
    assert _ODBC_RELATIONSHIP_GAP in evidence["coverage"]["relationships"]["unsupported"]["relationships"]
    assert evidence["coverage"]["relationships"]["failed"] == {}
    queries = _queries_by_name({"envelope": envelope})
    assert list(queries) == _ODBC_VISIBLE_QUERIES
    for name in _ODBC_ACTION_QUERIES:
        assert name not in queries
    for query in queries.values():
        assert query["sql"]["state"] == "unavailable" and query["sql"]["reason"].strip()
        assert query["type"]["state"] == "unavailable" and query["type"]["reason"].strip()
    assert queries["qryOrdersByCustomer"]["parameters"]["state"] == "observed"
    assert [(item["name"], item["type"], item["type_name"]) for item in queries["qryOrdersByCustomer"]["parameters"]["value"]] == [
        ("[pCustomerID]", 4, "INTEGER")
    ]
    assert evidence["coverage"]["queries"]["inspected"] == _ODBC_VISIBLE_QUERIES
    assert evidence["coverage"]["queries"]["failed"] == {}

    # No observed mutation and no lock residue from the filtered ODBC open.
    assert after == before == e2e_build["inventory"]["core.accdb"]["sha256"]
    assert _lock_files(directory, "core.accdb") == []

    # The filtered ODBC evidence renders one coherent database report for the subset.
    report = _render_database_report(completed.stdout, work_dir / "inspect-core-filtered-tables-odbc.json", directory)
    markdown = report["markdown"]
    assert report["schema"] == "access.documentation.report/1"
    assert report["status"] == "partial"
    assert report["sha256"] == hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    assert "- Backend: ODBC" in markdown
    assert "- Tables: Orders, Sales Notes, Customers" in markdown
    for name in requested:
        assert f"### {name}" in markdown, name
    for name in omitted:
        assert f"### {name}" not in markdown, name
    assert "relCustomersProfiles" not in markdown
    assert "| t1 | Orders |" in markdown
    assert "| t2 | Sales Notes |" in markdown
    assert "| t3 | Customers |" in markdown
    block = _mermaid_block(markdown)
    assert [line for line in block.splitlines() if _EDGE_RE.match(line)] == []


def test_inspect_filtered_queries_via_odbc_returns_exactly_the_requested_view(
    e2e_build: dict[str, Any], work_dir: Path
) -> None:
    """T3: ``--queries`` selects exactly the requested ODBC ``VIEW`` row and nothing else.

    Only the three row-returning queries are ODBC-visible. Selecting one leaves the table
    axis at all eight user tables and the relationship catalogue at its explicit ODBC
    unsupported gap (never an observed-empty catalogue). The unselected ODBC-visible
    queries are absent from evidence and every ``coverage.queries`` bucket, and the action
    query names are never visible to ODBC at all.
    """
    directory = e2e_build["directory"]
    core = directory / "core.accdb"
    before = _sha256(core)
    completed = _run_cli(
        [
            "documentation", "inspect", "--db", str(core), "--backend", "odbc",
            "--queries", "qryOrderSummary", "--json",
        ],
        _cli_env(directory),
    )
    after = _sha256(core)
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True
    evidence = envelope["data"]
    payload = evidence["data"]
    expected = e2e_build["expected"]["databases"]["core.accdb"]

    assert [query["name"] for query in payload["queries"]] == ["qryOrderSummary"]
    assert evidence["coverage"]["queries"]["inspected"] == ["qryOrderSummary"]
    assert evidence["coverage"]["queries"]["failed"] == {}
    assert evidence["coverage"]["queries"]["excluded"] == {}
    for name in ["qryOpenOrders", "qryOrdersByCustomer", *_ODBC_ACTION_QUERIES]:
        assert name not in {query["name"] for query in payload["queries"]}, name
        for bucket in ("inspected", "excluded", "unsupported", "failed"):
            assert name not in evidence["coverage"]["queries"][bucket], (name, bucket)

    # A query filter does not narrow the table scope or the relationship catalogue.
    assert {table["name"] for table in payload["tables"]} == set(expected["tables"])
    assert {table["name"] for table in payload["tables"]} == set(evidence["coverage"]["tables"]["inspected"])
    assert evidence["run"]["scope"] == {
        "tables": [table["name"] for table in payload["tables"]],
        "queries": ["qryOrderSummary"],
    }
    assert payload["relationships"] == []
    assert _ODBC_RELATIONSHIP_GAP in evidence["coverage"]["relationships"]["unsupported"]["relationships"]

    # The requested query keeps its honest ODBC gaps and known-empty parameters.
    query = payload["queries"][0]
    assert query["sql"]["state"] == "unavailable" and query["sql"]["reason"].strip()
    assert query["type"]["state"] == "unavailable" and query["type"]["reason"].strip()
    assert query["parameters"]["state"] == "observed" and query["parameters"]["value"] == []
    assert {fact["state"] for fact in _iter_facts(payload)} <= LEGAL_FACT_STATES

    assert after == before == e2e_build["inventory"]["core.accdb"]["sha256"]
    assert _lock_files(directory, "core.accdb") == []

    report = _render_database_report(completed.stdout, work_dir / "inspect-core-filtered-queries-odbc.json", directory)
    markdown = report["markdown"]
    assert report["status"] == "partial"
    assert "- Backend: ODBC" in markdown
    assert "qryOrderSummary" in markdown
    for name in ["qryOpenOrders", "qryOrdersByCustomer", *_ODBC_ACTION_QUERIES]:
        assert f"### {name}" not in markdown, name


def test_inspect_combined_table_and_query_filters_via_odbc_scope_both_axes(
    e2e_build: dict[str, Any], work_dir: Path
) -> None:
    """T3: one ODBC invocation can filter tables and queries at the same time.

    ``--tables Orders --tables OrderLines --queries qryOrdersByCustomer`` scopes both
    axes independently: exactly those two requested tables (in order), exactly that one
    ODBC-visible query, their own coverage ``inspected`` lists, and empty (unsupported)
    ODBC relationships. The filtered evidence renders one coherent database report for
    the intersection.
    """
    directory = e2e_build["directory"]
    core = directory / "core.accdb"
    before = _sha256(core)
    completed = _run_cli(
        [
            "documentation", "inspect", "--db", str(core), "--backend", "odbc",
            "--tables", "Orders", "--tables", "OrderLines", "--queries", "qryOrdersByCustomer", "--json",
        ],
        _cli_env(directory),
    )
    after = _sha256(core)
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True
    evidence = envelope["data"]
    payload = evidence["data"]

    assert [table["name"] for table in payload["tables"]] == ["Orders", "OrderLines"]
    assert [query["name"] for query in payload["queries"]] == ["qryOrdersByCustomer"]
    assert evidence["run"]["scope"] == {"tables": ["Orders", "OrderLines"], "queries": ["qryOrdersByCustomer"]}
    assert evidence["coverage"]["tables"]["inspected"] == ["Orders", "OrderLines"]
    assert evidence["coverage"]["queries"]["inspected"] == ["qryOrdersByCustomer"]
    for table in payload["tables"]:
        assert table["primary_key"]["state"] == "unavailable" and _ODBC_PK_GAP in table["primary_key"]["reason"]
    assert payload["relationships"] == []
    assert _ODBC_RELATIONSHIP_GAP in evidence["coverage"]["relationships"]["unsupported"]["relationships"]
    assert {fact["state"] for fact in _iter_facts(payload)} <= LEGAL_FACT_STATES

    assert after == before == e2e_build["inventory"]["core.accdb"]["sha256"]
    assert _lock_files(directory, "core.accdb") == []

    report = _render_database_report(completed.stdout, work_dir / "inspect-core-combined-odbc.json", directory)
    markdown = report["markdown"]
    assert "- Tables: Orders, OrderLines" in markdown
    assert "### Orders" in markdown and "### OrderLines" in markdown
    assert "### Customers" not in markdown
    assert "qryOrdersByCustomer" in markdown and "qryOpenOrders" not in markdown


def test_inspect_unknown_requested_name_via_odbc_is_a_hard_error(e2e_build: dict[str, Any]) -> None:
    """T3: an unknown requested name over ODBC is the same hard ``Err`` as DAO.

    ODBC exposes the same eligible object names as the DAO port here, so an unknown
    table or query must fail with the identical named error envelope, CLI exit 1, and no
    evidence payload, while leaving the source bytes untouched with no lock residue.
    """
    directory = e2e_build["directory"]
    core = directory / "core.accdb"
    before = _sha256(core)
    table_run = _run_cli(
        ["documentation", "inspect", "--db", str(core), "--backend", "odbc", "--tables", "NoSuchTable", "--json"],
        _cli_env(directory),
    )
    query_run = _run_cli(
        ["documentation", "inspect", "--db", str(core), "--backend", "odbc", "--queries", "NoSuchQuery", "--json"],
        _cli_env(directory),
    )
    after = _sha256(core)
    for completed, expected_error in (
        (table_run, "Unknown requested table name(s): NoSuchTable"),
        (query_run, "Unknown requested query name(s): NoSuchQuery"),
    ):
        assert completed.returncode == 1
        assert "Traceback" not in completed.stderr
        envelope = json.loads(completed.stdout)
        assert envelope["success"] is False
        assert envelope["error"] == expected_error
        assert "data" not in envelope
    assert after == before == e2e_build["inventory"]["core.accdb"]["sha256"]
    assert _lock_files(directory, "core.accdb") == []


def test_inspect_filtered_out_linked_source_is_not_contacted_via_odbc(
    e2e_build: dict[str, Any], dao_engine: Any
) -> None:
    """T3: selecting only ``LocalNotes`` never contacts the filtered-out linked source.

    ``linked.accdb`` carries one local table (``LocalNotes``) and one linked table
    (``LinkedCustomers`` -> ``sentinel.accdb``). With ``--tables LocalNotes --backend odbc``
    the linked synonym is filtered out before any column/index read, so it never reaches
    evidence; the sentinel is held exclusively throughout, so any attempted contact would
    fail loudly. A successful inspection is positive non-contact evidence: the sentinel
    bytes are unchanged, no lock residue remains, and the filtered-out linked name leaves
    no trace in the serialized envelope (no classification result, no read probe).
    """
    directory = e2e_build["directory"]
    sentinel = directory / "sentinel.accdb"
    sentinel_before = _sha256(sentinel)
    handle = dao_engine.OpenDatabase(str(sentinel), True, False)
    try:
        completed = _run_cli(
            [
                "documentation", "inspect", "--db", str(directory / "linked.accdb"),
                "--backend", "odbc", "--tables", "LocalNotes", "--json",
            ],
            _cli_env(directory),
        )
    finally:
        handle.Close()
    sentinel_after = _sha256(sentinel)

    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    assert "LinkedCustomers" not in completed.stdout
    envelope = json.loads(completed.stdout)
    assert envelope["success"] is True
    evidence = envelope["data"]
    payload = evidence["data"]
    assert evidence["source"]["backend_identity"] == "ODBC"
    assert [table["name"] for table in payload["tables"]] == ["LocalNotes"]
    assert evidence["run"]["scope"] == {"tables": ["LocalNotes"], "queries": []}
    assert evidence["coverage"]["tables"]["inspected"] == ["LocalNotes"]
    assert evidence["coverage"]["tables"]["failed"] == {}
    for bucket in ("inspected", "excluded", "unsupported", "failed"):
        assert "LinkedCustomers" not in evidence["coverage"]["tables"][bucket], bucket
    local = payload["tables"][0]
    assert local["kind"] == "local"
    assert local["fields"], "LocalNotes must expose its real columns"
    assert local["primary_key"]["state"] == "unavailable" and _ODBC_PK_GAP in local["primary_key"]["reason"]
    assert payload["relationships"] == []
    assert {fact["state"] for fact in _iter_facts(payload)} <= LEGAL_FACT_STATES

    # No contact: the locked sentinel is untouched and no lock file remains.
    assert sentinel_after == sentinel_before == e2e_build["inventory"]["sentinel.accdb"]["sha256"]
    assert _lock_files(directory, "linked.accdb") == []
    assert _lock_files(directory, "sentinel.accdb") == []


def test_inspect_empty_selection_via_odbc_is_select_none_and_transport_parity(
    e2e_build: dict[str, Any], core_odbc_evidence: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T3: an explicit empty list selects no object, distinct from ``None`` selecting all.

    The repeatable CLI flags cannot express an empty list (omitting ``--tables`` means
    "every eligible table"), so the documented empty-selection contract is exercised
    through the public application inspect interface over a real ODBC port. ``None`` on
    the same port selects every eligible object. Transport parity then asserts the public
    MCP tool over the same real ODBC backend and the public CLI ODBC run produce the same
    selection, evidence body, coverage, and source; the empty and full selections differ.
    """
    from ms_access_mcp.application.database import DatabaseUseCases
    from ms_access_mcp.application.documentation_inspect import (
        DocumentationInspectService,
        open_odbc_inspection_port,
    )
    from ms_access_mcp.backends.odbc import OdbcAdapter
    from ms_access_mcp.mcp_tools import analysis

    directory = e2e_build["directory"]
    core = directory / "core.accdb"
    before = _sha256(core)

    # (a) public application inspect interface over a real ODBC port: [] selects none.
    adapter = OdbcAdapter(str(core))
    adapter.connect(str(core))
    try:
        empty = DocumentationInspectService().inspect(open_odbc_inspection_port(adapter), tables=[], queries=[])
        every = DocumentationInspectService().inspect(open_odbc_inspection_port(adapter), tables=None, queries=None)
    finally:
        adapter.disconnect()
    empty_dict = empty.to_dict()
    every_dict = every.to_dict()

    assert empty_dict["run"]["scope"] == {"tables": [], "queries": []}
    assert empty_dict["data"]["tables"] == []
    assert empty_dict["data"]["queries"] == []
    assert empty_dict["data"]["relationships"] == []
    assert empty_dict["coverage"]["tables"] == {"inspected": [], "excluded": {}, "unsupported": {}, "failed": {}}
    assert empty_dict["coverage"]["queries"] == {"inspected": [], "excluded": {}, "unsupported": {}, "failed": {}}
    assert empty_dict["coverage"]["relationships"]["inspected"] == []
    assert _ODBC_RELATIONSHIP_GAP in empty_dict["coverage"]["relationships"]["unsupported"]["relationships"]
    assert empty_dict["source"]["backend_identity"] == "ODBC"
    # The relationship gap keeps even an empty document honestly partial, not complete.
    assert empty_dict["data"]["status"] == "partial"

    # (b) ``None`` selects every eligible object on the same real ODBC port.
    expected = e2e_build["expected"]["databases"]["core.accdb"]
    assert set(every_dict["run"]["scope"]["tables"]) == set(expected["tables"])
    assert every_dict["run"]["scope"]["queries"] == _ODBC_VISIBLE_QUERIES

    # (c) transport parity: the public MCP tool and the public CLI over the same real
    # ODBC backend select the same objects with the same body, coverage, and source.
    session = DatabaseUseCases(backend="odbc", readonly=True, allowed_dirs=[directory])
    monkeypatch.setattr(analysis, "_new_db", lambda readonly=True: session)
    try:
        result = analysis.documentation_inspect(str(core), tables=None, queries=None)
    finally:
        session.close_database()
    assert result.isError is False
    mcp_payload = json.loads(result.content[0].text)
    assert mcp_payload["success"] is True
    mcp_evidence = mcp_payload["data"]
    cli_evidence = core_odbc_evidence["envelope"]["data"]
    assert mcp_evidence["run"]["scope"] == cli_evidence["run"]["scope"] == every_dict["run"]["scope"]
    assert mcp_evidence["coverage"] == cli_evidence["coverage"] == every_dict["coverage"]
    assert mcp_evidence["data"] == cli_evidence["data"] == every_dict["data"]
    assert mcp_evidence["source"] == cli_evidence["source"] == every_dict["source"]

    # (d) the empty and full explicit selections differ; the source is untouched.
    assert empty_dict["run"]["scope"] != every_dict["run"]["scope"]
    assert _sha256(core) == before == e2e_build["inventory"]["core.accdb"]["sha256"]
    assert _lock_files(directory, "core.accdb") == []


# ---------------------------------------------------------------------------
# 4. non-execution invariants across the whole session (must run last)
# ---------------------------------------------------------------------------


def test_non_execution_invariants_after_inspect_render_save(
    e2e_build: dict[str, Any],
    dao_engine: Any,
    core_evidence: dict[str, Any],
    core_reports: dict[str, Any],
) -> None:
    """After the whole pipeline the fixtures are byte-identical and rows are untouched.

    The hashes prove no *observed* mutation. They do not prove engine-level read-only
    enforcement: the DAO handle still opens with ``read_only=False`` (the known
    ``backends/dao.py`` gap) and no assertion here claims that protection.
    """
    del core_evidence, core_reports  # requested only to force inspect/render to run first
    expected = e2e_build["expected"]["databases"]["core.accdb"]
    directory = e2e_build["directory"]
    with _open_readonly(dao_engine, directory / "core.accdb") as db:
        for table_name, spec in expected["tables"].items():
            assert len(_snapshot_rows(db, table_name)) == spec["rows"], table_name
        orders = sorted((row["OrderID"], row["Status"]) for row in _snapshot_rows(db, "Orders"))
        sentinels = [tuple(pair) for pair in expected["sentinels"]["Orders"]]
        assert orders == sentinels
    with _open_readonly(dao_engine, directory / "sentinel.accdb") as sentinel:
        assert len(_snapshot_rows(sentinel, "SentinelCustomers")) == 1
    with _open_readonly(dao_engine, directory / "linked.accdb") as linked:
        assert len(_snapshot_rows(linked, "LocalNotes")) == 1
    for name in ("core.accdb", "sentinel.accdb", "linked.accdb"):
        assert _sha256(directory / name) == e2e_build["inventory"][name]["sha256"], name


# ---------------------------------------------------------------------------
# 9. startup-code non-execution through the inspection pipeline
# ---------------------------------------------------------------------------


def _run_startup_cycle(directory: Path, work_dir: Path, backend: str) -> dict[str, Any]:
    """Run inspect -> render -> save on the startup fixture through the public CLI.

    The whole cycle runs in subprocesses against the DAO or ODBC backend. Neither
    backend launches ``Access.Application`` (DAO uses ``DAO.DBEngine.120``; ODBC uses
    the Access ODBC driver), so the embedded AutoExec macro is unreachable by
    construction; the absent marker is the observable confirmation.
    """
    startup = directory / "startup.accdb"
    before = _sha256(startup)
    pids_before = _msaccess_pids()
    inspect = _run_cli(
        ["documentation", "inspect", "--db", str(startup), "--backend", backend, "--json"],
        _cli_env(directory),
    )
    evidence_path = work_dir / f"startup-{backend}-evidence.json"
    evidence_path.write_text(inspect.stdout, encoding="utf-8")
    render = _run_cli(
        ["documentation", "render", "--evidence", str(evidence_path), "--kind", "database", "--json"],
        _cli_env(directory),
    )
    report_path = work_dir / f"startup-{backend}-report.json"
    report_path.write_text(render.stdout, encoding="utf-8")
    destination = work_dir / f"startup-{backend}-report.md"
    save = _run_cli(
        ["documentation", "save", "--report", str(report_path), "--output-path", str(destination),
         "--allow-doc-write", "--yes", "--json"],
        _cli_env(directory, allow_doc_write=True),
    )
    after = _sha256(startup)
    return {
        "inspect": inspect,
        "render": render,
        "save": save,
        "destination": destination,
        "before": before,
        "after": after,
        "markers": _marker_files(directory),
        "new_pids": _msaccess_pids() - pids_before,
        "locks": sorted(path.name for path in directory.iterdir() if path.name.startswith("startup.accdb.")),
    }


@pytest.mark.parametrize("backend", ["dao", "odbc"])
def test_startup_code_is_never_executed_by_the_pipeline(
    e2e_build: dict[str, Any], work_dir: Path, backend: str
) -> None:
    """Full inspect+render+save on startup.accdb leaves no marker and no Access process.

    The DAO inspection path uses ``DAO.DBEngine.120`` and the ODBC path the Access ODBC
    driver; neither launches ``Access.Application``, so the AutoExec macro is
    unreachable by construction and marker absence is the observable proof. The gated
    control run (``test_documentation_startup_control.py``) proves the same fixture
    genuinely executes when deliberately opened, so these results are not vacuous.
    """
    directory = e2e_build["directory"]
    result = _run_startup_cycle(directory, work_dir, backend)
    assert result["inspect"].returncode == 0, result["inspect"].stderr
    assert result["render"].returncode == 0, result["render"].stderr
    assert result["save"].returncode == 0, result["save"].stderr
    envelope = json.loads(result["inspect"].stdout)
    assert envelope["success"] is True
    payload = envelope["data"]["data"]
    assert [table["name"] for table in payload["tables"]] == ["StartupNotes"]
    assert result["markers"] == []
    assert not (directory / "startup_executed.marker").exists()
    assert result["locks"] == []
    assert result["before"] == result["after"]
    assert result["new_pids"] == set()
    markdown = json.loads(result["render"].stdout)["data"]["markdown"]
    assert result["destination"].read_bytes() == markdown.encode("utf-8")
