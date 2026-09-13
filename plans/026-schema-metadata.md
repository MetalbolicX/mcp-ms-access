# Plan 026: Backend schema-metadata completeness (pre-024 foundation)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat ce3d6c8..HEAD -- src/ms_access_mcp/ tests/ plans/README.md`
> Plans 022 and 023 landed before this plan was written — their changes (ddl
> profile, xlsx export) are EXPECTED drift. Anything else in scope changing
> means: compare excerpts against live code; on mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: plans/023-xlsx-iisam-export.md (planned at its HEAD `ce3d6c8`; also touches `application/database.py` — sequence only)
- **Category**: feature
- **Planned at**: commit `ce3d6c8`, 2026-09-11

## Execution mode

`@medium` + strict TDD: RED (fake-COM dispatcher tests in the existing
`tests/unit/test_backend_dao.py` style) → GREEN (real implementations) →
REFACTOR. This plan completes backend capabilities that registered MCP tools
already call — no new user-facing surface, so no SDD cycle.

## Why this matters

Plan 024's SDD exploration (`openspec/changes/plan-024-external-database-migration-profile/exploration.md`)
blocked the migration profile: the source-schema seam cannot supply the
PK/FK/index metadata the migration contract requires. The gap is wider than
migration — the entire DAO schema-read surface is a stub, two registered MCP
tools are latently broken, and plan 025's `get_er_diagram` / `generate_sql`
have no backend to call. This plan is the foundation: it ports the oracle's
proven DAO/ODBC schema extraction, after which plans 024 and 025 can proceed.

## Target architecture (this project's layering — the oracle is BEHAVIOR, not layout)

The oracle provides the behavioral spec (what COM calls, what shapes). Module
placement and type conventions follow THIS project's architecture (plan 014
layering), NOT the oracle's (`adapters/`+`services/`+pydantic `models/`
package). Do not mirror oracle file paths.

```
mcp_tools/*.py      thin wrappers — core.py:56 _result_to_dict folds Ok/Err →
                    {"success","data","rowcount"} / {"success":False,"error"};
                    core.py:49 to_mcp_result does json.dumps(payload)
application/*.py    use cases — call adapter methods, convert dataclasses to
                    dicts via asdict, wrap in Ok/Err. Precedent:
                    automation.py:129-133 `_to_dict` (asdict when the object
                    has __dataclass_fields__)
backends/*.py       adapters + SRP-split helper modules. Precedents:
                    dao.py delegates to db_operations.py / dao_sql.py /
                    schema_inspector.py; versioning backend in its own module.
                    Backends return domain dataclasses from models.py (or
                    plain {"success":...} dicts for command operations)
models.py           FLAT MODULE, stdlib dataclasses. Header: "Models ported
                    from oracle src/ms_access_mcp/models/database.py using
                    stdlib dataclasses". There is NO models/ package and no
                    pydantic in this project — new models are APPENDED here
backends/protocols.py   structural Protocols per consumer ("slots actually
                    used") — see VersioningDispatcherProtocol :14,
                    AccessDispatcherProtocol :30
capabilities.py     backend capability flags (pinned by
                    tests/unit/test_capabilities.py)
```

**The serialization contract (load-bearing)**: `to_mcp_result` runs
`json.dumps` on the use-case dict, so anything in `Ok.data` must be
JSON-serializable. Domain dataclasses are NOT. Therefore: backends return
dataclasses; USE CASES convert with `asdict` before `_ok(...)`. Today's
`get_tables`/`get_indexes`/`get_relationships` use cases
(`database.py:174-264`) pass adapter output straight into `_ok(data=...)` —
they survive only because the stubs return `[]` (json.dumps([]) works). The
moment this plan makes real dataclasses flow, those use cases MUST convert,
or the MCP boundary raises TypeError. This conversion step is Step 7c below
and is not optional.

## Current state

### The gap (verified against live code at `ce3d6c8`)

**1. `backends/schema_inspector.py` (64 lines) — every method is a stub.**
Read in full during planning. Representative excerpts, verbatim:

```python
class SchemaInspector:
    """DAO schema read operations via ComDispatcher."""

    def __init__(self, dispatcher: Any) -> None:
        self._dispatcher = dispatcher

    def get_tables(self) -> list:
        if not self._dispatcher.is_connected():
            return []
        return []

    def get_indexes(self, table_name: str) -> list:
        return []

    def get_relationships(self) -> list:
        return []

    def get_table_schema_plan(self) -> tuple:
        return ([], "unknown")

    def generate_sql(self, output_path: str) -> dict:
        return {"success": False, "error": "Not implemented"}

    def create_relationship(...) -> dict:
        return {"success": False, "error": "Not implemented"}

    def delete_relationship(self, table_name: str, relationship_name: str) -> dict:
        return {"success": False, "error": "Not implemented"}
```

