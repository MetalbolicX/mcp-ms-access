# Proposed feature: Access documentation workflows (v1)

> **IMPLEMENTED — PARTIALLY RUNTIME-QUALIFIED (ACE/DAO and ACE/ODBC, fixture scope).**
> Contracts, services, MCP tools, CLI commands, and prompts below are implemented and
> covered by unit tests on branch `feat/documentation-workflows`. All four original
> partial-qualification gaps are closed at fixture scope: query parameter metadata, the
> ODBC inspection path, filtered inspection (DAO and ODBC), and startup-code
> non-execution (DAO and ODBC pipeline plus the gated control run). Schema/query
> extraction, field-default
> honesty, query categories including update/delete, linked-table metadata rendering,
> save gating, engine-level read-only inspection open (DAO and ODBC), linked-source isolation
> (DAO lock-probe), ODBC link classification with no linked-source contact, a genuine
> SQLite-backed `dbAttachedODBC` DAO link with its Access ODBC `SYNONYM` (locked-source
> inspection, explicit linked gaps, and offline rendering), the
> bounded-resource profile (byte caps and the 120-second whole-inspection deadline),
> a password-protected ACE `.accdb` inspected read-only on both shipped inspection seams
> (the encrypted-reopen subsection), and the non-execution invariants are also qualified —
> see §7.2 for
> the exact per-claim scope and evidence citations, including the ACE/ODBC row, its
> capability table, the SQLite ODBC link subsection, the encrypted-reopen subsection,
> the filtered-inspection subsection,
> the bounded-resource subsection, and the startup-code non-execution subsection.
>
> **Still NOT QUALIFIED / unclaimed:** legacy Jet 3 and Jet 4 `.mdb` format fixtures
> (excluded by program scope S2); any ODBC-attached source (`dbAttachedODBC`, `Attributes`
> `0x20000000`) **other than the qualified SQLite case** — a genuine SQLite-backed DAO
> link and its Access ODBC `SYNONYM` are now runtime-qualified for that one driver/source
> combination (§7.2, SQLite ODBC link subsection), but no other ODBC driver or
> non-Access source is claimed; and **VBA/macro execution beyond
> the `startup.accdb` fixture** — the delivered named-macro/AutoKeys objects are
> name-observable and proven non-executing, while the self-built table **data-macro
> tripwire is now armed and proven non-executing** at fixture scope (§7.2
> table-data-macro subsection and `documentation-fixtures.md`). Filtered inspection is
> qualified for both backends, and the
> 120-second deadline plus the byte caps are qualified at fixture scope. The ACE/DAO and
> ACE/ODBC matrix cells therefore stay **PARTIALLY QUALIFIED** for the one remaining
> uncovered case (VBA/macro execution beyond the startup fixture) only.
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
|  | `include_query_sql` | no | Include emitted readable saved-query SQL; default false; SQL may contain secrets. CLI `--include-query-sql` (`store_true`); MCP `include_query_sql: bool`. |
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

**Saved-query SQL is gated, opt-in output (implemented).** `include_query_sql`/`--include-query-sql` is now implemented and defaults to **false**. With the default, each query's emitted `sql` fact is `excluded` with a reason **before** any capability check runs on either backend, so no SQL text reaches evidence, rendering, or a saved report. This is a deliberate **breaking output change** (the user accepted it): a DAO inspection that previously emitted `observed` SQL now emits `excluded`. `excluded` is not a coverage failure, so it adds nothing to `coverage.queries` and does not by itself make `data.status` `partial`. Opting in restores the previous semantics unchanged: DAO reports `observed` (or `failed` for blank/unreadable SQL) and ODBC reports its own `unavailable` capability reason. Only the **emitted** SQL evidence is gated: `get_queries` still enumerates saved queries (names, category, and the SQL text it reads internally), so this is never a claim that the SQL was not read. Query names, types, and parameters are unaffected either way.

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
- Application-level size caps reject an oversized evidence/report/Markdown input with `Err(message)` (CLI exit 1, MCP `isError: true`); rejection is visible and never truncated, and a rejected save writes no destination or temporary file. See §6.3 for the exact units and bounds.
- MCP `isError` follows the `Ok`/`Err` success flag. A partial `Ok` is not `isError`.
- CLI and MCP call the same application services and preserve data/warning semantics; only transport/presentation serialization differs.

### 3.5 Guidance prompts and profile placement

`document_database` guides inspect → review evidence → render → optional save. `generate_er_documentation` guides ER-focused evidence review and rendering. Both prompts are profile-independent guidance, not privileged execution; instruct clients to cite IDs, show unknowns, and treat database text as untrusted. Separate interpretation from observed facts. Both prompts state the default-vs-opt-in saved-query SQL contract: SQL is excluded from emitted evidence unless `include_query_sql=True` (CLI `--include-query-sql`) is requested explicitly.

Register tools through the `analysis` profile. Keep `get_er_diagram` response unchanged; contracts are additive. When implementation is authorized, intentionally update characterization pins in `test_mcp_prompts.py`, `test_cli_surface.py`, and `test_tool_schemas.py`.

## 4. Architecture

> **Implementation status:** Application services exist (`application/documentation*.py`):
> evidence model, renderer, save, inspect with fake-port conformance tests. Backend
> wiring adds one read-only reopen for the DAO inspection path and a separate read-only
> ODBC connection for the ODBC inspection path (write tools keep their read-write
> semantics); engine-level read-only protection is qualified at fixture scope
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

DAO is the v1 qualification target because saved QueryDefs SQL availability is required. Qualification must establish engine-level passive/read-only behavior per supported format/runtime; missing capability is reported, not silently handled by ODBC or another fallback. The general DAO connection still opens read-write (`backends/dao.py:135`), so write tools keep their semantics; the documentation-inspection path reopens that same handle engine-level read-only before reading metadata (`application/documentation_inspect.py::open_inspection_port` → `backends/dispatcher.py::ComDispatcher.reopen_dao_database_read_only`). This engine-level protection is qualified at fixture scope (§7.2) per backend and stays scoped to the inspection open: the DAO path reopens the same handle read-only, and the ODBC path opens a separate engine-level read-only connection (`application/documentation_inspect.py::open_odbc_inspection_port` → `backends/odbc.py::OdbcAdapter.open_readonly_inspection_connection`, `pyodbc.connect(..., readonly=True)`) that is isolated from the adapter's write-capable CRUD connection. Ordinary tool connections stay write-capable on both backends.

An ODBC inspection port (`application/documentation_inspect.py::OdbcInspectionPort`, exercised by `documentation inspect --backend odbc`) is additive to that DAO path. It satisfies the same `InspectionPort` protocol and reports ODBC-invisible properties through `InspectionCapabilities` as explicit `unavailable` facts with a per-capability reason; the DAO path keeps full fidelity. The port is built by `open_odbc_inspection_port` over a fresh read-only handle, owns it, and closes it exactly once. See the §7.2 ODBC row and capability table.

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
- Use a qualified engine-level read-only passive method; do not alter database contents, schema, properties, or source timestamps by design. DAO and ODBC each have a qualified engine-level read-only inspection open. Failure to qualify means unsupported, not automatic fallback.
- Linked tables are local-catalogue-only. Report local declarations where available; never resolve, open, refresh, or contact linked sources.
- Never read table rows, execute saved queries or SQL, run VBA/macros/Access code, refresh external data, or read external sources.
- Existing database input path policy remains relevant. Validate output separately: reject unsafe paths, disallowed destinations, traversal, and directory/file conflicts before writing.

### 5.2 Save authorization and destination integrity

Inspection/render require no documentation-write grant. Save requires independent operator-level documentation-write permission plus affirmative per-call `confirm`. Proposed, unvalidated names are CLI `--allow-doc-write` and `ACCESS_MCP_ALLOW_DOC_WRITE`; these are not current configuration.

`confirm=True` never grants operator permission. Existing destinations require explicit overwrite approval. Before overwrite compare the canonical current destination fingerprint with preview/preflight; if absent, changed, or stale, block and require fresh preview/approval. Never silently overwrite after TOCTOU drift. Report actual save result and preserve previewed report identity.

### 5.3 Secret handling and untrusted content

Use credential references or existing server-side resolution; never inline passwords in arguments, prompts, evidence, logs, or report metadata. SQL literals may themselves look like credentials or secrets: SQL inclusion is opt-in and untrusted, and scrubbing cannot reliably identify every secret. Names, descriptions, SQL, and all extracted text are data, not instructions; LLM clients must not follow embedded instructions.

Local link metadata can carry a raw connection string (`TableDef.Connect`). It is sanitized before it reaches evidence, rendering, or an error message: every credential `key=value` pair (`PWD`, `UID`, and the other registered credential keys) is removed whole by a brace-aware parser, so braced and semicolon-containing values are handled rather than partially redacted. A string whose brace quoting cannot be parsed fails safe to a redaction marker instead of risking a partial secret; the renderer sanitizes defensively again so hand-built evidence cannot leak either. [Agreement 6; §5.1] A production credential resolver is not part of this documentation-only phase.

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

