# Synthetic documentation fixtures

Status: generator, expected manifest, the opt-in raw-readback integration suite, and
the opt-in documentation-pipeline qualification suite are implemented. A separate opt-in
suite with its own helper-built fixture qualifies a genuine SQLite-backed
`dbAttachedODBC` link (see the SQLite ODBC link fixture section). The pipeline
suite qualifies engine-level read-only protection for both the DAO and ODBC inspection
opens, exercises the ODBC inspection path with engine-level refusal plus no-write
evidence, and qualifies filtered
inspection (`--tables`/`--queries`, DAO and ODBC backends). Startup-code non-execution is now
qualified at fixture scope: the pipeline suite proves the DAO and ODBC backends never
execute the fixture, and a doubly gated control run proves the same fixture genuinely
executes when deliberately opened under isolation. The fixture was extended with a
never-auto-run named macro (`ManualMarker`) and an `AutoKeys` macro group (loaded under a
neutral name and renamed last); both are name-observable through DAO `Containers("Scripts")`.
A table data-macro DML tripwire was **not** added: the client data-macro import format
could not be established within the bounded attempt budget (see the blocked-tripwire
section below).

## Generate

From the repository root:

```powershell
.venv\Scripts\python.exe scripts\generate_documentation_fixtures.py --describe
.venv\Scripts\python.exe scripts\generate_documentation_fixtures.py --build
```

`--describe` prints the manifest and needs neither ACE nor pywin32. `--build`
requires Windows with ACE DAO, pywin32, and a Microsoft Access installation
(`MSACCESS.EXE`): the executable `startup.accdb` fixture needs `Access.Application` to
load its module and macro, so a machine with only the ACE Database Engine driver
cannot build it.

The default output is `db/fixtures/documentation/`; it holds four databases
(`core.accdb`, `sentinel.accdb`, `linked.accdb`, `startup.accdb`), the two per-build
records `expected.json` and `inventory.json`, and four retained provenance sources:
`startup_module.bas` (raw VBA module source), `startup_macro.txt` (framed `AutoExec`
definition), `named_macro.txt` (framed `ManualMarker` definition), and
`autokeys_macro.txt` (framed `AutoKeys` macro group). The provenance files are the
`Application.LoadFromText` inputs for the startup module and macros; they are
intentionally kept beside the built database for inspection and rebuild, and they are
never executed by the generator or the inspection pipeline. The whole directory is a
local, git-ignored build output. For a fresh independent run:

```powershell
.venv\Scripts\python.exe scripts\generate_documentation_fixtures.py --build --output-dir db\fixtures\documentation-run-2
```

No arguments prints help without building. Existing output directories are rejected;
there is no force/overwrite option. Paths outside this project's `db/fixtures/` are
rejected. Failed builds retain any new partial directory for diagnosis, never label it
a successful fixture, and never delete or overwrite a previous build. A rejected or
failed build exits 2 and prints the reason on stderr; `--describe`, a successful build,
and the no-argument help exit 0.

Database binaries remain git-ignored. The committed ground truth is
[`documentation_expected.json`](../../tests/fixtures/documentation_expected.json),
which `--describe` reproduces exactly; both the CLI/manifest equality and the absence
of writes are enforced by `tests/unit/test_generate_documentation_fixtures.py`.
Each successful build writes `expected.json` and
`inventory.json` beside the databases (plus the four provenance sources named above).
The inventory records the actual hashes, sizes, and DAO-canonical query SQL of that
build. ACE metadata can vary between runs: regeneration yields **structural**, not
byte-level, identity, and the recorded hashes are per-build observations rather than a
stability promise.

## Expected databases

- **core.accdb:** Customers, CustomerProfiles, Orders, OrderLines, Products,
  Warehouses, Stock, Sales Notes (eight tables).
- **sentinel.accdb:** one SentinelCustomers table with one synthetic row.
- **linked.accdb:** one LocalNotes table with one row, and LinkedCustomers pointing
  to SentinelCustomers in the generated sentinel.accdb.
- **startup.accdb:** one StartupNotes table with one row, a standard VBA module
  `StartupMarkerModule`, a never-auto-run named macro `ManualMarker`, an `AutoKeys`
  macro group, and a macro renamed to `AutoExec`; it is the only fixture that contains
  executable code (see the startup-code fixture section below).

