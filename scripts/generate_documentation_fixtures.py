"""Build local synthetic ACE fixtures; never overwrite existing databases.

Use --describe for the expected structural manifest, or --build to create the
three databases. No Access.Application, VBA, startup macro, or saved query is
executed. Linking during construction intentionally accesses the local sentinel;
that activity is not evidence of inspection-time isolation.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "db" / "fixtures"
DEFAULT_OUTPUT = FIXTURE_ROOT / "documentation"
# sentinel.accdb must exist first so linked.accdb can append its link to it.
DATABASE_ORDER = ("sentinel.accdb", "core.accdb", "linked.accdb")
LOCALE = ";LANGID=0x0409;CP=1252;COUNTRY=0"


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
    return {
        "schema": "access.documentation.fixtures/1", "format": "ACE", "startup_execution": "not_authorized",
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
        # Hash only after the last CreateDatabase. Reading a just-closed ACE file inside
        # the close-then-create window increased DAO 5003045 failures in scratch runs
        # (28 of 35 passes with in-loop reads versus 35 of 35 without), so that window
        # stays free of unrelated file I/O.
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
