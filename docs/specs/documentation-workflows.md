# Proposed feature: Access documentation workflows (v1)

> **IMPLEMENTED — PARTIALLY RUNTIME-QUALIFIED (ACE/DAO and ACE/ODBC, fixture scope).**
> Contracts, services, MCP tools, CLI commands, and prompts below are implemented and
> covered by unit tests on branch `feat/documentation-workflows`. All four original
> partial-qualification gaps are closed at fixture scope: query parameter metadata, the
> ODBC inspection path, filtered inspection (DAO), and startup-code non-execution (DAO
> and ODBC pipeline plus the gated control run). Schema/query extraction, field-default
> honesty, query categories including update/delete, linked-table metadata rendering,
> save gating, engine-level read-only inspection open (DAO), linked-source isolation
> (DAO lock-probe), and the non-execution invariants are also qualified — see §7.2 for
> the exact per-claim scope and evidence citations, including the ACE/ODBC row, its
> capability table, the filtered-inspection subsection, and the startup-code
> non-execution subsection.
>
> **Still NOT QUALIFIED / unclaimed:** legacy Jet 3 and Jet 4 `.mdb` format fixtures;
> ODBC-attached links (`dbAttachedODBC`, `Attributes` `0x20000000`, are read as local
> tables); filtered inspection over the ODBC backend; engine-level read-only for ODBC
> (the claim is DAO-scoped only); VBA/macro execution beyond the `startup.accdb`
> fixture; and bounded resource limits (§6.3: inspection timeout and
> evidence/report-size caps remain TBD). The ACE/DAO matrix cell therefore stays
> **PARTIALLY QUALIFIED**.
>
> **Status:** Implemented; runtime qualification matrix in §7.2 (ACE/DAO and ACE/ODBC **PARTIALLY QUALIFIED** at fixture scope). Related: [Architecture](../01-architecture.md), [Contracts](../02-contracts.md), [Safety and configuration](../03-safety-and-configuration.md), [Feature task](../../odd/tasks/documentation-workflows.md).

## 1. Overview and scope

> **Implementation status:** Implemented and unit-tested (evidence model, renderer, save
> and inspect services, analysis-profile tools, CLI group, prompts). Runtime behavior
> against real databases is not exercised in the unit suite.

This specification defines deterministic, offline workflows that turn eligible Microsoft Access metadata into one Markdown report: schema/data dictionary, saved-query catalogue, relationship catalogue, Mermaid ER diagram, coverage, and limitations.

The v1 sequence is inspect metadata into typed evidence; render that evidence deterministically; preview and optionally save under separate authorization. The operations are `documentation_inspect`, `documentation_render`, and `documentation_save`. Guidance is provided by `document_database` and `generate_er_documentation`. Operations are deterministic application behavior; optional LLM interpretation is client-side and cannot alter facts.

### In scope

- Tables, fields, primary keys, indexes, declared relationships, and saved queries, subject to qualified metadata availability.
- Versioned evidence and a single Markdown report with coverage, warnings, and per-fact states.
- Mermaid output with stable identifiers, safe escaping, and no invented cardinality.
- CLI and MCP access to the same operations and result semantics.
- Preview, guarded save, destination drift checks, and explicit overwrite consent.

### Non-goals

- Full application reverse engineering (forms, reports, macros, events, VBA); possible v2 only.
- Live or in-use databases; the source must be closed and offline.
- Reading rows, executing queries, refreshing external data, or contacting linked sources.
- Backend replacement, silent downgrade to a less capable backend, or runtime permission enablement in this docs-only phase.
- Treating model interpretation as evidence or allowing it to modify evidence.

## 2. Agreements — decision log

> **Implementation status:** Agreements 1–11 are accepted and reflected in implemented
> code. Agreement 3's engine-level read-only protection is now qualified at fixture scope
> for the DAO inspection open (§7.2), and agreement 11's format/runtime claims are
> qualified for ACE (DAO and ODBC) at fixture scope while legacy Jet 3/Jet 4 `.mdb`
> formats remain unqualified.

