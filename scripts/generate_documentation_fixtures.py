"""Build local synthetic ACE fixtures; never overwrite existing databases.

Use --describe for the expected structural manifest, or --build to create the
four databases. Only the startup fixture contains executable VBA and macros. The
generator keeps strict build-order discipline around it: tables are created through
DAO-only DDL, Access.Application is opened only while no auto-run object exists, the
module, the named macro, the AutoKeys group, and the AutoExec macro are loaded from
text definitions, the auto-run macro names are assigned last, and the database is
never reopened. The generator never runs VBA or a macro, and no saved query is
executed. Linking during construction intentionally accesses the local sentinel; that
activity is not evidence of inspection-time isolation.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "db" / "fixtures"
DEFAULT_OUTPUT = FIXTURE_ROOT / "documentation"
# sentinel.accdb must exist first so linked.accdb can append its link to it.
DATABASE_ORDER = ("sentinel.accdb", "core.accdb", "linked.accdb", "startup.accdb")
LOCALE = ";LANGID=0x0409;CP=1252;COUNTRY=0"
# AcObjectType constants for Access.Application.LoadFromText and DoCmd.Rename.
_AC_MACRO = 4
_AC_MODULE = 5
# msoAutomationSecurityForceDisable: the build opens Access only before any auto-run
# object exists, and this explicit value is a second guard that forbids macros from
# running at all during that phase.
_MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3
# Never an absolute, user, home, temporary, or network path: the VBA derives the
# marker location from the database's own directory, so a copied database writes
# beside the copy and a session build writes nowhere. This filename is the only
# path component in the generated VBA.
STARTUP_MARKER_FILENAME = "startup_executed.marker"
# Auto-run-adjacent names are assigned only after every load, immediately before the
# database is closed: AutoExec runs at startup and AutoKeys runs on keyboard input.
# Neither can run while only the neutral control names exist.
MANUAL_MARKER_MACRO_NAME = "ManualMarker"
AUTOKEYS_MACRO_NAME = "AutoKeys"
AUTOKEYS_CONTROL_NAME = "AutoKeysControl"
# Standard modules load as raw VBA source (the ``Version``/``Begin`` wrapper is a
# SaveAsText export framing, not module code, and would import as a compile error).
# Macros load in the framed macro text format.
_STARTUP_MODULE_SOURCE = """Option Compare Database
Option Explicit

Public Function StartupMarker() As Boolean
    Dim markerPath As String
    markerPath = Application.CurrentProject.Path & "\\{marker}"
    Open markerPath For Output As #1
    Print #1, "startup fixture executed"
    Close #1
    StartupMarker = True
