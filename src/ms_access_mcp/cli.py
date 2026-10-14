"""CLI entry point with argparse subcommands mapping to use cases."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .application.automation import AutomationUseCases
from .application.database import DatabaseUseCases, Err
from .application.documentation import EvidenceDoc
from .application.documentation_inspect import DocumentationInspectService, OdbcInspectionPort, open_inspection_port
from .application.documentation_render import ReportDoc, render_document
from .application.documentation_save import DocumentationSaveService, DocumentationWritePolicy
from .application.maintenance import MaintenanceUseCases
from .application.protocols import AutomationUseCasesProtocol, MaintenanceUseCasesProtocol, VersioningUseCasesProtocol
from .application.versioning import VersioningUseCases
from .cli_format import cli_allowed_dirs, format_result
from .credentials import (
    list_entries,
    list_profiles,
    remove_entry,
    remove_profile,
    write_entry,
    write_profile,
)
from .doctor import doctor_main
from .opencode_config import Ok as OpenCodeOk
from .opencode_config import entry_from, register, remove
from .profiles import PROFILES
from .safety import allowed_dirs_from_env

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
    return format_result(result, args.json)


def _cmd_db_close(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: db close"""
    result = db.close_database()
    return format_result(result, args.json)


def _cmd_db_list(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: db list"""
    result = db.list_connections()
    return format_result(result, args.json)


def _cmd_db_active(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: db active"""
    result = db.get_active()
    return format_result(result, args.json)


def _cmd_tables(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: tables [--system]"""
    if args.system:
        result = db.get_system_tables()
    else:
        result = db.get_tables()
    return format_result(result, args.json)


def _cmd_schema(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: schema TABLE"""
    if not args.table:
        print("Error: TABLE argument required", file=sys.stderr)
        return 2
    result = db.get_table_schema(args.table)
    return format_result(result, args.json)


def _cmd_indexes(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: indexes TABLE"""
    if not args.table:
        print("Error: TABLE argument required", file=sys.stderr)
        return 2
    result = db.get_indexes(args.table)
    return format_result(result, args.json)


def _cmd_relationships(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: relationships"""
    result = db.get_relationships()
    return format_result(result, args.json)


def _cmd_stats(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: stats"""
    result = db.get_database_statistics()
    return format_result(result, args.json)


def _cmd_props(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: props get|set"""
    if args.props_action == "get":
        result = db.get_database_properties()
    else:
        if not args.name:
            print("Error: --name required for props set", file=sys.stderr)
            return 2
        result = db.set_database_property(args.name, args.value, confirm=args.yes)
    return format_result(result, args.json)


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
    return format_result(result, args.json)


def _cmd_query(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: query SQL [--param P...] [--limit N]"""
    if not args.sql:
        print("Error: SQL argument required", file=sys.stderr)
        return 2
    result = db.query_data(args.sql, params=args.param, limit=args.limit)
    return format_result(result, args.json)


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
    return format_result(result, args.json)


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
    return format_result(result, args.json)


def _cmd_delete(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: delete TABLE ..."""
    if not args.table:
        print("Error: TABLE argument required", file=sys.stderr)
        return 2
    result = db.delete_data(args.table, where=args.where, confirm=args.yes)
    return format_result(result, args.json)


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
    return format_result(result, args.json)


def _cmd_export(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: export TABLE|--query Q --format csv|json|xlsx --sheet-name SHEET --out PATH"""
    if not args.out:
        print("Error: --out required", file=sys.stderr)
        return 2
    source = args.table or args.query
    if not source:
        print("Error: TABLE or --query required", file=sys.stderr)
        return 2
    result = db.export_data(
        source,
        args.out,
        format=args.format,
        sheet_name=args.sheet_name,
    )
    return format_result(result, args.json)


def _cmd_links(args: argparse.Namespace, db: DatabaseUseCases) -> int:
    """Handle: links list"""
    result = db.get_linked_tables()
    return format_result(result, args.json)


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
    return VersioningUseCases(
        readonly=readonly,
        db_path=getattr(opts, "db", None),
        allowed_dirs=allowed_dirs_from_env(),
    )


def _maintenance_use_cases(opts: argparse.Namespace) -> MaintenanceUseCases:
    """Construct MaintenanceUseCases honoring --allow-write and --db."""
    readonly = not getattr(opts, "allow_write", False)
    return MaintenanceUseCases(
        readonly=readonly,
        db_path=getattr(opts, "db", None),
        allowed_dirs=allowed_dirs_from_env(),
    )


# ---- Forms ----


def _cmd_automation_forms_list(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation forms list"""
    result = uc.list_objects("form")
    return format_result(result, args.json)


def _cmd_automation_forms_exists(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation forms exists"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.object_exists("form", args.name)
    return format_result(result, args.json)


def _cmd_automation_forms_create(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation forms create"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.create_object("form", args.name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_forms_rename(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation forms rename"""
    if not args.old_name or not args.new_name:
        print("Error: --old-name and --new-name required", file=sys.stderr)
        return 2
    result = uc.rename_object("form", args.old_name, args.new_name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_forms_delete(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation forms delete"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.delete_object("form", args.name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_forms_properties(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation forms properties"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_properties("form", args.name)
    return format_result(result, args.json)


def _cmd_automation_forms_controls(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation forms controls"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_controls("form", args.name)
    return format_result(result, args.json)


def _cmd_automation_forms_add_control(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
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
    return format_result(result, args.json)


def _cmd_automation_forms_remove_control(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation forms remove-control"""
    if not args.name or not args.control_name:
        print("Error: --name and --control-name required", file=sys.stderr)
        return 2
    result = uc.remove_control("form", args.name, args.control_name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_forms_sections(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation forms sections"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_sections("form", args.name)
    return format_result(result, args.json)


def _cmd_automation_forms_export(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation forms export"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.export_to_text("form", args.name)
    return format_result(result, args.json)


def _cmd_automation_forms_import(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation forms import"""
    if not args.name or not args.text:
        print("Error: --name and --text required", file=sys.stderr)
        return 2
    result = uc.import_from_text("form", args.name, args.text, confirm=args.yes)
    return format_result(result, args.json)


# ---- Reports ----


def _cmd_automation_reports_list(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation reports list"""
    result = uc.list_objects("report")
    return format_result(result, args.json)


def _cmd_automation_reports_exists(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation reports exists"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.object_exists("report", args.name)
    return format_result(result, args.json)


def _cmd_automation_reports_create(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation reports create"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.create_object("report", args.name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_reports_rename(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation reports rename"""
    if not args.old_name or not args.new_name:
        print("Error: --old-name and --new-name required", file=sys.stderr)
        return 2
    result = uc.rename_object("report", args.old_name, args.new_name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_reports_delete(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation reports delete"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.delete_object("report", args.name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_reports_properties(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation reports properties"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_properties("report", args.name)
    return format_result(result, args.json)


def _cmd_automation_reports_controls(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation reports controls"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_controls("report", args.name)
    return format_result(result, args.json)


def _cmd_automation_reports_add_control(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
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
    return format_result(result, args.json)


def _cmd_automation_reports_remove_control(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation reports remove-control"""
    if not args.name or not args.control_name:
        print("Error: --name and --control-name required", file=sys.stderr)
        return 2
    result = uc.remove_control("report", args.name, args.control_name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_reports_sections(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation reports sections"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_sections("report", args.name)
    return format_result(result, args.json)


def _cmd_automation_reports_export(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation reports export"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.export_to_text("report", args.name)
    return format_result(result, args.json)


def _cmd_automation_reports_import(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation reports import"""
    if not args.name or not args.text:
        print("Error: --name and --text required", file=sys.stderr)
        return 2
    result = uc.import_from_text("report", args.name, args.text, confirm=args.yes)
    return format_result(result, args.json)


# ---- Macros ----


def _cmd_automation_macros_list(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation macros list"""
    result = uc.get_macros()
    return format_result(result, args.json)


def _cmd_automation_macros_exists(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation macros exists"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.macro_exists(args.name)
    return format_result(result, args.json)


def _cmd_automation_macros_create(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation macros create"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.create_macro(args.name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_macros_rename(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation macros rename"""
    if not args.old_name or not args.new_name:
        print("Error: --old-name and --new-name required", file=sys.stderr)
        return 2
    result = uc.rename_macro(args.old_name, args.new_name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_macros_delete(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation macros delete"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.delete_macro(args.name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_macros_run(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation macros run"""
    if not args.yes:
        print("Error: 'macros run' requires --yes", file=sys.stderr)
        return 2
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.run_macro(args.name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_macros_properties(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation macros properties"""
    if not args.name:
        print("Error: --name required", file=sys.stderr)
        return 2
    result = uc.get_macro_properties(args.name)
    return format_result(result, args.json)


# ---- VBA ----


def _cmd_automation_vba_modules(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba modules"""
    result = uc.get_modules()
    return format_result(result, args.json)


def _cmd_automation_vba_get_code(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba get-code"""
    if not args.module:
        print("Error: --module required", file=sys.stderr)
        return 2
    result = uc.get_vba_code(args.module)
    return format_result(result, args.json)


def _cmd_automation_vba_set_code(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba set-code"""
    if not args.yes:
        print("Error: 'vba set-code' requires --yes", file=sys.stderr)
        return 2
    if not args.module or args.code is None:
        print("Error: --module and --code required", file=sys.stderr)
        return 2
    result = uc.set_vba_code(args.module, args.code, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_vba_add_procedure(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba add-procedure"""
    if not args.module or not args.procedure or not args.code:
        print("Error: --module, --procedure, and --code required", file=sys.stderr)
        return 2
    result = uc.add_vba_procedure(args.module, args.procedure, args.code, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_vba_compile(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba compile"""
    result = uc.compile_vba()
    return format_result(result, args.json)


def _cmd_automation_vba_save(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba save"""
    result = uc.save_database(confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_vba_module_exists(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba module-exists"""
    if not args.module:
        print("Error: --module required", file=sys.stderr)
        return 2
    result = uc.module_exists(args.module)
    return format_result(result, args.json)


def _cmd_automation_vba_create_module(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba create-module"""
    if not args.module:
        print("Error: --module required", file=sys.stderr)
        return 2
    result = uc.create_module(args.module, module_type=getattr(args, "module_type", 1), confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_vba_delete_module(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba delete-module"""
    if not args.module:
        print("Error: --module required", file=sys.stderr)
        return 2
    result = uc.delete_module(args.module, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_vba_rename_module(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba rename-module"""
    if not args.old_name or not args.new_name:
        print("Error: --old-name and --new-name required", file=sys.stderr)
        return 2
    result = uc.rename_module(args.old_name, args.new_name, confirm=args.yes)
    return format_result(result, args.json)


def _cmd_automation_vba_list_procedures(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba list-procedures"""
    if not args.module:
        print("Error: --module required", file=sys.stderr)
        return 2
    result = uc.vba_list_procedures(args.module)
    return format_result(result, args.json)


def _cmd_automation_vba_get_procedure(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba get-procedure"""
    if not args.module or not args.procedure:
        print("Error: --module and --procedure required", file=sys.stderr)
        return 2
    result = uc.vba_get_procedure(args.module, args.procedure)
    return format_result(result, args.json)


def _cmd_automation_vba_replace_procedure(args: argparse.Namespace, uc: AutomationUseCasesProtocol) -> int:
    """Handle: automation vba replace-procedure"""
    if not args.module or not args.procedure or not args.code:
        print("Error: --module, --procedure, and --code required", file=sys.stderr)
        return 2
    result = uc.vba_replace_procedure(args.module, args.procedure, args.code, confirm=args.yes)
    return format_result(result, args.json)


# =============================================================================
# Versioning Subcommand
# =============================================================================


def _cmd_versioning_export_object(args: argparse.Namespace, uc: VersioningUseCasesProtocol) -> int:
    """Handle: versioning export-object"""
    result = uc.export_object(args.kind, args.name, args.out_dir)
    return format_result(result, args.json)


def _cmd_versioning_export_all(args: argparse.Namespace, uc: VersioningUseCasesProtocol) -> int:
    """Handle: versioning export-all"""
    result = uc.export_all(args.out_dir)
    return format_result(result, args.json)


def _cmd_versioning_import_object(args: argparse.Namespace, uc: VersioningUseCasesProtocol) -> int:
    """Handle: versioning import-object"""
    if not args.yes:
        print("Error: --yes required for destructive import-object", file=sys.stderr)
        return 2
    result = uc.import_object(args.kind, args.name, args.file_path, confirm=True)
    return format_result(result, args.json)


def _cmd_versioning_import_all(args: argparse.Namespace, uc: VersioningUseCasesProtocol) -> int:
    """Handle: versioning import-all"""
    if not args.yes:
        print("Error: --yes required for destructive import-all", file=sys.stderr)
        return 2
    result = uc.import_all(args.input_dir, confirm=True)
    return format_result(result, args.json)


def _cmd_versioning_compare(args: argparse.Namespace, uc: VersioningUseCasesProtocol) -> int:
    """Handle: versioning compare"""
    result = uc.compare(args.export_dir)
    return format_result(result, args.json)


def _cmd_versioning_export_schema_ddl(args: argparse.Namespace, uc: VersioningUseCasesProtocol) -> int:
    """Handle: versioning export-schema-ddl"""
    result = uc.export_schema_ddl(args.out_dir)
    return format_result(result, args.json)


# =============================================================================
# Maintenance Subcommand
# =============================================================================


def _cmd_maintenance_backup(args: argparse.Namespace, uc: MaintenanceUseCasesProtocol) -> int:
    """Handle: maintenance backup"""
    result = uc.backup(args.path, retention=getattr(args, "retention", None))
    return format_result(result, args.json)


def _cmd_maintenance_dev_copy_status(args: argparse.Namespace, uc: MaintenanceUseCasesProtocol) -> int:
    """Handle: maintenance dev-copy-status"""
    result = uc.dev_copy_status()
    return format_result(result, args.json)


def _cmd_maintenance_dev_copy_create(args: argparse.Namespace, uc: MaintenanceUseCasesProtocol) -> int:
    """Handle: maintenance dev-copy-create"""
    result = uc.create_dev_copy(source=getattr(args, "source", None))
    return format_result(result, args.json)


def _cmd_maintenance_dev_copy_deploy(args: argparse.Namespace, uc: MaintenanceUseCasesProtocol) -> int:
    """Handle: maintenance dev-copy-deploy"""
    if not args.yes:
        print("Error: --yes required for destructive dev-copy-deploy", file=sys.stderr)
        return 2
    result = uc.deploy_dev_copy(args.dev_path, args.target, confirm=True)
    return format_result(result, args.json)


def _cmd_maintenance_compact_repair(args: argparse.Namespace, uc: MaintenanceUseCasesProtocol) -> int:
    """Handle: maintenance compact-repair"""
    if not args.yes:
        print("Error: --yes required for destructive compact-repair", file=sys.stderr)
        return 2
    result = uc.compact_repair(args.path, args.mode, confirm=True)
    return format_result(result, args.json)


def _cmd_maintenance_diagnose(args: argparse.Namespace, uc: MaintenanceUseCasesProtocol) -> int:
    """Handle: maintenance diagnose"""
    result = uc.diagnose_environment()
    return format_result(result, args.json)


def _cmd_maintenance_recover(args: argparse.Namespace, uc: MaintenanceUseCasesProtocol) -> int:
    """Handle: maintenance recover"""
    if not args.yes:
        print("Error: --yes required for destructive recover", file=sys.stderr)
        return 2
    result = uc.recover_access(confirm=True)
    return format_result(result, args.json)


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
    """Handle: configure opencode [--global] [--remove] [--force] [--profile] [--exe-path|--python-mode].

    Exit codes:
        0: success, no-op, or skipped (validator unavailable + warned)
        1: user error (conflict, missing key on remove)
        3: internal failure (executable not found)
    """
    target = _resolve_opencode_target(args.global_scope, Path.cwd())

    # Resolve exe path — python_mode uses sys.executable directly
    if getattr(args, "python_mode", False):
        exe_path = Path(sys.executable).resolve()
        module_mode = True
    else:
        exe_path = _resolve_exe_path(args.exe_path)
        module_mode = False

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
        entry = entry_from(exe_path, profile=args.profile or "core", module_mode=module_mode)
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
# Credentials Subcommands
# =============================================================================


def _FORBIDDEN_IN_PASSWORD() -> frozenset[str]:
    """Characters forbidden in passwords (ODBC connection-string safety)."""
    return frozenset({";", "{", "}"})


def _cmd_credentials_set(args: argparse.Namespace) -> int:
    """Handle: credentials set --db <path> [--local] [--yes].

    Exit codes:
        0: success
        2: user error (empty, mismatch, forbidden chars, unconfirmed overwrite)
    """
    db_path = args.db_path

    # Prompt twice for confirmation
    try:
        pw1 = getpass.getpass(f"Enter password for {db_path}: ")
    except EOFError:
        print("Error: cannot read password (EOF)", file=sys.stderr)
        return 2

    if not pw1:
        print("Error: password may not be empty", file=sys.stderr)
        return 2

    forbidden = _FORBIDDEN_IN_PASSWORD()
    if any(c in pw1 for c in forbidden):
        print(
            f"Error: password may not contain any of: {' '.join(sorted(forbidden))} "
            "(ODBC connection-string safety; edit the file manually if needed)",
            file=sys.stderr,
        )
        return 2

    try:
        pw2 = getpass.getpass("Confirm password: ")
    except EOFError:
        print("Error: cannot read confirmation (EOF)", file=sys.stderr)
        return 2

    if pw1 != pw2:
        print("Error: passwords do not match", file=sys.stderr)
        return 2

    # Check for existing entry (require --yes or confirmation)
    existing = [p for p, _ in list_entries() if str(Path(db_path).resolve()) == str(p.resolve())]
    if existing and not getattr(args, "yes", False):
        confirm = input(f"Overwrite existing entry for {db_path}? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return 2

    try:
        write_entry(db_path, pw1, local=getattr(args, "local", False))
    except OSError as e:
        print(f"Error: cannot write credentials file: {e}", file=sys.stderr)
        return 2

    print(f"Saved credentials for {db_path}", file=sys.stderr)
    return 0


def _cmd_credentials_list(_args: argparse.Namespace) -> int:
    """Handle: credentials list.

    Exit codes:
        0: always (malformed files reported as invalid)
    """
    entries = list_entries()
    if not entries:
        print("No credentials entries found.", file=sys.stderr)
        return 0

    for path, source in entries:
        print(f"[{source}] {path}", file=sys.stderr)
    return 0


def _cmd_credentials_remove(args: argparse.Namespace) -> int:
    """Handle: credentials remove --db <path> [--local].

    Exit codes:
        0: always (missing entry is a no-op)
    """
    removed = remove_entry(args.db_path, local=getattr(args, "local", False))
    if removed:
        print(f"Removed credentials for {args.db_path}", file=sys.stderr)
    else:
        print(f"No entry found for {args.db_path} (no-op)", file=sys.stderr)
    return 0


# =============================================================================
# credentials profile subcommands (step 6)
# =============================================================================


def _cmd_credentials_profile_set(args: argparse.Namespace) -> int:
    """Handle: credentials profile set --name <name>.

    Exit codes:
        0: success
        2: user error (empty, mismatch, invalid name, unconfirmed overwrite)
    """
    name = args.name

    # Validate name charset: [A-Za-z0-9_.-]
    invalid_chars = frozenset(name) - frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")
    if invalid_chars:
        print(
            f"Error: profile name may only contain A-Za-z0-9_.-; "
            f"found disallowed characters: {sorted(invalid_chars)}",
            file=sys.stderr,
        )
        return 2

    # Prompt twice for the connection string
    try:
        conn1 = getpass.getpass(f"Enter connection string for '{name}': ")
    except EOFError:
        print("Error: cannot read connection string (EOF)", file=sys.stderr)
        return 2

    if not conn1:
        print("Error: connection string may not be empty", file=sys.stderr)
        return 2

    try:
        conn2 = getpass.getpass("Confirm connection string: ")
    except EOFError:
        print("Error: cannot read confirmation (EOF)", file=sys.stderr)
        return 2

    if conn1 != conn2:
        print("Error: connection strings do not match", file=sys.stderr)
        return 2

    # Check for existing profile (require --yes or confirmation)
    existing = list_profiles()
    if name in existing and not getattr(args, "yes", False):
        confirm = input(f"Overwrite existing profile '{name}'? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return 2

    try:
        write_profile(name, conn1)
    except OSError as e:
        print(f"Error: cannot write credentials file: {e}", file=sys.stderr)
        return 2

    from .credentials import _global_credentials_path

    cred_path = _global_credentials_path()
    print(f"Saved profile '{name}' to {cred_path}", file=sys.stderr)
    return 0


def _cmd_credentials_profile_list(_args: argparse.Namespace) -> int:
    """Handle: credentials profile list.

    Exit codes:
        0: always
    """
    profiles = list_profiles()
    if not profiles:
        print("No credential profiles found.", file=sys.stderr)
        return 0

    for name in profiles:
        print(name, file=sys.stderr)
    return 0


def _cmd_credentials_profile_remove(args: argparse.Namespace) -> int:
    """Handle: credentials profile remove --name <name>.

    Exit codes:
        0: always (missing profile is a no-op)
    """
    name = args.name
    removed = remove_profile(name)
    if removed:
        print(f"Removed profile '{name}'", file=sys.stderr)
    else:
        print(f"No profile named '{name}' (no-op)", file=sys.stderr)
    return 0


# =============================================================================
# Main Entry Point
# =============================================================================


# =============================================================================
# Pure Parser Constructor
# =============================================================================


# =============================================================================
# Family Builders — each builds one subcommand family
# =============================================================================

def _build_doctor_parser(sub: argparse._SubParsersAction) -> None:
    """Add doctor subcommand family."""
    doctor_parser = sub.add_parser("doctor", help="Run diagnostics")
    doctor_parser.add_argument("--json", action="store_true", help="JSON output")
    doctor_parser.add_argument("--deep", action="store_true", help="Deep checks including Access create+release")
    doctor_parser.set_defaults(func=lambda opts: doctor_main(deep=opts.deep, json_output=opts.json))


def _build_configure_parser(sub: argparse._SubParsersAction) -> None:
    """Add configure > opencode subcommand family."""
    configure_parser = sub.add_parser("configure", help="Configure ms-access-mcp integration")
    configure_subparsers = configure_parser.add_subparsers(dest="configure_action")

    opencode_parser = configure_subparsers.add_parser("opencode", help="Register or remove ms-access-mcp in opencode.json")
    opencode_parser.add_argument("--global", action="store_true", dest="global_scope", help="Target user-level opencode.json")
    opencode_parser.add_argument("--remove", action="store_true", help="Remove mcp.ms-access entry instead of registering")
    opencode_parser.add_argument("--force", action="store_true", help="Overwrite existing entry with different command")
    opencode_parser.add_argument("--profile", type=str, default="core", help="MCP profile (default: core)")
    exe_path_group = opencode_parser.add_mutually_exclusive_group()
    exe_path_group.add_argument("--exe-path", type=str, default=None, help="Absolute path to ms-access-mcp executable")
    exe_path_group.add_argument("--python-mode", action="store_true", help="Use sys.executable with -m ms_access_mcp (portable distribution)")
    opencode_parser.set_defaults(func=_cmd_configure_opencode)


def _build_credentials_parser(sub: argparse._SubParsersAction) -> None:
    """Add credentials subcommand family (set, list, remove, profile)."""
    credentials_parser = sub.add_parser("credentials", help="Manage server-side database credentials")
    cred_subparsers = credentials_parser.add_subparsers(dest="credentials_action")

    cred_set = cred_subparsers.add_parser("set", help="Store credentials for a database")
    cred_set.add_argument("--db", type=str, required=True, dest="db_path", help="Path to the Access database")
    cred_set.add_argument("--local", action="store_true", help="Store in <db_dir>/.ms-access-mcp.json instead of global file")
    cred_set.add_argument("--yes", action="store_true", help="Skip overwrite confirmation")
    cred_set.set_defaults(func=_cmd_credentials_set)

    cred_list = cred_subparsers.add_parser("list", help="List stored credentials")
    cred_list.set_defaults(func=_cmd_credentials_list)

    cred_remove = cred_subparsers.add_parser("remove", help="Remove credentials for a database")
    cred_remove.add_argument("--db", type=str, required=True, dest="db_path", help="Path to the Access database")
    cred_remove.add_argument("--local", action="store_true", help="Remove from <db_dir>/.ms-access-mcp.json instead of global file")
    cred_remove.set_defaults(func=_cmd_credentials_remove)

    cred_profile = cred_subparsers.add_parser("profile", help="Manage named credential profiles for external databases")
    cred_profile_subparsers = cred_profile.add_subparsers(dest="credentials_profile_action")

    cred_profile_set = cred_profile_subparsers.add_parser("set", help="Store a named credential profile")
    cred_profile_set.add_argument("--name", type=str, required=True, help="Profile name (A-Za-z0-9_.-)")
    cred_profile_set.add_argument("--yes", action="store_true", help="Skip overwrite confirmation")
    cred_profile_set.set_defaults(func=_cmd_credentials_profile_set)

    cred_profile_list = cred_profile_subparsers.add_parser("list", help="List stored credential profile names")
    cred_profile_list.set_defaults(func=_cmd_credentials_profile_list)

    cred_profile_remove = cred_profile_subparsers.add_parser("remove", help="Remove a credential profile")
    cred_profile_remove.add_argument("--name", type=str, required=True, help="Profile name to remove")
    cred_profile_remove.set_defaults(func=_cmd_credentials_profile_remove)


def _build_db_parser(sub: argparse._SubParsersAction) -> None:
    """Add db subcommand family (open, close, list, active)."""
    db_parser = sub.add_parser("db", help="Database connection commands")
    _add_global_flags(db_parser)
    db_subparsers = db_parser.add_subparsers(dest="db_action")

    db_open = db_subparsers.add_parser("open", help="Open a database")
    db_open.set_defaults(db_action="open", func=_with_db(_cmd_db_open))
    _add_global_flags(db_open)

    db_close = db_subparsers.add_parser("close", help="Close connection")
    db_close.set_defaults(db_action="close", func=_with_db(_cmd_db_close))
    _add_global_flags(db_close)

    db_list = db_subparsers.add_parser("list", help="List connections")
    db_list.set_defaults(db_action="list", func=_with_db(_cmd_db_list))
    _add_global_flags(db_list)

    db_active = db_subparsers.add_parser("active", help="Show active connection")
    db_active.set_defaults(db_action="active", func=_with_db(_cmd_db_active))
    _add_global_flags(db_active)


def _build_tables_parser(sub: argparse._SubParsersAction) -> None:
    """Add tables subcommand family."""
    tables_parser = sub.add_parser("tables", help="List tables")
    tables_parser.add_argument("--system", action="store_true", help="List system tables")
    _add_global_flags(tables_parser)
    tables_parser.set_defaults(func=_with_db(_cmd_tables))


def _build_schema_parser(sub: argparse._SubParsersAction) -> None:
    """Add schema subcommand family."""
    schema_parser = sub.add_parser("schema", help="Get table schema")
    schema_parser.add_argument("table", type=str, help="Table name")
    _add_global_flags(schema_parser)
    schema_parser.set_defaults(func=_with_db(_cmd_schema))


def _build_indexes_parser(sub: argparse._SubParsersAction) -> None:
    """Add indexes subcommand family."""
    indexes_parser = sub.add_parser("indexes", help="Get table indexes")
    indexes_parser.add_argument("table", type=str, help="Table name")
    _add_global_flags(indexes_parser)
    indexes_parser.set_defaults(func=_with_db(_cmd_indexes))


def _build_relationships_parser(sub: argparse._SubParsersAction) -> None:
    """Add relationships subcommand family."""
    rels_parser = sub.add_parser("relationships", help="List relationships")
    _add_global_flags(rels_parser)
    rels_parser.set_defaults(func=_with_db(_cmd_relationships))


def _build_stats_parser(sub: argparse._SubParsersAction) -> None:
    """Add stats subcommand family."""
    stats_parser = sub.add_parser("stats", help="Get database statistics")
    _add_global_flags(stats_parser)
    stats_parser.set_defaults(func=_with_db(_cmd_stats))


def _build_props_parser(sub: argparse._SubParsersAction) -> None:
    """Add props subcommand family (get, set)."""
    props_parser = sub.add_parser("props", help="Database properties")
    props_subparsers = props_parser.add_subparsers(dest="props_action")
    props_get = props_subparsers.add_parser("get", help="Get properties")
    props_get.set_defaults(props_action="get", func=_with_db(_cmd_props))
    _add_global_flags(props_get)
    props_set = props_subparsers.add_parser("set", help="Set a property")
    props_set.set_defaults(props_action="set", func=_with_db(_cmd_props))
    props_set.add_argument("--name", type=str, required=True)
    props_set.add_argument("--value", type=str, required=True)
    _add_global_flags(props_set)


def _build_queries_parser(sub: argparse._SubParsersAction) -> None:
    """Add queries subcommand family (list, create, set, delete)."""
    queries_parser = sub.add_parser("queries", help="Query management")
    queries_subparsers = queries_parser.add_subparsers(dest="queries_action")
    queries_list = queries_subparsers.add_parser("list", help="List queries")
    queries_list.set_defaults(queries_action="list", func=_with_db(_cmd_queries))
    _add_global_flags(queries_list)
    queries_create = queries_subparsers.add_parser("create", help="Create query")
    queries_create.set_defaults(queries_action="create", func=_with_db(_cmd_queries))
    queries_create.add_argument("--name", type=str, required=True)
    queries_create.add_argument("--sql", type=str, required=True)
    _add_global_flags(queries_create)
    queries_set = queries_subparsers.add_parser("set", help="Set query SQL")
    queries_set.set_defaults(queries_action="set", func=_with_db(_cmd_queries))
    queries_set.add_argument("--name", type=str, required=True)
    queries_set.add_argument("--sql", type=str, required=True)
    _add_global_flags(queries_set)
    queries_delete = queries_subparsers.add_parser("delete", help="Delete query")
    queries_delete.set_defaults(queries_action="delete", func=_with_db(_cmd_queries))
    queries_delete.add_argument("--name", type=str, required=True)
    _add_global_flags(queries_delete)


def _build_query_parser(sub: argparse._SubParsersAction) -> None:
    """Add query subcommand family."""
    query_parser = sub.add_parser("query", help="Execute a SELECT query")
    query_parser.add_argument("sql", type=str, help="SQL SELECT statement")
    query_parser.add_argument("--param", nargs="*", help="Query parameters")
    query_parser.add_argument("--limit", type=int, help="Row limit")
    _add_global_flags(query_parser)
    query_parser.set_defaults(func=_with_db(_cmd_query))


def _build_insert_parser(sub: argparse._SubParsersAction) -> None:
    """Add insert subcommand family."""
    insert_parser = sub.add_parser("insert", help="Insert data")
    insert_parser.add_argument("table", type=str, help="Table name")
    insert_parser.add_argument("data", nargs="*", help="column=value pairs")
    _add_global_flags(insert_parser)
    insert_parser.set_defaults(func=_with_db(_cmd_insert))


def _build_update_parser(sub: argparse._SubParsersAction) -> None:
    """Add update subcommand family."""
    update_parser = sub.add_parser("update", help="Update data")
    update_parser.add_argument("table", type=str, help="Table name")
    update_parser.add_argument("data", nargs="*", help="column=value pairs")
    update_parser.add_argument("--where", type=str, help="WHERE clause")
    _add_global_flags(update_parser)
    update_parser.set_defaults(func=_with_db(_cmd_update))


def _build_delete_parser(sub: argparse._SubParsersAction) -> None:
    """Add delete subcommand family."""
    delete_parser = sub.add_parser("delete", help="Delete data")
    delete_parser.add_argument("table", type=str, help="Table name")
    delete_parser.add_argument("--where", type=str, help="WHERE clause")
    _add_global_flags(delete_parser)
    delete_parser.set_defaults(func=_with_db(_cmd_delete))


def _build_ddl_parser(sub: argparse._SubParsersAction) -> None:
    """Add ddl subcommand family (create-table, alter, drop-table, create-index, drop-index, relationship)."""
    ddl_parser = sub.add_parser("ddl", help="DDL operations")
    ddl_subparsers = ddl_parser.add_subparsers(dest="ddl_action")
    ddl_create_table = ddl_subparsers.add_parser("create-table", help="Create table")
    ddl_create_table.set_defaults(ddl_action="create-table", func=_with_db(_cmd_ddl))
    ddl_create_table.add_argument("--table", type=str, required=True)
    ddl_create_table.add_argument("--columns", nargs="*", help="column=type pairs")
    _add_global_flags(ddl_create_table)
    ddl_alter = ddl_subparsers.add_parser("alter", help="Alter table")
    ddl_alter.set_defaults(ddl_action="alter", func=_with_db(_cmd_ddl))
    ddl_alter.add_argument("--table", type=str, required=True)
    ddl_alter.add_argument("--action", choices=["add", "drop"], required=True)
    ddl_alter.add_argument("--column", type=str)
    ddl_alter.add_argument("--type", type=str)
    _add_global_flags(ddl_alter)
    ddl_drop_table = ddl_subparsers.add_parser("drop-table", help="Drop table")
    ddl_drop_table.set_defaults(ddl_action="drop-table", func=_with_db(_cmd_ddl))
    ddl_drop_table.add_argument("--table", type=str, required=True)
    _add_global_flags(ddl_drop_table)
    ddl_create_index = ddl_subparsers.add_parser("create-index", help="Create index")
    ddl_create_index.set_defaults(ddl_action="create-index", func=_with_db(_cmd_ddl))
    ddl_create_index.add_argument("--table", type=str, required=True)
    ddl_create_index.add_argument("--index", type=str, required=True)
    ddl_create_index.add_argument("--columns", nargs="*", required=True)
    ddl_create_index.add_argument("--unique", action="store_true")
    _add_global_flags(ddl_create_index)
    ddl_drop_index = ddl_subparsers.add_parser("drop-index", help="Drop index")
    ddl_drop_index.set_defaults(ddl_action="drop-index", func=_with_db(_cmd_ddl))
    ddl_drop_index.add_argument("--table", type=str, required=True)
    ddl_drop_index.add_argument("--index", type=str, required=True)
    _add_global_flags(ddl_drop_index)
    ddl_relationship = ddl_subparsers.add_parser("relationship", help="Create relationship")
    ddl_relationship.set_defaults(ddl_action="relationship", func=_with_db(_cmd_ddl))
    ddl_relationship.add_argument("--name", type=str, required=True)
    ddl_relationship.add_argument("--source-table", type=str, required=True)
    ddl_relationship.add_argument("--source-column", type=str, required=True)
    ddl_relationship.add_argument("--dest-table", type=str, required=True)
    ddl_relationship.add_argument("--dest-column", type=str, required=True)
    _add_global_flags(ddl_relationship)


def _build_export_parser(sub: argparse._SubParsersAction) -> None:
    """Add export subcommand family."""
    export_parser = sub.add_parser("export", help="Export data")
    export_group = export_parser.add_mutually_exclusive_group(required=True)
    export_group.add_argument("table", type=str, nargs="?", help="Table name")
    export_group.add_argument("--query", type=str, help="Query name")
    export_parser.add_argument("--format", choices=["csv", "json", "xlsx"], default="csv")
    export_parser.add_argument("--sheet-name", type=str, default="Sheet1", help="Sheet name for xlsx export")
    export_parser.add_argument("--out", type=str, required=True, help="Output file path")
    _add_global_flags(export_parser)
    export_parser.set_defaults(func=_with_db(_cmd_export))


def _build_links_parser(sub: argparse._SubParsersAction) -> None:
    """Add links subcommand family (list)."""
    links_parser = sub.add_parser("links", help="Linked table commands")
    links_subparsers = links_parser.add_subparsers(dest="links_action")
    links_list = links_subparsers.add_parser("list", help="List linked tables")
    links_list.set_defaults(links_action="list", func=_with_db(_cmd_links))
    _add_global_flags(links_list)


def _run_mcp(opts: Any) -> Any:
    from .server import main as mcp_main  # lazy: keeps --version/--help import-light

    return mcp_main(["--profile", opts.profile] if opts.profile else [])


def _build_mcp_parser(sub: argparse._SubParsersAction) -> None:
    """Add mcp subcommand family."""
    mcp_parser = sub.add_parser("mcp", help="Run the MCP server over stdio")
    valid_profiles = ", ".join(sorted(PROFILES))
    mcp_parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help=(
            "MCP tool profile to expose. "
            "Token-cost rationale: core <= 30 tools keeps LLM context lean; "
            "additional profiles are opt-in. "
            f"Valid values: {valid_profiles}. "
            "Default: core. Can also be set via ACCESS_MCP_PROFILE env var."
        ),
    )
    mcp_parser.set_defaults(command="mcp", func=_run_mcp)


def _build_automation_parser(sub: argparse._SubParsersAction) -> None:
    """Add automation subcommand family (forms, reports, macros, vba)."""
    automation_parser = sub.add_parser("automation", help="Automation commands (forms, reports, VBA, macros)")
    automation_subparsers = automation_parser.add_subparsers(dest="automation_action")
    _add_global_flags(automation_parser)

    # ---- automation forms sub-group ----
    forms_parser = automation_subparsers.add_parser("forms", help="Form operations")
    forms_subparsers = forms_parser.add_subparsers(dest="forms_action")
    forms_list = forms_subparsers.add_parser("list", help="List forms")
    forms_list.set_defaults(forms_action="list", func=_with_automation(_cmd_automation_forms_list))
    _add_global_flags(forms_list)

    forms_exists = forms_subparsers.add_parser("exists", help="Check form exists")
    forms_exists.set_defaults(forms_action="exists", func=_with_automation(_cmd_automation_forms_exists))
    forms_exists.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_exists)

    forms_create = forms_subparsers.add_parser("create", help="Create form")
    forms_create.set_defaults(forms_action="create", func=_with_automation(_cmd_automation_forms_create))
    forms_create.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_create)

    forms_rename = forms_subparsers.add_parser("rename", help="Rename form")
    forms_rename.set_defaults(forms_action="rename", func=_with_automation(_cmd_automation_forms_rename))
    forms_rename.add_argument("--old-name", type=str, required=True)
    forms_rename.add_argument("--new-name", type=str, required=True)
    _add_global_flags(forms_rename)

    forms_delete = forms_subparsers.add_parser("delete", help="Delete form")
    forms_delete.set_defaults(forms_action="delete", func=_with_automation(_cmd_automation_forms_delete))
    forms_delete.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_delete)

    forms_properties = forms_subparsers.add_parser("properties", help="Get form properties")
    forms_properties.set_defaults(forms_action="properties", func=_with_automation(_cmd_automation_forms_properties))
    forms_properties.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_properties)

    forms_controls = forms_subparsers.add_parser("controls", help="Get form controls")
    forms_controls.set_defaults(forms_action="controls", func=_with_automation(_cmd_automation_forms_controls))
    forms_controls.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_controls)

    forms_add_control = forms_subparsers.add_parser("add-control", help="Add control to form")
    forms_add_control.set_defaults(forms_action="add-control", func=_with_automation(_cmd_automation_forms_add_control))
    forms_add_control.add_argument("--name", type=str, required=True)
    forms_add_control.add_argument("--type", type=str, required=True)
    forms_add_control.add_argument("--control-name", type=str, required=True)
    forms_add_control.add_argument("--section", type=int, default=0)
    _add_global_flags(forms_add_control)

    forms_remove_control = forms_subparsers.add_parser("remove-control", help="Remove control from form")
    forms_remove_control.set_defaults(forms_action="remove-control", func=_with_automation(_cmd_automation_forms_remove_control))
    forms_remove_control.add_argument("--name", type=str, required=True)
    forms_remove_control.add_argument("--control-name", type=str, required=True)
    _add_global_flags(forms_remove_control)

    forms_sections = forms_subparsers.add_parser("sections", help="Get form sections")
    forms_sections.set_defaults(forms_action="sections", func=_with_automation(_cmd_automation_forms_sections))
    forms_sections.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_sections)

    forms_export = forms_subparsers.add_parser("export", help="Export form to text")
    forms_export.set_defaults(forms_action="export", func=_with_automation(_cmd_automation_forms_export))
    forms_export.add_argument("--name", type=str, required=True)
    _add_global_flags(forms_export)

    forms_import = forms_subparsers.add_parser("import", help="Import form from text")
    forms_import.set_defaults(forms_action="import", func=_with_automation(_cmd_automation_forms_import))
    forms_import.add_argument("--name", type=str, required=True)
    forms_import.add_argument("--text", type=str, required=True)
    _add_global_flags(forms_import)

    # ---- automation reports sub-group ----
    reports_parser = automation_subparsers.add_parser("reports", help="Report operations")
    reports_subparsers = reports_parser.add_subparsers(dest="reports_action")
    reports_list = reports_subparsers.add_parser("list", help="List reports")
    reports_list.set_defaults(reports_action="list", func=_with_automation(_cmd_automation_reports_list))
    _add_global_flags(reports_list)

    reports_exists = reports_subparsers.add_parser("exists", help="Check report exists")
    reports_exists.set_defaults(reports_action="exists", func=_with_automation(_cmd_automation_reports_exists))
    reports_exists.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_exists)

    reports_create = reports_subparsers.add_parser("create", help="Create report")
    reports_create.set_defaults(reports_action="create", func=_with_automation(_cmd_automation_reports_create))
    reports_create.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_create)

    reports_rename = reports_subparsers.add_parser("rename", help="Rename report")
    reports_rename.set_defaults(reports_action="rename", func=_with_automation(_cmd_automation_reports_rename))
    reports_rename.add_argument("--old-name", type=str, required=True)
    reports_rename.add_argument("--new-name", type=str, required=True)
    _add_global_flags(reports_rename)

    reports_delete = reports_subparsers.add_parser("delete", help="Delete report")
    reports_delete.set_defaults(reports_action="delete", func=_with_automation(_cmd_automation_reports_delete))
    reports_delete.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_delete)

    reports_properties = reports_subparsers.add_parser("properties", help="Get report properties")
    reports_properties.set_defaults(reports_action="properties", func=_with_automation(_cmd_automation_reports_properties))
    reports_properties.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_properties)

    reports_controls = reports_subparsers.add_parser("controls", help="Get report controls")
    reports_controls.set_defaults(reports_action="controls", func=_with_automation(_cmd_automation_reports_controls))
    reports_controls.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_controls)

    reports_add_control = reports_subparsers.add_parser("add-control", help="Add control to report")
    reports_add_control.set_defaults(reports_action="add-control", func=_with_automation(_cmd_automation_reports_add_control))
    reports_add_control.add_argument("--name", type=str, required=True)
    reports_add_control.add_argument("--type", type=str, required=True)
    reports_add_control.add_argument("--control-name", type=str, required=True)
    reports_add_control.add_argument("--section", type=int, default=0)
    _add_global_flags(reports_add_control)

    reports_remove_control = reports_subparsers.add_parser("remove-control", help="Remove control from report")
    reports_remove_control.set_defaults(reports_action="remove-control", func=_with_automation(_cmd_automation_reports_remove_control))
    reports_remove_control.add_argument("--name", type=str, required=True)
    reports_remove_control.add_argument("--control-name", type=str, required=True)
    _add_global_flags(reports_remove_control)

    reports_sections = reports_subparsers.add_parser("sections", help="Get report sections")
    reports_sections.set_defaults(reports_action="sections", func=_with_automation(_cmd_automation_reports_sections))
    reports_sections.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_sections)

    reports_export = reports_subparsers.add_parser("export", help="Export report to text")
    reports_export.set_defaults(reports_action="export", func=_with_automation(_cmd_automation_reports_export))
    reports_export.add_argument("--name", type=str, required=True)
    _add_global_flags(reports_export)

    reports_import = reports_subparsers.add_parser("import", help="Import report from text")
    reports_import.set_defaults(reports_action="import", func=_with_automation(_cmd_automation_reports_import))
    reports_import.add_argument("--name", type=str, required=True)
    reports_import.add_argument("--text", type=str, required=True)
    _add_global_flags(reports_import)

    # ---- automation macros sub-group ----
    macros_parser = automation_subparsers.add_parser("macros", help="Macro operations")
    macros_subparsers = macros_parser.add_subparsers(dest="macros_action")
    macros_list = macros_subparsers.add_parser("list", help="List macros")
    macros_list.set_defaults(macros_action="list", func=_with_automation(_cmd_automation_macros_list))
    _add_global_flags(macros_list)

    macros_exists = macros_subparsers.add_parser("exists", help="Check macro exists")
    macros_exists.set_defaults(macros_action="exists", func=_with_automation(_cmd_automation_macros_exists))
    macros_exists.add_argument("--name", type=str, required=True)
    _add_global_flags(macros_exists)

    macros_create = macros_subparsers.add_parser("create", help="Create macro")
    macros_create.set_defaults(macros_action="create", func=_with_automation(_cmd_automation_macros_create))
    macros_create.add_argument("--name", type=str, required=True)
    _add_global_flags(macros_create)

    macros_rename = macros_subparsers.add_parser("rename", help="Rename macro")
    macros_rename.set_defaults(macros_action="rename", func=_with_automation(_cmd_automation_macros_rename))
    macros_rename.add_argument("--old-name", type=str, required=True)
    macros_rename.add_argument("--new-name", type=str, required=True)
    _add_global_flags(macros_rename)

    macros_delete = macros_subparsers.add_parser("delete", help="Delete macro")
    macros_delete.set_defaults(macros_action="delete", func=_with_automation(_cmd_automation_macros_delete))
    macros_delete.add_argument("--name", type=str, required=True)
    _add_global_flags(macros_delete)

    macros_run = macros_subparsers.add_parser("run", help="Run macro")
    macros_run.set_defaults(macros_action="run", func=_with_automation(_cmd_automation_macros_run))
    macros_run.add_argument("--name", type=str, required=True)
    _add_global_flags(macros_run)

    macros_properties = macros_subparsers.add_parser("properties", help="Get macro properties")
    macros_properties.set_defaults(macros_action="properties", func=_with_automation(_cmd_automation_macros_properties))
    macros_properties.add_argument("--name", type=str, required=True)
    _add_global_flags(macros_properties)

    # ---- automation vba sub-group ----
    vba_parser = automation_subparsers.add_parser("vba", help="VBA module operations")
    vba_subparsers = vba_parser.add_subparsers(dest="vba_action")
    vba_modules = vba_subparsers.add_parser("modules", help="List VBA modules")
    vba_modules.set_defaults(vba_action="modules", func=_with_automation(_cmd_automation_vba_modules))
    _add_global_flags(vba_modules)

    vba_get_code = vba_subparsers.add_parser("get-code", help="Get VBA module code")
    vba_get_code.set_defaults(vba_action="get-code", func=_with_automation(_cmd_automation_vba_get_code))
    vba_get_code.add_argument("--module", type=str, required=True)
    _add_global_flags(vba_get_code)

    vba_set_code = vba_subparsers.add_parser("set-code", help="Set VBA module code")
    vba_set_code.set_defaults(vba_action="set-code", func=_with_automation(_cmd_automation_vba_set_code))
    vba_set_code.add_argument("--module", type=str, required=True)
    vba_set_code.add_argument("--code", type=str, required=True)
    _add_global_flags(vba_set_code)

    vba_add_procedure = vba_subparsers.add_parser("add-procedure", help="Add VBA procedure")
    vba_add_procedure.set_defaults(vba_action="add-procedure", func=_with_automation(_cmd_automation_vba_add_procedure))
    vba_add_procedure.add_argument("--module", type=str, required=True)
    vba_add_procedure.add_argument("--procedure", type=str, required=True)
    vba_add_procedure.add_argument("--code", type=str, required=True)
    _add_global_flags(vba_add_procedure)

    vba_compile = vba_subparsers.add_parser("compile", help="Compile VBA project")
    vba_compile.set_defaults(vba_action="compile", func=_with_automation(_cmd_automation_vba_compile))
    _add_global_flags(vba_compile)

    vba_save = vba_subparsers.add_parser("save", help="Save database")
    vba_save.set_defaults(vba_action="save", func=_with_automation(_cmd_automation_vba_save))
    _add_global_flags(vba_save)

    vba_module_exists = vba_subparsers.add_parser("module-exists", help="Check module exists")
    vba_module_exists.set_defaults(vba_action="module-exists", func=_with_automation(_cmd_automation_vba_module_exists))
    vba_module_exists.add_argument("--module", type=str, required=True)
    _add_global_flags(vba_module_exists)

    vba_create_module = vba_subparsers.add_parser("create-module", help="Create VBA module")
    vba_create_module.set_defaults(vba_action="create-module", func=_with_automation(_cmd_automation_vba_create_module))
    vba_create_module.add_argument("--module", type=str, required=True)
    vba_create_module.add_argument("--module-type", type=int, default=1)
    _add_global_flags(vba_create_module)

    vba_delete_module = vba_subparsers.add_parser("delete-module", help="Delete VBA module")
    vba_delete_module.set_defaults(vba_action="delete-module", func=_with_automation(_cmd_automation_vba_delete_module))
    vba_delete_module.add_argument("--module", type=str, required=True)
    _add_global_flags(vba_delete_module)

    vba_rename_module = vba_subparsers.add_parser("rename-module", help="Rename VBA module")
    vba_rename_module.set_defaults(vba_action="rename-module", func=_with_automation(_cmd_automation_vba_rename_module))
    vba_rename_module.add_argument("--old-name", type=str, required=True)
    vba_rename_module.add_argument("--new-name", type=str, required=True)
    _add_global_flags(vba_rename_module)

    vba_list_procedures = vba_subparsers.add_parser("list-procedures", help="List procedures in module")
    vba_list_procedures.set_defaults(vba_action="list-procedures", func=_with_automation(_cmd_automation_vba_list_procedures))
    vba_list_procedures.add_argument("--module", type=str, required=True)
    _add_global_flags(vba_list_procedures)

    vba_get_procedure = vba_subparsers.add_parser("get-procedure", help="Get VBA procedure")
    vba_get_procedure.set_defaults(vba_action="get-procedure", func=_with_automation(_cmd_automation_vba_get_procedure))
    vba_get_procedure.add_argument("--module", type=str, required=True)
    vba_get_procedure.add_argument("--procedure", type=str, required=True)
    _add_global_flags(vba_get_procedure)

    vba_replace_procedure = vba_subparsers.add_parser("replace-procedure", help="Replace VBA procedure")
    vba_replace_procedure.set_defaults(vba_action="replace-procedure", func=_with_automation(_cmd_automation_vba_replace_procedure))
    vba_replace_procedure.add_argument("--module", type=str, required=True)
    vba_replace_procedure.add_argument("--procedure", type=str, required=True)
    vba_replace_procedure.add_argument("--code", type=str, required=True)
    _add_global_flags(vba_replace_procedure)


def _build_versioning_parser(sub: argparse._SubParsersAction) -> None:
    """Add versioning subcommand family (export-object, export-all, import-object, import-all, compare, export-schema-ddl)."""
    versioning_parser = sub.add_parser("versioning", help="Versioning commands (export, import, compare)")
    versioning_subparsers = versioning_parser.add_subparsers(dest="versioning_action")
    _add_global_flags(versioning_parser)

    export_object = versioning_subparsers.add_parser("export-object", help="Export a single object to text")
    export_object.set_defaults(versioning_action="export-object", func=_with_versioning(_cmd_versioning_export_object))
    export_object.add_argument("--kind", type=str, required=True)
    export_object.add_argument("--name", type=str, required=True)
    export_object.add_argument("--out-dir", type=str, required=True)
    _add_global_flags(export_object)

    export_all = versioning_subparsers.add_parser("export-all", help="Export all objects to directory tree")
    export_all.set_defaults(versioning_action="export-all", func=_with_versioning(_cmd_versioning_export_all))
    export_all.add_argument("--out-dir", type=str, required=True)
    _add_global_flags(export_all)

    import_object = versioning_subparsers.add_parser("import-object", help="Import object from text (destructive)")
    import_object.set_defaults(versioning_action="import-object", func=_with_versioning(_cmd_versioning_import_object))
    import_object.add_argument("--kind", type=str, required=True)
    import_object.add_argument("--name", type=str, required=True)
    import_object.add_argument("--file-path", type=str, required=True)
    _add_global_flags(import_object)

    import_all = versioning_subparsers.add_parser("import-all", help="Import all objects from directory (destructive)")
    import_all.set_defaults(versioning_action="import-all", func=_with_versioning(_cmd_versioning_import_all))
    import_all.add_argument("--input-dir", type=str, required=True)
    _add_global_flags(import_all)

    compare = versioning_subparsers.add_parser("compare", help="Compare exported objects against database")
    compare.set_defaults(versioning_action="compare", func=_with_versioning(_cmd_versioning_compare))
    compare.add_argument("--export-dir", type=str, required=True)
    _add_global_flags(compare)

    export_schema_ddl = versioning_subparsers.add_parser("export-schema-ddl", help="Export schema DDL")
    export_schema_ddl.set_defaults(versioning_action="export-schema-ddl", func=_with_versioning(_cmd_versioning_export_schema_ddl))
    export_schema_ddl.add_argument("--out-dir", type=str, required=True)
    _add_global_flags(export_schema_ddl)


def _build_maintenance_parser(sub: argparse._SubParsersAction) -> None:
    """Add maintenance subcommand family (backup, dev-copy-status, dev-copy-create, dev-copy-deploy, compact-repair, diagnose, recover)."""
    maintenance_parser = sub.add_parser("maintenance", help="Maintenance commands (backup, dev copy, compact/repair, diagnose, recover)")
    maintenance_subparsers = maintenance_parser.add_subparsers(dest="maintenance_action")
    _add_global_flags(maintenance_parser)

    m_backup = maintenance_subparsers.add_parser("backup", help="Create a timestamped backup")
    m_backup.set_defaults(maintenance_action="backup", func=_with_maintenance(_cmd_maintenance_backup))
    m_backup.add_argument("path", type=str, help="Path to .accdb file")
    m_backup.add_argument("--retention", type=int, help="Number of backups to retain")
    _add_global_flags(m_backup)

    m_dev_copy_status = maintenance_subparsers.add_parser("dev-copy-status", help="Check dev copy status")
    m_dev_copy_status.set_defaults(maintenance_action="dev-copy-status", func=_with_maintenance(_cmd_maintenance_dev_copy_status))
    _add_global_flags(m_dev_copy_status)

    m_dev_copy_create = maintenance_subparsers.add_parser("dev-copy-create", help="Create a dev copy")
    m_dev_copy_create.set_defaults(maintenance_action="dev-copy-create", func=_with_maintenance(_cmd_maintenance_dev_copy_create))
    m_dev_copy_create.add_argument("--source", type=str, help="Source .accdb path")
    _add_global_flags(m_dev_copy_create)

    m_dev_copy_deploy = maintenance_subparsers.add_parser("dev-copy-deploy", help="Deploy dev copy over production (destructive)")
    m_dev_copy_deploy.set_defaults(maintenance_action="dev-copy-deploy", func=_with_maintenance(_cmd_maintenance_dev_copy_deploy))
    m_dev_copy_deploy.add_argument("--dev-path", type=str, required=True)
    m_dev_copy_deploy.add_argument("--target", type=str, required=True)
    _add_global_flags(m_dev_copy_deploy)

    m_compact_repair = maintenance_subparsers.add_parser("compact-repair", help="Compact and/or repair database (destructive)")
    m_compact_repair.set_defaults(maintenance_action="compact-repair", func=_with_maintenance(_cmd_maintenance_compact_repair))
    m_compact_repair.add_argument("path", type=str, help="Path to .accdb file")
    m_compact_repair.add_argument("--mode", choices=["compact", "repair", "compact_and_repair"], default="compact", help="Operation mode")
    _add_global_flags(m_compact_repair)

    m_diagnose = maintenance_subparsers.add_parser("diagnose", help="Run environment diagnostics")
    m_diagnose.set_defaults(maintenance_action="diagnose", func=_with_maintenance(_cmd_maintenance_diagnose))
    _add_global_flags(m_diagnose)

    m_recover = maintenance_subparsers.add_parser("recover", help="Kill Access processes and attempt recovery (destructive)")
    m_recover.set_defaults(maintenance_action="recover", func=_with_maintenance(_cmd_maintenance_recover))
    _add_global_flags(m_recover)


def _unwrap_cli_envelope(payload: Any) -> Any:
    """Accept saved CLI output without relaxing document schema validation."""
    if isinstance(payload, dict):
        if payload.get("success") is False:
            raise ValueError(str(payload.get("error", "CLI operation failed")))
        if payload.get("success") and isinstance(payload.get("data"), dict):
            return payload["data"]
    return payload


def _inspect_open_session(database: Any, *, tables: Any, queries: Any) -> Any:
    """Inspect an open session through its DAO or ODBC inspection port.

    The DAO path reopens engine-level read-only before reading; the ODBC path reuses
    the adapter's already-open connection (ODBC exposes no read-only reopen) and
    reports ODBC-invisible metadata as `unavailable` instead of fabricating it.
    """
    adapter = getattr(database, "_adapter", None)
    dispatcher = getattr(adapter, "_dispatcher", None)
    if dispatcher is not None and dispatcher.is_connected():
        port = open_inspection_port(dispatcher)

        def leave_session_open() -> None:
            return None

        port.close = leave_session_open
        return dispatcher.call(
            lambda: DocumentationInspectService().inspect(port, tables=tables, queries=queries)
        )
    connection = getattr(adapter, "connection", None)
    if connection is not None:
        return DocumentationInspectService().inspect(
            OdbcInspectionPort(connection), tables=tables, queries=queries
        )
    raise ValueError("Documentation inspection requires a connected DAO or ODBC backend")


def _cmd_documentation(args: argparse.Namespace) -> int:
    """Run inspect/render/save documentation operations."""
    from .errors import err, ok

    if args.documentation_action == "inspect":
        if not args.db:
            print("Error: --db required", file=sys.stderr)
            return 2
        database = DatabaseUseCases(backend=args.backend, readonly=True, allowed_dirs=cli_allowed_dirs())
        try:
            opened = database.open_database(args.db)
            if isinstance(opened, Err):
                result = opened
            else:
                try:
                    evidence = _inspect_open_session(database, tables=args.tables, queries=args.queries)
                    payload = evidence.to_dict()
                    payload["status"] = evidence.status
                    result = ok(data=payload)
                except Exception as exc:
                    result = err(str(exc))
        finally:
            database.close_database()
    elif args.documentation_action == "render":
        try:
            evidence = EvidenceDoc.from_dict(_unwrap_cli_envelope(json.loads(Path(args.evidence).read_text(encoding="utf-8"))))
            result = ok(data=render_document(evidence, kind=args.kind, language=args.language).to_dict())
        except Exception as exc:
            result = err(str(exc))
    else:
        if not args.yes:
            print("Error: documentation save requires --yes", file=sys.stderr)
            return 2
        try:
            report = ReportDoc.from_dict(_unwrap_cli_envelope(json.loads(Path(args.report).read_text(encoding="utf-8"))))
            allowed = args.allow_doc_write or os.environ.get("ACCESS_MCP_ALLOW_DOC_WRITE", "").casefold() in {"1", "true"}
            saved = DocumentationSaveService(DocumentationWritePolicy(allowed=allowed)).save(
                report.markdown,
                args.output_path,
                confirm=True,
                overwrite=args.overwrite,
                expected_fingerprint=args.expected_fingerprint,
            )
            result = ok(data=saved.__dict__)
        except Exception as exc:
            result = err(str(exc))
    return format_result(result, args.json)


def _build_documentation_parser(sub: argparse._SubParsersAction) -> None:
    """Add inspect, render, and guarded save documentation commands."""
    parser = sub.add_parser("documentation", help="Inspect and render database documentation")
    actions = parser.add_subparsers(dest="documentation_action")

    inspect = actions.add_parser("inspect", help="Inspect metadata into evidence")
    inspect.add_argument("--db", required=True, help="Closed, offline Access database path")
    inspect.add_argument("--backend", choices=["auto", "dao", "odbc"], default="auto", help="Backend to use")
    inspect.add_argument("--json", action="store_true", help="JSON output")
    inspect.add_argument("--tables", action="append", help="Table to inspect (repeatable)")
    inspect.add_argument("--queries", action="append", help="Saved query to inspect (repeatable)")
    inspect.set_defaults(func=_cmd_documentation)

    render = actions.add_parser("render", help="Render evidence JSON to a Markdown report")
    render.add_argument("--evidence", required=True, help="Path to evidence JSON")
    render.add_argument("--kind", choices=["database", "er"], default="database")
    render.add_argument("--language", default="en")
    render.add_argument("--db", help="Database path (not used by offline rendering)")
    render.add_argument("--backend", choices=["auto", "dao", "odbc"], default="auto", help="Backend (not used by offline rendering)")
    render.add_argument("--json", action="store_true", help="JSON output")
    render.set_defaults(func=_cmd_documentation)

    save = actions.add_parser("save", help="Save a rendered report JSON")
    save.add_argument("--report", required=True, help="Path to rendered report JSON")
    save.add_argument("--output-path", required=True, help="Markdown destination")
    save.add_argument("--allow-doc-write", action="store_true", help="Grant documentation-write permission for this invocation")
    save.add_argument("--yes", action="store_true", help="Confirm the documentation file write")
    save.add_argument("--overwrite", action="store_true", help="Approve replacing an existing report")
    save.add_argument("--expected-fingerprint", help="Expected current destination SHA-256")
    save.add_argument("--db", help="Database path (not used when saving a report)")
    save.add_argument("--backend", choices=["auto", "dao", "odbc"], default="auto", help="Backend (not used when saving a report)")
    save.add_argument("--json", action="store_true", help="JSON output")
    save.set_defaults(func=_cmd_documentation)


# =============================================================================
# Parser Orchestrator
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser (no I/O, no COM/dispatcher).

    Each subcommand family is built by a dedicated builder function.
    New CLI commands should be added inside the owning family builder.
    """
    parser = argparse.ArgumentParser(prog="ms-access-mcp")
    parser.add_argument("--version", action="version", version="ms-access-mcp 0.2.0")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    _build_doctor_parser(subparsers)
    _build_documentation_parser(subparsers)
    _build_configure_parser(subparsers)
    _build_credentials_parser(subparsers)
    _build_db_parser(subparsers)
    _build_tables_parser(subparsers)
    _build_schema_parser(subparsers)
    _build_indexes_parser(subparsers)
    _build_relationships_parser(subparsers)
    _build_stats_parser(subparsers)
    _build_props_parser(subparsers)
    _build_queries_parser(subparsers)
    _build_query_parser(subparsers)
    _build_insert_parser(subparsers)
    _build_update_parser(subparsers)
    _build_delete_parser(subparsers)
    _build_ddl_parser(subparsers)
    _build_export_parser(subparsers)
    _build_links_parser(subparsers)
    _build_mcp_parser(subparsers)
    _build_automation_parser(subparsers)
    _build_versioning_parser(subparsers)
    _build_maintenance_parser(subparsers)

    return parser


# =============================================================================
# Dispatch Wrapper Factories
# =============================================================================


def _close_cli_use_case(uc: object) -> None:
    """Best-effort teardown of a CLI-built use case (E3: never leak Access COM)."""
    try:
        close_database = getattr(uc, "close_database", None)
        if callable(close_database):
            close_database()
            return
        close = getattr(uc, "close", None)
        if callable(close):
            close()
    except Exception:
        logging.getLogger(__name__).debug("CLI use-case teardown failed", exc_info=True)


def _with_db(handler):
    """Build DatabaseUseCases + auto-open --db, then run handler(opts, db).

    Preserves the exact pre-refactor ordering: construct use cases from global
    flags, auto-open when --db is present, short-circuit on open failure.
    """
    def _run(opts: argparse.Namespace) -> int:
        readonly = not getattr(opts, "allow_write", False)
        db = DatabaseUseCases(
            backend=getattr(opts, "backend", "auto"),
            readonly=readonly,
            allowed_dirs=cli_allowed_dirs(),
        )
        try:
            if getattr(opts, "db", None):
                result = db.open_database(opts.db)
                if isinstance(result, Err):
                    return format_result(result, getattr(opts, "json", False))
            return handler(opts, db)
        finally:
            _close_cli_use_case(db)
    return _run


def _with_automation(handler):
    """Build AutomationUseCases and run handler(opts, uc)."""
    def _run(opts: argparse.Namespace) -> int:
        uc = _automation_use_cases(opts)
        try:
            return handler(opts, uc)
        finally:
            _close_cli_use_case(uc)
    return _run


def _with_versioning(handler):
    """Build VersioningUseCases and run handler(opts, uc)."""
    def _run(opts: argparse.Namespace) -> int:
        uc = _versioning_use_cases(opts)
        try:
            return handler(opts, uc)
        finally:
            _close_cli_use_case(uc)
    return _run


def _with_maintenance(handler):
    """Build MaintenanceUseCases and run handler(opts, uc)."""
    def _run(opts: argparse.Namespace) -> int:
        uc = _maintenance_use_cases(opts)
        try:
            return handler(opts, uc)
        finally:
            _close_cli_use_case(uc)
    return _run


# =============================================================================
# Main Entry Point
# =============================================================================


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to sys.argv when None).
    """
    parser = build_parser()
    opts = parser.parse_args(argv)

    # Dispatch via set_defaults(func=...) registered on each subparser
    func = getattr(opts, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return func(opts)


if __name__ == "__main__":
    sys.exit(main())
