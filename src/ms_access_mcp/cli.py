"""CLI entry point with argparse subcommands mapping to use cases."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .application.database import DatabaseUseCases, Err, Ok
from .doctor import doctor_main

# =============================================================================
# Result Formatting
# =============================================================================


def _format_result(result: Ok | Err, json_output: bool = False) -> int:
    """Format a result and return exit code.

    Args:
        result: Ok or Err from a use case
        json_output: If True, output JSON

    Returns:
        Exit code: 0 for success, 1 for error
    """
    if isinstance(result, Ok):
        if json_output:
            print(json.dumps({"success": True, "data": result.data, "rowcount": result.rowcount}))
        else:
            if result.data is not None:
                print(result.data)
            print("Success")
        return 0
    else:
        if json_output:
            print(json.dumps({"success": False, "error": result.message}))
        else:
            print(f"Error: {result.message}", file=sys.stderr)
        return 1


def _result_to_dict(result: Ok | Err) -> dict[str, Any]:
    """Convert result to dict for JSON output."""
    if isinstance(result, Ok):
        return {"success": True, "data": result.data, "rowcount": result.rowcount}
    return {"success": False, "error": result.message}


# =============================================================================
# Global Flags
# =============================================================================


def _add_global_flags(parser: argparse.ArgumentParser) -> None:
    """Add global flags to a parser or subparser."""
    parser.add_argument("--db", type=str, help="Path to .accdb file")
    parser.add_argument("--backend", choices=["auto", "dao", "odbc"], default="auto", help="Backend to use")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation for destructive operations")
    parser.add_argument("--allow-write", action="store_true", help="Enable write operations")


# =============================================================================
# CLI Commands
# =============================================================================


def _cmd_db_open(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: db open"""
    if not args.db:
        print("Error: --db required", file=sys.stderr)
        return 2
    result = db.open_database(args.db)
    return _format_result(result, args.json)


