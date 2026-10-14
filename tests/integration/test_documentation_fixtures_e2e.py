"""Opt-in isolated ACE integration test: built fixtures match the committed manifest.

This suite builds the synthetic documentation fixtures once in a fresh
``db/fixtures/e2e-<uuid>`` directory through the public generator CLI, then verifies
each built database against the committed ground truth in
``tests/fixtures/documentation_expected.json`` using raw, read-only DAO construction
readback. It does not exercise the documentation extractor, inspection, rendering, or
saving; those are covered separately.

The startup fixture (``startup.accdb``) is read back for its construction facts: the
table and row, and the module and macro names as DAO exposes them through the
``Modules`` and ``Scripts`` containers. DAO exposes the module and macro *names* only;
it cannot read the VBA source or macro actions, so code non-execution over this suite
is checked by the absent marker beside the built database, not by inspecting the code.

Run explicitly on Windows with ACE DAO and pywin32:

    $env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
    .venv\\Scripts\\python.exe -m pytest tests\\integration\\test_documentation_fixtures_e2e.py -q

Without ``ACCESS_DOCUMENTATION_FIXTURES_TEST=1`` the module skips cleanly.
"""

from __future__ import annotations

import hashlib
import json
import os
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
BUILD_TIMEOUT_SECONDS = 600
ATTACHED_TABLE_ATTRIBUTE = 0x40000000  # DAO dbAttachedTable
DB_OPEN_SNAPSHOT = 4  # DAO dbOpenSnapshot

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.skipif(not _has_ace_dao(), reason="Requires ACE DAO (DAO.DBEngine.120)"),
    pytest.mark.skipif(
        os.environ.get(OPT_IN_ENV) != "1",
        reason=f"Set {OPT_IN_ENV}=1 to run the isolated ACE fixture integration test",
    ),
    pytest.mark.com_integration,
]


def _venv_python() -> str:
    """Return the repository virtualenv interpreter, falling back to the current one."""
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def _clean(value: Any) -> Any:
    """Strip ACE's trailing NUL padding from a COM string read."""
    return value.rstrip("\x00") if isinstance(value, str) else value


def _iter_collection(collection: Any) -> Iterator[Any]:
    """Yield each item of a DAO COM collection in index order."""
    for index in range(collection.Count):
        yield collection(index)


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


def _user_table_names(db: Any) -> set[str]:
    return {_clean(table.Name) for table in _iter_collection(db.TableDefs) if not table.Name.startswith("MSys")}


def _container_document_names(db: Any, container: str) -> set[str]:
    """Return the document names in a DAO container, e.g. ``Modules`` or ``Scripts``.

    DAO stores standard modules in ``Modules`` and macros in ``Scripts`` (there is no
    ``Macros`` container). Only names are exposed; the code body and macro actions are
    not readable through these documents.
    """
    return {_clean(db.Containers(container).Documents(position).Name)
            for position in range(db.Containers(container).Documents.Count)}


def _primary_key(db: Any, table_name: str) -> list[str]:
    table_def = db.TableDefs(table_name)
    for index in _iter_collection(table_def.Indexes):
        if index.Primary:
            return [_clean(index.Fields(position).Name) for position in range(index.Fields.Count)]
    return []


def _unique_indexes(db: Any, table_name: str) -> dict[str, list[str]]:
    table_def = db.TableDefs(table_name)
    return {
        _clean(index.Name): [_clean(index.Fields(position).Name) for position in range(index.Fields.Count)]
        for index in _iter_collection(table_def.Indexes)
        if index.Unique and not index.Primary
    }


def _snapshot_rows(db: Any, table_name: str) -> list[dict[str, Any]]:
    """Read every row of a table through a read-only snapshot recordset.

    The raw table name is passed without brackets, matching DAO name resolution.
    """
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


def _normalize_sql(sql: str) -> str:
    """Compare ACE-canonical SQL ignoring whitespace, brackets, and case.

    ACE rewrites ``DELETE FROM`` to ``DELETE *``; both spellings normalize the same.
    """
    text = _clean(sql).replace("[", "").replace("]", "")
    text = " ".join(text.split()).lower()
    return text.replace("delete * from", "delete from")


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