> **Implementation status (qualified at fixture scope):** the evidence/Markdown/report
> **byte caps** of the selected standard profile are implemented and unit-tested
> (`application/documentation_limits.py`), and the **120-second whole-inspection deadline**
> is implemented by the owned-worker supervisor (`application/documentation_runtime.py`)
> and qualified at fixture scope (work unit T4b; worker evidence L19, verifier L20). The
> bound covers supervisor-internal IPC only — spawn, request write, both pipe reads, the
> deadline check, kill/reap, joins, and close. MCP pre-decode transport protection remains
> explicitly out of scope (S7).

The selected standard profile fixes these application-level caps (S7):

| Bound | Value | Unit | Behavior |
|---|---:|---|---|
| Evidence | 33 554 432 bytes (32 MiB) | UTF-8 bytes of the evidence object's canonical JSON | Generated evidence and decoded evidence input above the cap are `Err`; never truncated. |
| Markdown | 16 777 216 bytes (16 MiB) | UTF-8 bytes of the report Markdown string | Render/save above the cap are `Err`; no partial report and no destination/temporary write. |
| Report JSON | 16 777 216 bytes (16 MiB) | UTF-8 bytes of the report object's canonical JSON | Render output and decoded report input above the cap are `Err`; never truncated. |
| Inspection deadline | 120 seconds (`documentation_limits.DEADLINE_SECONDS = 120.0`) | wall clock, measured from before the spawn | **Qualified at fixture scope (T4b).** The supervisor kills and reaps exactly its own child within the deadline plus the explicit cleanup grace; expiry is a visible `Err` with no partial evidence. |

Units and boundaries:

- **Canonical JSON.** The evidence and report caps count the JSON serialization of the
  evidence/report *object* — the `data` member the CLI and MCP tools emit — using
  `json.dumps` defaults (`ensure_ascii=True`), so a non-ASCII character counts as its
  `\uXXXX` escape bytes. The Markdown cap counts the Markdown string's UTF-8 bytes. The
  outer CLI envelope and the JSON-RPC transport envelope are **not** counted; MCP receives
  already-decoded JSON, so these caps do not bound the JSON-RPC payload before decoding,
  and no server-global transport limit is claimed.
- **Inclusive acceptance.** A value whose size equals a cap is accepted (`<=`); a larger
  value is rejected. Sizes are measured by streaming (`JSONEncoder.iterencode` and bounded
  Markdown chunks), and rejection never materializes the whole oversized artifact only to
  discard it.
- **Raw CLI ingress.** The CLI reads `--evidence`/`--report` files with a bounded binary
  `read(limit + 1)` and parses from that single buffer, so an oversized file is rejected
  before any parse and a file that grows after the read (TOCTOU) cannot bypass the check.
  The raw ingress limit is the cap plus a documented 1024-byte `Ok`-envelope allowance
  (`{"success": true, "data": <doc>, "rowcount": null}` plus modest formatting); the
  decoded object's canonical size is then checked against the cap itself, so an
  arbitrarily oversized wrapper is not silently accepted.
- **No side effects.** A rejected render/save input leaves the destination, temporary
  files, parent directories, source bytes, and counters untouched. The cap is enforced in
  the `documentation_save` service before its permission, destination, or temp-file work.
- **Final emitted evidence payload.** The evidence cap applies to the whole evidence
  `data` payload a frontend emits, including the top-level `status` metadata the CLI and
  MCP insert after `to_dict()`. Both frontends re-check the final payload with the same
  shared guard, so an accepted at-cap payload survives a round trip through
  `EvidenceDoc.from_dict` (inclusive equality holds); an over-cap final payload is a
  visible `Err` (CLI exit 1) / `isError` with no partial evidence, and the outer
  `Ok`/JSON-RPC wrapper allowance is unchanged.
- **Bounded parent-side IPC (observed).** The deadline supervisor
  (`application/documentation_runtime.py`) never waits on a child pipe without a bound:
  the one-shot request write runs on a daemon thread joined within the cleanup grace,
  and a stream whose reader/writer thread is still blocked in a pipe call is never
  closed, because `BufferedReader.close()` would otherwise wait for a blocked `read`
  that only returns at end-of-file (unit
  `test_request_write_to_a_child_that_never_reads_stdin_is_bounded`,
  `test_surviving_child_with_a_live_reader_pipe_still_returns`; PRESERVE:
  `test_request_is_delivered_whole_and_stdin_is_still_closed`). A child that outlives the
  bounded termination attempts is therefore an explicit cleanup `Err`, never an
  unbounded caller wait. Every supervision step is bounded this way — spawn, request
  write, both pipe reads, the deadline check, kill/reap, joins, and close — so the
  supervisor never waits on the owned child outside the deadline and the cleanup grace.
- **Deadline supervision (qualified at fixture scope).** The 120-second default is pinned
  as the constant `documentation_limits.DEADLINE_SECONDS = 120.0`. The supervisor kills
  and reaps exactly its own child within the deadline plus the cleanup grace, with no
  partial evidence and the parent's own session preserved
  (`test_deadline_after_open_kills_reaps_and_preserves_parent_session`,
  `test_deadline_during_source_open_setup_kills_and_reaps_owned_child` — the deadline
  `-k` group observed green by the T4b worker L19 and independently by the verifier L20,
  and re-observed green by the parent spot check). The real production child is
  exercised end-to-end through the public CLI and MCP seams
  (`test_real_worker_success_through_cli_and_mcp`). The qualify gate was green at fixture
  scope (L20: **1866 passed / 3 skipped**). This qualifies supervisor-internal IPC only;
  MCP pre-decode transport protection remains explicitly out of scope (S7).

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
| Bounded input/output | Evidence/report JSON and Markdown at or below the §6.3 caps succeed; input above a cap is a visible `Err`/`isError` with no truncation, no destination/temporary write, and unchanged source bytes. |

### 7.2 Format/runtime qualification matrix

Support is declared per format/runtime, never inferred from suffix. Every cell begins **NOT QUALIFIED** and changes only after controlled evidence and explicit implementation approval.