Core has explicit primary keys, two composite keys, seven declared relationships
including the composite Stock-to-OrderLines FK, required and nullable customer
FKs, defaults, validation, and names containing spaces, Unicode and a reserved
word. CustomerProfiles has a genuine unique customer FK index called
CustomersProfiles: its relation-like name must not invalidate its real uniqueness.
The CustomerProfiles relation is therefore named `relCustomersProfiles`, because DAO
auto-creates a child FK index named after the relation (DAO 5003284 "Index already
exists") and the declared unique index already legitimately owns the name
`CustomersProfiles`. Table creation refreshes `db.TableDefs` after each CREATE TABLE
data-definition statement, since the collection is stale immediately after DDL and a
lookup would fail with DAO 5003265 "Item not found in this collection". Reserved
identifiers are bracketed in every generated DDL/DML statement (`[Order]` in Sales
Notes, `[Note]` in LocalNotes): an unquoted `Note` fails with DAO 5003134 "Syntax error
in INSERT INTO statement." Each database is closed and its DAO object graph released
before the next `CreateDatabase`, and the inventory hashes are computed only after the
final create, so the close-then-create window carries no unrelated file I/O. ACE can
otherwise fail that next create intermittently with DAO 5003045 "Could not use
'<path>'; file already in use."

Five saved queries are created, never executed: qryOpenOrders,
qryOrdersByCustomer (declared parameter), qryOrderSummary,
qryUpdateOrderStatus (DAO type 48), qryDeleteDraftOrders (DAO type 32).
Orders contains known sentinel statuses `(1, Open)` and `(2, Draft)`; unchanged
rows help detect accidental action-query execution. Only the builder executes
reviewed CREATE/INSERT statements on newly created synthetic databases.

## Startup-code fixture (`startup.accdb`)

`startup.accdb` is the executable fixture that closes the startup-code non-execution
gap. It contains one synthetic table `StartupNotes` (one row), a standard VBA module
`StartupMarkerModule` with a public `StartupMarker` function, a never-auto-run named
macro `ManualMarker`, an `AutoKeys` macro group, and a macro renamed to `AutoExec`.
`AutoExec` and the single `AutoKeys` submacro (`^+{F12}`) both invoke `StartupMarker()`
through `RunCode`; `ManualMarker` does the same and is only ever invoked by name. The
manifest records the module name, the macro names, the pre-rename control names, the
marker filename, and the execution policy `authorized-isolated-control-only`.

The `StartupMarker` function writes a harmless one-line text file named
`startup_executed.marker`, beside the database it runs from:
`Application.CurrentProject.Path & "\startup_executed.marker"`. No absolute, user,
home, temporary, or network path appears anywhere in the generated VBA, so a copied
database writes beside the copy and a database that never runs writes nothing.

### Build-without-executing discipline

The generator creates the table through DAO-only DDL, then opens Access.Application
*only while no auto-run object exists*. It loads the module (raw VBA source; the
`Version`/`Begin`/`End` wrapper is export framing, not module code), the named macro
`ManualMarker` (never auto-runs, so it is loaded under its final name), and the
`AutoExec` and `AutoKeys` macros (temporarily named `StartupControl` and
`AutoKeysControl`) from text definitions written beside the database, then assigns the
auto-run-adjacent names last with `DoCmd.Rename` (AutoKeys first, AutoExec last),
closes the database, and quits without reopening. Access.Application is confined to
this pre-auto-run build phase; the generator never runs VBA or a macro.
`Application.LoadFromText` is used because `DoCmd.LoadFromText` is not exposed on the
late-bound `DoCmd` object; it is the same database operation. After the phase the build
asserts that the built database produced no marker, no `startup.accdb.laccdb` lock
survives, and no MSACCESS.EXE process that the build started remains, before it records
the hashes and sizes in `inventory.json`.

### DAO observability

DAO exposes construction facts and object *names* only: `db.Containers("Modules")`
lists `StartupMarkerModule` and `db.Containers("Scripts")` lists `AutoExec`,
`ManualMarker`, and `AutoKeys` (there is no `Macros` container). Neither container
exposes the VBA source or the macro actions, so DAO readback can assert *that* the module
and macros exist, but not what they contain. The raw-readback suite therefore proves
fixture construction and the absent marker; the executable-code proof is the control run.
The `AutoKeys` group holds one submacro keyed to `Ctrl+Shift+F12`; without Access UI
keyboard input it never runs, which is why it can be loaded and renamed during the build
without executing anything.

### Data-macro DML tripwire: not implemented (blocked)

T5's accepted scope also asked for a `DmlTripWire` table whose Before Change table data
macro (`SetField TrippedAt = Now()` when `TrippedAt` is null) would arm an engine-level
DML tripwire, created with `Application.LoadFromText` `acTableDataMacro` (AcObjectType
`12`) from hand-authored XML, plus a doubly gated deliberate-DML control that proves
whether DAO DML fires table data macros on this runtime. **This was not implemented:**
the client data-macro import text format could not be established within the bounded
attempt budget, and no such fixture is committed.

Seven controlled `--build` runs were executed (each opening Access.Application exactly as
the normal build does), probing roughly 65 bounded encodings: unprefixed and `axl:`-prefixed
`<DataMacro>`/`<EventDataMacro>`/`<DataMacros>`/`<TableDataMacro>` roots; the Access
application-part namespaces `2009/11`, `2010/12`, and `2011/07`; `Event`/`Name`/`Table`
attributes and an `<Event>` child; `Action` with `Name`/`ActionName`; arguments with and
without an `<Arguments>` wrapper and named `Field`/`Name`/`Value`; bracketed and bare field
references; `Now()` and `=Now()`; element-per-action form; an `<axl:Table>` CSDL wrapper; a
UTF-16 (BOM) encoding pass; and the `Version`/`ColumnsShown`/`Begin`/`Action`/`Argument`
frame. Every one failed. The exact observed errors are:

- ObjectName `DmlTripWire` with any probe encoding:
  `Microsoft Access cannot interpret the text you are pasting as a data macro. Correct the
  text and then try again.` (HRESULT `-2146824418`), for every candidate; and
- ObjectName `DmlTripWire.BeforeChange`: the database engine could not find that object
  (`jeterr40.chm` `5003011`), so the event is not encoded in the ObjectName;
- pre-import `SaveAsText acTableDataMacro, "DmlTripWire"` on a table without a data macro
  fails with `Reserved Error` (`-2146825338`), so Access exposes no skeleton to seed the
  format from;
- the shipped Access application-part template `accessparts.xml` only ever contains
  empty `<axl:DataMacro Event="..." />` elements, and the machine has no Access fixture
  with a populated table data macro to export as ground truth.

The import is loud: the generator raises when the tripwire XML cannot be round-tripped,
so a build can never silently produce an unarmed tripwire. Because the tripwire could not
be armed, the `DmlTripWire` table, the hand-authored XML provenance source, and the
doubly gated `test_documentation_datamacro_control.py` deliberate-DML control were not
added. This remains an explicit, unqualified gap; the named macro and AutoKeys vector is
implemented and observationally proven instead.

### Non-execution proof and the control run

The documentation pipeline (DAO inspection through `DAO.DBEngine.120` and ODBC
inspection through the Access ODBC driver) never launches Access.Application. `AutoExec`
is only run by the Access application at database startup, so the pipeline cannot
execute it by construction; the absent marker is the observable confirmation. Two
pipeline tests run the full `documentation inspect` → `render` → `save` cycle on
`startup.accdb` (one per backend) and assert exit 0, the `StartupNotes` table in
evidence, an unchanged source hash, no lock residue, no new MSACCESS.EXE process, and no
`*marker*` file anywhere under the session tree.

Because "nothing executed" is only meaningful if the fixture *could* execute, the
control run `tests/integration/test_documentation_startup_control.py` deliberately
executes it under isolation. It needs both opt-in gates:
`ACCESS_DOCUMENTATION_FIXTURES_TEST=1` (build/reuse fixtures) and
`ACCESS_STARTUP_CONTROL_TEST=1` (authorize the deliberate startup execution). It copies
`startup.accdb` into a scratch subdirectory of the self-created session tree, opens the
copy with `AutomationSecurity` at the low (macro-enabled) automation level, polls at
most 30 s for `startup_executed.marker` beside the copy, asserts it exists and contains
the harmless line, quits Access (force-killing only the captured PID if Quit hangs),
and asserts no MSACCESS.EXE process survives, no `.laccdb` remains, and the marker
exists only beside the copy (not beside the session original and nowhere else in the
session tree or repository).

## SQLite ODBC link fixture (`db/fixtures/sqlite-odbc-*`)

The genuine non-Access-linked fixture is built on demand by the opt-in integration suite
through `tests/helpers/sqlite_odbc_link_worker.py`, not by
`generate_documentation_fixtures.py`. It is a disposable, repository-confined root
`db/fixtures/sqlite-odbc-<uuid>/` holding:

- `remote.sqlite` — a SQLite database with one table `remote_items`
  (`id INTEGER PRIMARY KEY`, `label TEXT NOT NULL`) and exactly two rows,
  `(1, 'alpha')` and `(2, 'beta')`;
- `linked.accdb` — a plain ACE database with a **local** control table `local_items`
  (one row, primary key `PK_local_items`) and a genuine DSN-less ODBC link
  `remote_items_link` (`SourceTableName = remote_items`, stored `Attributes` `0x20020000`
  = `dbAttachedODBC` `0x20000000` with `dbAttachSavePWD` `0x00020000`);
- `inventory.json` — the per-build SHA-256 of both files and the expected remote rows.

No password-protected Access file is required for this case (the separate
`protected-link-*` stored-credential fixture below does build one), no DSN or system data
source is created, and no `Access.Application` is launched (the ACE DAO engine
`DAO.DBEngine.120` and the Access ODBC driver are used directly). Only a local `TABLE`
and a linked `SYNONYM` exist in the fixture, so the fail-closed unknown-table-type path
is not exercised here and remains unit-proven. The helper's `build` command also accepts
`--database-style relative`, which stores `Database=remote.sqlite` so the driver's
completed string fits under ACE's 254-character persistence cut and the stored `Connect`
keeps its `PWD` segment; that style is used only by the stored-credential suite, and its
steps run with CWD pinned to the fixture root because the driver resolves the relative
value against the process CWD.

The suite runs under the validated dialog watcher (`tests/helpers/dialog_watcher.py`,
unit-proven in `tests/unit/test_dialog_watcher.py`), so it cannot be stopped by a driver
connection dialog. The deliberate locked linked read raises the driver's own
`SQLite3 ODBC Driver Connect` window (class `#32770`) owned by the helper's process tree;
the watcher observes exactly that one expected dialog, closes it with `WM_CLOSE`, and the
read is then refused (DAO `5003151`). Every other helper and public-CLI step must observe
zero dialogs from its own process tree; an unexpected dialog, a repeat past the dismissal
cap, or a hung step is closed/terminated and fails the run with the dialog's
title/class/owner recorded. The per-step census is helper build `0`, `prove-contact` `0`,
`prove-isolation` `1` (the expected driver dialog), `catalog-types` `0`, and `0` for each
locked inspect/render run. Every subprocess CWD is pinned inside the fixture root, so a
relative `Database=` path cannot resolve against the repository root and leave a stray
source file.

### Prerequisites, invocation, and skips

Windows with ACE DAO (pywin32), the Access ODBC driver, and the x64 `SQLite3 ODBC Driver`
registered with the ODBC installer are required; the suite builds the fixture itself, so
the driver must already be installed but the tests configure nothing system-wide. Run it
explicitly on Windows:

```powershell
$env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
.venv/Scripts/python.exe -m pytest tests/integration/test_documentation_sqlite_odbc_link.py -q
```

Without `ACCESS_DOCUMENTATION_FIXTURES_TEST=1` (or off Windows, or without ACE DAO, or
without the SQLite ODBC driver) the module skips cleanly with a reason. The helper's
`environment` command reports the same reasons and exits 3 when unsupported.

### Retention and cleanup

Generated binary and JSON outputs under `db/fixtures/sqlite-odbc-*` are **retained as
evidence**: the suite creates a new root per session and never deletes it, and the helper
refuses to overwrite an existing root. The helper exposes a guarded `cleanup` command
that removes exactly one self-created direct child of `db/fixtures/`, but no cleanup runs
as part of the suite and deletion is separately authorized.

### Qualification and limits

The suite qualifies the shipped inspection path only (no production source changes); its
observed evidence is recorded in [workflow specification](documentation-workflows.md)
§7.2 (SQLite ODBC link subsection). Its scope limits are deliberate: the exclusive
`share=0` source lock plus the zero-dialog observation proves the qualifying DAO and ODBC
inspections performed no **source-file open** and that no SQLite-driver connect attempt
surfaced through ACE while they ran — still not every contact form, so the claim is
paired with the classification-before-read unit proofs in
`tests/unit/test_odbc_inspection_port.py`. The absolute-path fixture's stored `Connect`
has no `PWD` segment because ACE persists only the first 254 characters of the driver's
completed string, so that truncation — not credential filtering — is the reason a
positive runtime redaction of a stored credential is qualified by the separate
`protected-link` fixture (and the relative-database SQLite build) described below, not
here; sanitization itself stays unit-proven. Rendering the emitted
evidence is offline and deterministic, and the source bytes (`remote.sqlite`,
`linked.accdb`) are asserted unchanged around the locked runs. Every public-CLI and helper
invocation runs as its own short-lived, timeout-bounded subprocess under the dialog
watcher, so no warmed COM or ODBC handle crosses between the DAO and ODBC inspections and
no step can block on a dialog or hang.

## Stored-credential link fixture (`db/fixtures/protected-link-*`)

The non-vacuous stored-credential qualification (S4a/T5) uses a second on-demand fixture
built by the opt-in suite through `tests/helpers/protected_link_worker.py` (not by
`generate_documentation_fixtures.py`). It is a disposable, repository-confined root
`db/fixtures/protected-link-<uuid>/` holding:

- `backend.accdb` — a disposable ACE database created with the fake password in the DAO
  `CreateDatabase` locale (`;LANGID=0x0409;CP=1252;COUNTRY=0;pwd=canary-7f3a`,
  `dbVersion120`) and one `secret_items` row. The password is real: an unprotected DAO
  open is refused (DAO `5003031`).
- `frontend.accdb` — a disposable ACE database with a local control table `local_items`
  (one row, primary key `PK_local_items`) and a `dbAttachedTable` link `link_items`
  (`Attributes` `0x40000000`, `SourceTableName = secret_items`) whose `Connect` is
  `;PWD=canary-7f3a;DATABASE=<backend absolute path>`; ACE stores it verbatim and
  normalizes the scheme to `MS Access`;
- `inventory.json` — the per-build SHA-256 of both files and the expected backend row.

No DSN or system data source is created, no `Access.Application` is launched, and every
helper command performs at most one DAO `DBEngine.120` dispatch/release cycle in its own
process. The helper emits only booleans, counts, hex attribute masks, and connection-string
key names, so the raw `Connect` and the credential never reach its stdout. The suite runs
under the validated dialog watcher with `PYTHONFAULTHANDLER` stripped, holds the backend
under an exclusive `share=0` lock while the public DAO and Access-ODBC inspections and
both offline renders run, and requires every step to exit 0 with zero dialogs. Positive
controls (stored credential present, unprotected open refused, linked read returns the
row) are observed in a new process before any pipeline run; an unprotected open is also
re-verified after the run. Rendered `db/fixtures/protected-link-*` roots are retained as
evidence, and the helper's guarded `cleanup` command removes exactly one self-created
direct child of `db/fixtures/`; no cleanup runs as part of the suite. Exact opt-in run:

```powershell
$env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
.venv/Scripts/python.exe -m pytest tests/integration/test_documentation_stored_credential_link.py -q
```

The relative-database SQLite build used by the same suite remains under
`db/fixtures/sqlite-odbc-*` and is documented in the SQLite fixture section above and in
[workflow specification](documentation-workflows.md) §7.2.

## Test and qualification boundaries

Pure CLI/manifest tests need no ACE: `tests/unit/test_generate_documentation_fixtures.py`
checks exit codes, exact stderr, the manifest bound to `--describe` stdout, and the
absence of filesystem mutations. The expected parameter name is the DAO-reported
bracketed form `[pCustomerID]`, matching engine readback rather than the unquoted
name written in the query SQL.

DAO readback of a field `ValidationRule` can carry a trailing NUL character; the
expected manifest records the authored rule (`>=0`), so runtime comparison should
normalize that readback rather than treat the NUL as a property of the fixture.

The opt-in suite `tests/integration/test_documentation_fixtures_e2e.py` proves that a
freshly built fixture set matches the committed manifest using raw, read-only DAO
construction readback. It builds once per session through the public generator CLI into
a fresh self-created `db/fixtures/e2e-<uuid>/` directory, asserts the build exited 0
with empty stderr, and then removes exactly that `e2e-<uuid>` tree (creating
`db/fixtures/` itself only when it is absent, and removing it again only if empty). It
verifies tables, primary keys, unique indexes, relation orientation, query definitions
and parameters, row counts, stored defaults, the validation rule, and the
sentinel/linked databases against `documentation_expected.json`. Linked tables are
read as metadata only (`Connect`, `SourceTableName`, and the `0x40000000` attached
marker); the suite never opens linked records, never refreshes links, and never
executes a saved query. It touches neither `db/fixtures/documentation/` nor
`db/northwind.accdb`; the sentinel hash is compared with the build inventory before and
after the session.

Run it explicitly on Windows with ACE DAO and pywin32:

```powershell
$env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
.venv\Scripts\python.exe -m pytest tests\integration\test_documentation_fixtures_e2e.py -q
```

Without `ACCESS_DOCUMENTATION_FIXTURES_TEST=1` (and off Windows or without ACE) the
module skips cleanly instead of failing. This suite verifies fixture construction
separately from extractor correctness: it does not exercise extraction, inspection,
rendering, or saving. Failures are qualification findings, not permission to weaken the
expected manifest.

Link creation intentionally contacts the local sentinel during construction.
Hash equality or lack of lock files cannot establish that later inspection never
contacts it. That claim needs access-observation evidence during the inspection
window, which is outside this initial suite's proof. The SQLite ODBC link fixture suite
supplies exactly that access-observation evidence for its own source: it holds the
SQLite file under an exclusive Windows lock across the public inspections **and** runs each
of them under a validated dialog watcher, so those inspections could not have opened the
source file and surfaced no SQLite-driver connect attempt through ACE (see the SQLite ODBC
link fixture section for the retained limits of that proof).

The opt-in suite `tests/integration/test_documentation_pipeline_e2e.py` qualifies the
documentation pipeline against the same synthetic fixtures through the public CLI
(`documentation inspect|render|save`) in subprocesses. It proves: index-derived
composite primary keys (`OrderLines`, `Stock`) and the genuine unique
`CustomerProfiles.CustomersProfiles` index, exactly the eight manifest tables and five
saved queries, legal evidence states (relationship-attribute capabilities stay
`unavailable` with partial coverage), saved-query parameter metadata read from DAO
`QueryDef.Parameters` (`qryOrdersByCustomer` → `observed` `[pCustomerID]` type `4`
with the reused DAO type name `Long Integer`; parameterless queries `observed` empty,
never `unavailable` or fabricated; a non-exposing port maps to `unavailable` at the
seam) and rendered in the catalogue as `- Parameters: [pCustomerID] (Long Integer)`,
deterministic database/ER rendering with safe Mermaid aliases and original names preserved, ER-only
output excluding the query catalogue, the child-side `}o--||` cardinality rule
(including the unique-index-backed one-to-one `relCustomersProfiles` and the composite
`StockOrderLines` FK), save gating (operator permission, per-call confirm, overwrite
approval, expected/stale fingerprint, byte-identical rejected saves), and linked
inspection succeeding while `sentinel.accdb` is held open exclusively elsewhere
(access-observation evidence that inspection did not contact the linked source). It
also proves the inspection open is engine-level read-only:
`test_inspect_open_is_engine_read_only_and_refuses_write` opens through the production
seam (`documentation_inspect.open_inspection_port` →
`ComDispatcher.reopen_dao_database_read_only`) and shows the read-only DAO handle
refuses an `UPDATE` (`Operation must use an updateable query.`) with `core.accdb`
sha256 unchanged while `documentation inspect` still exits 0 with complete metadata
readback.

The same suite now exercises the ODBC inspection path end-to-end
(`test_inspect_core_via_odbc_exposes_odbc_metadata_with_honest_unavailable_gaps`):
`documentation inspect --db <built core.accdb> --backend odbc --json` runs the Access ODBC
driver over the same built fixture copy, and the resulting evidence renders offline. It
asserts the ODBC-visible facts with the driver's own values (eight user tables including
`Sales Notes`; `INTEGER`/`VARCHAR`/`CURRENCY` type names and sizes; SQLStatistics index
names, columns, and uniqueness including composite `PK_OrderLines` order; the three
row-returning saved queries as `VIEW` rows) and every probed invisible capability as an
explicit `unavailable` fact with a reason (field `required`/`allow_zero_length`, primary
key, relationships, saved-query SQL/category, linked-table target). Query parameters are
`observed` through `SQLProcedureColumns` (`[pCustomerID]`, ODBC type `4` = `INTEGER`;
parameterless queries report a known empty, not `unavailable`). The test also asserts the
source copy's sha256 is unchanged and that no `core.accdb.*` lock file remains. The ODBC
open is a separate engine-level read-only connection
(`OdbcAdapter.open_readonly_inspection_connection` → `pyodbc.connect(..., readonly=True)`,
qualified on ACEODBC `16.0.20326.20022` x64) isolated from the adapter's CRUD
connection: `test_odbc_inspection_open_is_engine_read_only_and_refuses_write` opens it
through the production seam and shows a narrow `UPDATE` is refused (`-3035`,
`Operation must use an updateable query.`) with the disposable copy byte-identical and no
lock residue, while the adapter's own CRUD connection still reads and a separate writable
control copy still accepts an ordinary CRUD write. Behavior hashes alone are not the
claim; the engine-level refusal is. ODBC
classifies every listed table by its `SQLTables` type before any column/index read, so a
`SYNONYM` (linked table) is never passed to `SQLColumns` or `SQLStatistics` and the linked
source is not contacted; the table is reported as an explicit coverage `unsupported` gap
with empty fields/indexes and an `unavailable` primary key. A separate lock-probe
regression
(`test_inspect_linked_via_odbc_does_not_contact_the_linked_source`) holds the session
sentinel exclusively and confirms the linked table is neither a failed read nor an
observed-empty schema, with the sentinel sha256 unchanged.