@pytest.fixture(scope="session")
def e2e_build() -> Iterator[dict[str, Any]]:
    """Build the fixtures once through the public CLI into a fresh isolated directory."""
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
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stderr == "", completed.stderr
        inventory = json.loads((output_dir / "inventory.json").read_text(encoding="utf-8"))
        yield {"directory": output_dir, "inventory": inventory}
        # After the whole session the sentinel must still match the build inventory:
        # a RefreshLink or linked-record contact would rewrite the file.
        sentinel_hash = hashlib.sha256((output_dir / "sentinel.accdb").read_bytes()).hexdigest()
        assert sentinel_hash == inventory["sentinel.accdb"]["sha256"]
    finally:
        _remove_self_created_tree(output_dir, created_fixture_root)


@pytest.fixture(scope="session")
def dao_engine() -> Iterator[Any]:
    """Dispatch the per-thread DAO engine used for read-only construction readback."""
    pythoncom = pytest.importorskip("pythoncom")
    win32com_client = pytest.importorskip("win32com.client")
    pythoncom.CoInitialize()
    try:
        yield win32com_client.Dispatch("DAO.DBEngine.120")
    finally:
        pythoncom.CoUninitialize()


def test_core_structure_matches_committed_manifest(e2e_build: dict[str, Any], dao_engine: Any) -> None:
    """Eight core tables, primary keys, unique index, and relation orientation."""
    expected = _load_manifest()["databases"]["core.accdb"]
    with _open_readonly(dao_engine, e2e_build["directory"] / "core.accdb") as db:
        tables = _user_table_names(db)
        assert tables == set(expected["tables"])
        assert "Sales Notes" in tables
        for table_name, spec in expected["tables"].items():
            assert _primary_key(db, table_name) == spec["primary_key"], table_name
            assert _unique_indexes(db, table_name) == spec["unique_indexes"], table_name

        relations = {
            _clean(relation.Name): (
                _clean(relation.Table),
                _clean(relation.ForeignTable),
                [
                    (_clean(relation.Fields(position).Name), _clean(relation.Fields(position).ForeignName))
                    for position in range(relation.Fields.Count)
                ],
            )
            for relation in _iter_collection(db.Relations)
        }

    assert set(relations) == set(expected["relationships"])
    for name, spec in expected["relationships"].items():
        parent, child, pairs = relations[name]
        assert parent == spec["parent_table"], name
        assert child == spec["child_table"], name
        assert pairs == list(zip(spec["parent_columns"], spec["child_columns"], strict=True)), name
    assert relations["StockOrderLines"][2] == [("ProductID", "ProductID"), ("WarehouseID", "WarehouseID")]
    assert relations["BillingCustomersOrders"][2] == [("CustomerID", "BillingCustomerID")]


def test_queries_match_committed_manifest(e2e_build: dict[str, Any], dao_engine: Any) -> None:
    """Five saved queries with committed DAO types, the declared parameter, and SQL."""
    expected = _load_manifest()["databases"]["core.accdb"]["queries"]
    canonical = e2e_build["inventory"]["core.accdb"]["canonical_query_sql"]
    with _open_readonly(dao_engine, e2e_build["directory"] / "core.accdb") as db:
        query_defs = {_clean(query.Name): query for query in _iter_collection(db.QueryDefs)}
        assert set(query_defs) == set(expected)
        for name, spec in expected.items():
            query_def = query_defs[name]
            assert int(_clean(query_def.Type)) == spec["dao_type"], name
            parameters = {
                _clean(query_def.Parameters(position).Name): int(_clean(query_def.Parameters(position).Type))
                for position in range(query_def.Parameters.Count)
            }
            assert parameters == spec["parameters"], name
            readback_sql = _clean(query_def.SQL)
            assert _normalize_sql(readback_sql) == _normalize_sql(spec["sql"]), name
            assert readback_sql == canonical[name], name

        parameter_query = query_defs["qryOrdersByCustomer"]
        assert int(_clean(parameter_query.Type)) == 0
        assert parameter_query.Parameters.Count == 1
        assert _clean(parameter_query.Parameters(0).Name) == "[pCustomerID]"
        assert int(_clean(parameter_query.Parameters(0).Type)) == 4