| Database format | Runtime / engine combination | Initial status |
|---|---|---|
| Jet 3 (`.mdb`) | DAO / Access runtime combination to be specified and tested | **NOT QUALIFIED** |
| Jet 4 (`.mdb`) | DAO / Access runtime combination to be specified and tested | **NOT QUALIFIED** |
| ACE (`.accdb`) | DAO backend; Northwind 8-table closed fixture (2026-10-08) and the synthetic fixture suite (`core`/`sentinel`/`linked`/`startup`), Windows/ACE | **PARTIALLY QUALIFIED (fixture scope).** Covered, each with its scope and evidence: schema/relationships/saved queries (Northwind 8-table closed fixture, ledger T11 of `odd/tasks/documentation-workflows.md`; synthetic fixture `test_inspect_core_covers_exactly_manifest_tables_and_queries`); index-derived composite PKs and the genuine unique index (`test_inspect_core_primary_keys_and_unique_index`); field-default honesty — DAO `Field.DefaultValue` as `observed` stored expressions, defaultless fields `observed` empty, a `None` producer mapped to `unavailable` at the seam (`test_inspect_core_field_defaults_are_read_from_dao`; unit `test_field_default_value_is_read_from_dao_and_nul_stripped`, `test_field_default_none_maps_to_unavailable_while_real_defaults_stay_observed`); query categories including update/delete (32/48/64 → `action`, 80/96 → `ddl`; unit `test_maps_dao_update_type_48_to_action_category`); saved-query parameter metadata from DAO `QueryDef.Parameters` (`test_inspect_core_query_parameters_are_read_from_dao`: `[pCustomerID]` `observed` type `4`/`Long Integer`, parameterless `observed` empty, a non-exposing port `unavailable`); linked-table local catalogue metadata reaching evidence and rendering as `- Linked table: <name> → <file>/<source-table>` (`test_finding_inspect_linked_local_link_metadata_absent_from_evidence`, now passing); save gating (`test_save_gating_contract_on_report_copies`); engine-level read-only inspection open (`test_inspect_open_is_engine_read_only_and_refuses_write`: the read-only handle refuses `UPDATE` with `Operation must use an updateable query.`); engine-level read-only inspection of a password-protected ACE source (`test_encrypted_inspection_connections_refuse_writes_at_runtime`: the reopened DAO handle refuses `5003073`, with source bytes unchanged; encrypted-reopen subsection); linked-source isolation under an exclusive lock probe (`test_inspect_linked_succeeds_while_sentinel_is_exclusively_locked`); filtered inspection (`test_inspect_filtered_tables_returns_exactly_the_requested_subset`, `test_inspect_filtered_queries_returns_exactly_the_requested_query`, `test_inspect_combined_table_and_query_filters_scope_both_axes`, `test_inspect_unknown_requested_name_is_a_hard_error`); startup-code non-execution over the extended executable-vectors fixture (`test_startup_code_is_never_executed_by_the_pipeline[dao]`; name-observability `test_startup_fixture_is_observable_and_never_executed`; plus the doubly gated control run `test_control_run_executes_marker_under_isolation` that proves the fixture genuinely executes under isolation); non-execution invariants after inspect → render → save (`test_non_execution_invariants_after_inspect_render_save`); deterministic rendering and Mermaid cardinality rules (`test_render_database_is_deterministic`, `test_render_mermaid_relationship_cardinality_matches_documented_rules`); and bounded resources — the evidence/Markdown/report byte caps plus the 120-second whole-inspection deadline, qualified at fixture scope (§6.3, with its named deadline, runtime-guard, and real-worker tests). The cell stays PARTIALLY QUALIFIED because one claim inside the ACE/DAO scope remains unproven. **NOT QUALIFIED (DAO scope):** **VBA/macro execution beyond the `startup.accdb` fixture** (only the single control run executes code). Table data-macro non-execution is now qualified at fixture scope: the self-built armed `tblTripwire` fixture is inspected by the full pipeline while `tblTripwireLog` stays empty and the source bytes stay unchanged (table-data-macro subsection; `test_documentation_pipeline_inspects_tripwire_fixture_without_firing_data_macros`). The former **ODBC-attached links at runtime** gap is now runtime-qualified for one source only: a genuine SQLite-backed `dbAttachedODBC` DAO link inspected while its source is exclusively locked (SQLite ODBC link subsection; `test_locked_dao_inspection_needs_no_source_open_and_reports_linked_gaps`). `SchemaInspector.get_table_link_metadata` recognizes both attached bits (`0x40000000` ACE/Jet and `0x20000000` `dbAttachedODBC`) and `get_passive_tables` lists either as linked without reading its fields (unit `test_odbc_attached_table_is_recognized_as_linked`, `test_odbc_attached_table_is_also_enumerated_passively`). Cosmetic gaps intentionally left open, not qualification claims: nullable-FK cardinality rendering, the pywin32 `IUnknown`/`0x80010108` teardown stderr banner, and the `source.path`/`source.format` placeholders. |
| ACE (`.accdb`) | ODBC backend (`Microsoft Access Driver (*.mdb, *.accdb)`), synthetic-fixture pipeline suite (`test_inspect_core_via_odbc_exposes_odbc_metadata_with_honest_unavailable_gaps`), Windows, 2026-10-08 | **PARTIALLY QUALIFIED (inspection only)**: `documentation inspect --backend odbc` reads the same built `core.accdb` fixture copy through the Access ODBC driver, produces evidence, and that evidence renders offline (database report). ODBC-visible facts are `observed` with the driver's own values (never the DAO equivalent): eight user tables including `Sales Notes`; SQLColumns type names/sizes (`INTEGER` 10, `VARCHAR`, `CURRENCY` — not DAO `Long Integer`/`Currency`); SQLStatistics index names, columns, and uniqueness including composite `PK_OrderLines` order; three row-returning saved queries as `VIEW` rows. ODBC-invisible properties are `unavailable` with a per-capability reason and are never fabricated: `field_required`/`field_allow_zero_length` (SQLColumns reports `NULLABLE` for every column and has no `AllowZeroLength` equivalent), `primary_key` (`SQLPrimaryKeys` unsupported IM001 and SQLStatistics does not flag the primary index, so reported indexes carry `is_primary=false` and every table records a `coverage.tables.unsupported` reason), `relationships` (`SQLForeignKeys` unsupported IM001; `MSysRelationships` unreadable), `query_sql`/`query_type` (only `VIEW` rows are visible), and a linked table's `Connect`/`SourceTableName` (ODBC reports `SYNONYM` without the target). Declared query parameters are `observed` through `SQLProcedureColumns` (`[pCustomerID]`, ODBC SQL data type `4` = `INTEGER`; parameterless queries report a driver-known empty, distinct from `unavailable`). Action queries (`qryUpdateOrderStatus`, `qryDeleteDraftOrders`) are not `VIEW` rows and are invisible to ODBC, so the ODBC query catalogue covers the three row-returning queries only. The test asserts the source copy's sha256 is unchanged before and after and that no `core.accdb.*` lock file remains; the ODBC open is a separate engine-level read-only connection (see below), and T4's engine-level read-only qualification is now per-backend rather than DAO-scoped. ODBC classifies every listed table by its `SQLTables` type before any column or index read: a `SYNONYM` (linked table) is never passed to `SQLColumns` or `SQLStatistics`, so the linked source is **not contacted** over ODBC either. The linked table is reported as an explicit coverage `unsupported` gap with empty fields/indexes and an `unavailable` primary key, proved by `test_inspect_linked_via_odbc_does_not_contact_the_linked_source` (which holds the fixture sentinel exclusively and asserts the linked table is neither failed nor observed-empty); the units `test_list_tables_does_not_read_columns_for_a_linked_synonym` and `test_linked_table_indexes_are_refused_without_a_statistics_read` additionally assert no catalog call ever targets it, and a table type that cannot be classified is refused the same way (fail closed; `test_unclassified_table_type_is_not_read_and_fails_closed`). The ODBC open is a separate engine-level read-only connection (`OdbcAdapter.open_readonly_inspection_connection` → `pyodbc.connect(..., readonly=True)`, qualified on ACEODBC `16.0.20326.20022` x64): a narrow UPDATE is refused with driver error `-3035` "Operation must use an updateable query.", the adapter's own CRUD connection is untouched and still works, and a controlled writable copy proves the ordinary CRUD path still writes (`test_odbc_inspection_open_is_engine_read_only_and_refuses_write`). Startup-code non-execution is qualified for this backend by `test_startup_code_is_never_executed_by_the_pipeline[odbc]`: the full inspect → render → save cycle over `startup.accdb` through the Access ODBC driver exits 0, carries `StartupNotes` in evidence, and leaves the source hash unchanged with no lock residue, no new MSACCESS.EXE process, and no `*marker*` file. Filtered inspection is also qualified for ODBC (see the filtered-inspection subsection): an explicit request-order `--tables` subset over ODBC selects exactly the requested tables with the untouched view-only query scope, `--queries` selects exactly the requested row-returning `VIEW` query with the table scope untouched, both axes combine, and an unknown name is the same hard exit-1 `Err` as DAO (`test_inspect_filtered_tables_via_odbc_returns_exactly_the_requested_subset`, `test_inspect_filtered_queries_via_odbc_returns_exactly_the_requested_view`, `test_inspect_combined_table_and_query_filters_via_odbc_scope_both_axes`, `test_inspect_unknown_requested_name_via_odbc_is_a_hard_error`). Selecting only `LocalNotes` in `linked.accdb` while the sentinel is exclusively locked never contacts the filtered-out linked source (`test_inspect_filtered_out_linked_source_is_not_contacted_via_odbc`). The explicit empty-list selection, which the repeatable CLI flags cannot express, is exercised over a real ODBC port through the public application inspect interface and the public MCP tool with a transport-parity assertion against the CLI ODBC run (`test_inspect_empty_selection_via_odbc_is_select_none_and_transport_parity`): `[]` selects no table/query (empty `run.scope` and `coverage`, with the relationship `unavailable` gap keeping the document `partial`) while `None` selects every eligible object. A genuine SQLite-backed link is recognised as a `SYNONYM` at runtime and refused a column/index read while the source is exclusively locked (`test_locked_odbc_inspection_recognizes_synonym_gap_without_source_open`, `test_offline_render_of_odbc_synonym_exposes_no_link_target`; SQLite ODBC link subsection). The read-only reopen is also runtime-proven on a password-protected `.accdb`: the read-only inspection connection refuses the probe insert with `-3035`/`HY000`, an unprotected ODBC open is refused with `-1905`/`42000`, and each backend lists `local_items` with `auto` resolving to DAO (encrypted-reopen subsection; `test_encrypted_inspection_connections_refuse_writes_at_runtime`, `test_encrypted_inspection_succeeds_and_lists_the_local_table`). NOT COVERED: VBA/macro execution beyond the startup fixture. Table data-macro non-execution is qualified for this backend too (table-data-macro subsection; `test_documentation_pipeline_inspects_tripwire_fixture_without_firing_data_macros`). |

#### ODBC capability table (Access ODBC driver, `core.accdb` fixture)