The suite also qualifies the T4a bounded CLI ingress at the real default caps
(`test_cli_rejects_oversized_evidence_and_report_files_at_the_default_caps`): a raw
`--evidence` file above 32 MiB plus the 1024-byte `Ok`-envelope allowance, and a raw
`--report` file above 16 MiB plus allowance, each deliberately starting with non-JSON
bytes, are rejected with the raw-ingress size `Err` (CLI exit 1) before any parse —
proving the bounded read rejected them rather than a JSON parser — and the rejected
save leaves no destination or temporary file. The same run still saves a normal report
with exactly its Markdown bytes. Units, inclusive boundaries, and the 120-second
inspection deadline (qualified at fixture scope by the deadline integration suite and the
runtime guard tests) are recorded in `documentation-workflows.md` §6.3.

All three blocking qualification findings from that suite are resolved in T3 phase 2:

1. **Resolved.** Field defaults are read from DAO `Field.DefaultValue` (`Customers.Status = "Active"`,
   `Orders.Status = "Open"`, defaultless fields `''`) and reported as `observed` stored
   expressions; the former `unavailable` expectation was replaced because ACE exposes the
   capability (`documentation-workflows.md` §3.2). Acceptance test
   `test_inspect_core_field_defaults_are_read_from_dao`; unit
   `TestGetTables::test_field_default_value_is_read_from_dao_and_nul_stripped` plus its
   preserve case.
