"""CLI entry point with argparse subcommands mapping to use cases."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .application.automation import AutomationUseCases
from .application.database import DatabaseUseCases, Err, Ok
from .application.versioning import VersioningUseCases
from .doctor import doctor_main
from .opencode_config import Ok as OpenCodeOk
from .opencode_config import entry_from, register, remove
from .server import main as mcp_main

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


def _cli_allowed_dirs() -> list[Path]:
    """Read ``ACCESS_MCP_ALLOWED_DIRS`` for the CLI subprocess (semicolon on Windows)."""
    raw = os.environ.get("ACCESS_MCP_ALLOWED_DIRS", "")
    if not raw:
        return []
    sep = ";" if os.name == "nt" else ":"
    return [Path(p).expanduser() for p in raw.split(sep) if p.strip()]


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
# Automation Subcommand Helpers
# =============================================================================


def _automation_use_cases(opts: argparse.Namespace) -> AutomationUseCases:
    """Construct AutomationUseCases honoring --allow-write and --db."""
    readonly = not getattr(opts, "allow_write", False)
    return AutomationUseCases(readonly=readonly, db_path=getattr(opts, "db", None))


def _versioning_use_cases(opts: argparse.Namespace) -> VersioningUseCases:
    """Construct VersioningUseCases honoring --allow-write and --db."""
    readonly = not getattr(opts, "allow_write", False)
    return VersioningUseCases(readonly=readonly, db_path=getattr(opts, "db", None))


# ---- Forms ----


def _cmd_automation_forms_list(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms list"""
    result = uc.list_objects("form")
    return _format_result(result, args.json)