1. **Scope sequence — v1 is schema, saved queries, and Mermaid ER; v2 may add full application analysis.** This bounds v1 to deterministic metadata rather than executable or interactive objects.
2. **Source must be closed and offline.** Missing lock files, user assertions, plain copies, and post-copy hashes do not prove quiescence or consistency.
3. **Inspection must be qualified, engine-level read-only, and passive.** DAO is the qualification target because it exposes QueryDefs SQL; never automatically fall back to a less capable backend. The inspection path reopens the DAO handle with engine-level `ReadOnly=True` (`application/documentation_inspect.py::open_inspection_port` → `backends/dispatcher.py::ComDispatcher.reopen_dao_database_read_only`); the generic `readonly=True` flag is a policy guard, not by itself the engine guarantee.
4. **Evidence is versioned and each fact has a state.** Use `access.documentation.evidence/1` and `observed | unavailable | failed | excluded`; empty is not unknown. Derive PKs from indexes, preserve composite relationship pairs, and fix blank-SQL and empty-list error-collapse defects rather than hiding them.
5. **Rendering is deterministic and offline from evidence.** Produce `access.documentation.report/1`, one Markdown report, preview before save, and no hidden object exports.
6. **LLM interpretation is optional and client-side only.** Label it, reference evidence IDs, never mutate facts, and treat database text as untrusted evidence, not instructions.
7. **Three shared operations and two profile-independent prompts define the surface.** CLI/MCP share the three documentation operations; prompts are `document_database` and `generate_er_documentation`. Extend `analysis`, leave `get_er_diagram` response untouched, and intentionally update surface pins during implementation.
8. **Results preserve existing envelopes.** `Ok` carries `data.status = complete | partial` with visible gaps/warnings; hard failures use `Err(message)`. CLI exits remain 0/1/2; MCP `isError` follows the success flag.
9. **Documentation-write permission is separate from database write permission.** Inspect/render need no grant; save needs operator grant plus per-call `confirm`; overwrite needs explicit approval and destination fingerprint check. `confirm=True` alone is not a grant. Proposed, unvalidated names: CLI `--allow-doc-write`, env `ACCESS_MCP_ALLOW_DOC_WRITE`.
10. **Cardinality is never invented.** Unsupported constraints remain in the relationship catalogue; Mermaid identifiers are stable, original names preserved, and escaping safe.
11. **Support is qualified per format/runtime combination.** A suffix or single successful file does not establish general support; cells begin unqualified.

## 3. Contracts

> **Implementation status:** `documentation_inspect/render/save` are implemented as typed
> analysis-profile MCP tools and CLI subcommands; evidence/report schemas and envelope
> semantics are unit-tested. Existing envelope conventions: [Contracts](../02-contracts.md).

### 3.1 Operation arguments

These are proposed shared logical arguments. CLI spelling and MCP serialization must be equivalent; additions require deliberate contract and surface-pin updates.

| Operation | Argument | Required | Meaning |
|---|---|---:|---|
| `documentation_inspect` | `database` | yes | Source path; apply existing database path policy. |
|  | `format` | no | Qualified format/runtime hint; never overrides detection. |
|  | `include_query_sql` | no | Include readable saved-query SQL; default false; SQL may contain secrets. |
|  | `tables` | no | Repeatable object filter: inspect only the named tables, in request order; omitted inspects every eligible table. Repeat the argument for each name — a comma-joined value is one whole name, and an unknown name is a hard `Err` (CLI exit 1). |
|  | `queries` | no | Repeatable saved-query filter with the same semantics as `tables`. |
| `documentation_render` | `evidence` | yes | Validated `access.documentation.evidence/1`; no database connection. |
|  | `options` | no | Deterministic presentation options, including Mermaid and section selection. |
| `documentation_save` | `report` | yes | Rendered report with schema/version and content fingerprint. |
|  | `destination` | yes | Markdown output path checked by output guards. |
|  | `confirm` | yes | Per-call affirmative confirmation, not operator permission. |
|  | `overwrite` | no | Explicit approval; required when destination exists. |
|  | `expected_fingerprint` | conditional | Fingerprint observed at preview/preflight; required for safe overwrite. |

These logical names do not promise final wire spellings. Implementation must publish matching CLI help and MCP schemas and reject unsupported versions or malformed inputs rather than guess.

### 3.2 Evidence schema: `access.documentation.evidence/1`

Evidence is a typed, serializable inspection snapshot. Each fact has a stable evidence ID, subject/reference, value when observed, state, and optional explanation/source metadata. The exact nested layout must be finalized before release; schema version and state semantics are fixed here.

| State | Meaning |
|---|---|
| `observed` | Inspection returned the stated value; empty collections can be observed. |
| `unavailable` | Qualified engine/runtime did not expose the capability or metadata. |
| `failed` | A specific extraction attempt failed; retain safe error context. |
| `excluded` | Deliberately not inspected by scope or policy; state the exclusion. |

