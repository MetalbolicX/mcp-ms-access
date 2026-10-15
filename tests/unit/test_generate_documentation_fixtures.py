"""Public CLI contract for the synthetic fixture builder (no ACE required)."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import uuid
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "generate_documentation_fixtures.py"
MANIFEST = ROOT / "tests" / "fixtures" / "documentation_expected.json"
DB_ROOT = ROOT / "db"


def _load_generator():
    """Load the fixture generator by path; it is a script, not an installed module."""
    spec = spec_from_file_location("generate_documentation_fixtures", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load_generator()


def invoke(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )


def workspace_state() -> tuple[list[str], list[str]]:
    """Repository root names plus every path under db/, so any write becomes visible."""
    root_names = sorted(path.name for path in ROOT.iterdir())
    if not DB_ROOT.exists():
        return root_names, []
    return root_names, sorted(str(path.relative_to(DB_ROOT)) for path in DB_ROOT.rglob("*"))


def test_describe_matches_approved_fixture_and_committed_manifest() -> None:
    before = workspace_state()
    result = invoke("--describe")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    # Golden fixture: read_text normalizes newlines, so this pins content and formatting.
    assert result.stdout == MANIFEST.read_text(encoding="utf-8")
    manifest = json.loads(result.stdout)
    core = manifest["databases"]["core.accdb"]
    assert list(core["tables"]) == [
        "Customers", "CustomerProfiles", "Orders", "OrderLines", "Products",
        "Warehouses", "Stock", "Sales Notes",
    ]
    assert core["tables"]["Stock"]["primary_key"] == ["ProductID", "WarehouseID"]
    assert core["tables"]["OrderLines"]["primary_key"] == ["OrderID", "LineNo"]
    assert core["tables"]["Orders"]["fields"]["CustomerID"]["required"] is True
    assert core["tables"]["Orders"]["fields"]["BillingCustomerID"]["required"] is False
    assert "Résumé" in core["tables"]["Sales Notes"]["fields"]
    assert core["queries"]["qryOrdersByCustomer"]["parameters"] == {"[pCustomerID]": 4}
    assert list(core["relationships"]) == [
        "relCustomersProfiles", "CustomersOrders", "BillingCustomersOrders",
        "OrdersOrderLines", "ProductsStock", "WarehousesStock", "StockOrderLines",
    ]
    assert core["relationships"]["StockOrderLines"]["parent_columns"] == ["ProductID", "WarehouseID"]
    assert core["relationships"]["StockOrderLines"]["child_columns"] == ["ProductID", "WarehouseID"]
    assert list(core["queries"]) == [
        "qryOpenOrders", "qryOrdersByCustomer", "qryOrderSummary",
        "qryUpdateOrderStatus", "qryDeleteDraftOrders",
    ]
    assert core["tables"]["Orders"]["rows"] == 2
    assert core["tables"]["CustomerProfiles"]["unique_indexes"] == {"CustomersProfiles": ["CustomerID"]}
    assert manifest["startup_execution"] == "authorized-isolated-control-only"
    assert list(manifest["databases"]) == [
        "core.accdb", "sentinel.accdb", "linked.accdb", "startup.accdb",
    ]
    startup = manifest["databases"]["startup.accdb"]
    assert list(startup["tables"]) == ["StartupNotes"]
    assert startup["tables"]["StartupNotes"]["primary_key"] == ["NoteID"]
    assert startup["tables"]["StartupNotes"]["rows"] == 1
    assert startup["relationships"] == {}
    assert startup["queries"] == {}
    assert startup["startup"] == {
        "module": "StartupMarkerModule",
        "macro": "AutoExec",
        "control_name": "StartupControl",
        "named_macro": "ManualMarker",
        "autokeys_macro": "AutoKeys",
        "autokeys_control_name": "AutoKeysControl",
        "marker_filename": "startup_executed.marker",
        "execution_policy": "authorized-isolated-control-only",
    }
    assert manifest["databases"]["linked.accdb"]["linked_tables"] == {
        "LinkedCustomers": {"source_database": "sentinel.accdb", "source_table": "SentinelCustomers"}
    }
    assert workspace_state() == before


@pytest.mark.parametrize(
    "destination",
    [
        str(ROOT / "scripts"),
        "scripts",
        str(ROOT / "db" / "fixtures" / ".." / "scripts"),
    ],
)
def test_build_refuses_destination_outside_fixture_root_without_mutations(destination: str) -> None:
    before = workspace_state()
    result = invoke("--build", "--output-dir", destination)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "Error: output directory must be inside db/fixtures.\n"
    assert workspace_state() == before


def test_no_arguments_only_shows_help_without_building() -> None:
    before = workspace_state()
    result = invoke()
    assert result.returncode == 0
    assert result.stderr == ""
    assert "--describe" in result.stdout and "--build" in result.stdout
    assert workspace_state() == before


def test_build_refuses_existing_output_directory_without_mutations(request: pytest.FixtureRequest) -> None:
    fixtures_root = DB_ROOT / "fixtures"
    fixtures_root_created = not fixtures_root.exists()
    output = fixtures_root / f"existing-{uuid.uuid4().hex}"
    output.mkdir(parents=True)

    def cleanup() -> None:
        output.rmdir()
        if fixtures_root_created:
            fixtures_root.rmdir()

    request.addfinalizer(cleanup)
    before = workspace_state()
    result = invoke("--build", "--output-dir", str(output))
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "Error: output directory already exists; existing fixtures are never overwritten.\n"
    assert workspace_state() == before
    assert output.exists()


def test_startup_vba_derives_only_the_fixed_marker_without_escape_hatches() -> None:
    """Lock the generated VBA safety property independently of the manifest.

    Applicability exception: this is a locking pin for the marker-safety property that the
    T4 audit already verified, so it passes against the current source immediately and no
    synthetic RED is manufactured. It fails if a future edit introduces an ambient path
    source, a shell/registry/object-creation primitive, a network path, or a traversal.
    """
    vba_source = generator._STARTUP_MODULE_SOURCE.format(marker=generator.STARTUP_MARKER_FILENAME)
    assert generator.STARTUP_MARKER_FILENAME == "startup_executed.marker"
    assert "Application.CurrentProject.Path" in vba_source
    assert generator.STARTUP_MARKER_FILENAME in vba_source

    for token in ("Shell", "Environ", "WScript", "RegRead", "RegWrite", "CreateObject"):
        assert token not in vba_source, f"generated VBA must not reference {token}"
    assert re.search(r"\bKill\b", vba_source) is None, "generated VBA must not call Kill"
    assert "http" not in vba_source.lower(), "generated VBA must not reference a network URL"
    assert "\\\\" not in vba_source, "generated VBA must not reference a UNC path"
    assert ".." not in vba_source, "generated VBA must not traverse with '..'"

    function_match = re.search(r"Public Function (\w+)\(", vba_source)
    assert function_match is not None, "the module must define a public marker function"
    function_name = function_match.group(1)

    macro_source = generator._STARTUP_MACRO_SOURCE
    assert re.findall(r'Action ="([^"]*)"', macro_source) == ["RunCode"]
    assert re.findall(r'Argument ="([^"]*)"', macro_source) == [f"{function_name}()"]


def test_generated_executable_and_macro_sources_have_no_escape_hatches() -> None:
    """Lock the generated executable/macro source safety property.

    Applicability exception: this is a locking pin over the newly added generator
    surface, so it passes against the intended sources and no synthetic RED is
    manufactured. It fails if a future edit introduces an ambient path source, a
    shell/registry/object-creation primitive, a network URL, or a traversal, and it
    pins the named macro and AutoKeys group to the same single RunCode marker call as
    AutoExec.
    """
    vba_source = generator._STARTUP_MODULE_SOURCE.format(marker=generator.STARTUP_MARKER_FILENAME)
    for label, text in (
        ("VBA module", vba_source),
        ("AutoExec macro", generator._STARTUP_MACRO_SOURCE),
        ("named macro", generator._MANUAL_MARKER_MACRO_SOURCE),
        ("AutoKeys macro", generator._AUTOKEYS_MACRO_SOURCE),
    ):
        for token in ("Shell", "Environ", "WScript", "RegRead", "RegWrite", "CreateObject"):
            assert token not in text, f"{label} must not reference {token}"
        assert re.search(r"\bKill\b", text) is None, f"{label} must not call Kill"
        assert "http" not in text.lower(), f"{label} must not reference a network URL"
        assert "\\\\" not in text, f"{label} must not reference a UNC path"
        assert ".." not in text, f"{label} must not traverse with '..'"

    assert re.findall(r'Action ="([^"]*)"', generator._MANUAL_MARKER_MACRO_SOURCE) == ["RunCode"]
    assert re.findall(r'Argument ="([^"]*)"', generator._MANUAL_MARKER_MACRO_SOURCE) == ["StartupMarker()"]
    assert generator._AUTOKEYS_MACRO_SOURCE.count("Begin") == 1
    assert generator._AUTOKEYS_MACRO_SOURCE.count("End") == 1
    assert re.findall(r'MacroName ="([^"]*)"', generator._AUTOKEYS_MACRO_SOURCE) == ["^+{F12}"]
    assert re.findall(r'Action ="([^"]*)"', generator._AUTOKEYS_MACRO_SOURCE) == ["RunCode"]
    assert re.findall(r'Argument ="([^"]*)"', generator._AUTOKEYS_MACRO_SOURCE) == ["StartupMarker()"]


def _generator_provenance_filenames() -> set[str]:
    """Return the ``output / "<literal>"`` provenance filenames the startup builder writes.

    The generator is a script, not an installed module, so the set is read from its
    source: within ``create_startup_code`` every ``output / "<name>"`` path is a
    provenance file left beside the built databases.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "create_startup_code":
            return {
                child.right.value
                for child in ast.walk(node)
                if isinstance(child, ast.BinOp)
                and isinstance(child.op, ast.Div)
                and isinstance(child.left, ast.Name)
                and child.left.id == "output"
                and isinstance(child.right, ast.Constant)
                and isinstance(child.right.value, str)
            }
    raise AssertionError("generator function create_startup_code not found")


def test_provenance_filenames_contain_no_marker_token() -> None:
    """Lock the provenance names so no built artifact carries the ``marker`` token.

    Applicability exception: this is a locking pin over the audit-verified generator
    provenance names (the same exception class as the prior startup-safety pin), so it
    passes against the current source immediately and no synthetic RED is manufactured.
    It fails if a provenance file is renamed to include ``marker`` -- the T5
    ``manual_marker_macro.txt`` -> ``named_macro.txt`` rename is exactly this risk --
    because the pipeline suites' ``*marker*`` detector globs file names beside the built
    databases, and a provenance file matching it would be a false non-execution failure.
    """
    provenance = _generator_provenance_filenames()
    assert provenance == {
        "startup_module.bas",
        "startup_macro.txt",
        "named_macro.txt",
        "autokeys_macro.txt",
    }
    assert not any("marker" in name.lower() for name in provenance)