Driver scope for the read-only claim and this table: `Microsoft Access Driver (*.mdb, *.accdb)`, ACEODBC `16.0.20326.20022` (x64), Windows. `pyodbc.connect(..., readonly=True)` sets `SQL_ATTR_ACCESS_MODE` to `SQL_MODE_READ_ONLY` at the engine level; the driver refuses writes with `-3035` and the behavior is not claimed for any other driver version or bitness.

Probed before implementation; the pipeline test asserts every row. ODBC-visible metadata is reported as-is, and every invisible property is `unavailable` with a reason.

| Metadata | ODBC call | Result |
|---|---|---|
| User tables | `SQLTables(TABLE)` | Exposed: eight tables including `Sales Notes`; `SYSTEM TABLE` rows excluded by the inspection filter. |
| Linked tables | `SQLTables(SYNONYM)` | Name and link-ness only (`SYNONYM`); `Connect`/`SourceTableName` are not exposed (proved on `linked.accdb`). The `SYNONYM` is classified before any read, so `SQLColumns`/`SQLStatistics` are never called for it and the source is not contacted. |
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

#### SQLite ODBC link qualification (ACE/DAO link and Access ODBC `SYNONYM`)

This closes the "genuine `dbAttachedODBC` DAO link at runtime" gap for **one** source: a
disposable SQLite database reached through the x64 `SQLite3 ODBC Driver`. It is not a
claim of general ODBC-source support; another driver/source combination stays
unqualified until it has its own controlled evidence.

Fixture and operator controls (helper `tests/helpers/sqlite_odbc_link_worker.py`; suite
`tests/integration/test_documentation_sqlite_odbc_link.py`): the helper builds a confined
`db/fixtures/sqlite-odbc-<uuid>/` root holding `remote.sqlite` (`remote_items`, exactly
two rows `(1, "alpha")`, `(2, "beta")`) and a plain ACE `linked.accdb` with a local
control `local_items` table and a genuine DSN-less ODBC link `remote_items_link`
(`SourceTableName = remote_items`, `Attributes` `0x20020000` = `dbAttachedODBC`
`0x20000000` | `dbAttachSavePWD` `0x00020000`). The SQLite link fixture itself needs
no password-protected Access file (the separate stored-credential qualification below
builds one), no `Access.Application` is launched, and the tests create no DSN and no
system or machine-wide configuration; the driver must already be installed
(prerequisites below).

The positive controls are validated before source isolation is trusted
(`test_deliberate_contact_proves_a_genuine_odbc_link`,
`test_exclusive_lock_refuses_linked_reads_then_release_restores_them`,
`test_runtime_catalog_separates_local_table_from_linked_synonym`), and every helper and
public-CLI step runs under the validated dialog watcher `tests/helpers/dialog_watcher.py`
(its own rules are unit-proven in `tests/unit/test_dialog_watcher.py`):

| Control | Observed qualification evidence |
|---|---|
| Genuine link | stored `Attributes` carry `dbAttachedODBC` and `dbAttachSavePWD`; a deliberate read through the link returns exactly `[[1, "alpha"], [2, "beta"]]`, matching a direct driver read; `local_items` holds one row |
| Watcher and tripwire validity | the deliberate *locked* linked read makes the driver raise its own `SQLite3 ODBC Driver Connect` window (class `#32770`) owned by the helper's process tree; the watcher observes exactly that one dialog, closes it with `WM_CLOSE`, and the read is then refused (DAO `5003151`), with the linked read returning both rows again after release. A missing dialog fails the control loudly, so the watcher's detection ability can never pass vacuously; the watcher also has a real in-process `MessageBoxW` positive control and a forged recycled-pid rejection case |
| Runtime classification | raw Access ODBC `SQLTables` reports `local_items` `TABLE` and `remote_items_link` `SYNONYM`; this fixture carries no unclassified user table type, so the fail-closed unknown-type path stays unit-only (`test_unclassified_table_type_is_not_read_and_fails_closed`) |

With the source lock held, every public-CLI run exits 0 **and raises zero dialogs from its
own process tree**: DAO inspect, ODBC inspect, and a `--tables local_items` DAO subset,
plus offline renders of the DAO evidence (twice, to assert determinism) and of the ODBC
evidence. The watcher auto-closes any dialog it does see and fails that run with its
title/class/owner recorded, and a step that hangs is bounded and terminated. The
qualifying runs therefore performed no source-file open and made no SQLite-driver connect
attempt through ACE. The per-step census asserted by
`test_dialog_census_per_step_and_zero_for_every_locked_cli_run` is helper build `0`,
`prove-contact` `0`, `prove-isolation` `1` (the expected driver dialog), `catalog-types`
`0`, and `0` for each of the DAO/ODBC/filtered inspections and the three renders. The CWD
of every launched subprocess is pinned inside the fixture root, so a relative `Database=`
path cannot resolve against the repository root and leave a stray source file.

- DAO (`test_locked_dao_inspection_needs_no_source_open_and_reports_linked_gaps`): the
  link is evidence `kind: "linked"` with empty fields/indexes, an `unavailable` primary
  key whose reason is the shipped linked-table gap, `source_table_name = remote_items`,
  and `coverage.tables.unsupported`; it is never `failed`. `local_items` keeps its
  observed `["id", "note"]` fields, `observed` primary key `["id"]`, and
  `PK_local_items`.
- ODBC (`test_locked_odbc_inspection_recognizes_synonym_gap_without_source_open`): the
  `SYNONYM` is evidence `kind: "linked"` with empty fields/indexes, `connect` and
  `source_table_name` `None` (the driver exposes no target), and the same explicit
  `unavailable` primary-key gap; `local_items` keeps its `["id", "note"]` columns with
  `observed` types and `unavailable` `required` facts.
- Filtered (`test_filtered_dao_inspection_selects_only_the_local_control`): `--tables
  local_items` selects exactly that table, the linked source is absent, and the link-free
  subset is `complete` with an empty `coverage.tables.unsupported`.

Credential behavior on the absolute-path build: the link is created with a fake
`PWD=canary-7f3a` credential and `dbAttachSavePWD`, and the stored `Connect` carries
**no `PWD` segment at all** (`pwd_segment_present` is `False`). The observed root cause
is *not* credential filtering by ACE: the driver's completed connection string echoes the
credential, but ACE persists only the **first 254 characters** of it, and with a
225-character absolute `Database=` value the `pwd` key sits past that cut (a longer path
truncates the `Database` value mid-value, proving truncation). What
`test_stored_link_credential_is_dropped_and_streams_stay_clean` asserts here is
therefore only the observable outcome: the canary and any `PWD=` segment are absent from
the sanitized emitted `Connect`, from every captured stdout/stderr stream, and from the
rendered Markdown. A *positive* runtime redaction of a **stored** credential needs a
link whose stored metadata really carries one; that is qualified separately in the
stored-credential subsection below, and credential sanitization also stays unit-proven
at the connection-string seam (§5.3). Offline rendering keeps the linked gap visible
(`test_offline_render_keeps_linked_gaps_without_credentials`: `partial`, exactly one
`coverage.tables.unsupported`, `Linked table: remote_items_link` and the gap reason in
Markdown, byte-identical repeated renders), and the ODBC `SYNONYM` renders "unknown
source" with no target and no credential
(`test_offline_render_of_odbc_synonym_exposes_no_link_target`).

**No-contact proof limits (do not overstate).** The exclusive `share=0` lock plus the
zero-dialog observation proves the qualifying inspection performed no **source-file
open** and that **no SQLite-driver connect attempt surfaced through ACE** while it ran.
It is still not proof against every contact form: a live attempt on an unlocked source
raises no dialog, and a swallowed failed attempt or another contact form would look
identical, so this is *not* exhaustive "no attempted contact" proof. The claim is paired
with the shipped classification-before-read unit assertions
(`test_list_tables_does_not_read_columns_for_a_linked_synonym`,
`test_linked_table_indexes_are_refused_without_a_statistics_read`), and source absence or
non-recreation is deliberately **not** used as an isolation oracle. The watcher observes
only the process tree each step launches (creation-time-guarded descendants) and ignores
and never closes any foreign window, so an unrelated process's dialog is out of scope by
construction. Prerequisites: Windows,
ACE DAO (`DAO.DBEngine.120`) via pywin32, and the x64 `SQLite3 ODBC Driver` registered
with the ODBC installer; missing Windows/ACE/driver or an unset
`ACCESS_DOCUMENTATION_FIXTURES_TEST` skips the module with a reason. The helper records the
per-build `remote.sqlite` and `linked.accdb` SHA-256 hashes in `inventory.json`, and the
locked runs assert both files are byte-identical before and after. Observed runtime here
is ACE 16 with SQLite ODBC DBMS `3.43.2`, x64 Windows. Generated fixture roots (SQLite
database, ACE database, `inventory.json`) are **retained** at `db/fixtures/sqlite-odbc-*`
as evidence; the suite never cleans up, and the helper's guarded `cleanup` command is
separately authorized. Exact opt-in run:

```powershell
$env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
.venv/Scripts/python.exe -m pytest tests/integration/test_documentation_sqlite_odbc_link.py -q
```

The run is unattended: it never waits for a human to dismiss a driver dialog. The suite
itself detects every dialog owned by a step's process tree, closes it automatically, and
fails that step with the dialog's title/class/owner if it was not the one expected
deliberate-locked-read control.

#### Stored-credential runtime redaction qualification (S4a/T5)

This closes the stored-credential runtime redaction sub-gap for two fixture kinds with a
**non-vacuous** stored credential. Helper `tests/helpers/protected_link_worker.py`; suite
`tests/integration/test_documentation_stored_credential_link.py`, opt-in via
`ACCESS_DOCUMENTATION_FIXTURES_TEST=1`. Windows and ACE DAO are required; the ODBC kind
also requires the x64 `SQLite3 ODBC Driver`; every missing prerequisite skips with a
reason. Nothing here changes production code.

Two fixture kinds carry the fake `canary-7f3a` credential in a linked `TableDef.Connect`
observed **in a new process before any pipeline run**:

- **(b) Access link (`dbAttachedTable`, `0x40000000`).** A disposable backend is created
  with the credential in the DAO `CreateDatabase` locale
  (`;LANGID=0x0409;CP=1252;COUNTRY=0;pwd=canary-7f3a`, `dbVersion120`) and one
  `secret_items` row; the front-end stores `;PWD=canary-7f3a;DATABASE=<backend absolute
  path>` on a `link_items` link. `test_protected_access_link_stores_the_credential_in_a_new_process`
  is the positive control: the stored `Connect` contains the canary
  (`stored_connect_has_canary`), an unprotected DAO open of the backend is refused (DAO
  `5003031`), and a read through the link returns the protected row.
- **(a) SQLite ODBC link with a relative `Database=`.** `tests/helpers/sqlite_odbc_link_worker.py`
  is built with `--database-style relative`, so the driver's completed string fits under
  ACE's 254-character cut and the stored `Connect` retains `PWD` verbatim
  (`connect_length` 223) instead of being truncated away (root cause above).
  `test_sqlite_relative_link_stores_the_credential_with_pinned_cwd` is the positive
  control: `stored_connect_contains_canary` and `stored_connect_has_pwd_segment` are
  true and a linked read returns both rows with CWD pinned to the fixture root (the
  relative value is resolved against the process CWD).

With the *source* file held under an exclusive `share=0` lock (the protected backend for
kind (b), `remote.sqlite` for kind (a)), the suite runs the public CLI `documentation
inspect --backend dao` and `--backend odbc`, then `documentation render` on each emitted
evidence. Every subprocess runs under `tests/helpers/dialog_watcher.py` with a bounded
timeout and `PYTHONFAULTHANDLER` stripped, and each run must exit 0 and observe **zero**
dialogs:

- `test_protected_access_link_credential_never_reaches_documentation_output` and
  `test_sqlite_relative_link_credential_never_reaches_documentation_output`: the canary
  and any `PWD=` segment are absent from the evidence JSON, stdout, stderr, and the
  rendered Markdown. The live link stays `kind: "linked"` with empty fields/indexes and
  the explicit `unavailable` gap; the local control keeps its `["id", "note"]` fields
  (and, for DAO, its `observed` `["id"]` primary key). Non-credential connect metadata
  the backend exposes is preserved: the Access link emits `MS Access;DATABASE=<backend>`,
  and the ODBC link emits `ODBC;Driver={SQLite3 ODBC Driver};Database=remote.sqlite;...`
  with the credential pair removed. Access ODBC reports either link as a `SYNONYM` with
  `connect`/`source_table_name` `None`. Source/backend bytes are unchanged.
- `test_protected_steps_are_dialog_free` and `test_sqlite_relative_steps_are_dialog_free`:
  the per-step dialog census is `0` everywhere, so the suite is unattended.
- `test_protected_backend_still_refuses_an_unprotected_open_after_the_runs`: after the
  pipeline, the protected backend still refuses an unprotected DAO open (DAO `5003031`)
  and the stored credential is still present, so the redaction claim cannot be vacuous.

Exact opt-in run:

```powershell
$env:ACCESS_DOCUMENTATION_FIXTURES_TEST = '1'
.venv/Scripts/python.exe -m pytest tests/integration/test_documentation_stored_credential_link.py -q
```

#### Encrypted (password-protected) read-only reopen qualification (ACE/DAO and ACE/ODBC)

This closes the previously **unit-only** "encrypted (password-protected) ODBC read-only
reopen" gap with a genuine protected source. Helper
`tests/helpers/encrypted_db_worker.py`; suite
`tests/integration/test_documentation_encrypted_reopen.py`, opt-in via
`ACCESS_DOCUMENTATION_FIXTURES_TEST=1`. Windows with ACE DAO and the Access ODBC driver
are required, and every missing prerequisite skips the module with a reason. Nothing here
changes production code.

The fixture is a disposable, repository-confined root `db/fixtures/encrypted-db-<uuid>/`
holding an `encrypted.accdb` created with a real fake password in the DAO `CreateDatabase`
locale (`;LANGID=0x0409;CP=1252;COUNTRY=0;pwd=canary-e9c4`, `dbVersion120`) and one local
`local_items` row, its per-build SHA-256 in `inventory.json`, and the fixture-local
credential store `.ms-access-mcp.json`. The password is supplied **only** through the
shipped credential store: `documentation inspect` has no password flag and interactive
`credentials set` uses console `getpass`, so the suite writes the entry with the shipped
writer (`credentials.write_entry(..., local=True)`) and every subprocess resolves it with
the shipped resolver (`credentials.resolve_password`) — never on argv, in the environment,
or in a result.

Positive controls are observed in a new process before any pipeline run: the stored
password resolves, an unprotected DAO open is refused (ACE scode `5003031`), and a
protected read returns the fixture row
(`test_encrypted_source_positive_controls_hold_in_a_new_process`). The ODBC counterpart,
an unprotected ODBC open refused with native `-1905` / SQLSTATE `42000`, is asserted by the
ODBC write probe in `test_encrypted_inspection_connections_refuse_writes_at_runtime`.
The public pipeline then succeeds on every shipped backend:
`documentation inspect --backend odbc`, `--backend dao`, and `--backend auto` each exit 0
and list `local_items` with its `[id, note]` fields, while `auto` resolves to DAO and the
Access ODBC primary key stays an honest `unavailable` with the `SQLPrimaryKeys` reason
(`test_encrypted_inspection_succeeds_and_lists_the_local_table`).

Engine-level read-only is proven at runtime on both shipped inspection seams, not only by
unit pinning (`test_encrypted_inspection_connections_refuse_writes_at_runtime`): the
read-only DAO reopen reuses the recorded target and password and its handle refuses the
probe insert with scode `5003073` (on a byte-identical disposable copy, because a
read-only DAO handle refuses `BeginTrans` itself), and an `INSERT` on the read-only ODBC
inspection connection is refused by the driver with native `-3035`/SQLSTATE `HY000`. Both
probes record an unchanged row count and unchanged database bytes, so a writable
inspection connection would fail loudly.

The fake password never reaches output: the canary and any `PWD=` segment are absent from
raw stdout and stderr of every step, the on-disk evidence JSON, the rendered Markdown, and
the fixture inventory (`test_encrypted_password_never_reaches_documentation_output`); the
fixture-local credential store is the one artifact excluded, because it legitimately holds
the fake password. A wrong stored password and no stored entry at all each fail closed
(`test_encrypted_wrong_or_missing_password_fails_closed`): exit 1, `success: false`, a
sanitized error naming the driver refusal code (`5003031` DAO / `-1905` ODBC), no partial
evidence and no credential echo. Every step runs under
`tests/helpers/dialog_watcher.py` with its CWD pinned inside the fixture root, and the
observed census is 15 watched steps with zero dialogs
(`test_encrypted_every_step_observed_zero_dialogs`); the source bytes are identical before
and after every run.

**Scope limits (do not overstate).** The claim holds for this ACE (`.accdb`) source, this
ACE/DAO build, and this Access ODBC driver build; it does not extend to other encryption
schemes such as legacy Jet `.mdb` files or workgroup security, which stay unqualified.
Generated fixture roots `db/fixtures/encrypted-db-*` are **retained** as evidence; the
suite never deletes a root, and the helper's guarded `cleanup` command removes exactly one
self-created direct child of `db/fixtures/` and is separately authorized. Exact opt-in
run:

```bash
ACCESS_DOCUMENTATION_FIXTURES_TEST=1 .venv/Scripts/python.exe -m pytest tests/integration/test_documentation_encrypted_reopen.py -q
```

#### Filtered-inspection qualification (DAO and ODBC, synthetic-fixture pipeline suite)