Record source identity/format/runtime as available, method, timestamp, evidence IDs, object inventories, fields, indexes/PKs, relationships, saved-query metadata, warnings, and coverage. Distinguish known empty from unavailable. Exclude rows, query results, VBA, passwords, and hidden exports.

Minimum evidence subject categories:

| Category | Evidence content |
|---|---|
| Database | Detected format/runtime and inspection capability/limitations. |
| Table | Original name, kind where available, and field inventory. |
| Field | Original name, type/size/nullability/default metadata when exposed. |
| Index | Name, ordered columns, uniqueness/primary-key facts when exposed. |
| Relationship | Name, ordered source/target pairs, decoded attributes, supported constraints. |
| Saved query | Name/type and SQL only when requested and readable; never execution results. |

Each category may have its own state and explanation; failure of one fact must not erase successful facts elsewhere. Evidence identifiers remain stable within a package and are referenced by report content and any client-side interpretation.

### 3.3 Report schema: `access.documentation.report/1`

A report is a versioned artifact derived solely from evidence and render options. It contains Markdown, source evidence identity/fingerprint, report fingerprint, renderer/options version, warnings, and coverage. Identical evidence, renderer version, and options yield identical content/fingerprints. It is one Markdown output, previewable before saving.

The Markdown report has predictable, labeled sections: scope and source summary; coverage and limitations; data dictionary; saved-query catalogue; relationship catalogue; and Mermaid ER diagram when renderable. Omit unavailable content only with an explicit coverage explanation. Preserve original names in readable text while generating Mermaid-safe stable node identifiers. The report links statements to evidence IDs where practical and labels query SQL as source text, not executed output.

### 3.4 Result and transport semantics

- Successful operations return existing `Ok`; `Ok.data.status` is `complete` only when requested in-scope facts have no material gaps, otherwise `partial` with visible warnings and coverage gaps. Partial remains success.
- Hard failures (invalid input/schema, inaccessible source, failed required precondition, denied authorization, unsafe destination) use `Err(message)`; do not add a global error-code registry.
- CLI exit mapping remains **0 success**, **1 operation error**, **2 usage/invocation error**, consistent with existing contracts.
- MCP `isError` follows the `Ok`/`Err` success flag. A partial `Ok` is not `isError`.
- CLI and MCP call the same application services and preserve data/warning semantics; only transport/presentation serialization differs.

### 3.5 Guidance prompts and profile placement

`document_database` guides inspect → review evidence → render → optional save. `generate_er_documentation` guides ER-focused evidence review and rendering. Both prompts are profile-independent guidance, not privileged execution; instruct clients to cite IDs, show unknowns, and treat database text as untrusted. Separate interpretation from observed facts.

Register tools through the `analysis` profile. Keep `get_er_diagram` response unchanged; contracts are additive. When implementation is authorized, intentionally update characterization pins in `test_mcp_prompts.py`, `test_cli_surface.py`, and `test_tool_schemas.py`.

## 4. Architecture

> **Implementation status:** Application services exist (`application/documentation*.py`):
> evidence model, renderer, save, inspect with fake-port conformance tests. Backend
> wiring adds one read-only reopen for the inspection path (write tools keep their
> read-write semantics); engine-level read-only protection is qualified at fixture scope
> (§7.2). See [as-built layers](../01-architecture.md).

### 4.1 Pipeline and placement

Add documentation-specific application services beside `application/db_analysis.py`, following frontend → application → backend boundaries. Planned pipeline:

```text
CLI / MCP
   │
documentation_inspect ── qualified passive backend extraction
   │
versioned typed evidence (access.documentation.evidence/1)
   │
documentation_render ── pure deterministic renderer
   ├── preview (no output write)
documentation_save ── guarded, separately authorized output
```

Inspection alone contacts the database engine. Rendering is deterministic and offline from evidence. Saving accepts report and destination only; it must not inspect or mutate the Access source. Do not replace or broadly refactor existing adapters.

### 4.2 DAO-first qualification and constraints