def test_rows_and_sentinels_match_committed_manifest(e2e_build: dict[str, Any], dao_engine: Any) -> None:
    """Row counts, Order sentinel values, stored defaults, and validation readback."""
    expected = _load_manifest()["databases"]["core.accdb"]
    with _open_readonly(dao_engine, e2e_build["directory"] / "core.accdb") as db:
        for table_name, spec in expected["tables"].items():
            assert len(_snapshot_rows(db, table_name)) == spec["rows"], table_name

        orders = [(row["OrderID"], row["Status"]) for row in _snapshot_rows(db, "Orders")]
        sentinels = [tuple(pair) for pair in expected["sentinels"]["Orders"]]
        assert sorted(orders) == sentinels

        customers = _snapshot_rows(db, "Customers")
        assert customers[0]["Status"] == "Active"

        validation = db.TableDefs("Products").Fields("UnitPrice").ValidationRule
        assert _clean(validation) == expected["tables"]["Products"]["fields"]["UnitPrice"]["validation"]


def test_sentinel_and_linked_fixtures_match_committed_manifest(
    e2e_build: dict[str, Any], dao_engine: Any
) -> None:
    """Sentinel contents, the local linked table, and the attached metadata."""
    databases = _load_manifest()["databases"]
    sentinel_spec = databases["sentinel.accdb"]
    linked_spec = databases["linked.accdb"]
    directory = e2e_build["directory"]

    with _open_readonly(dao_engine, directory / "sentinel.accdb") as sentinel:
        assert _user_table_names(sentinel) == set(sentinel_spec["tables"])
        sentinel_rows = _snapshot_rows(sentinel, "SentinelCustomers")
        assert len(sentinel_rows) == sentinel_spec["tables"]["SentinelCustomers"]["rows"]

    with _open_readonly(dao_engine, directory / "linked.accdb") as linked:
        assert _user_table_names(linked) == {"LocalNotes", "LinkedCustomers"}
        assert len(_snapshot_rows(linked, "LocalNotes")) == linked_spec["tables"]["LocalNotes"]["rows"]
        linked_table = linked.TableDefs("LinkedCustomers")
        assert "sentinel.accdb" in _clean(linked_table.Connect)
        expected_source = linked_spec["linked_tables"]["LinkedCustomers"]["source_table"]
        assert _clean(linked_table.SourceTableName) == expected_source
        attached = linked_table.Attributes & ATTACHED_TABLE_ATTRIBUTE
        assert attached == ATTACHED_TABLE_ATTRIBUTE

    sentinel_hash = hashlib.sha256((directory / "sentinel.accdb").read_bytes()).hexdigest()
    assert sentinel_hash == e2e_build["inventory"]["sentinel.accdb"]["sha256"]


def test_startup_fixture_is_observable_and_never_executed(
    e2e_build: dict[str, Any], dao_engine: Any
) -> None:
    """Startup table/row readback plus module and macro names; no marker is produced.

    DAO observability is limited to the object names: ``Modules`` exposes the VBA
    module name and ``Scripts`` exposes the macro name (there is no ``Macros``
    container), but neither exposes the VBA source or the macro's actions. Reading the
    fixture through DAO therefore cannot execute anything, and the absence of the
    marker beside the built database is the observable non-execution proof for this
    suite. The deliberate execution proof lives in the gated control run.
    """
    startup = _load_manifest()["databases"]["startup.accdb"]
    spec = startup["startup"]
    directory = e2e_build["directory"]
    database = directory / "startup.accdb"
    with _open_readonly(dao_engine, database) as db:
        assert _user_table_names(db) == set(startup["tables"])
        assert len(_snapshot_rows(db, "StartupNotes")) == startup["tables"]["StartupNotes"]["rows"]
        assert _container_document_names(db, "Modules") == {spec["module"]}
        assert _container_document_names(db, "Scripts") == {spec["macro"]}
    assert not (directory / spec["marker_filename"]).exists()
    assert not (directory / "startup.accdb.laccdb").exists()