(get_system_tables, get_queries, get_object_metadata, get_database_statistics
are equally empty.) Note: `create_relationship`/`delete_relationship` back
plan 022's `ddl` profile tools — those tools are registered but the DAO
backend currently returns "Not implemented" for them.

**2. `dao.py:185-226` — pure delegation to the stub.** Verbatim pattern:

```python
    @property
    def _schema_inspector(self) -> Any:
        if self._schema is None:
            from .schema_inspector import SchemaInspector
            self._schema = SchemaInspector(self._dispatcher)
        return self._schema

    def get_tables(self) -> list:
        return self._schema_inspector.get_tables()
```

All schema methods delegate the same way (`get_system_tables :196`,
`get_queries :200`, `get_indexes :204`, `get_relationships :208`,
`get_object_metadata :212`, `get_table_schema_plan :216`, `generate_sql :220`,
`get_database_statistics :224`, `create_relationship :277→:293`,
`delete_relationship :301→:303`).

**3. `odbc.py` — partial real introspection, stubbed relationships/indexes.**

- `get_tables :173` / `get_columns :191` REAL (SQLTables / SQLColumns; columns
  yield `{"name", "type", "size", "nullable"}` — no default, no autoincrement).
- `get_indexes :213-217` STUB: `if not connected: return []; return []`.
- `get_relationships :219-223` STUB: returns `[]` unconditionally.
- `get_object_metadata :377` → `{}`, `get_table_schema_plan :381` →
  `([], "unknown")`, `generate_sql :385` → not implemented,
  `get_database_statistics :389` → `{}`.

**4. Latent bug A — `get_object_metadata` arity mismatch.**
`application/database.py:281` calls `adapter.get_object_metadata(object_name,
object_type)` (2 positional args); every real backend accepts 1
(`dao.py:212`, `odbc.py:377`, `schema_inspector.py:35`). Result: TypeError →
caught → Err on every real backend. The plan 022 `ddl` MCP tool
`get_object_metadata` is broken in production; unit tests mask it because the
fake (`tests/unit/test_database.py:50`) matches the wrong 2-arg signature.

**5. Latent bug B — `get_table_schema` calls a method no backend has.**
`application/database.py:224` calls `adapter.get_table_schema(table_name)`;
grep confirms NO backend defines `get_table_schema` (only
`get_table_schema_plan` exists). The registered MCP core tool
`get_table_schema` (`mcp_tools/core.py:190-208`) therefore always errors.
The oracle implements this at the MCP layer by composing `get_tables()`
(`oracle mcp/schema.py:31-37`):

```python
def _get_table_schema(adapter, table_name: str):
    tables = adapter.get_tables()
    for table in tables:
        if table.name == table_name:
            return table
    return None
```

**6. Live models (`models.py`)** — a FLAT module — already provides
dataclasses `FieldInfo` (name, type, size, required, allow_zero_length,
default_value, is_autoincrement), `TableInfo` (name, fields, record_count,
primary_key), `RelationshipInfo` (name, table, foreign_table, attributes,
columns, foreign_columns), `ForeignKeyInfo`, `IndexInfo` (name, columns,
is_unique, is_primary, ignore_nulls). Header: "Models ported from oracle
src/ms_access_mcp/models/database.py using stdlib dataclasses" — the 5 new
schema-plan dataclasses in this plan are APPENDED to `models.py` following
the same convention (no new module, no package conversion, no pydantic).