DAO is the v1 qualification target because saved QueryDefs SQL availability is required. Qualification must establish engine-level passive/read-only behavior per supported format/runtime; missing capability is reported, not silently handled by ODBC or another fallback. The general DAO connection still opens read-write (`backends/dao.py:135`), so write tools keep their semantics; the documentation-inspection path reopens that same handle engine-level read-only before reading metadata (`application/documentation_inspect.py::open_inspection_port` → `backends/dispatcher.py::ComDispatcher.reopen_dao_database_read_only`). This engine-level protection is qualified at fixture scope (§7.2) and stays **DAO-scoped**: the ODBC inspection path has no read-only reopen (`OdbcInspectionPort` reuses the adapter's existing connection) and is qualified only by behavioral no-write evidence (source hashes).

An ODBC inspection port (`application/documentation_inspect.py::OdbcInspectionPort`, exercised by `documentation inspect --backend odbc`) is additive to that DAO path. It satisfies the same `InspectionPort` protocol and reports ODBC-invisible properties through `InspectionCapabilities` as explicit `unavailable` facts with a per-capability reason; the DAO path keeps full fidelity. See the §7.2 ODBC row and capability table.

Implementation must characterize and fix these known extraction defects before claiming coverage:

| Defect / gap | Required behavior and source reference |
|---|---|
| DAO `get_table_schema` always leaves `primary_key` empty. | Derive from index metadata and preserve composite key order. `backends/schema_inspector.py`. |
| Query read failure can produce blank SQL. | Do not report observed blank SQL; record `failed` with safe explanation. `backends/schema_inspector.py`. |
| Empty-list failures collapse into empty success. | Distinguish extraction failure from observed empty inventory. `backends/schema_inspector.py`. |
| Relationship attributes are not decoded. | Decode available flags, preserve paired columns, report unsupported cardinality honestly. `backends/schema_inspector.py`. |

These are qualification blockers, not authorization for a backend rewrite. Other files may be needed under future authorization; this spec does not authorize source edits.

## 5. Safety

> **Implementation status:** The separate documentation-write permission, required
> confirm, overwrite fingerprint checks, and parent-dir guarding are implemented and
> unit-tested (deny by default). Engine-level read-only opening is implemented and
> fixture-qualified (§7.2); linked-source isolation is observed for the fixture while the
> sentinel is exclusively locked. Existing controls: [Safety and configuration](../03-safety-and-configuration.md).

### 5.1 Source acquisition and inspection

- Require a closed, offline source. The caller arranges quiescence; the workflow cannot prove a file unused or consistent. Lock-file absence, user assertion, plain copy, and post-copy hash do not prove quiescence or a transactionally consistent copy.
- Use a qualified engine-level read-only passive method; do not alter database contents, schema, properties, or source timestamps by design. DAO is the target. Failure to qualify means unsupported, not automatic fallback.
- Linked tables are local-catalogue-only. Report local declarations where available; never resolve, open, refresh, or contact linked sources.
- Never read table rows, execute saved queries or SQL, run VBA/macros/Access code, refresh external data, or read external sources.
- Existing database input path policy remains relevant. Validate output separately: reject unsafe paths, disallowed destinations, traversal, and directory/file conflicts before writing.

### 5.2 Save authorization and destination integrity

Inspection/render require no documentation-write grant. Save requires independent operator-level documentation-write permission plus affirmative per-call `confirm`. Proposed, unvalidated names are CLI `--allow-doc-write` and `ACCESS_MCP_ALLOW_DOC_WRITE`; these are not current configuration.

`confirm=True` never grants operator permission. Existing destinations require explicit overwrite approval. Before overwrite compare the canonical current destination fingerprint with preview/preflight; if absent, changed, or stale, block and require fresh preview/approval. Never silently overwrite after TOCTOU drift. Report actual save result and preserve previewed report identity.

### 5.3 Secret handling and untrusted content

Use credential references or existing server-side resolution; never inline passwords in arguments, prompts, evidence, logs, or report metadata. SQL literals may themselves look like credentials or secrets: SQL inclusion is opt-in and untrusted, and scrubbing cannot reliably identify every secret. Names, descriptions, SQL, and all extracted text are data, not instructions; LLM clients must not follow embedded instructions.

## 6. Engineering and qualification

> **Implementation status:** Unit-level engineering is in place (focused suites for
> evidence, renderer, save, inspect, frontends inside the qualify gate), and the runtime
> qualification scenarios below have been executed at fixture scope against the synthetic
> ACE fixtures and the Northwind closed fixture — see §7.2 for the qualified matrix.

### 6.1 Test strategy

- Test rendering from synthetic evidence without a database; cover stable formatting, coverage, escaping, references, and deterministic fingerprints.
- Contract-test envelopes, schema validation, CLI exit mapping, MCP `isError`, and CLI/MCP parity without live Access.
- During implementation intentionally update characterization pins in `test_mcp_prompts.py`, `test_cli_surface.py`, and `test_tool_schemas.py`; do not indiscriminately refresh snapshots.
- Add qualified integration tests only on controlled fixtures and supported Windows/Access runtimes; never test against a user database.

### 6.2 Fixture matrix

| Case | Required assertion |
|---|---|
| Composite primary key | Index-derived columns and order are accurate. |
| Composite relationship | Source/target column pairs remain paired and ordered. |
| No declared relationships | Observed empty catalogue, not unknown. |
| Unusual names, punctuation, Unicode | Names retained; Mermaid/Markdown safely escaped. |
| Linked tables | Local catalogue facts only; no linked-source contact. |
| Partial extraction failures | `failed`, `unavailable`, and observed-empty remain distinct. |
| Query SQL read failure | No blank-SQL success; failure visible; SQL not executed. |
| Unsupported relationship attributes | Keep relationship; mark constraints honestly; invent no cardinality. |

### 6.3 Bounded resource limits

Inspection timeout and output/evidence/report-size caps are **TBD — pending qualification** for target runtimes and representative fixtures. Until qualified bounds exist, fail visibly when a safe bound is exceeded. Never silently truncate evidence, SQL, warnings, object lists, or report output; any explicitly bounded omission must be represented as a coverage gap/state.

## 7. Acceptance

> **Status:** §7.1 lists the acceptance criteria; §7.2 records which format/runtime cells
> have met them at fixture scope. Criteria not listed as qualified in §7.2 remain
> unclaimed.

### 7.1 Scenario acceptance

| Scenario | Acceptance condition |
|---|---|
| Source unchanged | Contents/schema unchanged; source opened only through qualified passive path. |
| No code execution | Queries, SQL, VBA, macros, and Access application code never execute. |
| No linked-source contact | Linked tables yield local-catalogue evidence only; no connection or refresh. |
| Primary/composite key correctness | Index-derived PKs, including composite keys/order, match fixtures. |
| Honest unavailable metadata | Unsupported/inaccessible facts have explicit states/reasons; empty is not unknown. |
| Valid escaped Mermaid | Diagram parses, names are escaped, cardinality is not invented. |
| Determinism | Identical evidence/options/renderer version yield identical content/fingerprint. |
| Preflight and drift blocking | Invalid path, denied grant, stale fingerprint, or unapproved overwrite blocks save. |
| Preview/cancel/overwrite | Preview/cancel write nothing; overwrite requires explicit approval and fresh matching fingerprint. |
| Partial versus complete | Partial is successful `Ok` with `data.status=partial` and gaps; complete requires requested in-scope facts without material gaps. |

### 7.2 Format/runtime qualification matrix

Support is declared per format/runtime, never inferred from suffix. Every cell begins **NOT QUALIFIED** and changes only after controlled evidence and explicit implementation approval.

| Database format | Runtime / engine combination | Initial status |
|---|---|---|
| Jet 3 (`.mdb`) | DAO / Access runtime combination to be specified and tested | **NOT QUALIFIED** |
| Jet 4 (`.mdb`) | DAO / Access runtime combination to be specified and tested | **NOT QUALIFIED** |
| ACE (`.accdb`) | DAO backend; Northwind 8-table closed fixture (2026-10-08) and the synthetic fixture suite (`core`/`sentinel`/`linked`/`startup`), Windows/ACE | **PARTIALLY QUALIFIED (fixture scope).** Covered, each with its scope and evidence: schema/relationships/saved queries (Northwind 8-table closed fixture, ledger T11 of `odd/tasks/documentation-workflows.md`; synthetic fixture `test_inspect_core_covers_exactly_manifest_tables_and_queries`); index-derived composite PKs and the genuine unique index (`test_inspect_core_primary_keys_and_unique_index`); field-default honesty — DAO `Field.DefaultValue` as `observed` stored expressions, defaultless fields `observed` empty, a `None` producer mapped to `unavailable` at the seam (`test_inspect_core_field_defaults_are_read_from_dao`; unit `test_field_default_value_is_read_from_dao_and_nul_stripped`, `test_field_default_none_maps_to_unavailable_while_real_defaults_stay_observed`); query categories including update/delete (32/48/64 → `action`, 80/96 → `ddl`; unit `test_maps_dao_update_type_48_to_action_category`); saved-query parameter metadata from DAO `QueryDef.Parameters` (`test_inspect_core_query_parameters_are_read_from_dao`: `[pCustomerID]` `observed` type `4`/`Long Integer`, parameterless `observed` empty, a non-exposing port `unavailable`); linked-table local catalogue metadata reaching evidence and rendering as `- Linked table: <name> → <file>/<source-table>` (`test_finding_inspect_linked_local_link_metadata_absent_from_evidence`, now passing); save gating (`test_save_gating_contract_on_report_copies`); engine-level read-only inspection open (`test_inspect_open_is_engine_read_only_and_refuses_write`: the read-only handle refuses `UPDATE` with `Operation must use an updateable query.`); linked-source isolation under an exclusive lock probe (`test_inspect_linked_succeeds_while_sentinel_is_exclusively_locked`); filtered inspection (`test_inspect_filtered_tables_returns_exactly_the_requested_subset`, `test_inspect_filtered_queries_returns_exactly_the_requested_query`, `test_inspect_combined_table_and_query_filters_scope_both_axes`, `test_inspect_unknown_requested_name_is_a_hard_error`); startup-code non-execution (`test_startup_code_is_never_executed_by_the_pipeline[dao]` plus the doubly gated control run `test_control_run_executes_marker_under_isolation` that proves the fixture genuinely executes under isolation); non-execution invariants after inspect → render → save (`test_non_execution_invariants_after_inspect_render_save`); deterministic rendering and Mermaid cardinality rules (`test_render_database_is_deterministic`, `test_render_mermaid_relationship_cardinality_matches_documented_rules`). The cell stays PARTIALLY QUALIFIED because two claims inside the ACE/DAO scope remain unproven. **NOT QUALIFIED (DAO scope):** **ODBC-attached links** — `SchemaInspector.get_table_link_metadata` tests only the ACE attached-table bit `0x40000000`, so a `dbAttachedODBC` link (`Attributes` `0x20000000`) is reported as a local table, and no fixture exercises that link type; and **VBA/macro execution beyond the `startup.accdb` fixture** (only the single control run executes code). Cosmetic gaps intentionally left open, not qualification claims: nullable-FK cardinality rendering, the pywin32 `IUnknown`/`0x80010108` teardown stderr banner, and the `source.path`/`source.format` placeholders. |
| ACE (`.accdb`) | ODBC backend (`Microsoft Access Driver (*.mdb, *.accdb)`), synthetic-fixture pipeline suite (`test_inspect_core_via_odbc_exposes_odbc_metadata_with_honest_unavailable_gaps`), Windows, 2026-10-08 | **PARTIALLY QUALIFIED (inspection only)**: `documentation inspect --backend odbc` reads the same built `core.accdb` fixture copy through the Access ODBC driver, produces evidence, and that evidence renders offline (database report). ODBC-visible facts are `observed` with the driver's own values (never the DAO equivalent): eight user tables including `Sales Notes`; SQLColumns type names/sizes (`INTEGER` 10, `VARCHAR`, `CURRENCY` — not DAO `Long Integer`/`Currency`); SQLStatistics index names, columns, and uniqueness including composite `PK_OrderLines` order; three row-returning saved queries as `VIEW` rows. ODBC-invisible properties are `unavailable` with a per-capability reason and are never fabricated: `field_required`/`field_allow_zero_length` (SQLColumns reports `NULLABLE` for every column and has no `AllowZeroLength` equivalent), `primary_key` (`SQLPrimaryKeys` unsupported IM001 and SQLStatistics does not flag the primary index, so reported indexes carry `is_primary=false` and every table records a `coverage.tables.unsupported` reason), `relationships` (`SQLForeignKeys` unsupported IM001; `MSysRelationships` unreadable), `query_sql`/`query_type` (only `VIEW` rows are visible), and a linked table's `Connect`/`SourceTableName` (ODBC reports `SYNONYM` without the target). Declared query parameters are `observed` through `SQLProcedureColumns` (`[pCustomerID]`, ODBC SQL data type `4` = `INTEGER`; parameterless queries report a driver-known empty, distinct from `unavailable`). Action queries (`qryUpdateOrderStatus`, `qryDeleteDraftOrders`) are not `VIEW` rows and are invisible to ODBC, so the ODBC query catalogue covers the three row-returning queries only. The test asserts the source copy's sha256 is unchanged before and after and that no `core.accdb.*` lock file remains; the ODBC open carries **no engine-level read-only claim** (behavioral no-write evidence only) and T4's engine-level read-only qualification stays DAO-scoped. ODBC catalog reads on a linked table **do** contact the linked source read-only, so isolation is **NOT guaranteed** over ODBC (unlike the DAO path's lock-probe-qualified isolation); with the zero-columns fix, a listed table whose source is unreachable now surfaces as a `failed` table (never an observed-empty schema), proved by `test_inspect_linked_via_odbc_reports_unreachable_source_as_failed_table`. Startup-code non-execution is qualified for this backend by `test_startup_code_is_never_executed_by_the_pipeline[odbc]`: the full inspect → render → save cycle over `startup.accdb` through the Access ODBC driver exits 0, carries `StartupNotes` in evidence, and leaves the source hash unchanged with no lock residue, no new MSACCESS.EXE process, and no `*marker*` file. NOT COVERED: engine-level read-only for ODBC (DAO-scoped claim only), filtered inspection over ODBC, and VBA/macro execution beyond the startup fixture. |

#### ODBC capability table (Access ODBC driver, `core.accdb` fixture)

Probed before implementation; the pipeline test asserts every row. ODBC-visible metadata is reported as-is, and every invisible property is `unavailable` with a reason.

| Metadata | ODBC call | Result |
|---|---|---|
| User tables | `SQLTables(TABLE)` | Exposed: eight tables including `Sales Notes`; `SYSTEM TABLE` rows excluded by the inspection filter. |
| Linked tables | `SQLTables(SYNONYM)` | Name and link-ness only (`SYNONYM`); `Connect`/`SourceTableName` are not exposed (proved on `linked.accdb`). |
| Columns, types, sizes | `SQLColumns` | Exposed with ODBC type names/sizes. `NULLABLE` is `YES` for every column (it does not reflect DAO `Required`); no `AllowZeroLength`, default, or validation-rule column. |
| Indexes | `SQLStatistics` | Exposed index names, columns, and uniqueness (including composite-column order); no primary-index flag. |
| Primary key | `SQLPrimaryKeys` | Not exposed: driver returns IM001 "does not support this function". |
| Relationships / foreign keys | `SQLForeignKeys`, `MSysRelationships` | Not exposed: `SQLForeignKeys` is IM001 and `MSysRelationships` cannot be read. |
| Saved query names | `SQLTables(VIEW)` | Exposed for row-returning queries only; action queries are absent. |
| Saved-query SQL text / category | `MSysQueries`, query DDL | Not exposed: `MSysQueries` cannot be read and `VIEW` rows carry no category. |
| Query parameters | `SQLProcedureColumns` (`column_type = SQL_PARAM_INPUT`) | Exposed: declared parameter names plus ODBC SQL data type codes/names; a query with no input rows is a driver-known empty. |
| Field defaults / validation rules | `SQLColumns.column_def` | Not exposed (`None`). |

> **Index caveat (ODBC).** Index facts reported over ODBC carry **no primary-key flag**
> (the driver has no `SQLPrimaryKeys` and `SQLStatistics` does not mark the primary
> index). Where a reported index has `is_primary=false`, that means **not asserted**,
> not that it is a secondary index; primary keys are per-table `unavailable` over ODBC
> with the `coverage.tables.unsupported` reason.

#### Filtered-inspection qualification (DAO, synthetic-fixture pipeline suite)

`documentation inspect --tables <name> --tables <name> ...` and `--queries <name> ...`
are **repeatable CLI occurrences** (`action="append"`); there is no comma-separated form,
and a comma-joined value is one whole name and is rejected as unknown. Qualified through
the public CLI in subprocesses against the synthetic `core.accdb` fixture, **DAO backend
only** (`tests/integration/test_documentation_pipeline_e2e.py`):

| Input | Qualified behavior |
|---|---|
| no `--tables`/`--queries` | `None` selects every eligible object (the unchanged full inspection). |
| explicit `--tables` list | Evidence carries exactly the requested tables in request order; `coverage.tables.inspected` and `run.scope.tables` are the same list. |
| filtered-out table | Absent from the document and from every `coverage.tables` bucket — never `failed`, never `unsupported`, never a fabricated `unavailable`. |
| explicit `--queries` list | The query catalogue carries exactly the requested query; the other fixture queries are absent from evidence and coverage, and the table scope is not narrowed. |
| combined both | Both axes scope independently in one invocation. |
| table subset + relationships | A relationship whose parent or child table is not selected is not inspected and is recorded in `coverage.relationships.excluded` with reason `Relationship references a table outside the requested table subset`; every relationship that reaches evidence references two selected tables. |
| filtered evidence | Renders one coherent `database` report for the subset (subset alias nodes, subset edges, subset coverage, subset table sections). |
| unknown name | Hard `Err` naming the requested objects, CLI exit 1, no evidence payload, source bytes untouched — consistent with §3.4 (`Err(message)` = operation error). |

Blocking defect fixed in T3: filtered evidence retained every fixture relationship while
the document carried only the requested tables, so rendering raised `KeyError` while
resolving a relationship endpoint to a Mermaid alias and the documented inspect → render
pipeline produced **no report** for any subset that cut a relationship in half (for
example `--tables Customers --tables Orders`). The relationship catalogue is now scoped to the
selected subset at the inspection seam (`application/documentation_inspect.py`) with the
omission recorded in coverage; the renderer itself is unchanged.

Documented filter contract (the actual implemented behavior). `coverage.tables.excluded`
is reserved for **system/policy** exclusions such as `MSys*` objects and never records a
table removed by a requested `tables` filter; `coverage.tables.inspected` is the
filtering signal and is exactly the requested (or, when the filter is omitted, all
eligible) table list, while a filtered-out table leaves no trace in any
`coverage.tables` bucket. A requested name that exists but is excluded by policy (for
example `MSysObjects`) is rejected with the same
`Unknown requested table name(s): <name>` hard `Err` and exit 1 as a nonexistent name —
there is no distinct "excluded by policy" error. Duplicate requested names are **not
de-duplicated**: `--tables Orders --tables Orders` yields the table twice.

Known cosmetic limitation (a follow-up item, not fixed by this documentation-only
closure): when a filter excludes every relationship, the relationship catalogue renders
the same `_No relationships observed_` line as when no relationship exists at all, so
the renderer does not distinguish "none exist" from "none selected". The coverage
`excluded` entries carry that distinction for clients that need it.


#### Startup-code non-execution qualification (DAO and ODBC)

`startup.accdb` is the executable fixture: one `StartupNotes` table plus a standard VBA
module `StartupMarkerModule` and a macro renamed to `AutoExec` that runs
`StartupMarker()` through `RunCode`. The function writes a harmless
`startup_executed.marker` file beside whatever database it runs from
(`Application.CurrentProject.Path`); the generated VBA contains no absolute, user,
home, temporary, or network path. The generator builds it without executing it (DAO DDL
first, Access.Application opened only before `AutoExec` exists, macro renamed last, no
reopen) and asserts no marker, no lock, and no started MSACCESS.EXE process.

Non-execution is qualified for **both** inspection backends by
`test_startup_code_is_never_executed_by_the_pipeline[dao]` and `[odbc]`: the full
`documentation inspect` → `render` → `save` cycle over `startup.accdb` exits 0, carries
the `StartupNotes` table in evidence, leaves the source hash unchanged, leaves no lock
residue or new MSACCESS.EXE process, and creates no `*marker*` file. This is
non-execution by construction: the DAO path uses `DAO.DBEngine.120` and the ODBC path
the Access ODBC driver, and **neither launches Access.Application**, the only runtime
that runs `AutoExec`.

The property is not vacuous because the doubly gated control run
`tests/integration/test_documentation_startup_control.py` deliberately executes the
fixture: with `ACCESS_DOCUMENTATION_FIXTURES_TEST=1` and `ACCESS_STARTUP_CONTROL_TEST=1`
it copies `startup.accdb` into an isolated scratch subdirectory of the self-created
session tree, opens the copy with `AutomationSecurity` at the low (macro-enabled)
automation level, observes `startup_executed.marker` beside the copy within 30 s, quits
Access (force-killing only the captured PID if Quit hangs), and asserts the marker is
confined to the scratch directory with no surviving MSACCESS.EXE process and no
`.laccdb`. Missing either gate skips cleanly. The DAO backend's engine-level read-only
qualification stays scoped to the inspection open, and the ODBC path still has no
engine-level read-only claim. The matrix is finalized in the §7.2 cells above: ACE/DAO
stays PARTIALLY QUALIFIED for ODBC-attached links and VBA/macro execution beyond this
fixture.

Qualification records engine version/bitness, host requirements, passive/read-only evidence, QueryDefs SQL availability, relationship/index fidelity, fixture results, and failure behavior. No qualification means unsupported/unknown coverage, never implied support.

## Agreement-to-section map

| Agreement | Spec section(s) |
|---:|---|
| 1 | §1; §2.1 |
| 2 | §5.1 |
| 3 | §4.2; §5.1 |
| 4 | §3.2; §4.2; §6.2 |
| 5 | §3.3; §4.1; §5.2 |
| 6 | §3.5; §5.3 |
| 7 | §3.1; §3.5; §4.1 |
| 8 | §3.4 |
| 9 | §3.1; §5.2 |
| 10 | §3.2; §4.2; §6.2; §7.1 |
| 11 | §7.2 |
