# Synthetic documentation fixtures

Status: generator, expected manifest, the opt-in raw-readback integration suite, and
the opt-in documentation-pipeline qualification suite are implemented. The pipeline
suite qualifies engine-level read-only protection for the inspection open; startup-code
non-execution remains unqualified.

## Generate

From the repository root:

```powershell
.venv\Scripts\python.exe scripts\generate_documentation_fixtures.py --describe
.venv\Scripts\python.exe scripts\generate_documentation_fixtures.py --build
```

`--describe` prints the manifest and needs neither ACE nor pywin32. `--build`
requires Windows with ACE DAO and pywin32.

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
saved queries, legal evidence states with query-parameter and relationship-attribute
capabilities reported as `unavailable` with partial coverage, deterministic
database/ER rendering with safe Mermaid aliases and original names preserved, ER-only
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

Engine-level read-only enforcement is now qualified for the inspection open (see the
proof above). Still unproven by any fixture suite: startup/macro/VBA non-execution (no
executable fixture exists) and the ODBC backend. Linked-source isolation is now observed
for this fixture while the sentinel is exclusively locked, but it is scoped to that
observation and not to engine-level enforcement.

No startup macro or executable VBA fixture is generated or run here. Startup
execution remains separately authorized. An unchanged source hash alone establishes
no observed mutation; the inspection open additionally proves engine-level read-only
refusal, while non-execution remains unproven.

See [workflow specification](documentation-workflows.md) and
[task ledger](../../odd/tasks/documentation-fixtures.md).