End Function
"""
_STARTUP_MACRO_SOURCE = '''Version =196611
ColumnsShown =0
Begin
    Action ="RunCode"
    Argument ="StartupMarker()"
End
'''
# A named macro that only runs when a user or code invokes it by name; nothing in the
# build or the inspection pipeline ever invokes it.
_MANUAL_MARKER_MACRO_SOURCE = _STARTUP_MACRO_SOURCE
# An AutoKeys macro group whose only submacro is triggered by a keyboard chord. Without
# Access UI keyboard input it never runs, but the group is still loaded under a neutral
# name and renamed to AutoKeys last, exactly like AutoExec.
_AUTOKEYS_MACRO_SOURCE = '''Version =196611
ColumnsShown =0
Begin
    MacroName ="^+{F12}"
    Action ="RunCode"
    Argument ="StartupMarker()"
End
'''
def field(kind: str = "LONG", *, required: bool = False,
          default: str | None = None, validation: str | None = None) -> dict[str, Any]:
    return {"type": kind, "required": required, "default": default, "validation": validation}


def table(fields: dict[str, Any], pk: list[str], rows: int = 1,
          unique: dict[str, list[str]] | None = None) -> dict[str, Any]:
    return {"fields": fields, "primary_key": pk, "unique_indexes": unique or {}, "rows": rows}


def relationship(parent: str, child: str, columns: list[str],
                 child_columns: list[str] | None = None) -> dict[str, Any]:
    return {"parent_table": parent, "child_table": child,
            "parent_columns": columns, "child_columns": child_columns or columns}


def expected_manifest() -> dict[str, Any]:
    """Return plan-derived ground truth, not extracted documentation metadata."""
    core_tables = {
        "Customers": table({"CustomerID": field(required=True), "Name": field("TEXT(50)", required=True),
                            "Status": field("TEXT(20)", default='"Active"')}, ["CustomerID"]),
        "CustomerProfiles": table({"ProfileID": field(required=True), "CustomerID": field(required=True),
                                   "Notes": field("TEXT(50)")}, ["ProfileID"],
                                  unique={"CustomersProfiles": ["CustomerID"]}),
        "Orders": table({"OrderID": field(required=True), "CustomerID": field(required=True),
                         "BillingCustomerID": field(), "Status": field("TEXT(20)", default='"Open"')},
                        ["OrderID"], rows=2),
        "OrderLines": table({"OrderID": field(required=True), "LineNo": field(required=True),
                             "ProductID": field(required=True), "WarehouseID": field(required=True),
                             "Quantity": field(required=True)}, ["OrderID", "LineNo"]),
        "Products": table({"ProductID": field(required=True), "Name": field("TEXT(50)", required=True),
                           "UnitPrice": field("CURRENCY", validation=">=0")}, ["ProductID"]),
        "Warehouses": table({"WarehouseID": field(required=True), "Name": field("TEXT(50)")}, ["WarehouseID"]),
        "Stock": table({"ProductID": field(required=True), "WarehouseID": field(required=True),
                        "Quantity": field()}, ["ProductID", "WarehouseID"]),
        "Sales Notes": table({"NotesID": field(required=True), "Order": field("TEXT(50)"),
                              "Résumé": field("TEXT(50)")}, ["NotesID"]),
    }
    relations = {
        # Named relCustomersProfiles, not CustomersProfiles: DAO auto-creates a child FK
        # index named after the relation, which would collide (DAO 5003284) with the
        # declared unique CustomerProfiles.CustomerID index that legitimately owns the
        # relation-like name CustomersProfiles.
        "relCustomersProfiles": relationship("Customers", "CustomerProfiles", ["CustomerID"]),
        "CustomersOrders": relationship("Customers", "Orders", ["CustomerID"]),
        "BillingCustomersOrders": relationship("Customers", "Orders", ["CustomerID"], ["BillingCustomerID"]),
        "OrdersOrderLines": relationship("Orders", "OrderLines", ["OrderID"]),
        "ProductsStock": relationship("Products", "Stock", ["ProductID"]),
        "WarehousesStock": relationship("Warehouses", "Stock", ["WarehouseID"]),
        "StockOrderLines": relationship("Stock", "OrderLines", ["ProductID", "WarehouseID"]),
    }
    queries = {
        "qryOpenOrders": {"category": "select", "dao_type": 0, "parameters": {},
                          "sql": "SELECT * FROM [Orders] WHERE [Status] = 'Open';"},
        "qryOrdersByCustomer": {"category": "select", "dao_type": 0,
                                "parameters": {"[pCustomerID]": 4},
                                "sql": "PARAMETERS [pCustomerID] Long; SELECT * FROM [Orders] WHERE [CustomerID] = [pCustomerID];"},
        "qryOrderSummary": {"category": "select", "dao_type": 0, "parameters": {},
                            "sql": "SELECT [CustomerID], Count(*) AS [OrderCount] FROM [Orders] GROUP BY [CustomerID];"},
        "qryUpdateOrderStatus": {"category": "action", "dao_type": 48, "parameters": {},
                                 "sql": "UPDATE [Orders] SET [Status] = 'Closed' WHERE [OrderID] = 1;"},
        "qryDeleteDraftOrders": {"category": "action", "dao_type": 32, "parameters": {},
                                 "sql": "DELETE FROM [Orders] WHERE [Status] = 'Draft';"},
    }
    startup_tables = {
        "StartupNotes": table({"NoteID": field(required=True), "Note": field("TEXT(50)")}, ["NoteID"]),
    }
    startup_block = {
        "module": "StartupMarkerModule",
        "macro": "AutoExec",
        "control_name": "StartupControl",
        "named_macro": MANUAL_MARKER_MACRO_NAME,
        "autokeys_macro": AUTOKEYS_MACRO_NAME,
        "autokeys_control_name": AUTOKEYS_CONTROL_NAME,
        "marker_filename": STARTUP_MARKER_FILENAME,
        "execution_policy": "authorized-isolated-control-only",
    }
    return {
        "schema": "access.documentation.fixtures/1", "format": "ACE",
        "startup_execution": "authorized-isolated-control-only",
        "databases": {
            "core.accdb": {"tables": core_tables, "relationships": relations, "queries": queries,
                           "sentinels": {"Orders": [[1, "Open"], [2, "Draft"]]}},
            "sentinel.accdb": {"tables": {"SentinelCustomers": table(
                {"CustomerID": field(required=True), "Name": field("TEXT(50)")}, ["CustomerID"])},
                "relationships": {}, "queries": {}},
            "linked.accdb": {"tables": {"LocalNotes": table(
                {"NotesID": field(required=True), "Note": field("TEXT(50)")}, ["NotesID"])},
                "relationships": {}, "queries": {}, "linked_tables": {
                    "LinkedCustomers": {"source_database": "sentinel.accdb", "source_table": "SentinelCustomers"}}},
            "startup.accdb": {"tables": startup_tables, "relationships": {}, "queries": {},
                              "startup": startup_block},
        },
    }


def quote(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def create_tables(db: Any, spec: dict[str, Any]) -> None:
    for name, definition in spec["tables"].items():
        columns = [f"{quote(n)} {f['type']}{' NOT NULL' if f['required'] else ''}"
                   for n, f in definition["fields"].items()]
        keys = ", ".join(quote(k) for k in definition["primary_key"])
        columns.append(f"CONSTRAINT {quote('PK_' + name)} PRIMARY KEY ({keys})")
        db.Execute(f"CREATE TABLE {quote(name)} ({', '.join(columns)})", 128)
        # DAO's TableDefs collection is stale after DDL Execute; without this refresh the
        # lookup raises DAO 5003265 "Item not found in this collection" for every table
        # after the first one.
        db.TableDefs.Refresh()
        td = db.TableDefs(name)
        for n, f in definition["fields"].items():
            column = td.Fields(n)
            if f["default"] is not None:
                column.DefaultValue = f["default"]
            if f["validation"] is not None:
                column.ValidationRule = f["validation"]
        for index, names in definition["unique_indexes"].items():
            db.Execute(f"CREATE UNIQUE INDEX {quote(index)} ON {quote(name)} "
                       f"({', '.join(quote(n) for n in names)})", 128)


def seed_core(db: Any) -> None:
    inserts = [
        "INSERT INTO Customers (CustomerID, Name) VALUES (1, 'Synthetic customer')",
        "INSERT INTO CustomerProfiles (ProfileID, CustomerID, Notes) VALUES (1, 1, 'Synthetic profile')",
        "INSERT INTO Orders (OrderID, CustomerID) VALUES (1, 1)",
        "INSERT INTO Orders (OrderID, CustomerID, BillingCustomerID, Status) VALUES (2, 1, 1, 'Draft')",
        "INSERT INTO Products (ProductID, Name, UnitPrice) VALUES (1, 'Synthetic product', 5)",
        "INSERT INTO Warehouses (WarehouseID, Name) VALUES (1, 'Synthetic warehouse')",
        "INSERT INTO Stock (ProductID, WarehouseID, Quantity) VALUES (1, 1, 10)",
        "INSERT INTO OrderLines (OrderID, LineNo, ProductID, WarehouseID, Quantity) VALUES (1, 1, 1, 1, 2)",
        "INSERT INTO [Sales Notes] (NotesID, [Order], [Résumé]) VALUES (1, 'Synthetic order', 'Synthetic note')",
    ]
    for sql in inserts:
        db.Execute(sql, 128)


def populate(db: Any, name: str, spec: dict[str, Any], output: Path) -> dict[str, str]:
    create_tables(db, spec)
    canonical_sql: dict[str, str] = {}
    if name == "core.accdb":
        seed_core(db)
        for rel_name, rel in spec["relationships"].items():
            relation = db.CreateRelation(rel_name, rel["parent_table"], rel["child_table"], 0)
            for parent, child in zip(rel["parent_columns"], rel["child_columns"], strict=True):
                column = relation.CreateField(parent)
                column.ForeignName = child
                relation.Fields.Append(column)
            db.Relations.Append(relation)
        for query_name, query in spec["queries"].items():
            query_def = db.CreateQueryDef(query_name, query["sql"])
            canonical_sql[query_name] = str(query_def.SQL)
    elif name == "sentinel.accdb":
        db.Execute("INSERT INTO SentinelCustomers (CustomerID, Name) VALUES (1, 'Sentinel')", 128)
    elif name == "linked.accdb":
        # `Note` is an ACE reserved word, like `Order` in Sales Notes: unquoted it fails
        # with DAO 5003134 "Syntax error in INSERT INTO statement."
        db.Execute(f"INSERT INTO {quote('LocalNotes')} ({quote('NotesID')}, {quote('Note')}) "
                   "VALUES (1, 'Local note')", 128)
        for link_name, link_spec in spec["linked_tables"].items():
            link = db.CreateTableDef(link_name)
            link.SourceTableName = link_spec["source_table"]
            link.Connect = ";DATABASE=" + str(output / link_spec["source_database"])
            db.TableDefs.Append(link)
    elif name == "startup.accdb":
        db.Execute(f"INSERT INTO StartupNotes ({quote('NoteID')}, {quote('Note')}) "
                   "VALUES (1, 'Synthetic startup note')", 128)
    return canonical_sql


def lexical_absolute(path: Path) -> Path:
    """Return an absolute path with `.` and `..` collapsed, without following symlinks.

    `Path.absolute` keeps `..` in place, so `is_relative_to` would accept a lexically
    escaping destination; `Path.resolve` collapses `..` but also follows symlinks, which
    the second guard checks separately.
    """
    absolute = path if path.is_absolute() else Path.cwd() / path
    collapsed: list[str] = []
    for part in absolute.parts:
        if part == "..":
            if len(collapsed) > 1:
                collapsed.pop()
        elif part not in ("", "."):
            collapsed.append(part)
    return Path(*collapsed)


def validate_destination(output: Path) -> Path:
    # Reject lexical escapes before resolving symlinks outside the authorized root.
    lexical = lexical_absolute(output)
    if not lexical.is_relative_to(FIXTURE_ROOT):
        raise ValueError("output directory must be inside db/fixtures.")
    resolved = lexical.resolve()
    if not resolved.is_relative_to(FIXTURE_ROOT.resolve()):
        raise ValueError("output directory must be inside db/fixtures.")
    if resolved.exists():
        raise ValueError("output directory already exists; existing fixtures are never overwritten.")
    return resolved


def _msaccess_pids() -> set[int]:
    """Return every running MSACCESS.EXE process id (empty when the filter matches none).

    A PID is treated as owned only because it was absent from the pre-build baseline; an
    unrelated MSACCESS.EXE that starts during the build window would be misattributed as
    owned and force-killed, while instances already running at baseline stay protected.
    """
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


def _wait_for_owned_access_exit(owned: set[int], timeout: float = 30.0) -> set[int]:
    """Poll until none of the owned MSACCESS.EXE processes remain, or timeout.

    Only baseline-diff ``owned`` PIDs are watched, so no pre-existing instance is affected
    unless an unrelated MSACCESS.EXE starts during the build window and is misattributed.
    """
    deadline = time.monotonic() + timeout
    remaining = set(owned)
    while remaining and time.monotonic() < deadline:
        time.sleep(0.5)
        remaining = _msaccess_pids() & owned
    return remaining


def _ensure_no_owned_access(baseline: set[int]) -> None:
    """Assert the build started no surviving MSACCESS.EXE; force-kill only owned PIDs.

    Assumes no unrelated MSACCESS.EXE starts during the build window: such a newcomer
    would be misattributed as owned and force-killed, whereas instances already running
    at baseline are excluded by the diff.
    """
    owned = _msaccess_pids() - baseline
    remaining = _wait_for_owned_access_exit(owned)
    for pid in sorted(remaining):
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True,
                       timeout=30, check=False)
    remaining = _wait_for_owned_access_exit(remaining, timeout=15.0)
    if remaining:
        raise RuntimeError(
            "Access process(es) started by fixture generation did not exit: "
            f"{sorted(remaining)}"
        )


def create_startup_code(output: Path, name: str, startup: dict[str, Any]) -> None:
    """Add the VBA module and macros without ever running any of them.

    Build-order discipline: DAO already created the table, so Access.Application opens a
    database that has no macro named ``AutoExec`` (and no ``AutoKeys`` group). The module,
    the named macro, and the AutoKeys group (temporarily named ``AutoKeysControl``) are
    loaded from the text definitions written beside the database. Only then are the
    auto-run-named objects renamed: AutoKeys first, AutoExec last, immediately before the
    database is closed. The database is closed and the application is quit without
    reopening, so startup code cannot run during the build. ``Application.LoadFromText`` is
    used because ``DoCmd.LoadFromText`` is not exposed on the late-bound ``DoCmd`` object;
    it is the same database operation.
    """
    import win32com.client

    module_path = output / "startup_module.bas"
    macro_path = output / "startup_macro.txt"
    manual_macro_path = output / "named_macro.txt"
    autokeys_macro_path = output / "autokeys_macro.txt"
    manual_macro_name = startup["named_macro"]
    autokeys_name = startup["autokeys_macro"]
    autokeys_control = startup["autokeys_control_name"]

    module_path.write_text(
        _STARTUP_MODULE_SOURCE.format(marker=startup["marker_filename"]),
        encoding="utf-8", newline="\r\n",
    )
    macro_path.write_text(_STARTUP_MACRO_SOURCE, encoding="utf-8", newline="\r\n")
    manual_macro_path.write_text(_MANUAL_MARKER_MACRO_SOURCE, encoding="utf-8", newline="\r\n")
    autokeys_macro_path.write_text(_AUTOKEYS_MACRO_SOURCE, encoding="utf-8", newline="\r\n")
    app: Any = None
    try:
        app = win32com.client.Dispatch("Access.Application")
        app.Visible = False
        # Second guard on top of the no-auto-run-object-at-open ordering: forbid macros
        # from running at all while the module and macros are loaded and renamed. Those are
        # database edits, not macro execution, so they still succeed under force-disable.
        app.AutomationSecurity = _MSO_AUTOMATION_SECURITY_FORCE_DISABLE
        app.OpenCurrentDatabase(str(output / name))
        app.LoadFromText(_AC_MODULE, startup["module"], str(module_path))
        app.LoadFromText(_AC_MACRO, startup["control_name"], str(macro_path))
        app.LoadFromText(_AC_MACRO, manual_macro_name, str(manual_macro_path))
        app.LoadFromText(_AC_MACRO, autokeys_control, str(autokeys_macro_path))
        # Auto-run-adjacent names are the very last edits, then the database closes.
        app.DoCmd.Rename(autokeys_name, _AC_MACRO, autokeys_control)
        app.DoCmd.Rename(startup["macro"], _AC_MACRO, startup["control_name"])
        app.CloseCurrentDatabase()
    finally:
        if app is not None:
            # Do not mask a build failure from LoadFromText/Rename with a teardown error.
            try:
                app.Quit()
            except Exception:
                pass
        app = None


def build(output: Path) -> dict[str, Any]:
    output = validate_destination(output)
    if os.name != "nt":
        raise ValueError("fixture generation requires Windows and ACE DAO.")
    import pythoncom
    import win32com.client

    manifest = expected_manifest()
    pythoncom.CoInitialize()
    engine: Any = None
    try:
        # Baseline before any Access instance starts, so only processes this build
        # started are treated as owned and eligible for the post-build assertion.
        msaccess_baseline = _msaccess_pids()
        engine = win32com.client.Dispatch("DAO.DBEngine.120")
        output.mkdir(parents=True)
        canonical_sql: dict[str, dict[str, str]] = {}
        for name in DATABASE_ORDER:
            db: Any = engine.CreateDatabase(str(output / name), LOCALE, 128)
            try:
                canonical_sql[name] = populate(db, name, manifest["databases"][name], output)
            finally:
                db.Close()
                db = None
                # Release any DAO wrapper objects still reachable (TableDefs, Fields,
                # Relations, QueryDefs, and the link that opens the sentinel) before the
                # next CreateDatabase. A live reference keeps the previous file open in
                # the shared default workspace, which can fail the next create with DAO
                # 5003045 "Could not use '<path>'; file already in use."
                gc.collect()
        # Add the executable fixture last, through its own controlled Access phase.
        create_startup_code(output, "startup.accdb", manifest["databases"]["startup.accdb"]["startup"])
        _ensure_no_owned_access(msaccess_baseline)
        marker = output / STARTUP_MARKER_FILENAME
        if marker.exists():
            raise RuntimeError(
                "build-created database executed startup code: marker present at "
                f"{marker}"
            )
        startup_lock = output / "startup.accdb.laccdb"
        if startup_lock.exists():
            raise RuntimeError(f"Access lock file remains beside the built database: {startup_lock}")
        # Hash only after the last create and the startup Access phase.
        inventory: dict[str, Any] = {
            name: {"sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
                   "bytes": (output / name).stat().st_size,
                   "canonical_query_sql": canonical_sql[name]}
            for name in DATABASE_ORDER
        }
        (output / "expected.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (output / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"success": True, "output_dir": str(output), "inventory": inventory}
    finally:
        engine = None
        pythoncom.CoUninitialize()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--describe", action="store_true", help="Print expected manifest JSON; no DAO or file writes")
    mode.add_argument("--build", action="store_true", help="Create fixtures in a new directory; never overwrite")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT,
                        help="New directory under this project's db/fixtures (default: documentation)")
    args = parser.parse_args()
    if args.describe:
        print(json.dumps(expected_manifest(), ensure_ascii=True, indent=2))
    elif args.build:
        try:
            result = build(args.output_dir)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=True, indent=2))
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