2. **Resolved.** DAO type 48 (`dbUpdate`) now maps to `action` (`_DAO_QUERY_CATEGORY`:
   32/48/64 → `action`, 80/96 → `ddl`; 0/32/80 preserved), so `qryUpdateOrderStatus` and
   `qryDeleteDraftOrders` are distinguished. Unit coverage:
   `test_maps_dao_update_type_48_to_action_category` plus the `0/32/80` preserve case.
3. **Resolved.** `SchemaInspector.get_table_link_metadata` reads the LOCAL catalogue only
   (`Attributes & 0x40000000`, `Connect`, `SourceTableName`; trailing NUL stripped), the
   inspection port surfaces it, `TableEvidence` gains additive optional
   `kind`/`connect`/`source_table_name`, and the data dictionary renders
   `- Linked table: LinkedCustomers → sentinel.accdb/SentinelCustomers`. No `RefreshLink`,
   no linked-record access, no target contact.

The three cosmetic gaps stay open intentionally: nullable-FK cardinality rendering
(`Orders.BillingCustomerID` still diagrams `}o--||`), the pywin32 `IUnknown` teardown
stderr banner, and the `source.path`/`source.format` placeholders.

Engine-level read-only enforcement is now qualified for the DAO and ODBC inspection
opens (see the proofs above). The ODBC inspection path is exercised by the pipeline
suite with engine-level read-only refusal plus per-capability honest gaps. Linked-source
isolation is now observed for this fixture while the sentinel is exclusively locked, but
it is scoped to that observation and not to engine-level enforcement.

