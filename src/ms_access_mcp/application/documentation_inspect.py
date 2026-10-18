"""Passive metadata extraction into versioned documentation evidence."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from ..backends.schema_inspector import QueryParameterInfo, SchemaInspector
from ..errors import AccessMcpError
from ..models import FieldInfo, IndexInfo, QueryInfo, RelationshipInfo, TableInfo
from .documentation import (
    AppLogicEvidence,
    ContainerInventoryEvidence,
    Coverage,
    EvidenceDoc,
    FactProvenance,
    FactStatus,
    FieldEvidence,
    IndexEvidence,
    QueryEvidence,
    RelationshipEvidence,
    RunMetadata,
    SourceEvidence,
    StartupPropertyEvidence,
    TableEvidence,
    redact_connection_secrets,
    sanitize_connection_string,
)

_QUERY_TYPES = {"select": "select", "action": "action", "ddl": "ddl"}

# Tier 1 engine-level app-logic inventory (default-off ``include_app_logic``): DAO
# Containers and database properties are readable without ever instantiating
# ``Access.Application``, so every v1 safety proof still applies. The category names are
# additive to ``access.documentation.evidence/1`` (agreement 1), and every fact records
# the engine-level method so consumers can tell it apart from a future
# instantiation-level reader's facts (the central v2 design tension).
ENGINE_DAO_CONTAINERS_METHOD = "engine_dao_containers"
ENGINE_DAO_PROPERTIES_METHOD = "engine_dao_properties"

# Container -> additive category. ``Scripts`` carries the named macros (including
# AutoKeys) and ``Modules`` the VBA modules; both are DAO Containers reads, not
# instantiation-level content reads.
_CONTAINER_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("form_inventory", "Forms"),
    ("report_inventory", "Reports"),
    ("macro_inventory", "Scripts"),
    ("module_inventory", "Modules"),
)

# The startup catalogue is read generically (every database Property) and filtered to
# this allowlist, so an unrelated property never reaches evidence and an allowlisted
# property the database does not carry produces no fact (the enumeration observed its
# absence, which is different from an unreadable property).
_STARTUP_PROPERTY_ALLOWLIST = frozenset(
    {
        "AllowBreakIntoCode",
        "AllowBuiltInToolbars",
        "AllowBypassKey",
        "AllowFullMenus",
        "AllowShortcutMenus",
        "AllowSpecialKeys",
        "AllowToolbarChanges",
        "AppTitle",
        "AutoCompact",
        "DisplayNavigationPane",
        "ShowNavigationPane",
        "StartupForm",
        "StartupShowDBWindow",
        "StartupShowStatusBar",
        "UseAppIconForFrmRpt",
    }
)

# A linked table's fields and indexes are not read at all. The gap is recorded as a
# coverage ``unsupported`` reason so an unread schema is never presented as observed
# empty; only local catalogue link metadata (kind/connect/source table) is reported.
_LINKED_TABLE_NOT_INSPECTED_REASON = (
    "Linked table: fields and indexes are not read because the linked source is never "
    "resolved or contacted; only local link metadata is reported"
)

# Capability keys name an extraction-level property a port may not be able to observe.
# A key present in `InspectionCapabilities.unavailable` maps every dependent fact to
# `unavailable` with that reason instead of a fabricated value.
CAP_FIELD_REQUIRED = "field_required"
CAP_FIELD_ALLOW_ZERO_LENGTH = "field_allow_zero_length"
CAP_PRIMARY_KEY = "primary_key"
CAP_RELATIONSHIPS = "relationships"
CAP_QUERY_SQL = "query_sql"
CAP_QUERY_TYPE = "query_type"

# Emitted saved-query SQL is gated by an explicit opt-in (``include_query_sql``). The
# exclusion is a deliberate output policy applied before any capability check: SQL source
# text is untrusted and may embed credentials. ``get_queries`` still enumerates saved
# queries (names, category, and the SQL it needs internally); only the emitted SQL
# evidence is withheld, so this is never a claim that the SQL was not read at all.
_QUERY_SQL_EXCLUDED_REASON = (
    "Saved-query SQL is excluded from emitted evidence by default because it is "
    "untrusted source text that may contain credentials; request it explicitly "
    "(include_query_sql / --include-query-sql) to include it"
)


@dataclass(frozen=True)
class InspectionCapabilities:
    """Which extraction properties a port can observe, and what it cannot.

    ``backend_identity`` is recorded as ``source.backend_identity`` in evidence. Every
    key present in ``unavailable`` names a capability the port cannot observe; its value
    is the non-empty reason recorded for each dependent fact, so the seam never
    fabricates a value the engine cannot supply. The default (empty mapping) describes
    the DAO port, which observes every property it reads.
    """

    backend_identity: str = "DAO"
    unavailable: Mapping[str, str] = field(default_factory=dict)

    def reason(self, capability: str) -> str | None:
        """Return the honest unavailable reason for a capability, or None when expressible."""
        return self.unavailable.get(capability, "").strip() or None


class InspectionCapabilityUnavailableError(RuntimeError):
    """Raised by an inspection port method for a capability it cannot expose at all."""


class InspectionPort(Protocol):
    """Read-only inspection surface over an already-open database session."""

    def capabilities(self) -> InspectionCapabilities: ...

    def list_tables(self) -> list[TableInfo]: ...

    def get_table_link_metadata(self, table_name: str) -> dict[str, Any] | None: ...

    def get_indexes(self, table_name: str) -> list[IndexInfo]: ...

    def get_relationships(self) -> list[RelationshipInfo]: ...

    def get_queries(self) -> list[QueryInfo]: ...

    def get_query_parameters(self, query_name: str) -> list[QueryParameterInfo] | None: ...

    def list_container_objects(self, container_name: str) -> list[dict[str, Any]]: ...

    def get_database_properties(self) -> dict[str, str]: ...

    def close(self) -> None: ...


class _OwnerThreadDispatcher:
    """Dispatcher view that runs every inspection COM call on the owner thread.

    ``SchemaInspector`` drives every DAO read through ``call`` and reads ``current_db``
    inside the dispatched unit, so routing those through the real dispatcher keeps the
    apartment-affine DAO handle on the STA thread that opened it instead of letting a
    caller thread drive COM directly.

    The view is reentrancy-safe: when the caller already is the owner thread --
    ``inspect_open_database`` dispatches the whole extraction as one unit -- the
    operation runs inline. Enqueuing there would ask the owner thread to service a call
    it is itself blocked on, so the inline branch preserves that single dispatched unit
    instead of deadlocking until ``DISPATCH_TIMEOUT``.
    """

    def __init__(self, dispatcher: Any) -> None:
        self._dispatcher = dispatcher

    @property
    def current_db(self) -> Any:
        """Return the dispatcher's live DAO handle (read inside the dispatched unit)."""
        return self._dispatcher.current_db

    def is_connected(self) -> bool:
        """Forward the dispatcher's live-connection state to ``SchemaInspector``."""
        return self._dispatcher.is_connected()

    def call(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        """Run ``operation`` on the owner thread, inline when already on it."""
        if self._is_owner_thread():
            return operation(*args, **kwargs)
        return self._dispatcher.call(operation, *args, **kwargs)

    def _is_owner_thread(self) -> bool:
        """Report whether the current thread is the dispatcher's STA thread."""
        # ComDispatcher's STA thread is the authority here; a dispatcher double without
        # one (an inline recorder) takes the normal ``call`` path. Read via getattr so
        # the dependency stays explicit and private access stays audited by ruff.
        owner = getattr(self._dispatcher, "_thread", None)
        return owner is not None and owner is threading.current_thread()


class DaoInspectionPort:
    """Thin adapter over SchemaInspector for a dispatcher-owned open DAO handle.

    Every read is routed through the dispatcher, so the DAO handle is only ever
    touched on the thread that owns it; the port never stores a caller-thread proxy
    reference whose release would land outside the owning apartment.
    """

    def __init__(self, dispatcher: Any) -> None:
        self._dispatcher = _OwnerThreadDispatcher(dispatcher)
        self._inspector = SchemaInspector(self._dispatcher)

    def capabilities(self) -> InspectionCapabilities:
        return InspectionCapabilities()

    def list_tables(self) -> list[TableInfo]:
        return self._inspector.get_passive_tables()

    def get_table_link_metadata(self, table_name: str) -> dict[str, Any] | None:
        return self._inspector.get_table_link_metadata(table_name)

    def get_indexes(self, table_name: str) -> list[IndexInfo]:
        return self._inspector.get_indexes(table_name)

    def get_relationships(self) -> list[RelationshipInfo]:
        return self._inspector.get_relationships()

    def get_queries(self) -> list[QueryInfo]:
        return self._inspector.get_queries()

    def get_query_parameters(self, query_name: str) -> list[QueryParameterInfo] | None:
        return self._inspector.get_query_parameters(query_name)

    def list_container_objects(self, container_name: str) -> list[dict[str, Any]]:
        """Read one DAO container's document inventory on the dispatcher owner thread."""
        return self._dispatcher.call(self._read_container_objects, container_name)

    def get_database_properties(self) -> dict[str, str]:
        """Read every DAO database property on the dispatcher owner thread."""
        return self._dispatcher.call(self._read_database_properties)

    def _read_container_objects(self, container_name: str) -> list[dict[str, Any]]:
        """Read container Documents, keeping cheap timestamp reads optional.

        Only DAO ``Containers``/``Documents`` catalogue metadata is touched: no object is
        opened, no design view is entered, and no ``Access.Application`` is created.
        ``LastUpdated``/``DateCreated`` are recorded only when the engine exposes them;
        an unexposed timestamp is omitted rather than invented.
        """
        database = self._dispatcher.current_db
        objects: list[dict[str, Any]] = []
        for document in database.Containers(container_name).Documents:
            item: dict[str, Any] = {"name": _engine_text(document.Name)}
            last_modified = _document_timestamp(document, "LastUpdated")
            if last_modified is not None:
                item["last_modified"] = last_modified
            date_created = _document_timestamp(document, "DateCreated")
            if date_created is not None:
                item["date_created"] = date_created
            objects.append(item)
        return objects

    def _read_database_properties(self) -> dict[str, str]:
        """Read every database-level DAO property as a name -> string mapping.

        Per-property isolation (live T9 evidence): some database properties are
        unreadable in this open mode (e.g. ``Connection`` raises DAO 5003251), and
        one unreadable value must not erase the whole catalogue — the same
        discipline the fixture probes use. Unreadable properties are omitted;
        allowlisted startup properties that do not exist on a database are simply
        absent from the mapping.
        """
        database = self._dispatcher.current_db
        properties: dict[str, str] = {}
        for prop in database.Properties:
            try:
                properties[_engine_text(prop.Name)] = _engine_text(prop.Value)
            except Exception:  # noqa: BLE001 - one unreadable value never erases the rest
                continue
        return properties

    def close(self) -> None:
        """Close the dispatcher's live DAO handle on its owner thread."""
        self._dispatcher.call(self._close_current_database)

    def _close_current_database(self) -> None:
        """Close the live handle; nothing to release when the dispatcher has none."""
        database = self._dispatcher.current_db
        if database is None:
            return None
        database.Close()
        return None


# Access ODBC exposes user tables and linked-table synonyms through SQLTables; saved
# row-returning queries appear as VIEW rows. SQL_PARAM_INPUT marks a declared parameter
# in a SQLProcedureColumns row.
_ODBC_TABLE_TYPES = "TABLE,SYNONYM"
_ODBC_LOCAL_TABLE_TYPE = "TABLE"
_ODBC_QUERY_TABLE_TYPE = "VIEW"
_ODBC_SYNONYM_TABLE_TYPE = "SYNONYM"
_ODBC_PARAMETER_COLUMN_TYPE = 1

# A listed Access table always has at least one column, so the Access ODBC driver
# returning zero SQLColumns rows (without raising) for a local table means the column
# read failed. This is never an observed-empty schema. Linked (SYNONYM) tables are not
# read at all, so this failure path only applies to a local table whose read failed.
_ODBC_NO_COLUMNS_REASON = (
    "ODBC driver returned no columns for a listed local table; "
    "the column read failed"
)

# A SYNONYM is a linked table. SQLColumns/SQLStatistics on it can contact the linked
# source, so neither is called and the table is reported with an explicit gap instead.
_ODBC_LINKED_TABLE_REASON = (
    "ODBC exposes the linked table as a SYNONYM only; its columns and indexes are not "
    "read because the linked source is never resolved or contacted"
)

# Any other SQLTables row type is not provably a local table. Fail closed: do not read
# columns or statistics, because an unknown type might be backed by another source.
_ODBC_UNCLASSIFIED_TABLE_REASON = (
    "ODBC SQLTables reported a table type that is not a known local table; column and "
    "index reads are refused rather than risk contacting an unknown source"
)

# Probed on the Access ODBC driver: every property below is invisible through ODBC and
# must reach evidence as an explicit `unavailable` reason, never as a fabricated value.
_ODBC_UNAVAILABLE: dict[str, str] = {
    CAP_FIELD_REQUIRED: (
        "Access ODBC SQLColumns reports NULLABLE for every column and does not expose the DAO Required property"
    ),
    CAP_FIELD_ALLOW_ZERO_LENGTH: "Access ODBC SQLColumns has no AllowZeroLength equivalent",
    CAP_PRIMARY_KEY: (
        "Access ODBC SQLPrimaryKeys is unsupported (IM001) and SQLStatistics does not flag the primary index, "
        "so no primary key can be identified"
    ),
    CAP_RELATIONSHIPS: (
        "Access ODBC SQLForeignKeys is unsupported (IM001) and MSysRelationships cannot be read"
    ),
    CAP_QUERY_SQL: "Access ODBC exposes saved queries as VIEW rows only; their SQL text is not available",
    CAP_QUERY_TYPE: (
        "Access ODBC exposes saved queries as VIEW rows without a select, action, or DDL category"
    ),
}


def _safe_close(cursor: Any) -> None:
    """Close a catalog cursor without masking the operation's own result."""
    with contextlib.suppress(Exception):
        cursor.close()


class OdbcInspectionPort:
    """Inspection port over an already-open ODBC connection (T2).

    Reads only ODBC catalog metadata (SQLTables, SQLColumns, SQLStatistics, and
    SQLProcedureColumns). It never executes a saved query, reads a table row, or writes.
    A table is classified before any column or index read: a ``SYNONYM`` is a linked
    table and is never passed to SQLColumns or SQLStatistics, so the linked source is
    not contacted; a table whose SQLTables type is neither the known local ``TABLE``
    type nor ``SYNONYM`` also fails closed and is not read. The service records such a
    table as an explicit support gap with empty fields/indexes rather than as an
    observed-empty schema. Every property Access ODBC cannot expose is declared in
    ``capabilities()`` with a per-capability reason:

    * ``field_required`` / ``field_allow_zero_length`` -- not represented by SQLColumns.
    * ``primary_key`` -- SQLPrimaryKeys is unsupported and SQLStatistics does not flag the
      primary index, so reported indexes carry ``is_primary=False`` without claiming a key.
    * ``relationships`` -- SQLForeignKeys is unsupported and MSysRelationships is hidden.
    * ``query_sql`` / ``query_type`` -- saved queries are visible as VIEW rows only.

    The adapter owns the connection, so ``close`` is deliberately a no-op for a port
    built directly over an adapter-owned handle. A port built by
    ``open_odbc_inspection_port`` instead owns a fresh read-only connection and closes
    it exactly once.
    """

    def __init__(self, connection: Any, *, owns_connection: bool = False) -> None:
        self._connection = connection
        self._owns_connection = owns_connection
        self._closed = False
        # Populated by list_tables(). A linked or unclassified table is never read;
        # a local table whose column read returned zero rows is a failed read.
        self._tables_without_columns: set[str] = set()
        self._linked_tables: set[str] = set()
        self._unclassified_tables: set[str] = set()

    def capabilities(self) -> InspectionCapabilities:
        return InspectionCapabilities(backend_identity="ODBC", unavailable=dict(_ODBC_UNAVAILABLE))

    def list_tables(self) -> list[TableInfo]:
        tables: list[TableInfo] = []
        for name, table_type in self._catalog_rows(_ODBC_TABLE_TYPES):
            if table_type == _ODBC_SYNONYM_TABLE_TYPE:
                # Linked table: SQLColumns/SQLStatistics could contact its source.
                self._linked_tables.add(name)
                tables.append(TableInfo(name=name, fields=[]))
                continue
            if table_type != _ODBC_LOCAL_TABLE_TYPE:
                # Unknown classification: fail closed and read nothing.
                self._unclassified_tables.add(name)
                tables.append(TableInfo(name=name, fields=[]))
                continue
            fields = self._columns(name)
            if not fields:
                self._tables_without_columns.add(name)
            tables.append(TableInfo(name=name, fields=fields))
        return tables

    def get_table_link_metadata(self, table_name: str) -> dict[str, Any] | None:
        for name, table_type in self._catalog_rows(None, table=table_name):
            if name == table_name and table_type == _ODBC_SYNONYM_TABLE_TYPE:
                # The link target is local-catalogue metadata ODBC does not expose.
                self._linked_tables.add(table_name)
                return {"connect": None, "source_table_name": None}
        return None

    def get_indexes(self, table_name: str) -> list[IndexInfo]:
        if table_name in self._linked_tables:
            # Never read SQLStatistics for a linked table: it can contact the source.
            raise InspectionCapabilityUnavailableError(_ODBC_LINKED_TABLE_REASON)
        if table_name in self._unclassified_tables:
            raise InspectionCapabilityUnavailableError(_ODBC_UNCLASSIFIED_TABLE_REASON)
        if table_name in self._tables_without_columns:
            # The column read for this listed local table returned zero rows; surface the
            # same failed-table path the service uses for read failures rather than
            # letting SQLStatistics present an observed-empty index list for a broken read.
            raise AccessMcpError(_ODBC_NO_COLUMNS_REASON)
        grouped: dict[str, list[str]] = {}
        unique: dict[str, bool] = {}
        for row in self._statistics_rows(table_name):
            index_name = getattr(row, "index_name", None)
            column_name = getattr(row, "column_name", None)
            if not index_name or not column_name:
                continue  # SQL_TABLE_STAT carries no index
            grouped.setdefault(index_name, []).append(column_name)
            unique[index_name] = not bool(getattr(row, "non_unique", 0))
        return [
            IndexInfo(
                name=name,
                columns=columns,
                is_unique=unique[name],
                is_primary=False,
                ignore_nulls=False,
            )
            for name, columns in grouped.items()
        ]

    def get_relationships(self) -> list[RelationshipInfo]:
        raise InspectionCapabilityUnavailableError(_ODBC_UNAVAILABLE[CAP_RELATIONSHIPS])

    def get_queries(self) -> list[QueryInfo]:
        return [
            QueryInfo(name=name, sql="", type="")
            for name, _table_type in self._catalog_rows(_ODBC_QUERY_TABLE_TYPE)
        ]

    def get_query_parameters(self, query_name: str) -> list[QueryParameterInfo] | None:
        rows = self._procedure_column_rows(query_name)
        if rows is None:
            return None
        return [
            QueryParameterInfo(
                name=str(row.column_name),
                type=int(row.data_type),
                type_name=str(row.type_name or ""),
            )
            for row in rows
            if getattr(row, "column_type", None) == _ODBC_PARAMETER_COLUMN_TYPE
        ]

    def list_container_objects(self, container_name: str) -> list[dict[str, Any]]:
        """Refuse a DAO Containers read: ODBC exposes no container catalogue."""
        raise InspectionCapabilityUnavailableError(
            f"Access ODBC does not expose the DAO {container_name} container; "
            "engine-level container inventory requires the DAO engine"
        )

    def get_database_properties(self) -> dict[str, str]:
        """Refuse a DAO property read: ODBC exposes no database property catalogue."""
        raise InspectionCapabilityUnavailableError(
            "Access ODBC does not expose DAO database properties; startup properties "
            "require the DAO engine"
        )

    def close(self) -> None:
        """Close the connection only when this port owns it.

        A port wrapping an adapter-owned handle keeps the historical no-op semantics,
        so existing callers are unchanged. A port built by ``open_odbc_inspection_port``
        owns a fresh read-only connection and releases it exactly once; repeat calls
        never close it again.
        """
        if not self._owns_connection or self._closed:
            return None
        self._closed = True
        with contextlib.suppress(Exception):
            self._connection.close()
        return None

    def _catalog_rows(self, table_type: str | None, table: str | None = None) -> list[tuple[str, str]]:
        cursor = self._connection.cursor()
        try:
            cursor.tables(tableType=table_type, table=table)
            return [
                (str(row.table_name), str(getattr(row, "table_type", "")))
                for row in cursor.fetchall()
                if getattr(row, "table_name", None)
            ]
        finally:
            _safe_close(cursor)

    def _columns(self, table_name: str) -> list[FieldInfo]:
        cursor = self._connection.cursor()
        try:
            cursor.columns(table=table_name)
            return [
                FieldInfo(
                    name=str(row.column_name),
                    type=str(row.type_name or ""),
                    size=int(row.column_size or 0),
                    # Neither flag is observable through ODBC; capabilities() maps them
                    # to `unavailable`, so these placeholders never reach evidence.
                    required=False,
                    allow_zero_length=False,
                    default_value=None,
                )
                for row in cursor.fetchall()
                if getattr(row, "column_name", None)
            ]
        finally:
            _safe_close(cursor)

    def _statistics_rows(self, table_name: str) -> list[Any]:
        cursor = self._connection.cursor()
        try:
            cursor.statistics(table=table_name)
            return list(cursor.fetchall())
        finally:
            _safe_close(cursor)

    def _procedure_column_rows(self, query_name: str) -> list[Any] | None:
        cursor = self._connection.cursor()
        try:
            cursor.procedureColumns(procedure=query_name)
            return list(cursor.fetchall())
        except Exception:
            # A driver without SQLProcedureColumns support is unavailable, not empty.
            return None
        finally:
            _safe_close(cursor)


def open_inspection_port(dispatcher: Any) -> DaoInspectionPort:
    """Reopen the dispatcher's database engine-level read-only and wrap it for inspection.

    The documentation-inspection path must never hold a writable handle, so this
    asks the dispatcher to reopen the already-open database with DAO
    ``ReadOnly=True`` before metadata is read. Every other tool keeps its existing
    open semantics; only this path requests the read-only reopen.

    The returned port routes every COM read through the dispatcher, so the DAO handle
    is only ever touched on the STA thread that owns it -- whether a caller drives the
    port directly or the whole extraction runs inside one dispatched unit.
    """
    dispatcher.reopen_dao_database_read_only()
    return DaoInspectionPort(dispatcher)


def open_odbc_inspection_port(adapter: Any) -> OdbcInspectionPort:
    """Open an inspection port over a fresh engine-level read-only ODBC connection.

    Additive to the adapter's CRUD connection: the adapter opens a new read-only handle
    and the returned port owns and closes it exactly once. There is no writable
    fallback -- a missing read-only seam, a failing open, or an unusable open result
    raises (with credentials scrubbed) instead of reusing the adapter's writable
    connection. A setup exception after the open still releases the new handle.
    """
    opener = getattr(adapter, "open_readonly_inspection_connection", None)
    if not callable(opener):
        msg = "Documentation inspection requires an ODBC adapter with a read-only inspection connection"
        raise AccessMcpError(msg)
    try:
        connection: Any = opener()
    except Exception as error:
        raise AccessMcpError(
            redact_connection_secrets(str(error).strip() or type(error).__name__)
        ) from error
    if connection is None:
        msg = "ODBC inspection read-only connection open returned no connection"
        raise AccessMcpError(msg)
    try:
        return OdbcInspectionPort(connection, owns_connection=True)
    except Exception:
        # Never leak the freshly opened handle if wrapping it fails.
        with contextlib.suppress(Exception):
            connection.close()
        raise


def inspect_open_database(
    database: Any,
    *,
    tables: list[str] | None,
    queries: list[str] | None,
    include_query_sql: bool = False,
    include_app_logic: bool = False,
    include_app_logic_source: bool = False,
) -> EvidenceDoc:
    """Extract documentation evidence from an already-open ``DatabaseUseCases`` session.

    The DAO path reopens the dispatcher's handle engine-level read-only and reports the
    read-only handle as owned by ``database`` (the caller closes the use case); the ODBC
    path opens a separate engine-level read-only connection owned by the inspection port
    and releases it inside the inspection. Both branches fail closed when neither seam is
    available instead of reusing a writable handle. This is the single extraction seam
    the owned worker child runs; the parent frontends never call it on a parent handle.

    ``include_query_sql`` defaults to ``False``: the emitted saved-query SQL fact is then
    ``excluded`` before any capability check, so no SQL text reaches evidence. Passing
    ``True`` restores the observing behavior (DAO observed/failed, ODBC unavailable).

    ``include_app_logic`` defaults to ``False`` and keeps the emitted evidence
    byte-identical to v1. Passing ``True`` adds the Tier 1 engine-level inventory
    (DAO Containers and startup database properties) through the same read-only port;
    it never instantiates ``Access.Application``.

    The DAO extraction is dispatched as a single unit, so every COM read it performs
    happens on the dispatcher's owner thread.
    """
    adapter = getattr(database, "_adapter", None)
    dispatcher = getattr(adapter, "_dispatcher", None)
    if dispatcher is not None and dispatcher.is_connected():
        port = open_inspection_port(dispatcher)

        def leave_session_open() -> None:
            return None

        # The use case owns the DAO handle; the caller closes it after extraction.
        port.close = leave_session_open
        return dispatcher.call(
            lambda: DocumentationInspectService().inspect(
                port,
                tables=tables,
                queries=queries,
                include_query_sql=include_query_sql,
                include_app_logic=include_app_logic,
                include_app_logic_source=include_app_logic_source,
            )
        )
    if not callable(getattr(adapter, "open_readonly_inspection_connection", None)):
        msg = "Documentation inspection requires a connected DAO or ODBC backend"
        raise ValueError(msg)
    port = open_odbc_inspection_port(adapter)
    return DocumentationInspectService().inspect(
        port,
        tables=tables,
        queries=queries,
        include_query_sql=include_query_sql,
        include_app_logic=include_app_logic,
        include_app_logic_source=include_app_logic_source,
    )


class DocumentationInspectService:
    """Build evidence while preserving extraction gaps as explicit fact states."""

    def inspect(
        self,
        port: InspectionPort,
        *,
        tables: list[str] | None,
        queries: list[str] | None,
        include_query_sql: bool = False,
        include_app_logic: bool = False,
        include_app_logic_source: bool = False,
    ) -> EvidenceDoc:
        """Build evidence for the requested table/query subset.

        ``None`` selects every eligible object; an explicit list selects exactly its
        members in the given order (an empty list selects none). A requested name that
        is not an eligible table/query -- including a policy-excluded system table --
        raises ``AccessMcpError`` rather than being silently ignored. When ``tables`` is
        an explicit list, a relationship whose parent or child table is not selected is
        recorded as ``excluded`` in ``coverage.relationships`` and is not inspected, so
        every relationship that reaches evidence references two tables the document
        contains (the renderer resolves both endpoints to diagram nodes).

        ``include_query_sql`` gates only the emitted saved-query SQL fact, defaulting to
        ``False``; every other query fact (name, type, parameters) is unaffected.

        ``include_app_logic`` gates the additive Tier 1 engine-level inventory, defaulting
        to ``False``: the emitted evidence is then byte-identical to v1 (no new category,
        no new warning).

        ``include_app_logic_source`` opts into embedding the raw (untrusted) Tier 2
        source text. It is only meaningful together with ``include_app_logic``; a
        contradictory request fails closed here as a usage error.
        """
        if include_app_logic_source and not include_app_logic:
            msg = (
                "Documentation inspection requires include_app_logic=True when "
                "include_app_logic_source=True; raw source text is only meaningful with "
                "the app-logic catalogue opt-in"
            )
            raise AccessMcpError(msg)
        try:
            return self._inspect(
                port,
                tables=tables,
                queries=queries,
                include_query_sql=include_query_sql,
                include_app_logic=include_app_logic,
            )
        finally:
            port.close()

    def _inspect(
        self,
        port: InspectionPort,
        *,
        tables: list[str] | None,
        queries: list[str] | None,
        include_query_sql: bool = False,
        include_app_logic: bool = False,
    ) -> EvidenceDoc:
        capabilities = port.capabilities()
        raw_tables = port.list_tables()
        eligible_tables = [table for table in raw_tables if not _is_system_name(table.name)]
        raw_queries: list[QueryInfo] | None = None
        query_error: Exception | None = None
        try:
            raw_queries = port.get_queries()
        except Exception as error:
            query_error = error
        eligible_queries = (
            [query for query in raw_queries if not _is_system_name(query.name)]
            if raw_queries is not None
            else []
        )

        selected_tables = _select_objects("table", eligible_tables, tables)
        selected_queries = _select_objects("query", eligible_queries, queries) if raw_queries is not None else []

        coverage = Coverage()
        coverage.tables.excluded.update(
            {table.name: "System or temporary table excluded by schema-inspector rules" for table in raw_tables if _is_system_name(table.name)}
        )
        if raw_queries is not None:
            coverage.queries.excluded.update(
                {query.name: "System or temporary query excluded by schema-inspector rules" for query in raw_queries if _is_system_name(query.name)}
            )
        else:
            coverage.queries.failed["queries"] = _reason(query_error)

        table_evidence: list[TableEvidence] = []
        primary_key_reason = capabilities.reason(CAP_PRIMARY_KEY)
        for table in selected_tables:
            coverage.tables.inspected.append(table.name)
            try:
                link_metadata = port.get_table_link_metadata(table.name)
            except Exception as error:
                reason = _reason(error)
                coverage.tables.failed[table.name] = reason
                table_evidence.append(_table_evidence(table, None, failure=reason, capabilities=capabilities))
                continue
            if link_metadata is not None:
                # Classify before any column/index read: a linked table's fields and
                # indexes belong to another source, so they are never read or carried.
                coverage.tables.unsupported[table.name] = _LINKED_TABLE_NOT_INSPECTED_REASON
                table_evidence.append(
                    _table_evidence(
                        table,
                        None,
                        link_metadata,
                        indexes_unavailable_reason=_LINKED_TABLE_NOT_INSPECTED_REASON,
                        drop_fields=True,
                        capabilities=capabilities,
                    )
                )
                continue
            try:
                indexes = port.get_indexes(table.name)
            except InspectionCapabilityUnavailableError as gap:
                reason = _reason(gap)
                coverage.tables.unsupported[table.name] = reason
                table_evidence.append(
                    _table_evidence(
                        table,
                        None,
                        indexes_unavailable_reason=reason,
                        capabilities=capabilities,
                    )
                )
                continue
            except Exception as error:
                reason = _reason(error)
                coverage.tables.failed[table.name] = reason
                table_evidence.append(_table_evidence(table, None, failure=reason, capabilities=capabilities))
                continue
            if primary_key_reason is not None:
                # The port read the table but cannot identify its primary index.
                coverage.tables.unsupported[table.name] = primary_key_reason
            table_evidence.append(_table_evidence(table, indexes, link_metadata, capabilities=capabilities))

        relationship_evidence: list[RelationshipEvidence] = []
        relationship_gap = capabilities.reason(CAP_RELATIONSHIPS)
        if relationship_gap is not None:
            # An invisible capability is `unavailable`; it is never a read failure.
            coverage.relationships.unsupported["relationships"] = relationship_gap
        else:
            selected_table_names = {table.name for table in selected_tables}
            try:
                for relationship in port.get_relationships():
                    if _is_system_name(relationship.name):
                        coverage.relationships.excluded[relationship.name] = "System or temporary relationship excluded"
                        continue
                    if tables is not None and not {relationship.table, relationship.foreign_table} <= selected_table_names:
                        # A relationship whose endpoint was filtered out cannot be
                        # rendered (the renderer resolves both endpoints to table
                        # nodes), so it is deliberately not inspected. Record the
                        # scope exclusion instead of silently dropping it.
                        coverage.relationships.excluded[relationship.name] = (
                            "Relationship references a table outside the requested table subset"
                        )
                        continue
                    coverage.relationships.inspected.append(relationship.name)
                    coverage.relationships.unsupported[relationship.name] = (
                        "Relationship attributes are unavailable: DAO attributes are undecoded and ODBC supplies an empty value"
                    )
                    relationship_evidence.append(_relationship_evidence(relationship))
            except Exception as error:
                coverage.relationships.failed["relationships"] = _reason(error)

        query_evidence: list[QueryEvidence] = []
        for query in selected_queries:
            coverage.queries.inspected.append(query.name)
            parameter_data: list[QueryParameterInfo] | None = None
            parameter_failure: str | None = None
            try:
                parameter_data = port.get_query_parameters(query.name)
            except Exception as error:
                parameter_failure = _reason(error)
            evidence = _query_evidence(
                query,
                parameter_data,
                parameter_failure,
                capabilities,
                include_query_sql=include_query_sql,
            )
            if evidence.sql.state == "failed":
                coverage.queries.failed[query.name] = evidence.sql.reason or "Query SQL read failed"
            if parameter_failure is not None:
                coverage.queries.failed[query.name] = parameter_failure
            elif evidence.parameters.state == "unavailable":
                coverage.queries.unsupported[query.name] = evidence.parameters.reason or "Query parameters are unavailable"
            query_evidence.append(evidence)

        document = EvidenceDoc(
            run=RunMetadata(
                run_id=str(uuid4()),
                extractor_version="1.0",
                scope_tables=[item.name for item in selected_tables],
                scope_queries=[item.name for item in selected_queries],
            ),
            source=SourceEvidence(
                path="unspecified",
                format="unknown",
                backend_identity=capabilities.backend_identity,
                acquisition_mode="already_open_session",
                consistency_marker="caller_managed",
            ),
            coverage=coverage,
            tables=table_evidence,
            relationships=relationship_evidence,
            queries=query_evidence,
            # ``None`` unless the caller opted in: the default payload keeps the exact
            # v1 ``data`` member set, so a default call stays byte-identical (S4).
            app_logic=_read_app_logic(port) if include_app_logic else None,
        )
        # Serializing at the seam both validates the document and enforces the evidence
        # byte cap for generated evidence; frontends serialize the returned document
        # again to emit it.
        document.to_dict()
        return document


def _is_system_name(name: str) -> bool:
    return name.startswith("MSys") or name.startswith("~")


def _select_objects(kind: str, eligible: list[Any], requested: list[str] | None) -> list[Any]:
    if requested is None:
        return eligible
    by_name = {item.name: item for item in eligible}
    unknown = [name for name in requested if name not in by_name]
    if unknown:
        raise AccessMcpError(f"Unknown requested {kind} name(s): {', '.join(unknown)}")
    return [by_name[name] for name in requested]


def _reason(error: Exception | None) -> str:
    if error is None:
        return "Metadata read failed"
    return redact_connection_secrets(str(error).strip() or type(error).__name__)


def _engine_text(value: Any) -> str:
    """Normalize an engine string read, stripping ACE's trailing NUL padding."""
    if value is None:
        return ""
    return str(value).rstrip("\x00")


def _document_timestamp(document: Any, attribute: str) -> str | None:
    """Read a DAO Document date property when the engine exposes it cheaply.

    ``LastUpdated``/``DateCreated`` are optional catalogue metadata: an engine or DAO
    surface that does not expose one is omitted from the object entry, never invented
    and never promoted to a failed fact.
    """
    try:
        value = getattr(document, attribute)
    except Exception:  # noqa: BLE001 - an unexposed Document date is absent, not a failure
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _container_object_values(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a port's container object list to the evidence shape."""
    normalized: list[dict[str, Any]] = []
    for item in objects:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        entry: dict[str, Any] = {"name": name.strip()}
        for key in ("last_modified", "date_created"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = value.strip()
        normalized.append(entry)
    return normalized


def _startup_property_provenance(name: str) -> FactProvenance:
    return FactProvenance("startup_property", name, "value")


def _startup_property_value(value: Any) -> str:
    """Record a property value as a string; ``None`` is an empty string, never ``"None"``."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _read_app_logic(port: InspectionPort) -> AppLogicEvidence:
    """Read the Tier 1 engine-level container inventory and startup properties.

    Every container reads in its own exception boundary and every startup property is
    its own fact, so one failed read never erases another category. An engine that
    cannot expose DAO Containers/properties at all (ODBC) is ``unavailable`` with the
    port's engine-limitation explanation, never a fabricated empty list.
    """
    containers: list[ContainerInventoryEvidence] = []
    for category, container in _CONTAINER_CATEGORIES:
        provenance = FactProvenance(category, container, "objects")
        fact: FactStatus[Any]
        try:
            objects = port.list_container_objects(container)
        except InspectionCapabilityUnavailableError as gap:
            fact = FactStatus.unavailable(_reason(gap), provenance)
        except Exception as error:
            fact = FactStatus.failed(_reason(error), provenance)
        else:
            fact = FactStatus.observed(_container_object_values(objects), provenance)
        containers.append(ContainerInventoryEvidence(category, container, ENGINE_DAO_CONTAINERS_METHOD, fact))
    return AppLogicEvidence(containers=containers, startup_properties=_read_startup_properties(port))


def _read_startup_properties(port: InspectionPort) -> list[StartupPropertyEvidence]:
    """Read every allowlisted startup property as its own engine-level fact."""
    names = sorted(_STARTUP_PROPERTY_ALLOWLIST)
    try:
        raw = port.get_database_properties()
    except InspectionCapabilityUnavailableError as gap:
        return [
            StartupPropertyEvidence(
                name,
                ENGINE_DAO_PROPERTIES_METHOD,
                FactStatus.unavailable(_reason(gap), _startup_property_provenance(name)),
            )
            for name in names
        ]
    except Exception as error:
        return [
            StartupPropertyEvidence(
                name,
                ENGINE_DAO_PROPERTIES_METHOD,
                FactStatus.failed(_reason(error), _startup_property_provenance(name)),
            )
            for name in names
        ]
    return [
        StartupPropertyEvidence(
            name,
            ENGINE_DAO_PROPERTIES_METHOD,
            FactStatus.observed(_startup_property_value(raw[name]), _startup_property_provenance(name)),
        )
        for name in names
        if name in raw
    ]


def _provenance(kind: str, name: str, prop: str) -> FactProvenance:
    return FactProvenance(kind, name or "unnamed", prop)


def _table_evidence(
    table: TableInfo,
    indexes: list[IndexInfo] | None,
    link_metadata: dict[str, Any] | None = None,
    failure: str | None = None,
    capabilities: InspectionCapabilities | None = None,
    indexes_unavailable_reason: str | None = None,
    drop_fields: bool = False,
) -> TableEvidence:
    capabilities = capabilities or InspectionCapabilities()
    fields = [] if drop_fields else [_field_evidence(field, capabilities) for field in table.fields]
    if indexes_unavailable_reason is not None:
        # The column/index read was deliberately not performed (e.g. a linked table);
        # the primary key is an explicit gap, never a failed or observed-empty fact.
        primary_key = FactStatus.unavailable(
            indexes_unavailable_reason, _provenance("table", table.name, "primary_key")
        )
        index_evidence: list[IndexEvidence] = []
    elif indexes is None:
        primary_key = FactStatus.failed(failure or "Index metadata read failed", _provenance("table", table.name, "primary_key"))
        index_evidence = []
    else:
        index_evidence = [
            IndexEvidence(item.name, list(item.columns), item.is_unique, item.is_primary, item.ignore_nulls)
            for item in indexes
        ]
        if primary_reason := capabilities.reason(CAP_PRIMARY_KEY):
            # A read failure stays `failed`; an unobservable primary flag is `unavailable`.
            primary_key = FactStatus.unavailable(primary_reason, _provenance("table", table.name, "primary_key"))
        else:
            primary = next((item for item in indexes if item.is_primary), None)
            primary_key = FactStatus.observed(list(primary.columns) if primary else [], _provenance("table", table.name, "primary_key"))
    kind = "linked" if link_metadata else "local"
    # Never carry a raw connection string into evidence: drop credential pairs first.
    connect = sanitize_connection_string(link_metadata.get("connect")) if link_metadata else None
    source_table_name = link_metadata.get("source_table_name") if link_metadata else None
    return TableEvidence(
        name=table.name,
        fields=fields,
        primary_key=primary_key,
        indexes=index_evidence,
        kind=kind,
        connect=connect,
        source_table_name=source_table_name,
    )


def _field_evidence(field: FieldInfo, capabilities: InspectionCapabilities) -> FieldEvidence:
    """Map one field, keeping unobservable properties as explicit unavailable facts."""
    if required_reason := capabilities.reason(CAP_FIELD_REQUIRED):
        required = FactStatus.unavailable(required_reason, _provenance("field", field.name, "required"))
    else:
        required = FactStatus.observed(field.required, _provenance("field", field.name, "required"))
    if zero_length_reason := capabilities.reason(CAP_FIELD_ALLOW_ZERO_LENGTH):
        allow_zero_length = FactStatus.unavailable(zero_length_reason, _provenance("field", field.name, "allow_zero_length"))
    else:
        allow_zero_length = FactStatus.observed(field.allow_zero_length, _provenance("field", field.name, "allow_zero_length"))
    return FieldEvidence(
        name=field.name,
        type=FactStatus.observed(field.type, _provenance("field", field.name, "type")),
        size=FactStatus.observed(field.size, _provenance("field", field.name, "size")),
        required=required,
        allow_zero_length=allow_zero_length,
        default_value=_field_default_fact(field),
    )


def _field_default_fact(field: FieldInfo) -> FactStatus[Any]:
    """Map a field default without fabricating `observed null` for ports that expose none."""
    provenance = _provenance("field", field.name, "default_value")
    if field.default_value is None:
        return FactStatus.unavailable(
            "Field default value was not exposed by the engine or runtime", provenance
        )
    return FactStatus.observed(field.default_value, provenance)


def _query_evidence(
    query: QueryInfo,
    parameter_data: list[QueryParameterInfo] | None = None,
    parameter_failure: str | None = None,
    capabilities: InspectionCapabilities | None = None,
    *,
    include_query_sql: bool = False,
) -> QueryEvidence:
    """Map one saved query, keeping the emitted SQL fact an explicit opt-in.

    ``include_query_sql`` defaults to ``False``: the SQL fact is then ``excluded`` before
    any capability check, so no SQL text is emitted for either backend. When enabled, the
    historical semantics are unchanged: an unexpressible capability is ``unavailable``,
    blank or unreadable SQL is ``failed``, and a real QueryDef SQL string is ``observed``.
    """
    capabilities = capabilities or InspectionCapabilities()
    provenance_sql = _provenance("query", query.name, "sql")
    sql = query.sql if isinstance(query.sql, str) else ""
    if not include_query_sql:
        sql_fact: FactStatus[str] = FactStatus.excluded(_QUERY_SQL_EXCLUDED_REASON, provenance_sql)
    elif sql_reason := capabilities.reason(CAP_QUERY_SQL):
        sql_fact = FactStatus.unavailable(sql_reason, provenance_sql)
    elif sql.strip():
        sql_fact = FactStatus.observed(sql, provenance_sql)
    else:
        sql_fact = FactStatus.failed("QueryDef SQL is blank or could not be read", provenance_sql)
    query_type = str(query.type).strip().casefold()
    mapped_type = _QUERY_TYPES.get(query_type, "unknown")
    type_provenance = _provenance("query", query.name, "type")
    if type_reason := capabilities.reason(CAP_QUERY_TYPE):
        type_fact = FactStatus.unavailable(type_reason, type_provenance)
    else:
        type_fact = FactStatus.observed(mapped_type, type_provenance)
    return QueryEvidence(
        name=query.name,
        sql=sql_fact,
        type=type_fact,
        parameters=_query_parameters_fact(query.name, parameter_data, parameter_failure),
    )


def _query_parameters_fact(
    query_name: str,
    parameter_data: list[QueryParameterInfo] | None,
    parameter_failure: str | None,
) -> FactStatus[Any]:
    """Map producer parameter data at the seam without fabricating emptiness.

    ``None`` means the producer cannot expose parameters, so the fact is
    ``unavailable`` (never an invented empty/null). An empty list is a known empty
    (observed), which is a different thing. A per-query read failure is ``failed``.
    Parameter names and the producer's own type codes/names pass through unchanged
    (DAO field-type codes for the DAO port; ODBC SQL data-type codes for the ODBC port).
    """
    provenance = _provenance("query", query_name, "parameters")
    if parameter_failure is not None:
        return FactStatus.failed(parameter_failure, provenance)
    if parameter_data is None:
        return FactStatus.unavailable(
            "Query parameters were not exposed by the engine or runtime", provenance
        )
    return FactStatus.observed([parameter.to_dict() for parameter in parameter_data], provenance)


def _relationship_evidence(relationship: RelationshipInfo) -> RelationshipEvidence:
    return RelationshipEvidence(
        name=relationship.name,
        parent_table=relationship.foreign_table,
        child_table=relationship.table,
        parent_columns=list(relationship.foreign_columns),
        child_columns=list(relationship.columns),
        attributes=FactStatus.unavailable(
            "Relationship attributes are not decoded (DAO attributes are an undecoded bit string; ODBC supplies an empty value)",
            _provenance("relationship", relationship.name, "attributes"),
        ),
    )