`documentation inspect --tables <name> --tables <name> ...` and `--queries <name> ...`
are **repeatable CLI occurrences** (`action="append"`); there is no comma-separated form,
and a comma-joined value is one whole name and is rejected as unknown. Qualified through
the public CLI in subprocesses against the synthetic `core.accdb` fixture (DAO and ODBC
backends) and against `linked.accdb`
(`tests/integration/test_documentation_pipeline_e2e.py`):

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

The same selection contract is qualified for the **ODBC backend** by
`test_inspect_filtered_tables_via_odbc_returns_exactly_the_requested_subset`,
`test_inspect_filtered_queries_via_odbc_returns_exactly_the_requested_view`,
`test_inspect_combined_table_and_query_filters_via_odbc_scope_both_axes`, and
`test_inspect_unknown_requested_name_via_odbc_is_a_hard_error`. Over ODBC the visible
query catalogue is only the three row-returning saved queries exposed as `VIEW` rows
(`qryOpenOrders`, `qryOrdersByCustomer`, `qryOrderSummary`); the action queries
`qryUpdateOrderStatus`/`qryDeleteDraftOrders` are not `VIEW` rows and are never claimed
visible. A table subset preserves request order, leaves that view-only query scope
untouched, and keeps every selected table's primary key `unavailable` with the
`SQLPrimaryKeys` reason; a query subset leaves the eight-table scope untouched; ODBC
relationships stay an explicit `coverage.relationships.unsupported` gap. A filtered-out
linked table is never classified or read: selecting only `LocalNotes` in `linked.accdb`
while `sentinel.accdb` is exclusively locked succeeds without contacting the source,
leaves the sentinel bytes unchanged, and leaves no trace of `LinkedCustomers` in the
envelope (`test_inspect_filtered_out_linked_source_is_not_contacted_via_odbc`). The
explicit empty-list selection cannot be expressed with the repeatable CLI flags (an
omitted flag means "all"), so it is exercised over a real ODBC port through the public
application inspect interface and the public MCP tool, with a transport-parity assertion
against the CLI ODBC run
(`test_inspect_empty_selection_via_odbc_is_select_none_and_transport_parity`): `[]`
selects no object (empty `run.scope` and `coverage`, relationship gap still `partial`),
which differs from `None` selecting all eight tables and three views.

Known cosmetic limitation (a follow-up item, not fixed by this documentation-only
closure): when a filter excludes every relationship, the relationship catalogue renders
the same `_No relationships observed_` line as when no relationship exists at all, so
the renderer does not distinguish "none exist" from "none selected". The coverage
`excluded` entries carry that distinction for clients that need it.


#### Startup-code non-execution qualification (DAO and ODBC)

`startup.accdb` is the executable fixture: one `StartupNotes` table plus a standard VBA
module `StartupMarkerModule` and a macro renamed to `AutoExec` that runs
`StartupMarker()` through `RunCode`. It also carries a never-auto-run named macro
`ManualMarker` and an `AutoKeys` macro group (loaded under the neutral name
`AutoKeysControl` and renamed last, like `AutoExec`). DAO `Containers("Scripts")` exposes
exactly `AutoExec`, `ManualMarker`, and `AutoKeys`, and `Containers("Modules")` exposes
`StartupMarkerModule` (`test_startup_fixture_is_observable_and_never_executed`); DAO
readback can observe these names but not their bodies. The named macro and the `AutoKeys`
group are delivered and name-observable but **never auto-run**: `AutoKeys` fires only on a
keyboard chord and `ManualMarker` only when code or a user invokes it by name, and both
are loaded under neutral control names and renamed last, after which the database closes.
A table data-macro tripwire is **not** part of this fixture: it is a separate self-built
fixture whose non-execution is qualified in the table-data-macro subsection below. The
function writes a
harmless
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
that runs `AutoExec`, `AutoKeys`, or `ManualMarker`. The generator executes nothing
either: it opens `Access.Application` only while no auto-run object exists, with
`AutomationSecurity` forced disabled, loads the module and macros from text, renames the
auto-run names last, and never reopens (`generate_documentation_fixtures.py`). Generator
safety pins cover all four provenance sources — `startup_module.bas`, `startup_macro.txt`,
`named_macro.txt`, and `autokeys_macro.txt` — for shell/registry/object-creation
primitives, network URLs, UNC paths, and traversal
(`test_startup_vba_derives_only_the_fixed_marker_without_escape_hatches`,
`test_generated_executable_and_macro_sources_have_no_escape_hatches`), and
`test_provenance_filenames_contain_no_marker_token` pins the provenance file names so the
`*marker*` detector can never be tripped by a non-execution artifact.

The property is not vacuous because the doubly gated control run
`tests/integration/test_documentation_startup_control.py` deliberately executes the
fixture: with `ACCESS_DOCUMENTATION_FIXTURES_TEST=1` and `ACCESS_STARTUP_CONTROL_TEST=1`
it copies `startup.accdb` into an isolated scratch subdirectory of the self-created
session tree, opens the copy with `AutomationSecurity` at the low (macro-enabled)
automation level, observes `startup_executed.marker` beside the copy within 30 s, quits
Access (force-killing only the captured PID if Quit hangs), and asserts the marker is
confined to the scratch directory with no surviving MSACCESS.EXE process and no
`.laccdb`. Missing either gate skips cleanly. Each backend's engine-level read-only
qualification stays scoped to the inspection open: the DAO path reopens the existing
handle read-only, and the ODBC path opens a separate read-only handle, while the
ordinary tool connections remain write-capable. The matrix is finalized in the §7.2
cells above: ACE/DAO stays PARTIALLY QUALIFIED for VBA/macro execution beyond this
fixture; ACE/ODBC stays PARTIALLY QUALIFIED for VBA/macro execution beyond the startup
fixture; table data-macro non-execution is qualified at fixture scope for both backends
(table-data-macro subsection; filtered ODBC
inspection is qualified by the filtered-inspection subsection, and the genuine
SQLite-backed `dbAttachedODBC` link plus its Access ODBC `SYNONYM` by the SQLite ODBC
link subsection).

#### Table data-macro non-execution qualification (DAO, ODBC, and auto)

A table data macro is engine-level code: it runs inside the ACE engine on a DML event,
not inside `Access.Application`. Whether the documentation pipeline can fire one is
therefore observable directly, and the `tblTripwire` fixture makes that observation
non-vacuous. The fixture is self-built on demand by `tests/helpers/datamacro_worker.py`,
not by `generate_documentation_fixtures.py`; the import format is Access's own
serialization ground truth from the T4 probe
(`db/fixtures/datamacro-probe-0327ea09-9b4a-4c47-ac2f-8027eeaa67fd`): UTF-16 LE with a
BOM, a CRLF after the `<?xml ...?>` declaration, no trailing newline, and a `<DataMacros>`
root of `<DataMacro Event="...">` elements whose `<CreateRecord>`/`<SetField>` writes
`tblTripwireLog.Event`.

The helper builds `tblTripwire` (`ID` AutoNumber primary key, `Note` TEXT(50)) and
`tblTripwireLog` (`ID` AutoNumber primary key, `Event` TEXT(50)) through DAO, inserts
exactly one seed row into `tblTripwire` *before* any macro exists (so the log genuinely
starts empty), then loads ONE three-event XML through `Application.LoadFromText`
(`acTableDataMacro` = 12) in a hidden, macro-disabled `Access.Application`
(`AutomationSecurity = 3`, `UserControl = False`, `CloseCurrentDatabase` + `Quit` in a
`finally`), and re-exports it with `SaveAsText` to require all three
`AfterInsert`/`AfterUpdate`/`AfterDelete` events with `CreateRecord`/`SetField`
(`test_tripwire_fixture_is_self_built_and_armed`).

Each event is proven live **first**, on its own byte-copy: a single DAO `fire --event X`
followed by a read-only `count` must show exactly one `tblTripwireLog` row whose value is
that event, and the original's SHA-256 must be unchanged by the controls
(`test_tripwire_positive_controls_fire_each_event_on_byte_copies`). Only then does the
full public pipeline run on the original: `documentation inspect --backend dao|odbc|auto`
each exit 0 and list both tables, every evidence file renders, and one rendered report is
saved inside the fixture root through the shipped write gate (`--allow-doc-write --yes`
with `ACCESS_MCP_ALLOW_DOC_WRITE=1`). A final read-only `count` must still show zero
`tblTripwireLog` rows and exactly the one seed row, the original SHA-256 must be identical
before and after, and every step must observe zero dialogs
(`test_documentation_pipeline_inspects_tripwire_fixture_without_firing_data_macros`). A
fired macro or a mutated file is a real production defect, not an expectation to adjust.

Building the fixture requires the full `Access.Application` (not only ACE DAO), because
`LoadFromText`/`SaveAsText` are application methods; the suite skips with a reason off
Windows, without ACE DAO, without `Access.Application`, or without the opt-in. Exact
opt-in run:

```bash
ACCESS_DOCUMENTATION_FIXTURES_TEST=1 .venv/Scripts/python.exe -m pytest tests/integration/test_documentation_datamacro_tripwire.py -q
```

Scope limits: this qualifies non-execution of the three armed table events
(`AfterInsert`, `AfterUpdate`, `AfterDelete`) on this ACE/Access build only. Named data
macros, the `BeforeChange`/`BeforeDelete` events, and data-macro *reporting* in evidence
are **not** claimed; the fixture roots (`db/fixtures/datamacro-*`) are retained as
evidence and are never deleted by the suite.

#### Unreachable-by-class execution vectors (documented, no fixture)

Three execution mechanisms are **not** fixtured and are not claims: ribbon callbacks,
startup-form `OnLoad`, and form/report event VBA. They are **instantiation/UI-level**
code: each runs only when the Access application instantiates the hosting object and
dispatches the UI event, i.e. only inside `Access.Application`. Neither inspection backend
dispatches them — the DAO path opens `DAO.DBEngine.120` and the ODBC path opens the Access
ODBC driver, and neither launches `Access.Application` (same by-construction argument as
`AutoExec`). This is documented by-class rather than fixtured because a form/report event
fixture would need undocumented, version-specific form export text or VBProject-trust
programmatic creation — build risk disproportionate to a path the inspection code cannot
reach. The realistic future regression to guard is a documentation-path change that calls
the **generic** `get_object_metadata`/`get_database_statistics` tools, which do touch
`access_app.CurrentProject` (`backends/schema_inspector.py`; `application/database.py`)
and are generic-tool-only today; a documentation inspect that started using them would
reintroduce the only inventoried route to instantiation-level code.

#### Remaining unqualified and explicitly open (program closure)

- **Legacy Jet 3 / Jet 4 `.mdb`.** Excluded by program scope (S2); existing legacy
  rejection/conversion behavior is unchanged and no `.mdb` work was performed.
- **ODBC-attached links other than the qualified SQLite case.** A genuine SQLite-backed
  `dbAttachedODBC` DAO link and its Access ODBC `SYNONYM` are qualified at fixture scope
  (SQLite ODBC link subsection; ledger L5–L6 of
  `odd/tasks/sqlite-odbc-link-qualification.md`). No other ODBC driver or non-Access
  source is claimed: each source/driver combination needs its own controlled evidence
  (§7.2 initial-status rule), and the fail-closed unknown-table-type path stays unit-only.
- **Opt-in documentation suites run only on an Access runner.** The documentation
  fixtures, deadline, startup-control, tripwire, encrypted-reopen, SQLite-ODBC-link, and
  stored-credential-link suites are marked `com_integration` and gated by
  `ACCESS_DOCUMENTATION_FIXTURES_TEST`. `ci.yml`'s `[self-hosted, windows, access]` job sets
  that variable and runs `-m "com_integration or odbc_integration"`, so those suites are
  exercised there; the `windows-latest` unit job and any runner without Access still skip
  them. The claim above that CI sets no `ACCESS_*` variables was wrong and is corrected
  here: the open item is only that the suites cannot run on a plain hosted runner, so they
  must also be run explicitly on a Windows/Access host before publishing documentation
  evidence.
- **MCP pre-decode transport bounds.** The §6.3 caps are application-level and do not
  bound the JSON-RPC payload before decoding; server-global transport protection remains
  out of scope (S7).

Qualification records engine version/bitness, host requirements, passive/read-only evidence, QueryDefs SQL availability, relationship/index fidelity, fixture results, and failure behavior. No qualification means unsupported/unknown coverage, never implied support.

## 8. v2 application reverse engineering (unit-implemented; runtime gates at T9)

> **Status:** every item in §8.1 is implemented and covered by unit tests
> (`application/documentation_app_logic.py`, `application/documentation_render.py`,
> `application/documentation_worker.py`, `backends/access/app_logic_session.py`).
> **RUNTIME NOT QUALIFIED until T9.** No live `Access.Application` app-logic read is
> claimed here; the T3–T5 live gates qualified containment and content reads at fixture
> scope only, and §8.3 records exactly what is and is not qualified. Absence of a claim
> is never implied support.

### 8.1 Implemented at unit level

- **App-logic inventory + content reader.** `documentation_inspect` accepts the
  default-off opt-ins `include_app_logic` and `include_app_logic_source` (CLI
  `--include-app-logic`, `--include-app-logic-source`; MCP arguments of the same names).
  Tier 1 reads the DAO `Containers` (Forms, Reports, Scripts, Modules) and the allowlisted
  startup database properties; Tier 2 (`documentation_app_logic.AppLogicReader`) reads VBA
  modules, `SaveAsText` form/report/macro exports, and `Application.References` from one
  disposable Access instance. The new fact categories are additive to
  `access.documentation.evidence/1`, and a default-off call keeps the v1 evidence bytes
  (S4).
- **Containment design.** Both T3 layers are registered. The worker assigns the owned
  Access PID to a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so supervisor
  death reaps Access through the handle close; the supervisor's T2 PID+creation-scoped
  kill net covers the deadline and crash paths. Kills are PID-scoped (`/IM` is never
  used), and a failed Job Object assignment degrades to the kill net with a visible
  warning rather than a partial `Ok` (contract 6).
- **Flag contract.** Raw source is opt-in and never default: `include_app_logic_source`
  without `include_app_logic` is an `Err` (precedence is checked before any read). Raw
  code is never rendered inline in Markdown — reports reference it by evidence ID and
  hash — and opt-in source still counts against the existing 32 MiB evidence / 16 MiB
  Markdown caps (above the cap stays an `Err`, never truncation).
- **Scratch copy.** One unique disposable directory per inspection, under the
  `ACCESS_MCP_DOC_SCRATCH_DIR` override when set (otherwise the system temp directory).
  The original is never opened by `Access.Application`. Removal in the `finally` is
  best-effort: a removal that fails, or that reports success while the directory is still
  there, never raises from teardown and never hides the inspection's own result. If even
  the check for a leftover directory raises, the directory is treated as still present.
  Instead of raising, the run carries one sanitized warning (error class plus
  class-only reason) on `AppLogicContent.warnings`, which the merge adds to the document
  warnings. A successful removal adds no warning, so a successful run's evidence and
  Markdown stay byte-identical.
- **Diagnostic reasons are class-only, never runtime exception text.** Documentation
  evidence, warnings, and the worker's error envelope are shareable, so the four
  diagnostic boundaries that render an exception reason go through one shared formatter
  (`documentation.safe_documentation_error_reason`): the Tier 2 app-logic reader's reason
  (including the scratch-residue warning, whose class prefix is not duplicated), the
  Tier 1/Tier 2 inspection reason (which keeps its authored `None` fallback text), the
  ODBC read-only inspection-open wrapper, and the worker's catch-all error envelope. The
  formatter keeps only the exception class name. An `OSError` adds its numeric code —
  `winerror` when it is a real integer, otherwise `errno` — for example
  `PermissionError: [WinError 32]` or `PermissionError: [Errno 13]`. It never renders
  `str(error)`, `args`, `strerror`, `filename`, or `filename2`, because each of those can
  carry a user profile path or a credential; a code that is not a plain integer (a
  `bool`, a string, or another object) is ignored instead of formatted as code. Authored
  text that is not exception text is unaffected: fixed stage prefixes, the
  capability-limit and trust-prerequisite constants, standalone limitation reasons, and
  the published `unavailable`/`failed`/`observed` states keep their meaning. This is a
  rule for exception reasons at those boundaries, not universal sanitization of every
  evidence field, and it deliberately loses the raw exception detail: an operator
  diagnosing a failure reads the class and numeric code, not a driver message.
- **Authored detail is the one exception, and it is literal-only.** A class-only reason
  alone could not say *which* check failed, so a project-defined exception that subclasses
  `documentation.AuthoredDiagnostic` carries an authored, compile-time literal detail that
  renders as `<Class>: <detail>`. Three adoption sites use it: `ContainmentError` (the six
  containment raises, so the user-facing worker error names the failed check),
  `InspectionCapabilityUnavailableError` (so an ODBC capability fact reads
  `InspectionCapabilityUnavailableError: Access ODBC SQLForeignKeys is unsupported (IM001) …`
  instead of only a class name), and `WorkerRequestError` (child-side request validation,
  so a rejected field is named). The rule is enforced twice: `detail` is typed
  `LiteralString`, so pyright (which gates `src`) rejects an f-string or any other runtime
  value at the raise site, and the formatter additionally requires `type(detail) is str`
  and a non-empty value, so a `str` subclass with a hostile `__format__`/`__str__`/`__repr__`
  or an empty string falls back to the class-only reason. A chained `__cause__` is appended
  as `(cause: <class-only reason>)` — one level only, never the cause's own authored
  detail, and never an implicit `__context__`. Runtime text still never passes: a
  message that needs a runtime value (an observed `AutomationSecurity` readback, a
  container name, the IPC byte budget, a backend error, a database path) stays a plain
  `AccessMcpError` message and renders class-only.