**6b. Serialization gap (this plan's hidden constraint).**
`mcp_tools/core.py:49` `to_mcp_result` does `json.dumps(result)`; the use
cases at `database.py:174-264` pass raw adapter output into `_ok(data=...)`.
With stub backends returning `[]` this works; with real dataclass returns it
crashes at the MCP boundary. The established fix convention is
`asdict`-in-the-use-case (`automation.py:129-133`). Plan 025's
`get_er_diagram` (planned to compose these use cases) depends on the
converted dict shapes landing here first.

**7. The live dispatcher exposes `access_app`.**
`backends/protocols.py:47-49` (`AccessDispatcherProtocol.access_app` property)
and `:52-54` (`current_db`). The same ComDispatcher serves DaoAdapter's
SchemaInspector, so `dispatcher.access_app.CurrentProject.AllForms` etc. are
available with graceful fallback.

**8. Existing fake-COM test pattern.**
`tests/unit/test_backend_dao.py` provides `FakeDispatcher`, `FakeTableDefs`,
`FakeTableDef`, `FakeComError` (see `test_refresh_linked_table_* :832-853`).
The delegation tests at `:870-884` carry a comment that will become obsolete:
"Schema/database-property delegation requires real collaborators — only the
not-connected / lazy-init seam is safe to characterize here." This plan makes
real characterization possible; update that comment.

### Oracle behavioral spec (READ-ONLY oracle: `D:\code\python\ms-access-mcp-server`)

**Type map** — `adapters/schema_inspector.py:49-68`, verbatim values:
`1:Boolean, 2:Byte, 3:Integer, 4:Long Integer, 5:Currency, 6:Single,
7:Double, 8:Date/Time, 10:Text, 11:Binary, 12:Memo, 15:GUID, 16:Big Integer,
17:Unsigned Byte, 18:Unsigned Integer, 19:Unsigned Long Integer, 20:Decimal`;
fallback `f"Unknown({n})"`.

**get_tables** — `:74-119`: iterate `db.TableDefs`; skip names starting
`MSys` or `~`; skip `tdef.Attributes & 0x80000000` (hidden/linked); fields as
`{"name", "type": _access_type_name(fld.Type), "size": fld.Size, "required":
bool(fld.Required), "allow_zero_length": bool(fld.AllowZeroLength)}`;
record_count via `db.OpenRecordset("SELECT COUNT(*) FROM [<name>]")` with
silent 0 on failure; returns `list[TableInfo]`.

**get_indexes** — `:224-253`: `tdef.Indexes`; skip column-less indexes;
`IndexInfo(name=idx.Name, columns=[f.Name for f in idx.Fields],
is_unique=bool(getattr(idx,"Unique",False)), is_primary=bool(getattr(idx,
"Primary",False)), ignore_nulls=bool(getattr(idx,"IgnoreNulls",False)))`.

**get_relationships** — `:442-472`: iterate `db.Relations`; skip `~`/`MSys`;
child cols = `rel.Fields(j).Name`; parent cols = `rel.Fields(j).ForeignName`;
`RelationshipInfo(name=rel.Name, table=rel.Table, foreign_table=
rel.ForeignTable, attributes=str(rel.Attributes), columns=child,
foreign_columns=parent)`.

**get_table_schema_plan** — `:321-436`: builds `TableSchema(name, columns,
primary_key, foreign_keys, indexes)` + `UnknownMetadata` flags. Column
extraction (`:370-390`): `default_value = str(fld.DefaultValue)` (flag
`unknown.defaults` on exception), `is_autoincrement = bool(attributes & 0x10)`,
`allow_null = not Required`, `max_length = Size if Size>0 else None`. PK
detection (`:392-412`): `idx.Primary → primary_key = [f.Name for f in
idx.Fields]`; non-primary indexes → `IndexSchema(name, columns, is_unique)`;
empty PK sets `unknown.primary_keys`. FK orientation (`:336-357`):
`ForeignKeySchema(name=rel.Name, columns=parent_columns(ForeignName),
referenced_table=rel.Table, referenced_columns=child_columns)` attached to
`rel.ForeignTable`'s table.

**create_relationship / delete_relationship** — `:478-546`, verbatim core:

```python
rel = db.CreateRelation(relationship_name, table_name, foreign_table)
for i in range(len(columns)):
    f = rel.CreateField(columns[i])
    f.ForeignName = foreign_columns[i]
    rel.Fields.Append(f)
db.Relations.Append(rel)          # create
db.Relations.Delete(relationship_name)  # delete
```

(Live `dao.py:288-292` already does the columns-length check before
delegating — keep it there.)

**get_object_metadata** — `:552-592`: check tables first (name match →
`{"name", "type": "table", "properties": {"record_count": str(count)}}`),
then `access_app.CurrentProject.AllForms / AllReports / AllMacros` with
property-bag extraction; graceful `{}` on failure.

**get_database_statistics** — `:598-662`: `{"success": True, "objects":
{tables, queries, forms, reports, macros, modules}, "file": {name,
size_bytes, modified}, "system": {access_version, com_available}}`. Tables/
queries from `db.TableDefs.Count`/`db.QueryDefs.Count`; UI objects from
`access_app.CurrentProject.All*`; file info via `os.stat(db.Name)`; version
via `app.Version`. Zeroed + `com_available: False` when disconnected/failed.

**generate_sql** — `:664-838` + `services/sql_generator.py` (168 lines):
orchestrates direct DAO reads (enriched `FieldInfo` with autoincrement via
`attrs & 0x10`, `TableInfo.primary_key` from `idx.Primary`, `ForeignKeyInfo`
from relations) then `JetSqlGenerator(tables, relationships,
foreign_keys).generate()` → `CREATE TABLE` statements with FK topological
sort. `JET_TYPE_MAP` (verbatim from `sql_generator.py:7-24`):
`Text→VARCHAR({size}), Memo→MEMO, Long Integer→LONG, Integer→SHORT,
Byte→BYTE, Boolean→YESNO, Date/Time→DATETIME, Currency→CURRENCY,
Counter/AutoNumber→AUTOINCREMENT, Single→SINGLE, Double→DOUBLE,
Decimal→DECIMAL, OLE Object→LONGBINARY, GUID→CHAR(38), Binary→VARBINARY`,
fallback `VARCHAR(255)`. Writes `"\n".join(statements)` + trailing newline to
`output_path`. Uses `db` directly inside the dispatched `_do()` (avoids
nested-dispatch deadlock — oracle comment `:670-672`).

**ODBC relationships** — `adapters/odbc_schema_reader.py` (full 133 lines
read): `MSYS_RELATIONSHIPS_QUERY = "SELECT szObject, szColumn,
szReferencedObject, szReferencedColumn FROM MSysRelationships WHERE szObject
IS NOT NULL AND szReferencedObject IS NOT NULL ORDER BY szObject,
szReferencedObject, szColumn"`; groups per-column rows by `(child, parent)`
into one `RelationshipInfo(name=f"FK_{child}_{parent}", table=child,
foreign_table=parent, attributes="", columns, foreign_columns)`; deterministic
sort by name; degrades to `[]` on denial (MSysRelationships hidden in many
Jet/ACE builds — log warning, return []).

**Migration-style models** — `oracle models/migration.py:37-113`, exact
shapes (port as stdlib dataclasses APPENDED to live `models.py` — flat-module
convention, see Target architecture):
`ColumnSchema(name, source_type, max_length: int|None, allow_null=True,
is_autoincrement=False, default_value: str|None)` with name/source_type
stripped and empty-string default_value→None; `ForeignKeySchema(name,
columns, referenced_table, referenced_columns)`; `IndexSchema(name, columns,
is_unique=False)`; `UnknownMetadata(primary_keys=False, foreign_keys=False,
defaults=False, indexes=False, autoincrement=False)`; `TableSchema(name,
columns, primary_key, foreign_keys, indexes)` with name stripped.
`ExtractedSchema` stays OUT of this plan — plan 024 owns it and will compose
these building blocks (its SDD design phase decides where its own models
live; this flat-module convention applies there too unless the SDD cycle
deliberately restructures).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Unit suite | `uv run pytest tests/unit -q` | all pass, 0 failed |
| Full suite | `uv run pytest -q` | 0 failed (2 skipped normal) |
| Integration | `uv run pytest -q -m "com_integration or odbc_integration"` | 0 failed |
| Lint | `uv run ruff check .` | `All checks passed!` |
| Typecheck | `uv run pyright` | `0 errors, 0 warnings, 0 informations` |
| Kill stray Access | `taskkill /F /IM MSACCESS.EXE` | exit 0 or "not found" — either fine |

## Scope

**In scope** (the only files you should modify):

- `src/ms_access_mcp/backends/schema_inspector.py` (real implementations)
- `src/ms_access_mcp/backends/sql_generator.py` (create — JetSqlGenerator
  port; SRP-split convention, sibling of `db_operations.py`/`dao_sql.py`)
- `src/ms_access_mcp/backends/odbc_schema_reader.py` (create — MSys reader
  port; same convention)
- `src/ms_access_mcp/backends/odbc.py` (wire relationships/indexes/PK only)
- `src/ms_access_mcp/backends/protocols.py` (append
  `SchemaDispatcherProtocol` — structural protocol for the inspector's
  dispatcher slots: `is_connected`, `current_db`, `access_app`, `call`;
  follow the "slots actually used" comment convention at `:13`/`:29`)
- `src/ms_access_mcp/models.py` (APPEND the 5 schema-plan dataclasses —
  flat-module convention; do NOT create a models package or new module)
- `src/ms_access_mcp/application/database.py` (bug A fix at `:281`; bug B fix
  at `:224` — compose from `get_tables`; **Step 7c asdict serialization** in
  `get_tables`/`get_indexes`/`get_relationships`/`get_table_schema`; nothing
  else)
- `src/ms_access_mcp/capabilities.py` (ONLY if its schema-introspection flags
  contradict the new reality — review in Step 12; update flags + extend
  `tests/unit/test_capabilities.py` accordingly)
- `tests/unit/test_backend_dao.py` (extend fakes; real characterization tests;
  update the obsolete `:870-872` comment)
- `tests/unit/test_schema_inspector.py` (create — fake-dispatcher tests for
  every inspector method; this is where most RED lives)
- `tests/unit/test_odbc_schema_reader.py` (create — row-grouping unit tests
  with fake cursor rows; no real ODBC needed)
- `tests/unit/test_models_schema.py` (create — dataclass shape/normalization
  for the models.py additions)
- `tests/unit/test_database.py` (update FakeAdapter `get_object_metadata` to
  1-arg at `:50`; get_table_schema composition tests; Step 7c dataclass→dict
  conversion tests)
- `tests/integration/test_schema_extraction.py` (create — com_integration,
  fixture-copy round trips)
- `plans/README.md` (status rows)

**Out of scope**:

- `ExtractedSchema`, `models/migration.py`, anything migration — plan 024.
  (If plan 024's SDD design wants a `models/` package, THAT cycle makes the
  restructuring decision; this plan appends to the flat `models.py` and must
  not pre-convert.)
- Converting `models.py` into a package — no restructure without need.
- Pydantic anywhere — this project uses stdlib dataclasses.
- `backends/ado.py`, `backends/access.py` — no table-schema surface by design.
- `backends/versioning.py` / `export_schema_ddl` — separate concern; do NOT
  refactor it to share code with `sql_generator.py` (duplication is
  deliberate here; revisit only in a dedicated consolidation plan).
- ODBC `get_table_schema_plan` / `generate_sql` / `get_object_metadata` /
  `get_database_statistics` — stay stubbed sentinels (documented capability
  gap; plan 024's spec phase decides if ODBC needs more).
- `pyproject.toml`, `uv.lock`, `.opencode/` — never touch.
- ADO/Access adapters, MCP tool registration, CLI wiring — tools already
  registered; this plan makes their backends real.

## Git workflow

- Branch: `plan/026-schema-metadata` (from current HEAD)
- Conventional commits, English, no AI attribution. Suggested:
  `feat(backends): real DAO schema introspection via SchemaInspector port`
- Do NOT push. Do NOT commit `uv.lock` or `.opencode/`.

## Steps

### Step 1: Pre-flight

Drift check; `git status --porcelain` (expect ` M uv.lock`, `?? .opencode/`,
`?? plans/02*`); `taskkill /F /IM MSACCESS.EXE`; branch.

**Verify**: on branch `plan/026-schema-metadata`; `schema_inspector.py`
matches the stub excerpts above.

### Step 2: `models.py` schema-plan dataclasses (RED → GREEN)

RED: `tests/unit/test_models_schema.py` — dataclass field lists match the
oracle shapes exactly; `ColumnSchema("  id  ", " Long Integer ")` normalizes
to stripped values; `default_value="  "`→None; `TableSchema` name stripped.

GREEN: APPEND the 5 dataclasses to `src/ms_access_mcp/models.py` (verbatim
field lists from "Current state"; `__post_init__` for normalization). Match
the file's existing header and stdlib-dataclass style — no new module, no
package, no pydantic.

**Verify**: new tests pass; `uv run python -c "from ms_access_mcp.models import ColumnSchema, TableSchema, ForeignKeySchema, IndexSchema, UnknownMetadata"` succeeds.

### Step 3: Inspector — type map + table reads (RED → GREEN)

RED: `tests/unit/test_schema_inspector.py` — fake dispatcher whose
`FakeDb.TableDefs` yields fake tdefs (Name, Attributes, Fields with
Name/Type/Size/Required/AllowZeroLength); `OpenRecordset` returns COUNT.
Assert: hidden (`0x80000000`) and `MSys`/`~` skipped; fields mapped through
`_access_type_name`; record_count populated; disconnected → `[]`. Same for
`get_system_tables` (record_count hardcoded 0 per oracle `:147`) and
`get_queries` (QueryDefs walk — oracle parity: name/type/SQL if present;
check oracle `:150-196` before finalizing shape).

GREEN: implement in `schema_inspector.py` (type map + the three reads).
Follow the oracle `dispatcher.call(_do)` wrapper and per-method
`is_connected` guard. Also append `SchemaDispatcherProtocol` to
`backends/protocols.py` (slots actually used: `is_connected()`,
`current_db`, `access_app`, `call`) and type the inspector's dispatcher with
it — replacing `dispatcher: Any`. Precedent: plan 013 did exactly this
conversion for `AccessAdapter` ("from `_dispatcher: Any` to
`AccessDispatcherProtocol`" — `plans/013-adapter-protocol.md:64-67`). If the
protocol fights pyright on the fake dispatcher, keep `Any` and note it in the
final report rather than weakening the Protocol.

**Verify**: tests pass; `dao.py` delegation unchanged.

### Step 4: Inspector — indexes + relationships (RED → GREEN)

RED: fake tdef with `Indexes` (Primary/Unique/IgnoreNulls flags, Fields) —
assert IndexInfo shapes; column-less indexes skipped; `get_relationships`
with fake `db.Relations` (Fields(j).Name / ForeignName) — assert child/parent
orientation exactly per oracle (`columns`=child, `foreign_columns`=parent).

GREEN: implement both.

**Verify**: tests pass.

### Step 5: Inspector — relationship writers (RED → GREEN)

RED: fake `db.CreateRelation` returning fake rel with `CreateField`/`Fields.
Append`; fake `db.Relations.Append/Delete`. Assert exact call sequence per
the oracle excerpt (create); assert `Relations.Delete(name)` (delete); assert
COM failure → `{"success": False, "error": <message>}`.

GREEN: implement both. (The columns-length check stays in `dao.py`.)

**Verify**: tests pass. NOTE: this unbreaks plan 022's ddl tools
`create_relationship`/`delete_relationship` on real backends.

### Step 6: Inspector — schema plan + UnknownMetadata (RED → GREEN)

RED: fake db with tables + relations exercising every branch of oracle
`:321-436`: autoincrement via `Attributes & 0x10`; `allow_null = not
Required`; PK from `idx.Primary`; FK attached to `rel.ForeignTable`'s table
with parent-columns orientation; each `unknown.*` flag set by its failure
injection. Return shape: `(list[TableSchema], UnknownMetadata)` — the live
`([], "unknown")` sentinel is REPLACED by this real shape (update any test
that pinned `"unknown"`).

GREEN: implement `get_table_schema_plan` returning the real tuple.

**Verify**: tests pass.

### Step 7: Use-case bug fixes + object metadata (RED → GREEN)

RED in `tests/unit/test_database.py`:
1. FakeAdapter `get_object_metadata(self, object_name: str)` — 1 arg (update
   the existing fake at `:50`; this is a justified fake correction, not a
   behavior change). Use case test asserts the call succeeds (previously
   TypeError→Err on real backends).
2. `get_table_schema` composition: FakeAdapter `get_tables()` returning
   TableInfo-shaped fakes; assert `get_table_schema("T")` returns the matching
   table; missing → `Err("Table 'X' not found")` (oracle `schema.py:86-88`
   behavior).

GREEN: `database.py:281` → `adapter.get_object_metadata(object_name)`;
`database.py:210-227` → compose from `get_tables()` per oracle `:31-37`.

GREEN (inspector): real `get_object_metadata` per oracle `:552-592` — tables
first, then `dispatcher.access_app.CurrentProject.AllForms/AllReports/
AllMacros` with per-collection try/except and `{}` fallback when `access_app`
is unavailable.

### Step 7c: Use-case serialization (RED → GREEN) — the contract from Target architecture

RED in `tests/unit/test_database.py`: change the FakeAdapter's
`get_tables`/`get_indexes`/`get_relationships` to return REAL dataclass
instances (`TableInfo`, `IndexInfo`, `RelationshipInfo` from
`ms_access_mcp.models`) instead of dicts, and assert the use-case `Ok.data`
is a list of plain dicts with the same fields (i.e. `json.dumps(result.fold(
lambda d, r: d, lambda m: m))` succeeds and the field values match). Also
assert `get_table_schema`'s composed table is a dict.

GREEN: in `database.py`, convert adapter output with
`dataclasses.asdict` before `_ok(data=...)` in `get_tables` (`:174-187`),
`get_indexes` (`:229-246`), `get_relationships` (`:248-264`), and the new
`get_table_schema` composition. Follow the `automation.py:129-133` `_to_dict`
precedent (a small module-private helper is fine). List conversion:
`[asdict(x) for x in items]` — handle the empty-list case trivially.

**Verify**: unit tests pass; pyright clean on `database.py`.

### Step 8: Inspector — database statistics (RED → GREEN)

RED: fake dispatcher with `access_app.CurrentProject.All*` counts and
`db.TableDefs/QueryDefs.Count`, fake `os.stat` on `db.Name` (monkeypatch).
Assert the oracle shape keys exactly; disconnected → zeroed +
`com_available: False`.

GREEN: implement per oracle `:598-662`. If `access_app` raises, return the
zeroed shape with `com_available: False` (do not crash).

**Verify**: tests pass.

### Step 9: `backends/sql_generator.py` + `generate_sql` (RED → GREEN)

RED: `tests/unit/test_sql_generator.py` (create) — port-level tests against
the oracle `sql_generator.py` behavior: JET_TYPE_MAP coverage (pin every
mapping verbatim), `{size}` formatting, AUTOINCREMENT/DEFAULT/NOT NULL column
parts, PK constraint, FK constraint emission, topological sort (parent before
child), cycle fallback to input order. Then inspector-level:
`generate_sql(tmp_path)` writes the file; returns `{"success": True, "path",
"statements", "tables"}`.

GREEN: create `backends/sql_generator.py` (port of oracle 168-line module,
types imported from live `models.py`); implement inspector `generate_sql`
per oracle `:664-838` (direct `db` access inside `_do()`; enriched FieldInfo;
write `"\n".join(statements)` + trailing newline).

**Verify**: tests pass; ruff clean.

### Step 10: ODBC — relationships + indexes + PK (RED → GREEN)

RED: `tests/unit/test_odbc_schema_reader.py` — fake cursor rows for
`_group_rows`: multi-column FK merges into one RelationshipInfo; synthesized
`FK_{child}_{parent}` names; deterministic sort; exception → `[]` with
warning logged. For `odbc.py`: fake `cursor.statistics()` /
`cursor.primaryKeys()` rows → IndexInfo with `is_unique` from NON_UNIQUE
inversion and a synthetic PK IndexInfo; cursor failure → `[]`.

GREEN: create `backends/odbc_schema_reader.py` (port of the 133-line oracle
module, verbatim query string); wire `OdbcAdapter.get_relationships` to it
(lazy init like DaoAdapter's inspector); implement `get_indexes` via
`cursor.statistics(table_name)` + `cursor.primaryKeys(table_name)`,
best-effort with `[]` degradation. Update any unit test that pinned the
`return []` stub for these two methods (justified stub replacement; do not
touch unrelated ODBC tests).

**Verify**: tests pass; `odbc.py` diff limited to the three methods + wiring.

### Step 11: Integration — fixture round trips

Create `tests/integration/test_schema_extraction.py` (markers copied from
`test_access_adapter.py:10-16`), on temp fixture copies:

1. DAO `get_tables()` → 8 user tables, fields populated, record_count ≥ 0.
2. DAO `get_relationships()` → non-empty; every entry has non-empty
   `columns` AND `foreign_columns`. (Probe the fixture's Relations first —
   if the fixture has zero relationships, STOP and report; do not weaken the
   test by inventing data.)
3. DAO `get_indexes("Orders")` (or another fixture table with a PK) → at
   least one `is_primary=True` entry.
4. DAO `create_relationship` + `delete_relationship` round trip on the temp
   copy (create a test FK between two fixture tables, verify via
   `get_relationships`, delete, verify gone).
5. DAO `get_table_schema_plan()` → all 8 tables, PKs detected,
   `UnknownMetadata` flags all False.
6. DAO `generate_sql(tmp)` → file contains `CREATE TABLE` for fixture tables.
7. ODBC `get_relationships()` → non-empty OR empty-with-logged-warning
   (MSysRelationships may be hidden — assert it does NOT raise and returns a
   list; document the host's actual result in the test report).

**Verify**: integration file green; no MSACCESS.EXE orphans.

### Step 12: Capability flags + gates + index

Read `src/ms_access_mcp/capabilities.py` and `tests/unit/test_capabilities.py`
(`:39`/`:67` pin `can_introspect_schema`/`schema_caps`). If any flag still
claims DAO/ODBC schema introspection is unavailable, update it to the new
reality and extend the pinning tests. If the flags are generic/absent, make
no change — do not invent flags.

Then the full gate table, all green (quote each). `plans/README.md`: row 026
→ DONE with evidence.

**Verify**: `git status` shows only in-scope files.

## Test plan

- Unit per step above; the fake-COM dispatcher pattern is the existing one in
  `tests/unit/test_backend_dao.py` (extend its fakes: FakeTableDef gains
  Fields/Indexes; FakeDb gains Relations/QueryDefs/OpenRecordset/Name).
- The `tests/unit/test_backend_dao.py:870-872` comment gets updated — real
  collaborators are now faked.
- Integration: Step 11 list.
- Verification: `uv run pytest -q` → 0 failed; integration subset green.

## Done criteria

ALL must hold:

- [ ] `SchemaInspector` has zero "Not implemented"/empty-stub bodies
  (`grep -n "Not implemented" src/ms_access_mcp/backends/schema_inspector.py`
  → no matches)
- [ ] DAO `get_tables`/`get_indexes`/`get_relationships`/`get_table_schema_plan`/
  `get_object_metadata`/`get_database_statistics`/`generate_sql`/
  `create_relationship`/`delete_relationship` return real data on the fixture
  (integration evidence)
- [ ] ODBC `get_relationships` + `get_indexes` implemented (MSys reader +
  statistics/primaryKeys); graceful `[]` degradation preserved
- [ ] Bug A fixed: `database.py:281` is 1-arg; fake updated; tool works
- [ ] Bug B fixed: `get_table_schema` composes from `get_tables`; missing
  table → `Err("Table 'X' not found")`
- [ ] Serialization contract held: `get_tables`/`get_indexes`/`get_relationships`/
  `get_table_schema` `Ok.data` is JSON-serializable dicts (dataclasses
  converted via asdict in the use cases)
- [ ] `models.py` (flat module) contains the 5 new dataclasses; no `models/`
  package, no `models/schema.py`, no pydantic was introduced
- [ ] `uv run pytest -q` 0 failed; `ruff` clean; `pyright` 0/0/0
- [ ] `plans/README.md` row 026 updated; `uv.lock`/`.opencode/` untouched

## STOP conditions

Stop and report back (do not improvise) if:

- The live `schema_inspector.py` or `dao.py` delegation no longer matches the
  excerpts (drift).
- The live ComDispatcher does NOT expose `access_app` in a way the inspector
  can reach from DaoAdapter's dispatcher (then restrict Steps 7-8 to
  db-object counts only and report the reduction).
- The fixture database has zero relationships (Step 11 test 2 cannot be
  written as specified).
- Pre-existing tests fail for reasons unrelated to the justified updates
  listed in Scope (`test_database.py` fake arity, ODBC stub-pinning tests,
  the `"unknown"` sentinel pin).
- `cursor.statistics()`/`cursor.primaryKeys()` are unavailable in this
  pyodbc build — then ODBC `get_indexes` stays a documented stub and the
  reduction is reported.
- Two consecutive gates fail after one reasonable fix attempt.

## Maintenance notes

- Plan 024 composes `ExtractedSchema` FROM the `models.py` dataclasses this
  plan adds — do not let it redefine `TableSchema`/`ColumnSchema`. If its SDD
  design wants a `models/` package, that restructuring is a deliberate
  decision of the 024 cycle (with a migration of these models), not something
  to drift into.
- The serialization contract (backends return dataclasses; use cases
  `asdict` before `Ok`) now applies to EVERY future backend read method.
  Any plan adding a backend read without the conversion will crash at
  `to_mcp_result`'s `json.dumps` — the Step 7c tests are the canary.
- Plan 025's `get_er_diagram` composes the CONVERTED dict shapes from this
  plan's use cases (nodes/edges built from `get_tables`/`get_relationships`
  dict output) — its plan text says "compose from the EXISTING use cases
  ... their dict shapes"; those dict shapes only exist after Step 7c.
- Plan 025's `get_er_diagram` (relationships), `generate_sql`, and the ddl
  tools from 022 all consume this plan's surface; if any shape here changes
  later, all three plans' consumers break — treat these shapes as public
  backend contracts.
- `backends/versioning.py:590-652` has a parallel, simpler DDL generator.
  Consolidation (both using `sql_generator.py`) is deliberately deferred —
  the versioning path has its own tests and lifecycle; a consolidation plan
  would need to migrate `export_schema_ddl` tests too.
- Reviewer focus: FK orientation in BOTH `get_relationships` (child in
  `columns`) and `get_table_schema_plan` (parent in `columns`) — the two
  oracle views intentionally differ; swapping either silently corrupts ER
  diagrams and migration schemas. The pinned tests are the guard.
- `record_count` runs `SELECT COUNT(*)` per table — O(N) queries; fine for
  Access-scale databases, but do not call `get_tables` in a hot loop.