def _cmd_automation_forms_exists(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms exists"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.object_exists("form", args.name)
    return _format_result(result, args.json)


def _cmd_automation_forms_create(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms create"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.create_object("form", args.name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_forms_rename(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms rename"""
    if not args.old_name or not args.new_name:
        print("Error: --old-name and --new-name required", file=sys.stderr)
        return 2
    result = uc.rename_object("form", args.old_name, args.new_name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_forms_delete(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms delete"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.delete_object("form", args.name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_forms_properties(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms properties"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_properties("form", args.name)
    return _format_result(result, args.json)


def _cmd_automation_forms_controls(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms controls"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_controls("form", args.name)
    return _format_result(result, args.json)


def _cmd_automation_forms_add_control(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms add-control"""
    if not args.name or not args.type or not args.control_name:
        print("Error: --name, --type, and --control-name required", file=sys.stderr)
        return 2
    result = uc.add_control(
        "form",
        args.name,
        args.type,
        args.control_name,
        section=getattr(args, "section", 0),
        confirm=args.yes,
    )
    return _format_result(result, args.json)


def _cmd_automation_forms_remove_control(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms remove-control"""
    if not args.name or not args.control_name:
        print("Error: --name and --control-name required", file=sys.stderr)
        return 2
    result = uc.remove_control("form", args.name, args.control_name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_forms_sections(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms sections"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_sections("form", args.name)
    return _format_result(result, args.json)


def _cmd_automation_forms_export(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms export"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.export_to_text("form", args.name)
    return _format_result(result, args.json)


def _cmd_automation_forms_import(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation forms import"""
    if not args.name or not args.text:
        print("Error: --name and --text required", file=sys.stderr)
        return 2
    result = uc.import_from_text("form", args.name, args.text, confirm=args.yes)
    return _format_result(result, args.json)


# ---- Reports ----


def _cmd_automation_reports_list(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports list"""
    result = uc.list_objects("report")
    return _format_result(result, args.json)


def _cmd_automation_reports_exists(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports exists"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.object_exists("report", args.name)
    return _format_result(result, args.json)


def _cmd_automation_reports_create(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports create"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.create_object("report", args.name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_reports_rename(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports rename"""
    if not args.old_name or not args.new_name:
        print("Error: --old-name and --new-name required", file=sys.stderr)
        return 2
    result = uc.rename_object("report", args.old_name, args.new_name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_reports_delete(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports delete"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.delete_object("report", args.name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_reports_properties(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports properties"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_properties("report", args.name)
    return _format_result(result, args.json)


def _cmd_automation_reports_controls(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports controls"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_controls("report", args.name)
    return _format_result(result, args.json)


def _cmd_automation_reports_add_control(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports add-control"""
    if not args.name or not args.type or not args.control_name:
        print("Error: --name, --type, and --control-name required", file=sys.stderr)
        return 2
    result = uc.add_control(
        "report",
        args.name,
        args.type,
        args.control_name,
        section=getattr(args, "section", 0),
        confirm=args.yes,
    )
    return _format_result(result, args.json)


def _cmd_automation_reports_remove_control(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports remove-control"""
    if not args.name or not args.control_name:
        print("Error: --name and --control-name required", file=sys.stderr)
        return 2
    result = uc.remove_control("report", args.name, args.control_name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_reports_sections(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports sections"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_sections("report", args.name)
    return _format_result(result, args.json)


def _cmd_automation_reports_export(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports export"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.export_to_text("report", args.name)
    return _format_result(result, args.json)


def _cmd_automation_reports_import(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation reports import"""
    if not args.name or not args.text:
        print("Error: --name and --text required", file=sys.stderr)
        return 2
    result = uc.import_from_text("report", args.name, args.text, confirm=args.yes)
    return _format_result(result, args.json)


# ---- Macros ----


def _cmd_automation_macros_list(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation macros list"""
    result = uc.get_macros()
    return _format_result(result, args.json)


def _cmd_automation_macros_exists(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation macros exists"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.macro_exists(args.name)
    return _format_result(result, args.json)


def _cmd_automation_macros_create(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation macros create"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.create_macro(args.name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_macros_rename(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation macros rename"""
    if not args.old_name or not args.new_name:
        print("Error: --old-name and --new-name required", file=sys.stderr)
        return 2
    result = uc.rename_macro(args.old_name, args.new_name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_macros_delete(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation macros delete"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.delete_macro(args.name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_macros_run(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation macros run"""
    if not args.yes:
        print("Error: 'macros run' requires --yes", file=sys.stderr)
        return 2
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.run_macro(args.name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_macros_properties(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation macros properties"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_macro_properties(args.name)
    return _format_result(result, args.json)


# ---- VBA ----


def _cmd_automation_vba_modules(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba modules"""
    result = uc.get_modules()
    return _format_result(result, args.json)


def _cmd_automation_vba_get_code(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba get-code"""
    if not args.module:
        print("Error: --module required", file=sys.stderr)
        return 2
    result = uc.get_vba_code(args.module)
    return _format_result(result, args.json)


def _cmd_automation_vba_set_code(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba set-code"""
    if not args.yes:
        print("Error: 'vba set-code' requires --yes", file=sys.stderr)
        return 2
    if not args.module or args.code is None:
        print("Error: --module and --code required", file=sys.stderr)
        return 2
    result = uc.set_vba_code(args.module, args.code, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_vba_add_procedure(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba add-procedure"""
    if not args.module or not args.procedure or not args.code:
        print("Error: --module, --procedure, and --code required", file=sys.stderr)
        return 2
    result = uc.add_vba_procedure(args.module, args.procedure, args.code, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_vba_compile(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba compile"""
    result = uc.compile_vba()
    return _format_result(result, args.json)


def _cmd_automation_vba_save(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba save"""
    result = uc.save_database(confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_vba_module_exists(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba module-exists"""
    if not args.module:
        print("Error: --module required", file=sys.stderr)
        return 2
    result = uc.module_exists(args.module)
    return _format_result(result, args.json)


def _cmd_automation_vba_create_module(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba create-module"""
    if not args.module:
        print("Error: --module required", file=sys.stderr)
        return 2
    result = uc.create_module(args.module, module_type=getattr(args, "module_type", 1), confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_vba_delete_module(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba delete-module"""
    if not args.module:
        print("Error: --module required", file=sys.stderr)
        return 2
    result = uc.delete_module(args.module, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_vba_rename_module(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba rename-module"""
    if not args.old_name or not args.new_name:
        print("Error: --old-name and --new-name required", file=sys.stderr)
        return 2
    result = uc.rename_module(args.old_name, args.new_name, confirm=args.yes)
    return _format_result(result, args.json)


def _cmd_automation_vba_list_procedures(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba list-procedures"""
    if not args.module:
        print("Error: --module required", file=sys.stderr)
        return 2
    result = uc.vba_list_procedures(args.module)
    return _format_result(result, args.json)


def _cmd_automation_vba_get_procedure(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba get-procedure"""
    if not args.module or not args.procedure:
        print("Error: --module and --procedure required", file=sys.stderr)
        return 2
    result = uc.vba_get_procedure(args.module, args.procedure)
    return _format_result(result, args.json)


def _cmd_automation_vba_replace_procedure(args: argparse.Namespace, uc: AutomationUseCases) -> int:
    """Handle: automation vba replace-procedure"""
    if not args.module or not args.procedure or not args.code:
        print("Error: --module, --procedure, and --code required", file=sys.stderr)
        return 2
    result = uc.vba_replace_procedure(args.module, args.procedure, args.code, confirm=args.yes)
    return _format_result(result, args.json)


# =============================================================================
# Versioning Subcommand
# =============================================================================


def _cmd_versioning_export_object(args: argparse.Namespace, uc: VersioningUseCases) -> int:
    """Handle: versioning export-object"""
    result = uc.export_object(args.kind, args.name, args.out_dir)
    return _format_result(result, args.json)


def _cmd_versioning_export_all(args: argparse.Namespace, uc: VersioningUseCases) -> int:
    """Handle: versioning export-all"""
    result = uc.export_all(args.out_dir)
    return _format_result(result, args.json)


def _cmd_versioning_import_object(args: argparse.Namespace, uc: VersioningUseCases) -> int:
    """Handle: versioning import-object"""
    if not args.yes:
        print("Error: --yes required for destructive import-object", file=sys.stderr)
        return 2
    result = uc.import_object(args.kind, args.name, args.file_path, confirm=True)
    return _format_result(result, args.json)


def _cmd_versioning_import_all(args: argparse.Namespace, uc: VersioningUseCases) -> int:
    """Handle: versioning import-all"""
    if not args.yes:
        print("Error: --yes required for destructive import-all", file=sys.stderr)
        return 2
    result = uc.import_all(args.input_dir, confirm=True)
    return _format_result(result, args.json)


def _cmd_versioning_compare(args: argparse.Namespace, uc: VersioningUseCases) -> int:
    """Handle: versioning compare"""
    result = uc.compare(args.export_dir)
    return _format_result(result, args.json)


def _cmd_versioning_export_schema_ddl(args: argparse.Namespace, uc: VersioningUseCases) -> int:
    """Handle: versioning export-schema-ddl"""
    result = uc.export_schema_ddl(args.out_dir)
    return _format_result(result, args.json)


# =============================================================================
# Configure OpenCode Subcommand
# =============================================================================


def _resolve_opencode_target(global_scope: bool, cwd: Path) -> Path:
    """Resolve the target opencode.json path.

    Args:
        global_scope: If True, target user-level config; if False, CWD config.
        cwd: Current working directory.

    Returns:
        Absolute Path to the target opencode.json.
    """
    if global_scope:
        if os.name == "nt":
            return Path(os.environ["USERPROFILE"]) / ".config" / "opencode" / "opencode.json"
        return Path.home() / ".config" / "opencode" / "opencode.json"
    return cwd / "opencode.json"


def _resolve_exe_path(exe_path_arg: str | None = None) -> Path | None:
    """Resolve the absolute path to the ms-access-mcp executable.

    Args:
        exe_path_arg: Optional explicit path provided via --exe-path.

    Returns:
        Absolute Path to the executable, or None if not found.
    """
    if exe_path_arg:
        candidate = Path(exe_path_arg).resolve()
        if not candidate.exists():
            return None
        return candidate

    # Try shutil.which first
    found = shutil.which("ms-access-mcp")
    if found:
        return Path(found).resolve()

    # Platform-specific fallback for uv-installed tools
    if os.name == "nt":
        local_bin = Path.home() / ".local" / "bin" / "ms-access-mcp.exe"
        if local_bin.exists():
            return local_bin.resolve()
    else:
        local_bin = Path.home() / ".local" / "bin" / "ms-access-mcp"
        if local_bin.exists():
            return local_bin.resolve()

    return None


def _run_validator(target_dir: Path) -> str:
    """Run the opencode debug config validator in an isolated sandbox.

    The sandbox prevents opencode debug config (which can mutate opencode.json
    by normalising it, e.g. adding $schema on read) from touching the user's
    real ~/.config/opencode/opencode.json.

    On Windows, bare "opencode" argv does not resolve through PATHEXT for
    .CMD/.BAT via CreateProcessW with shell=False. We resolve the full path
    via shutil.which first, then execute it directly.

    Args:
        target_dir: Directory containing opencode.json.

    Returns:
        "ok" if validation passes, "failed" if validation fails,
        "unavailable" if opencode is not on PATH or executable not found.
    """
    resolved = shutil.which("opencode")
    if resolved is None:
        return "unavailable"

    sandbox_root = None
    try:
        sandbox_root = tempfile.mkdtemp(prefix="opencode_validate_")

        # Determine the target opencode.json to present to the sandboxed validator.
        # Prefer target_dir / "opencode.json", falling back to the user-level config.
        target_config = target_dir / "opencode.json"
        if not target_config.exists():
            if os.name == "nt":
                user_config_path = Path(os.environ.get("USERPROFILE", Path.home())) / ".config" / "opencode" / "opencode.json"
            else:
                user_config_path = Path.home() / ".config" / "opencode" / "opencode.json"
            target_config = user_config_path

        # Create the sandboxed config directory structure.
        sandbox_config_dir = Path(sandbox_root) / ".config" / "opencode"
        sandbox_config_dir.mkdir(parents=True, exist_ok=True)

        # Copy the target config into the sandbox so opencode can read it.
        if target_config.exists():
            shutil.copy2(target_config, sandbox_config_dir / "opencode.json")

        # Create a stub .jsonc to prevent opencode from picking up any real config.
        (sandbox_config_dir / "opencode.jsonc").touch()

        # Build the sandboxed environment: redirect HOME/USERPROFILE/XDG_CONFIG_HOME.
        sandbox_env = {**os.environ}
        sandbox_env["HOME"] = sandbox_root
        sandbox_env["USERPROFILE"] = sandbox_root
        sandbox_env["XDG_CONFIG_HOME"] = str(Path(sandbox_root) / ".config")

        result = subprocess.run(
            [resolved, "debug", "config"],
            cwd=sandbox_root,
            env=sandbox_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return "ok"
        return "failed"
    except FileNotFoundError:
        return "unavailable"
    except subprocess.TimeoutExpired:
        return "failed"
    finally:
        if sandbox_root is not None:
            shutil.rmtree(sandbox_root, ignore_errors=True)


def _cmd_configure_opencode(args: argparse.Namespace) -> int:
    """Handle: configure opencode [--global] [--remove] [--force] [--profile] [--exe-path].

    Exit codes:
        0: success, no-op, or skipped (validator unavailable + warned)
        1: user error (conflict, missing key on remove)
        3: internal failure (executable not found)
    """
    target = _resolve_opencode_target(args.global_scope, Path.cwd())

    # Resolve exe path
    exe_path = _resolve_exe_path(args.exe_path)
    if exe_path is None:
        print("Error: ms-access-mcp executable not found; use --exe-path to specify", file=sys.stderr)
        return 3

    # Build validator — runs real opencode debug config via shutil.which resolution
    def _validator(cfg_path: Path) -> None:
        result = _run_validator(cfg_path.parent)
        if result == "failed":
            raise RuntimeError("opencode debug config validation failed")
        if result == "unavailable":
            import warnings
            warnings.warn("opencode not on PATH; skipping validation", RuntimeWarning)

    if args.remove:
        result = remove(target, validator=_validator if target.exists() else None)
        if isinstance(result, OpenCodeOk):
            print(f"Removed mcp.ms-access from {target}", file=sys.stderr)
            return 0
        else:
            print(f"Error: {result.message}", file=sys.stderr)
            return 1
    else:
        entry = entry_from(exe_path, profile=args.profile or "core")
        result = register(
            target,
            entry,
            force=args.force,
            validator=_validator,
        )
        if isinstance(result, OpenCodeOk):
            print(f"Registered mcp.ms-access in {target}", file=sys.stderr)
            return 0
        else:
            print(f"Error: {result.message}", file=sys.stderr)
            return 1


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

    # configure opencode
    configure_parser = subparsers.add_parser("configure", help="Configure ms-access-mcp integration")
    configure_subparsers = configure_parser.add_subparsers(dest="configure_action")

    opencode_parser = configure_subparsers.add_parser("opencode", help="Register or remove ms-access-mcp in opencode.json")
    opencode_parser.add_argument("--global", action="store_true", dest="global_scope", help="Target user-level opencode.json")
    opencode_parser.add_argument("--remove", action="store_true", help="Remove mcp.ms-access entry instead of registering")
    opencode_parser.add_argument("--force", action="store_true", help="Overwrite existing entry with different command")
    opencode_parser.add_argument("--profile", type=str, default="core", help="MCP profile (default: core)")
    opencode_parser.add_argument("--exe-path", type=str, default=None, help="Absolute path to ms-access-mcp executable")

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

    # mcp — stdio MCP server
    mcp_parser = subparsers.add_parser("mcp", help="Run the MCP server over stdio")
    mcp_parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help=(
            "MCP tool profile to expose. "
            "Token-cost rationale: core <= 30 tools keeps LLM context lean; "
            "additional profiles (automation, maintenance, versioning) are opt-in. "
            "Valid values: core, automation, maintenance, versioning, full. "
            "Default: core. Can also be set via ACCESS_MCP_PROFILE env var."
        ),
    )
    mcp_parser.set_defaults(command="mcp")

    # automation subcommand group
    automation_parser = subparsers.add_parser("automation", help="Automation commands (forms, reports, VBA, macros)")
    automation_subparsers = automation_parser.add_subparsers(dest="automation_action")
    _add_global_flags(automation_parser)

    # ---- automation forms sub-group ----
    forms_parser = automation_subparsers.add_parser("forms", help="Form operations")
    forms_subparsers = forms_parser.add_subparsers(dest="forms_action")
    forms_list = forms_subparsers.add_parser("list", help="List forms")
    forms_list.set_defaults(forms_action="list")
    _add_global_flags(forms_list)

    forms_exists = forms_subparsers.add_parser("exists", help="Check form exists")
    forms_exists.set_defaults(forms_action="exists")
    forms_exists.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_exists)

    forms_create = forms_subparsers.add_parser("create", help="Create form")
    forms_create.set_defaults(forms_action="create")
    forms_create.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_create)

    forms_rename = forms_subparsers.add_parser("rename", help="Rename form")
    forms_rename.set_defaults(forms_action="rename")
    forms_rename.add_argument("--old-name", type=str, required=True)
    forms_rename.add_argument("--new-name", type=str, required=True)
    _add_global_flags(forms_rename)

    forms_delete = forms_subparsers.add_parser("delete", help="Delete form")
    forms_delete.set_defaults(forms_action="delete")
    forms_delete.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_delete)

    forms_properties = forms_subparsers.add_parser("properties", help="Get form properties")
    forms_properties.set_defaults(forms_action="properties")
    forms_properties.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_properties)

    forms_controls = forms_subparsers.add_parser("controls", help="Get form controls")
    forms_controls.set_defaults(forms_action="controls")
    forms_controls.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_controls)

    forms_add_control = forms_subparsers.add_parser("add-control", help="Add control to form")
    forms_add_control.set_defaults(forms_action="add-control")
    forms_add_control.add_argument("--name", type=str, required=True)
    forms_add_control.add_argument("--type", type=str, required=True)
    forms_add_control.add_argument("--control-name", type=str, required=True)
    forms_add_control.add_argument("--section", type=int, default=0)
    _add_global_flags(forms_add_control)

    forms_remove_control = forms_subparsers.add_parser("remove-control", help="Remove control from form")
    forms_remove_control.set_defaults(forms_action="remove-control")
    forms_remove_control.add_argument("--name", type=str, required=True)
    forms_remove_control.add_argument("--control-name", type=str, required=True)
    _add_global_flags(forms_remove_control)

    forms_sections = forms_subparsers.add_parser("sections", help="Get form sections")
    forms_sections.set_defaults(forms_action="sections")
    forms_sections.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_sections)

    forms_export = forms_subparsers.add_parser("export", help="Export form to text")
    forms_export.set_defaults(forms_action="export")
    forms_export.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_export)

    forms_import = forms_subparsers.add_parser("import", help="Import form from text")
    forms_import.set_defaults(forms_action="import")
    forms_import.add_argument("--name", type=str, required=True)
    forms_import.add_argument("--text", type=str, required=True)
    _add_global_flags(forms_import)

    # ---- automation reports sub-group ----
    reports_parser = automation_subparsers.add_parser("reports", help="Report operations")
    reports_subparsers = reports_parser.add_subparsers(dest="reports_action")
    reports_list = reports_subparsers.add_parser("list", help="List reports")
    reports_list.set_defaults(reports_action="list")
    _add_global_flags(reports_list)

    reports_exists = reports_subparsers.add_parser("exists", help="Check report exists")
    reports_exists.set_defaults(reports_action="exists")
    reports_exists.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_exists)

    reports_create = reports_subparsers.add_parser("create", help="Create report")
    reports_create.set_defaults(reports_action="create")
    reports_create.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_create)

    reports_rename = reports_subparsers.add_parser("rename", help="Rename report")
    reports_rename.set_defaults(reports_action="rename")
    reports_rename.add_argument("--old-name", type=str, required=True)
    reports_rename.add_argument("--new-name", type=str, required=True)
    _add_global_flags(reports_rename)

    reports_delete = reports_subparsers.add_parser("delete", help="Delete report")
    reports_delete.set_defaults(reports_action="delete")
    reports_delete.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_delete)

    reports_properties = reports_subparsers.add_parser("properties", help="Get report properties")
    reports_properties.set_defaults(reports_action="properties")
    reports_properties.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_properties)

    reports_controls = reports_subparsers.add_parser("controls", help="Get report controls")
    reports_controls.set_defaults(reports_action="controls")
    reports_controls.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_controls)

    reports_add_control = reports_subparsers.add_parser("add-control", help="Add control to report")
    reports_add_control.set_defaults(reports_action="add-control")
    reports_add_control.add_argument("--name", type=str, required=True)
    reports_add_control.add_argument("--type", type=str, required=True)
    reports_add_control.add_argument("--control-name", type=str, required=True)
    reports_add_control.add_argument("--section", type=int, default=0)
    _add_global_flags(reports_add_control)

    reports_remove_control = reports_subparsers.add_parser("remove-control", help="Remove control from report")
    reports_remove_control.set_defaults(reports_action="remove-control")
    reports_remove_control.add_argument("--name", type=str, required=True)
    reports_remove_control.add_argument("--control-name", type=str, required=True)
    _add_global_flags(reports_remove_control)

    reports_sections = reports_subparsers.add_parser("sections", help="Get report sections")
    reports_sections.set_defaults(reports_action="sections")
    reports_sections.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_sections)

    reports_export = reports_subparsers.add_parser("export", help="Export report to text")
    reports_export.set_defaults(reports_action="export")
    reports_export.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_export)

    reports_import = reports_subparsers.add_parser("import", help="Import report from text")
    reports_import.set_defaults(reports_action="import")
    reports_import.add_argument("--name", type=str, required=True)
    reports_import.add_argument("--text", type=str, required=True)
    _add_global_flags(reports_import)

    # ---- automation macros sub-group ----
    macros_parser = automation_subparsers.add_parser("macros", help="Macro operations")
    macros_subparsers = macros_parser.add_subparsers(dest="macros_action")
    macros_list = macros_subparsers.add_parser("list", help="List macros")
    macros_list.set_defaults(macros_action="list")
    _add_global_flags(macros_list)

    macros_exists = macros_subparsers.add_parser("exists", help="Check macro exists")
    macros_exists.set_defaults(macros_action="exists")
    macros_exists.add_argument("--name", type=str, required=True)
    _add_global_flags(macros_exists)

    macros_create = macros_subparsers.add_parser("create", help="Create macro")
    macros_create.set_defaults(macros_action="create")
    macros_create.add_argument("--name", type=str, required=True)
    _add_global_flags(macros_create)

    macros_rename = macros_subparsers.add_parser("rename", help="Rename macro")
    macros_rename.set_defaults(macros_action="rename")
    macros_rename.add_argument("--old-name", type=str, required=True)
    macros_rename.add_argument("--new-name", type=str, required=True)
    _add_global_flags(macros_rename)

    macros_delete = macros_subparsers.add_parser("delete", help="Delete macro")
    macros_delete.set_defaults(macros_action="delete")
    macros_delete.add_argument("--name", type=str, required=True)
    _add_global_flags(macros_delete)

    macros_run = macros_subparsers.add_parser("run", help="Run macro")
    macros_run.set_defaults(macros_action="run")
    macros_run.add_argument("--name", type=str, required=True)
    _add_global_flags(macros_run)

    macros_properties = macros_subparsers.add_parser("properties", help="Get macro properties")
    macros_properties.set_defaults(macros_action="properties")
    macros_properties.add_argument("--name", type=str, required=True)
    _add_global_flags(macros_properties)

    # ---- automation vba sub-group ----
    vba_parser = automation_subparsers.add_parser("vba", help="VBA module operations")
    vba_subparsers = vba_parser.add_subparsers(dest="vba_action")
    vba_modules = vba_subparsers.add_parser("modules", help="List VBA modules")
    vba_modules.set_defaults(vba_action="modules")
    _add_global_flags(vba_modules)

    vba_get_code = vba_subparsers.add_parser("get-code", help="Get VBA module code")
    vba_get_code.set_defaults(vba_action="get-code")
    vba_get_code.add_argument("--module", type=str, required=True)
    _add_global_flags(vba_get_code)

    vba_set_code = vba_subparsers.add_parser("set-code", help="Set VBA module code")
    vba_set_code.set_defaults(vba_action="set-code")
    vba_set_code.add_argument("--module", type=str, required=True)
    vba_set_code.add_argument("--code", type=str, required=True)
    _add_global_flags(vba_set_code)

    vba_add_procedure = vba_subparsers.add_parser("add-procedure", help="Add VBA procedure")
    vba_add_procedure.set_defaults(vba_action="add-procedure")
    vba_add_procedure.add_argument("--module", type=str, required=True)
    vba_add_procedure.add_argument("--procedure", type=str, required=True)
    vba_add_procedure.add_argument("--code", type=str, required=True)
    _add_global_flags(vba_add_procedure)

    vba_compile = vba_subparsers.add_parser("compile", help="Compile VBA project")
    vba_compile.set_defaults(vba_action="compile")
    _add_global_flags(vba_compile)

    vba_save = vba_subparsers.add_parser("save", help="Save database")
    vba_save.set_defaults(vba_action="save")
    _add_global_flags(vba_save)

    vba_module_exists = vba_subparsers.add_parser("module-exists", help="Check module exists")
    vba_module_exists.set_defaults(vba_action="module-exists")
    vba_module_exists.add_argument("--module", type=str, required=True)
    _add_global_flags(vba_module_exists)

    vba_create_module = vba_subparsers.add_parser("create-module", help="Create VBA module")
    vba_create_module.set_defaults(vba_action="create-module")
    vba_create_module.add_argument("--module", type=str, required=True)
    vba_create_module.add_argument("--module-type", type=int, default=1)
    _add_global_flags(vba_create_module)

    vba_delete_module = vba_subparsers.add_parser("delete-module", help="Delete VBA module")
    vba_delete_module.set_defaults(vba_action="delete-module")
    vba_delete_module.add_argument("--module", type=str, required=True)
    _add_global_flags(vba_delete_module)

    vba_rename_module = vba_subparsers.add_parser("rename-module", help="Rename VBA module")
    vba_rename_module.set_defaults(vba_action="rename-module")
    vba_rename_module.add_argument("--old-name", type=str, required=True)
    vba_rename_module.add_argument("--new-name", type=str, required=True)
    _add_global_flags(vba_rename_module)

    vba_list_procedures = vba_subparsers.add_parser("list-procedures", help="List procedures in module")
    vba_list_procedures.set_defaults(vba_action="list-procedures")
    vba_list_procedures.add_argument("--module", type=str, required=True)
    _add_global_flags(vba_list_procedures)

    vba_get_procedure = vba_subparsers.add_parser("get-procedure", help="Get VBA procedure")
    vba_get_procedure.set_defaults(vba_action="get-procedure")
    vba_get_procedure.add_argument("--module", type=str, required=True)
    vba_get_procedure.add_argument("--procedure", type=str, required=True)
    _add_global_flags(vba_get_procedure)

    vba_replace_procedure = vba_subparsers.add_parser("replace-procedure", help="Replace VBA procedure")
    vba_replace_procedure.set_defaults(vba_action="replace-procedure")
    vba_replace_procedure.add_argument("--module", type=str, required=True)
    vba_replace_procedure.add_argument("--procedure", type=str, required=True)
    vba_replace_procedure.add_argument("--code", type=str, required=True)
    _add_global_flags(vba_replace_procedure)

    # ---- versioning sub-group ----
    versioning_parser = subparsers.add_parser("versioning", help="Versioning commands (export, import, compare)")
    versioning_subparsers = versioning_parser.add_subparsers(dest="versioning_action")
    _add_global_flags(versioning_parser)

    export_object = versioning_subparsers.add_parser("export-object", help="Export a single object to text")
    export_object.set_defaults(versioning_action="export-object")
    export_object.add_argument("--kind", type=str, required=True)
    export_object.add_argument("--name", type=str, required=True)
    export_object.add_argument("--out-dir", type=str, required=True)
    _add_global_flags(export_object)

    export_all = versioning_subparsers.add_parser("export-all", help="Export all objects to directory tree")
    export_all.set_defaults(versioning_action="export-all")
    export_all.add_argument("--out-dir", type=str, required=True)
    _add_global_flags(export_all)

    import_object = versioning_subparsers.add_parser("import-object", help="Import object from text (destructive)")
    import_object.set_defaults(versioning_action="import-object")
    import_object.add_argument("--kind", type=str, required=True)
    import_object.add_argument("--name", type=str, required=True)
    import_object.add_argument("--file-path", type=str, required=True)
    _add_global_flags(import_object)

    import_all = versioning_subparsers.add_parser("import-all", help="Import all objects from directory (destructive)")
    import_all.set_defaults(versioning_action="import-all")
    import_all.add_argument("--input-dir", type=str, required=True)
    _add_global_flags(import_all)

    compare = versioning_subparsers.add_parser("compare", help="Compare exported objects against database")
    compare.set_defaults(versioning_action="compare")
    compare.add_argument("--export-dir", type=str, required=True)
    _add_global_flags(compare)

    export_schema_ddl = versioning_subparsers.add_parser("export-schema-ddl", help="Export schema DDL")
    export_schema_ddl.set_defaults(versioning_action="export-schema-ddl")
    export_schema_ddl.add_argument("--out-dir", type=str, required=True)
    _add_global_flags(export_schema_ddl)

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
        allowed_dirs=_cli_allowed_dirs(),
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

    elif cmd == "mcp":
        # mcp server has its own entry point
        return mcp_main(["--profile", opts.profile] if opts.profile else None)

    elif cmd == "configure":
        action = getattr(opts, "configure_action", None)
        if action == "opencode":
            return _cmd_configure_opencode(opts)
        return 0

    elif cmd == "automation":
        uc = _automation_use_cases(opts)
        action = getattr(opts, "automation_action", None)

        if action == "forms":
            fa = getattr(opts, "forms_action", None)
            if fa == "list":
                return _cmd_automation_forms_list(opts, uc)
            elif fa == "exists":
                return _cmd_automation_forms_exists(opts, uc)
            elif fa == "create":
                return _cmd_automation_forms_create(opts, uc)
            elif fa == "rename":
                return _cmd_automation_forms_rename(opts, uc)
            elif fa == "delete":
                return _cmd_automation_forms_delete(opts, uc)
            elif fa == "properties":
                return _cmd_automation_forms_properties(opts, uc)
            elif fa == "controls":
                return _cmd_automation_forms_controls(opts, uc)
            elif fa == "add-control":
                return _cmd_automation_forms_add_control(opts, uc)
            elif fa == "remove-control":
                return _cmd_automation_forms_remove_control(opts, uc)
            elif fa == "sections":
                return _cmd_automation_forms_sections(opts, uc)
            elif fa == "export":
                return _cmd_automation_forms_export(opts, uc)
            elif fa == "import":
                return _cmd_automation_forms_import(opts, uc)
            return 0

        elif action == "reports":
            ra = getattr(opts, "reports_action", None)
            if ra == "list":
                return _cmd_automation_reports_list(opts, uc)
            elif ra == "exists":
                return _cmd_automation_reports_exists(opts, uc)
            elif ra == "create":
                return _cmd_automation_reports_create(opts, uc)
            elif ra == "rename":
                return _cmd_automation_reports_rename(opts, uc)
            elif ra == "delete":
                return _cmd_automation_reports_delete(opts, uc)
            elif ra == "properties":
                return _cmd_automation_reports_properties(opts, uc)
            elif ra == "controls":
                return _cmd_automation_reports_controls(opts, uc)
            elif ra == "add-control":
                return _cmd_automation_reports_add_control(opts, uc)
            elif ra == "remove-control":
                return _cmd_automation_reports_remove_control(opts, uc)
            elif ra == "sections":
                return _cmd_automation_reports_sections(opts, uc)
            elif ra == "export":
                return _cmd_automation_reports_export(opts, uc)
            elif ra == "import":
                return _cmd_automation_reports_import(opts, uc)
            return 0

        elif action == "macros":
            ma = getattr(opts, "macros_action", None)
            if ma == "list":
                return _cmd_automation_macros_list(opts, uc)
            elif ma == "exists":
                return _cmd_automation_macros_exists(opts, uc)
            elif ma == "create":
                return _cmd_automation_macros_create(opts, uc)
            elif ma == "rename":
                return _cmd_automation_macros_rename(opts, uc)
            elif ma == "delete":
                return _cmd_automation_macros_delete(opts, uc)
            elif ma == "run":
                return _cmd_automation_macros_run(opts, uc)
            elif ma == "properties":
                return _cmd_automation_macros_properties(opts, uc)
            return 0

        elif action == "vba":
            va = getattr(opts, "vba_action", None)
            if va == "modules":
                return _cmd_automation_vba_modules(opts, uc)
            elif va == "get-code":
                return _cmd_automation_vba_get_code(opts, uc)
            elif va == "set-code":
                return _cmd_automation_vba_set_code(opts, uc)
            elif va == "add-procedure":
                return _cmd_automation_vba_add_procedure(opts, uc)
            elif va == "compile":
                return _cmd_automation_vba_compile(opts, uc)
            elif va == "save":
                return _cmd_automation_vba_save(opts, uc)
            elif va == "module-exists":
                return _cmd_automation_vba_module_exists(opts, uc)
            elif va == "create-module":
                return _cmd_automation_vba_create_module(opts, uc)
            elif va == "delete-module":
                return _cmd_automation_vba_delete_module(opts, uc)
            elif va == "rename-module":
                return _cmd_automation_vba_rename_module(opts, uc)
            elif va == "list-procedures":
                return _cmd_automation_vba_list_procedures(opts, uc)
            elif va == "get-procedure":
                return _cmd_automation_vba_get_procedure(opts, uc)
            elif va == "replace-procedure":
                return _cmd_automation_vba_replace_procedure(opts, uc)
            return 0

        return 0

    elif cmd == "versioning":
        uc = _versioning_use_cases(opts)
        action = getattr(opts, "versioning_action", None)

        if action == "export-object":
            return _cmd_versioning_export_object(opts, uc)
        elif action == "export-all":
            return _cmd_versioning_export_all(opts, uc)
        elif action == "import-object":
            return _cmd_versioning_import_object(opts, uc)
        elif action == "import-all":
            return _cmd_versioning_import_all(opts, uc)
        elif action == "compare":
            return _cmd_versioning_compare(opts, uc)
        elif action == "export-schema-ddl":
            return _cmd_versioning_export_schema_ddl(opts, uc)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
