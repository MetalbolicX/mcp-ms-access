# Proposed feature: Access documentation workflows (v1)

> **IMPLEMENTED — PARTIALLY RUNTIME-QUALIFIED (ACE/DAO, fixture scope).** Contracts,
> services, MCP tools,
> CLI commands, and prompts below are implemented and covered by unit tests on branch
> `feat/documentation-workflows`. No real Access database has been inspected by this
> feature: engine-level read-only protection, non-execution, linked-source isolation, and
> the format/runtime matrix — see §7.2 for the exact qualified scope. Claims outside
> that scope (linked-table isolation, VBA/macro non-execution, engine-level read-only
> protection, ODBC) remain **NOT QUALIFIED**.
>
> **Status:** Definition accepted; runtime implementation not authorized. Related: [Architecture](../01-architecture.md), [Contracts](../02-contracts.md), [Safety and configuration](../03-safety-and-configuration.md), [Feature task](../../odd/tasks/documentation-workflows.md).

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

> **Implementation status:** Agreements 1–11 are accepted and now reflected in implemented
> code where applicable; agreement 3's protection guarantee and agreement 11's qualified
> format/runtime claims remain pending runtime qualification.

1. **Scope sequence — v1 is schema, saved queries, and Mermaid ER; v2 may add full application analysis.** This bounds v1 to deterministic metadata rather than executable or interactive objects.
2. **Source must be closed and offline.** Missing lock files, user assertions, plain copies, and post-copy hashes do not prove quiescence or consistency.
3. **Inspection must be qualified, engine-level read-only, and passive.** DAO is the qualification target because it exposes QueryDefs SQL; never automatically fall back to a less capable backend. Existing `readonly=True` is not file read-only (`backends/dao.py:135`, connection uses `read_only=False`).
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
> changes beyond reuse of `schema_inspector` reads are not made; engine-level read-only
> protection is still unqualified. See [as-built layers](../01-architecture.md).

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

DAO is the v1 qualification target because saved QueryDefs SQL availability is required. Qualification must establish engine-level passive/read-only behavior per supported format/runtime; missing capability is reported, not silently handled by ODBC or another fallback. DAO connection construction currently uses `read_only=False` (`backends/dao.py:135`), despite readonly policy naming. This is a constraint to resolve and qualify, not evidence safe read-only behavior already exists.

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
> unit-tested (deny by default). Engine-level read-only/passive opening and linked-source
> isolation are NOT yet implemented or qualified — inspect still opens through existing
> session machinery. Existing controls: [Safety and configuration](../03-safety-and-configuration.md).

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
> evidence, renderer, save, inspect, frontends inside the qualify gate). Runtime
> qualification scenarios below have NOT run against real Access databases.

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

> **Proposal status:** Criteria for future authorized implementation; no scenario below is claimed to pass today.

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
| ACE (`.accdb`) | DAO backend, Northwind 8-table closed fixture, Windows/ACE, 2026-10-08 | **PARTIALLY QUALIFIED**: schema/relationship/query fidelity, integrity (sha unchanged), no lock residue, non-execution (scope: nothing executable in fixture), render/save E2E incl. permission/confirm/overwrite/fingerprint gates, cardinality honesty — all PASS with independent verification. A separate synthetic-fixture pipeline suite (public CLI in subprocesses; `tests/integration/test_documentation_pipeline_e2e.py`) additionally demonstrates composite-PK extraction, query-parameter/relationship-attribute `unavailable` states with partial coverage, deterministic database/ER rendering including the unique-index-backed one-to-one and composite-FK edges, ER query-catalogue exclusion, save gating on synthetic data, and linked inspection succeeding while the sentinel is exclusively locked elsewhere. Since the T3 phase-2 fixes the same suite also demonstrates DAO type 48 (dbUpdate) categorised `action` (32/48/64 → `action`, 80/96 → `ddl`, 0/32/80 preserved) so update and delete are distinguished; field defaults read from DAO `Field.DefaultValue` as `observed` stored expressions (`Customers.Status` = `"Active"`, `Orders.Status` = `"Open"`) with genuinely defaultless fields `observed` empty and never fabricated as `null`; and linked-table local catalogue metadata (`kind=linked`, `Connect`, `SourceTableName`) reaching evidence and rendering as `- Linked table: <name> → <database-file>/<source-table>`. All three phase-1 blocking findings are resolved; the cell stays PARTIALLY QUALIFIED because query parameters, engine-level read-only, ODBC, and startup-code non-execution remain open. NOT COVERED: VBA/macro non-execution, engine-level read-only (observed no mutation, but the DAO handle still opens read_only=False), filtered inspection, ODBC. |

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
