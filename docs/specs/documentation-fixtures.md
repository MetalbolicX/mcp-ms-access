# Synthetic documentation fixtures

Status: generator, expected manifest, the opt-in raw-readback integration suite, and
the opt-in documentation-pipeline qualification suite are implemented. The pipeline
suite qualifies engine-level read-only protection for the DAO inspection open, exercises
the ODBC inspection path with behavioral no-write evidence, and qualifies filtered
inspection (`--tables`/`--queries`, DAO backend). Startup-code non-execution is now
qualified at fixture scope: the pipeline suite proves the DAO and ODBC backends never
execute the fixture, and a doubly gated control run proves the same fixture genuinely
executes when deliberately opened under isolation.

## Generate

From the repository root:

```powershell
.venv\Scripts\python.exe scripts\generate_documentation_fixtures.py --describe
.venv\Scripts\python.exe scripts\generate_documentation_fixtures.py --build
```

`--describe` prints the manifest and needs neither ACE nor pywin32. `--build`
requires Windows with ACE DAO, pywin32, and Microsoft Access: the executable
`startup.accdb` fixture needs `Access.Application` to load its module and macro, so a
machine with only the ACE Database Engine driver cannot build it.

The default output is `db/fixtures/documentation/`. For a fresh independent run:

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
`inventory.json` beside the databases. The inventory records actual hashes, sizes,
and DAO-canonical query SQL. ACE metadata can vary: structural repeatability does
not promise byte-identical regeneration.

## Expected databases

- **core.accdb:** Customers, CustomerProfiles, Orders, OrderLines, Products,
  Warehouses, Stock, Sales Notes (eight tables).
- **sentinel.accdb:** one SentinelCustomers table with one synthetic row.
- **linked.accdb:** one LocalNotes table with one row, and LinkedCustomers pointing
  to SentinelCustomers in the generated sentinel.accdb.
- **startup.accdb:** one StartupNotes table with one row, a standard VBA module
  `StartupMarkerModule`, and a macro renamed to `AutoExec`; it is the only fixture that
  contains executable code (see the startup-code fixture section below).

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
`StartupMarkerModule` with a public `StartupMarker` function, and a macro renamed to
`AutoExec` that runs `StartupMarker()` through `RunCode`. The manifest records the
module name, macro name, pre-rename control name, marker filename, and the execution
policy `authorized-isolated-control-only`.

The `StartupMarker` function writes a harmless one-line text file named
`startup_executed.marker`, beside the database it runs from:
`Application.CurrentProject.Path & "\startup_executed.marker"`. No absolute, user,
home, temporary, or network path appears anywhere in the generated VBA, so a copied
database writes beside the copy and a database that never runs writes nothing.

### Build-without-executing discipline

The generator creates the table through DAO-only DDL, then opens Access.Application
*only while no macro named `AutoExec` exists*. It loads the module (raw VBA source; the
`Version`/`Begin`/`End` wrapper is export framing, not module code) and the macro
(temporarily named `StartupControl`) from text definitions written beside the database,
renames the macro to `AutoExec` last with `DoCmd.Rename`, closes the database, and quits
without reopening. Access.Application is confined to this pre-AutoExec build phase; the
generator never runs VBA or a macro. `Application.LoadFromText` is used because
`DoCmd.LoadFromText` is not exposed on the late-bound `DoCmd` object; it is the same
database operation. After the phase the build asserts that the built database produced
no marker, no `startup.accdb.laccdb` lock survives, and no MSACCESS.EXE process that
the build started remains, before it records the hashes and sizes in `inventory.json`.

### DAO observability

DAO exposes construction facts and object *names* only: `db.Containers("Modules")`
lists `StartupMarkerModule` and `db.Containers("Scripts")` lists `AutoExec` (there is no
`Macros` container). Neither container exposes the VBA source or the macro actions, so
DAO readback can assert *that* the module and macro exist, but not what they contain.
The raw-readback suite therefore proves fixture construction and the absent marker; the
executable-code proof is the control run.

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
window, which is outside this initial suite's proof.

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
path has no engine-level read-only reopen, so those hashes are behavioral no-write
evidence only; the engine-level read-only qualification above stays DAO-scoped. ODBC
catalog reads on a linked table contact the linked source read-only, so isolation is not
guaranteed over ODBC; a separate lock-probe regression
(`test_inspect_linked_via_odbc_reports_unreachable_source_as_failed_table`) holds the
session sentinel exclusively and confirms that a linked table whose source is unreachable
(which makes the driver return zero columns) surfaces as a `failed` table in
`coverage.tables.failed` -- never an observed-empty field/index fact -- with the sentinel
sha256 unchanged.

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

Engine-level read-only enforcement is now qualified for the DAO inspection open (see the
proof above). The ODBC inspection path is now exercised by the pipeline suite with
behavioral no-write evidence (source hashes unchanged, no lock residue) as well as
per-capability honest gaps; it has no engine-level read-only claim. Linked-source
isolation is now observed for this fixture while the sentinel is exclusively locked, but
it is scoped to that observation and not to engine-level enforcement.

Filtered inspection is now qualified for the DAO backend by the pipeline suite. The
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
(`test_inspect_unknown_requested_name_is_a_hard_error`). Still unproven by any fixture
suite: VBA/macro execution beyond the startup fixture, and filtered
inspection over the ODBC backend. The startup fixture's own non-execution is qualified
above (DAO and ODBC pipeline tests plus the gated control run).

No startup macro or executable VBA fixture is run by the generator or the inspection
pipeline. The `startup.accdb` fixture contains executable code and is deliberately run
only by the doubly gated control test under isolation. An unchanged source hash alone
establishes no observed mutation; the inspection open additionally proves engine-level
read-only refusal, and the control run proves the fixture could execute while the
pipeline tests prove it did not.

See [workflow specification](documentation-workflows.md) and
[task ledger](../../odd/tasks/documentation-fixtures.md).
