# Synthetic documentation fixtures

Status: generator and expected manifest implemented; ACE build and integration
qualification still pending. This suite does not qualify engine-level read-only
protection or startup-code non-execution.

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

Runtime qualification is not implemented yet. A later task adds an opt-in integration
file, `tests/integration/test_documentation_fixtures_e2e.py` (not present on this
branch), which will create independent fixture directories under `db/fixtures/`, use
raw read-only DAO as a reference, and exercise the documentation CLI on those
disposable databases:

```powershell
$env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
.venv\Scripts\python.exe -m pytest tests\integration\test_documentation_fixtures_e2e.py -q --basetemp=db\fixtures\pytest-documentation
```

Do not use a basetemp containing valuable files: pytest owns that scratch directory.
That file must skip unless the explicit environment opt-in is present on Windows, and
it must never use or modify Northwind. It will verify fixture construction separately
from extractor correctness; failures are qualification findings, not permission
to weaken the expected manifest.

Link creation intentionally contacts the local sentinel during construction.
Hash equality or lack of lock files cannot establish that later inspection never
contacts it. That claim needs access-observation evidence during the inspection
window, which is outside this initial suite's proof.

No startup macro or executable VBA fixture is generated or run here. Startup
execution remains separately authorized. An unchanged source hash establishes
no observed mutation, not engine-level read-only enforcement or non-execution.

See [workflow specification](documentation-workflows.md) and
[task ledger](../../odd/tasks/documentation-fixtures.md).