Filtered inspection is now qualified for the DAO and ODBC backends by the pipeline suite.
The
`--tables`/`--queries` flags are repeatable CLI occurrences (no comma-separated form).
An explicit table subset carries exactly the requested tables (including the
space-containing `Sales Notes`), `coverage.tables.inspected` matches the request, and a
filtered-out table is absent from evidence and from every `coverage.tables` bucket
(never failed, unsupported, or fabricated as unavailable). A query subset carries exactly
that query and leaves the table scope untouched, and both filters can be combined in one
invocation. A relationship whose parent or child table is not selected is recorded in
`coverage.relationships.excluded` and is not inspected, which fixed a blocking defect:
the retained relationship made the filtered document unrenderable (`KeyError` resolving a
Mermaid alias) so no report could be produced for a subset that cut a relationship in
half; filtered evidence now renders one coherent `database` report
(`test_inspect_filtered_tables_returns_exactly_the_requested_subset`,
`test_inspect_filtered_queries_returns_exactly_the_requested_query`,
`test_inspect_combined_table_and_query_filters_scope_both_axes`). An unknown requested
name is a hard `Err` with exit 1 and leaves the source bytes untouched
(`test_inspect_unknown_requested_name_is_a_hard_error`).

The same selection contract is qualified over the Access ODBC driver by
`test_inspect_filtered_tables_via_odbc_returns_exactly_the_requested_subset`,
`test_inspect_filtered_queries_via_odbc_returns_exactly_the_requested_view`,
`test_inspect_combined_table_and_query_filters_via_odbc_scope_both_axes`, and
`test_inspect_unknown_requested_name_via_odbc_is_a_hard_error`. ODBC keeps its honest
gaps under filtering (each selected table's primary key `unavailable` with the
`SQLPrimaryKeys` reason, relationships an explicit `coverage.relationships.unsupported`
gap) and its visible query catalogue is only the row-returning `VIEW` rows; the action
query names are never claimed visible. Selecting only `LocalNotes` in `linked.accdb`
while `sentinel.accdb` is exclusively locked never contacts the filtered-out linked
source (`test_inspect_filtered_out_linked_source_is_not_contacted_via_odbc`). The
explicit empty-list selection, inexpressible with the repeatable CLI flags, is exercised
over a real ODBC port through the public application inspect interface and the public MCP
tool with a CLI transport-parity assertion
(`test_inspect_empty_selection_via_odbc_is_select_none_and_transport_parity`). Still
unproven by any fixture suite: VBA/macro execution beyond the startup fixture. The
startup fixture's own non-execution is qualified above (DAO and ODBC pipeline tests plus
the gated control run).

No startup macro or executable VBA fixture is run by the generator or the inspection
pipeline. The `startup.accdb` fixture contains executable code and is deliberately run
only by the doubly gated control test under isolation. An unchanged source hash alone
establishes no observed mutation; the inspection open additionally proves engine-level
read-only refusal, and the control run proves the fixture could execute while the
pipeline tests prove it did not.

See [workflow specification](documentation-workflows.md) and
[task ledger](../../odd/tasks/documentation-fixtures.md).