- **Failure visibility uses the existing channels only.** The four formerly silent
  failure sites surface as sanitized warnings on the existing `EvidenceDoc.warnings` /
  `AppLogicContent.warnings` lists, or as error-message enrichment. No logging framework,
  schema change, or derived-status change is involved: `EvidenceDoc.status` keeps
  reflecting facts and coverage only, so a warning never turns a `complete` document
  `partial`.
  - *Baseline listing.* A failed `MSACCESS.EXE` baseline listing is distinguishable from a
    genuinely empty baseline and reaches the successful path as one `EvidenceDoc.warnings`
    entry. The empty-baseline decision, its kill-denying semantics, and the public
    behaviour of `list_msaccess_pids` are unchanged.
  - *Termination.* When cleanup cannot be established, the raised error message carries
    the per-PID termination detail plus any swallowed kill-attempt error class; the kill,
    retry, grace, and established/not-established decisions are unchanged. Termination
    details name an image by its executable file name only, never its directory.
  - *DAO properties.* Unreadable database properties are still omitted and readable
    siblings are still recorded as their own observed startup-property facts, but the
    omission is no longer silent: the successful path carries one warning stating how
    many properties were skipped and naming only those whose *name* was itself readable
    (an unreadable name is counted, never guessed). A fully readable catalogue adds no
    warning.
  - *Scratch residue.* A failed scratch-directory removal is reported as described in the
    scratch-copy bullet above.
- **Tier 2 degradation is fact-level, never silent — and only for operational failures.**
  Tier 2 is optional, so an *operational* failure of that whole stage — a filesystem/OS
  error, or a COM-shaped error carrying a failing HRESULT, at the scratch directory, the
  disposable copy, the owned dispatch, the armed open, or a whole content read — returns
  one `failed` fact per content family (form, report, macro, vba_module) plus one `failed`
  reference family fact per reference property, with the sanitized reason and one warning.
  The already collected v1 and Tier 1 facts are retained and the document's derived status
  becomes `partial`, so a failed Tier 2 family is **not** absent: absence means no such
  fact exists (no opt-in, or a filtered-out object), while `failed` names an attempt that
  errored and `unavailable` names one the runtime/prerequisite cannot provide. The
  classification is duck-typed (`hresult`, `args[0]`, or a nested COM `excepinfo` status
  normalized to unsigned; an `int`/`bool` that is not a failing HRESULT never counts), so
  the application layer still imports no pywin32/Win32. An unexpected **programmer
  defect** (`ValueError`, `TypeError`, `AttributeError`, a bare `RuntimeError`) at those
  same whole-stage boundaries is a hard error: it is never reported as successful partial
  evidence. The narrowing applies to those outer boundaries only; the documented
  per-family best-effort catches still turn one failed family into that family's own
  `failed` fact. Containment is not degradable: ForceDisable set/readback, owned-identity
  capture (including an invalid-PID read), the owned-identity announcement, and the
  identity re-read guarding the Job Object assignment raise `ContainmentError` (a hard
  `Err` at the worker boundary), and `KeyboardInterrupt`/`SystemExit` are never converted
  to partial success. No schema change is involved; the worker protocol's `ok:true` +
  `partial` shape is how a caller sees the retained evidence.
- **VBA trust is classified by status code, never by message text.** The documentation
  session reads the VBProject in the shared helper's optional `strict=True` mode, while
  every existing caller keeps the default swallow-and-DEBUG-log behaviour (an unreadable
  VBE still yields `None`, an empty project list still yields `None`). In strict mode a
  *positively recognized* trust refusal — the Microsoft-documented `0x800A17B4`/6068
  status, whether on `hresult`, on `args[0]`, or nested in a COM `excepinfo` — raises
  `VbaTrustRefusedError` and the reader reports the `vba_module` family `unavailable`
  together with the operator prerequisite (enable “Trust access to the VBA project object
  model”); no Trust Center setting is ever read or changed. Corruption, a truncated or
  unknown COM status such as `0x8000FFFF`, a plain failure, and an empty project list are
  **real failures**: they are reported `failed` and are never described as a trust
  refusal. Localized or English message text is never matched, and a successful read keeps
  its cached `VBProject` and observed facts unchanged.
- **Catalogue-only rendering.** The report gains a labeled `## Application logic` section
  (VBA modules, form/report/macro source catalogues, engine-level inventories, references,
  startup properties) plus `### Limitations` / `### Coverage notes`. Non-observed facts are
  visible limitation lines (`unavailable`/`failed`) or coverage notes (`excluded`), and the
  Markdown never contains raw source. Secret scrubbing applies at the error/reason
  boundary as in v1; embedded opt-in source stays raw untrusted text (the
  include_query_sql precedent) and is referenced by evidence ID + sha256 only.
  Agreement 9 is fully closed: every source object
  family — form/report/macro exports and VBA modules — always carries `sha256`/`bytes`
  (VBA digests are over the UTF-8 encoding of the module source, since modules have no
  `SaveAsText` file); an earlier known-gap note about missing VBA digests is superseded
  by the `a33fb5b` fix. Validation now holds that parity: observed `vba_module` evidence
  must carry a non-empty `sha256` string and an integer `bytes`, exactly like observed
  `SaveAsText` content, so older or hand-built evidence that lacks the digest no longer
  validates. Both branches enforce the recorded shape, not only its presence: `sha256`
  must be exactly 64 lowercase hexadecimal characters (the `hashlib.hexdigest` form the
  reader always emits) and `bytes` must be a non-negative integer (a negative size is
  rejected as a shape error). Because the reader always satisfies that contract, the
  renderer has no digest-gap limitation line: digest-complete content is validated before
  any render, so the removed branch was unreachable rather than untested. In strict VBA
  mode a classified raise replaces the default "vb project unavailable" DEBUG wording,
  which now belongs to the non-strict swallow path only.

### 8.2 Control-flow gate outcomes (evidence archives)

| Gate | Outcome | Evidence archive |
|---|---|---|
| T3 containment (Job Object + PID kill net) | GATE PASSED, 9/9 cells | `odd/evidence/documentation-v2-containment-20261017.zip` (sha `d5eb52eb…f81`) |
| T4 content reads (References 1-based, `SaveAsText` form/report/macro, VBA-trust read) | GATE PASSED, 9/9 cells / 13/13 checks | `odd/evidence/documentation-v2-content-reads-20261017.zip` (sha `7d4006a6…e64`) |
| T5 embedded event macros (2026-10-17) | BLOCKED-with-evidence (L14): every candidate used an invented block keyword, so Access rejected it at import | `odd/evidence/documentation-v2-embedded-blocked-20261017.zip` (sha `c37f7640…0d4`) |
| T5 embedded event macros, re-qualified (2026-10-23) | GATE PASSED, 9/9 cells: positive control (`AutomationSecurity=1`) inserted the canary row 3/3; force-disable 3/3 and the shipped `AppLogicReader` 3/3 left it empty | `odd/evidence/documentation-v2-embedded-macro-requal-20261023.zip` (sha `939dbfc6…e1e`) |

### 8.3 App-logic coverage matrix (fixture scope, measured runtime only)

All rows are fixture scope on the single measured Access 16.0 runtime on this host. When a
vector was exercised, its entry function was **suppressed at fixture scope on the qualified
runtime**; this section does not claim that arbitrary databases, hostile inputs, or other
runtimes are safe.

| Vector | Status |
|---|---|
| AutoExec `RunCode` → VBA | QUALIFIED (fixture scope) |
| Startup-form `OnLoad` expression → VBA | QUALIFIED (fixture scope) |
| AutoExec `OpenQuery` (non-VBA action) | QUALIFIED (fixture scope) |
| Startup-form embedded `OnLoad` macro (`OpenQuery`, non-VBA) | QUALIFIED (fixture scope; re-qualified 2026-10-23 with the real `OnLoadEmMacro` export grammar) |
| Embedded macros on other events or controls | UNQUALIFIED (only the startup-form `OnLoad` trigger was measured) |
| AutoKeys | UNSUPPORTED (no input synthesis; never claimed suppressed) |
| `SaveAsText` / References / VBA-trust reads | QUALIFIED (T4) |
| Any other Access runtime/version | UNQUALIFIED (single-host scope; facts report `unavailable`) |

> **No security overclaim.** Every suppression statement in this document is bounded to
> "suppressed at fixture scope on the qualified runtime". Nothing here claims that an
> arbitrary or unsupported database is safe to open.

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