def _cmd_db_close(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: db close"""
    result = db.close_database()
    return _format_result(result, args.json)


def _cmd_db_list(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: db list"""
    result = db.list_connections()
    return _format_result(result, args.json)


def _cmd_db_active(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: db active"""
    result = db.get_active()
    return _format_result(result, args.json)


def _cmd_tables(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: tables [--system]"""
    if args.system:
        result = db.get_system_tables()
    else:
        result = db.get_tables()
    return _format_result(result, args.json)


def _cmd_schema(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: schema TABLE"""
    if not args.table:
        print("Error: TABLE argument required", file=sys.stderr)
        return 2
    result = db.get_table_schema(args.table)
    return _format_result(result, args.json)


def _cmd_indexes(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: indexes TABLE"""
    if not args.table:
        print("Error: TABLE argument required", file=sys.stderr)
        return 2
    result = db.get_indexes(args.table)
    return _format_result(result, args.json)


def _cmd_relationships(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: relationships"""
    result = db.get_relationships()
    return _format_result(result, args.json)


def _cmd_stats(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: stats"""
    result = db.get_database_statistics()
    return _format_result(result, args.json)


def _cmd_props(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: props get|set"""
    if args.props_action == "get":
        result = db.get_database_properties()
    else:
        if not args.name:
            print("Error: --name required for props set", file=sys.stderr)
            return 2
        result = db.set_database_property(args.name, args.value, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_queries(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: queries [list|create|set|delete]"""
    if args.queries_action == "list":
        result = db.get_queries()
    elif args.queries_action == "create":
        if not args.name or not args.sql:
            print("Error: --name and --sql required for queries create", file=sys.stderr)
            return 2
        result = db.create_query(args.name, args.sql, confirm=args.yes)
    elif args.queries_action == "set":
        if not args.name or not args.sql:
            print("Error: --name and --sql required for queries set", file=sys.stderr)
            return 2
        result = db.set_query_sql(args.name, args.sql, confirm=args.yes)
    else:  # delete
        if not args.name:
            print("Error: --name required for queries delete", file=sys.stderr)
            return 2
        result = db.delete_query(args.name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_query(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: query SQL [--param P...] [--limit N]"""
    if not args.sql:
        print("Error: SQL argument required", file=sys.stderr)
        return 2
    result = db.query_data(args.sql, params=args.param, limit=args.limit)
    return _format_result(result, args.json)


def _cmd_insert(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: insert TABLE ..."""
    if not args.table:
        print("Error: TABLE argument required", file=sys.stderr)
        return 2
    # Parse column=value pairs
    data = {}
    for pair in args.data or []:
        if "=" in pair:
            key, val = pair.split("=", 1)
            data[key] = val
    if not data:
        print("Error: at least one column=value pair required", file=sys.stderr)
        return 2
    result = db.insert_data(args.table, data, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_update(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: update TABLE ..."""
    if not args.table:
        print("Error: TABLE argument required", file=sys.stderr)
        return 2
    data = {}
    for pair in args.data or []:
        if "=" in pair:
            key, val = pair.split("=", 1)
            data[key] = val
    if not data:
        print("Error: at least one column=value pair required", file=sys.stderr)
        return 2
    result = db.update_data(args.table, data, where=args.where, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_delete(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: delete TABLE ..."""
    if not args.table:
        print("Error: TABLE argument required", file=sys.stderr)
        return 2
    result = db.delete_data(args.table, where=args.where, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_ddl(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: ddl create-table|alter|drop-table|create-index|drop-index|relationship"""
    if args.ddl_action == "create-table":
        if not args.table or not args.columns:
            print("Error: --table and --columns required", file=sys.stderr)
            return 2
        cols = dict(c.split("=") for c in args.columns)
        result = db.create_table(args.table, cols, confirm=args.yes)
    elif args.ddl_action == "alter":
        if not args.table or not args.alter_action:
            print("Error: --table and --action required", file=sys.stderr)
            return 2
        result = db.alter_table(args.table, args.alter_action, column_name=args.column, sql_type=args.type, confirm=args.yes)
    elif args.ddl_action == "drop-table":
        if not args.table:
            print("Error: --table required", file=sys.stderr)
            return 2
        result = db.delete_table(args.table, confirm=args.yes)
    elif args.ddl_action == "create-index":
        if not args.table or not args.index or not args.columns:
            print("Error: --table, --index, and --columns required", file=sys.stderr)
            return 2
        result = db.create_index(args.table, args.index, args.columns, unique=args.unique, confirm=args.yes)
    elif args.ddl_action == "drop-index":
        if not args.table or not args.index:
            print("Error: --table and --index required", file=sys.stderr)
            return 2
        result = db.drop_index(args.table, args.index, confirm=args.yes)
    else:  # relationship
        if not all([args.name, args.source_table, args.source_column, args.dest_table, args.dest_column]):
            print("Error: --name, --source-table, --source-column, --dest-table, --dest-column required", file=sys.stderr)
            return 2
        result = db.create_relationship(args.name, args.source_table, args.source_column, args.dest_table, args.dest_column, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_export(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: export TABLE|--query Q --format csv|json --out PATH"""
    if not args.out:
        print("Error: --out required", file=sys.stderr)
        return 2
    source = args.table or args.query
    if not source:
        print("Error: TABLE or --query required", file=sys.stderr)
        return 2
    result = db.export_data(source, args.out, format=args.format)
    return _format_result(result, args.json)


def _cmd_links(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: links list"""
    result = db.get_linked_tables()
    return _format_result(result, args.json)


# =============================================================================
# Main Entry Point
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(prog="ms-access-mcp")
    parser.add_argument("--version", action="version", version="ms-access-mcp 0.1.0")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # doctor
    doctor_parser = subparsers.add_parser("doctor", help="Run diagnostics")
    doctor_parser.add_argument("--json", action="store_true", help="JSON output")
    doctor_parser.add_argument("--deep", action="store_true", help="Deep checks including Access create+release")
    doctor_parser.set_defaults(func=lambda opts: doctor_main(deep=opts.deep, json_output=opts.json))

    # db subcommands
    db_parser = subparsers.add_parser("db", help="Database connection commands")
    _add_global_flags(db_parser)
    db_subparsers = db_parser.add_subparsers(dest="db_action")

    db_open = db_subparsers.add_parser("open", help="Open a database")
    db_open.set_defaults(db_action="open")
    _add_global_flags(db_open)

    db_close = db_subparsers.add_parser("close", help="Close connection")
    db_close.set_defaults(db_action="close")
    _add_global_flags(db_close)

    db_list = db_subparsers.add_parser("list", help="List connections")
    db_list.set_defaults(db_action="list")
    _add_global_flags(db_list)

    db_active = db_subparsers.add_parser("active", help="Show active connection")
    db_active.set_defaults(db_action="active")
    _add_global_flags(db_active)

    # tables
    tables_parser = subparsers.add_parser("tables", help="List tables")
    tables_parser.add_argument("--system", action="store_true", help="List system tables")
    _add_global_flags(tables_parser)
    tables_parser.set_defaults(func=lambda opts: None)  # placeholder

    # schema
    schema_parser = subparsers.add_parser("schema", help="Get table schema")
    schema_parser.add_argument("table", type=str, help="Table name")
    _add_global_flags(schema_parser)

    # indexes
    indexes_parser = subparsers.add_parser("indexes", help="Get table indexes")
    indexes_parser.add_argument("table", type=str, help="Table name")
    _add_global_flags(indexes_parser)

    # relationships
    rels_parser = subparsers.add_parser("relationships", help="List relationships")
    _add_global_flags(rels_parser)

    # stats
    stats_parser = subparsers.add_parser("stats", help="Get database statistics")
    _add_global_flags(stats_parser)

    # props
    props_parser = subparsers.add_parser("props", help="Database properties")
    props_subparsers = props_parser.add_subparsers(dest="props_action")
    props_get = props_subparsers.add_parser("get", help="Get properties")
    props_get.set_defaults(props_action="get")
    _add_global_flags(props_get)
    props_set = props_subparsers.add_parser("set", help="Set a property")
    props_set.set_defaults(props_action="set")
    props_set.add_argument("--name", type=str, required=True)
    props_set.add_argument("--value", type=str, required=True)
    _add_global_flags(props_set)

    # queries
    queries_parser = subparsers.add_parser("queries", help="Query management")
    queries_subparsers = queries_parser.add_subparsers(dest="queries_action")
    queries_list = queries_subparsers.add_parser("list", help="List queries")
    queries_list.set_defaults(queries_action="list")
    _add_global_flags(queries_list)
    queries_create = queries_subparsers.add_parser("create", help="Create query")
    queries_create.set_defaults(queries_action="create")
    queries_create.add_argument("--name", type=str, required=True)
    queries_create.add_argument("--sql", type=str, required=True)
    _add_global_flags(queries_create)
    queries_set = queries_subparsers.add_parser("set", help="Set query SQL")
    queries_set.set_defaults(queries_action="set")
    queries_set.add_argument("--name", type=str, required=True)
    queries_set.add_argument("--sql", type=str, required=True)
    _add_global_flags(queries_set)
    queries_delete = queries_subparsers.add_parser("delete", help="Delete query")
    queries_delete.set_defaults(queries_action="delete")
    queries_delete.add_argument("--name", type=str, required=True)
    _add_global_flags(queries_delete)

    # query
    query_parser = subparsers.add_parser("query", help="Execute a SELECT query")
    query_parser.add_argument("sql", type=str, help="SQL SELECT statement")
    query_parser.add_argument("--param", nargs="*", help="Query parameters")
    query_parser.add_argument("--limit", type=int, help="Row limit")
    _add_global_flags(query_parser)

    # insert
    insert_parser = subparsers.add_parser("insert", help="Insert data")
    insert_parser.add_argument("table", type=str, help="Table name")
    insert_parser.add_argument("data", nargs="*", help="column=value pairs")
    _add_global_flags(insert_parser)

    # update
    update_parser = subparsers.add_parser("update", help="Update data")
    update_parser.add_argument("table", type=str, help="Table name")
    update_parser.add_argument("data", nargs="*", help="column=value pairs")
    update_parser.add_argument("--where", type=str, help="WHERE clause")
    _add_global_flags(update_parser)

    # delete
    delete_parser = subparsers.add_parser("delete", help="Delete data")
    delete_parser.add_argument("table", type=str, help="Table name")
    delete_parser.add_argument("--where", type=str, help="WHERE clause")
    _add_global_flags(delete_parser)

    # ddl
    ddl_parser = subparsers.add_parser("ddl", help="DDL operations")
    ddl_subparsers = ddl_parser.add_subparsers(dest="ddl_action")
    ddl_create_table = ddl_subparsers.add_parser("create-table", help="Create table")
    ddl_create_table.set_defaults(ddl_action="create-table")
    ddl_create_table.add_argument("--table", type=str, required=True)
    ddl_create_table.add_argument("--columns", nargs="*", help="column=type pairs")
    _add_global_flags(ddl_create_table)
    ddl_alter = ddl_subparsers.add_parser("alter", help="Alter table")
    ddl_alter.set_defaults(ddl_action="alter")
    ddl_alter.add_argument("--table", type=str, required=True)
    ddl_alter.add_argument("--action", choices=["add", "drop"], required=True)
    ddl_alter.add_argument("--column", type=str)
    ddl_alter.add_argument("--type", type=str)
    _add_global_flags(ddl_alter)
    ddl_drop_table = ddl_subparsers.add_parser("drop-table", help="Drop table")
    ddl_drop_table.set_defaults(ddl_action="drop-table")
    ddl_drop_table.add_argument("--table", type=str, required=True)
    _add_global_flags(ddl_drop_table)
    ddl_create_index = ddl_subparsers.add_parser("create-index", help="Create index")
    ddl_create_index.set_defaults(ddl_action="create-index")
    ddl_create_index.add_argument("--table", type=str, required=True)
    ddl_create_index.add_argument("--index", type=str, required=True)
    ddl_create_index.add_argument("--columns", nargs="*", required=True)
    ddl_create_index.add_argument("--unique", action="store_true")
    _add_global_flags(ddl_create_index)
    ddl_drop_index = ddl_subparsers.add_parser("drop-index", help="Drop index")
    ddl_drop_index.set_defaults(ddl_action="drop-index")
    ddl_drop_index.add_argument("--table", type=str, required=True)
    ddl_drop_index.add_argument("--index", type=str, required=True)
    _add_global_flags(ddl_drop_index)
    ddl_relationship = ddl_subparsers.add_parser("relationship", help="Create relationship")
    ddl_relationship.set_defaults(ddl_action="relationship")
    ddl_relationship.add_argument("--name", type=str, required=True)
    ddl_relationship.add_argument("--source-table", type=str, required=True)
    ddl_relationship.add_argument("--source-column", type=str, required=True)
    ddl_relationship.add_argument("--dest-table", type=str, required=True)
    ddl_relationship.add_argument("--dest-column", type=str, required=True)
    _add_global_flags(ddl_relationship)

    # export
    export_parser = subparsers.add_parser("export", help="Export data")
    export_group = export_parser.add_mutually_exclusive_group(required=True)
    export_group.add_argument("table", type=str, nargs="?", help="Table name")
    export_group.add_argument("--query", type=str, help="Query name")
    export_parser.add_argument("--format", choices=["csv", "json"], default="csv")
    export_parser.add_argument("--out", type=str, required=True, help="Output file path")
    _add_global_flags(export_parser)

    # links
    links_parser = subparsers.add_parser("links", help="Linked table commands")
    links_subparsers = links_parser.add_subparsers(dest="links_action")
    links_list = links_subparsers.add_parser("list", help="List linked tables")
    links_list.set_defaults(links_action="list")
    _add_global_flags(links_list)

    # Parse and execute
    opts = parser.parse_args()

    # Handle doctor separately (doesn't need db)
    if hasattr(opts, "func") and opts.command == "doctor":
        return opts.func(opts)

    # Build database use cases
    readonly = not getattr(opts, "allow_write", False)
    db = DatabaseUseCases(
        backend=getattr(opts, "backend", "auto"),
        readonly=readonly,
    )

    # Auto-open database if --db provided
    if getattr(opts, "db", None):
        result = db.open_database(opts.db)
        if isinstance(result, Err):
            return _format_result(result, getattr(opts, "json", False))

    # Dispatch commands
    cmd = getattr(opts, "command", None)

    if cmd == "db":
        action = getattr(opts, "db_action", None)
        if action == "open":
            return _cmd_db_open(opts, db)
        elif action == "close":
            return _cmd_db_close(opts, db)
        elif action == "list":
            return _cmd_db_list(opts, db)
        elif action == "active":
            return _cmd_db_active(opts, db)
        return 0

    elif cmd == "tables":
        return _cmd_tables(opts, db)

    elif cmd == "schema":
        return _cmd_schema(opts, db)

    elif cmd == "indexes":
        return _cmd_indexes(opts, db)

    elif cmd == "relationships":
        return _cmd_relationships(opts, db)

    elif cmd == "stats":
        return _cmd_stats(opts, db)

    elif cmd == "props":
        return _cmd_props(opts, db)

    elif cmd == "queries":
        return _cmd_queries(opts, db)

    elif cmd == "query":
        return _cmd_query(opts, db)

    elif cmd == "insert":
        return _cmd_insert(opts, db)

    elif cmd == "update":
        return _cmd_update(opts, db)

    elif cmd == "delete":
        return _cmd_delete(opts, db)

    elif cmd == "ddl":
        return _cmd_ddl(opts, db)

    elif cmd == "export":
        return _cmd_export(opts, db)

    elif cmd == "links":
        return _cmd_links(opts, db)

    return 0


if __name__ == "__main__":
    sys.exit(main())
