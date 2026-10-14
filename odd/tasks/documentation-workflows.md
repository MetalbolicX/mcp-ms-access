# ODD feature: documentation-workflows (v1)

Status: IMPLEMENTATION IN PROGRESS on `feat/documentation-workflows` (from docs tip 287a186);
delivery strategy: `feature-branch-chain` (user-selected 2026-10-08); docs slice = PR candidate 1.

## Goal

Deliver v1 database documentation workflows for the MCP server: `document_database` and
`generate_er_documentation`, producing one Markdown report (Mermaid ER + data dictionary +
query catalogue + coverage) from a closed/offline Access database, via a deterministic
evidence pipeline. Full application reverse engineering (VBA/forms/reports/macros/events)
is a later v2 phase, out of scope here.

## Non-goals (this cycle)

- No source code changes, no runtime permission enablement, no database operations.
- No execution of queries, VBA, macros, or external-source refreshes.
- No live/in-use database support (user decision: offline/closed source only).
- No new global error-code registry; existing `Result` envelope stays.

## Accepted agreements (definition phase, all PROPOSED until implemented)

1. Scope sequence: v1 = schema + saved queries + Mermaid ER; v2 = full application analysis.
2. Source must be closed/offline. Absence of a lock file, user assertion, plain copy, or
   post-copy hash do not prove quiescence or consistency.
3. Inspection must be qualified engine-level read-only and passive; no automatic fallback
   to a less-capable backend; DAO is the qualification target (QueryDefs SQL availability).
   Current policy `readonly=True` is NOT file read-only (`backends/dao.py:135`).
4. Evidence is a versioned package (`access.documentation.evidence/1`) with per-fact states
   `observed | unavailable | failed | excluded`; empty ≠ unknown; PKs derive from indexes;
   composite relationship column pairs preserved; known extraction-fallback defects
   (blank SQL, empty-list collapses) must be fixed by implementation, not hidden.
5. Rendering is deterministic, offline from evidence (`access.documentation.report/1`),
   single Markdown output, preview before save, no hidden object exports.
6. LLM interpretation is client-side only, optional, clearly labelled, references evidence
   IDs, never mutates facts; database text is untrusted evidence, not instructions.
7. Three deterministic operations: `documentation_inspect` / `documentation_render` /
   `documentation_save`, shared by CLI and MCP; two profile-independent guidance prompts
   (`document_database`, `generate_er_documentation`); extend `analysis` profile, keep
   `get_er_diagram` response untouched; surface pins updated intentionally when implementing.
8. Results: `Ok` carries `data.status = complete | partial` with visible gaps/warnings;
   hard failures use existing `Err(message)`; CLI exits 0/1/2 unchanged; MCP `isError`
   follows the success flag.
9. Documentation-write permission is separate from database `--allow-write` (user-approved):
   inspect/render need no write permission; save requires the separate operator permission
   plus per-call `confirm`; overwrite requires explicit approval plus destination
   fingerprint check; `confirm=True` alone is never an operator grant.
   Proposed naming (to validate when implementing): CLI `--allow-doc-write`,
   env `ACCESS_MCP_ALLOW_DOC_WRITE`.
10. Cardinality is never invented: unsupported constraints stay in the relationship
    catalogue; Mermaid uses stable identifiers preserving original names; escaping is safe.
11. Support is declared per qualified format/runtime combination, not per file suffix.

## Tasks

- [x] T1 (orchestrator): create this feature document + Engram mirror. Route: inline
  (single tracking artifact).
- [x] T2: write `docs/specs/documentation-workflows.md` — delivered 248 lines, 26 headings,
  all 11 agreements mapped (spec §2 decision log + agreement-to-section map). Route:
  delegated writer (gentle-ai-worker). Micro-decision accepted: `confirm` is REQUIRED
  (omission rejects) instead of default-false — equal-or-stricter safety posture.
- [x] T3: README.md Documentation row added (README.md:26, proposed/not-implemented marker).
  Route: delegated writer (same delegation as T2).
- [x] T4: structural verification + closure — writer self-verification + independent
  gentle-ai-verify (all 6 checks PASS with file:line evidence; native assess unassessable
  due to untracked files -> treated high -> independent verifier run per contract) +
  orchestrator spot-check (git status, banner, README row). Links resolve; scope proof:
  only README.md, docs/specs/documentation-workflows.md, odd/tasks/documentation-workflows.md
  changed; no src/ or tests/ paths. Route: delegated verify + inline spot-check.
  Commit still requires explicit user authorization (branch-first from main when approved).

## Checks

- Docs-only cycle: no runnable deterministic test applies; proportionate structural
  verification per task + path:line spot-checks (precedent: docs-as-built-consolidation).
- Scope proof: `git status` must show only `docs/specs/documentation-workflows.md`,
  `README.md`, `odd/tasks/documentation-workflows.md`.

## Implementation tasks (authorized 2026-10-08; test-first; one work-unit commit each)

Slices for the chain: S1 = T5+T6+T7 (offline core: evidence, render, save); S2 = T8
(inspection service); S3 = T9+T10 (frontends, pins, spec sync). Runtime qualification
(T11) is operator-gated and separately authorized.

- [x] T5: Evidence model `access.documentation.evidence/1` — delivered in commit 0447f09
  (application/documentation.py + 5 focused tests; RED ModuleNotFoundError → GREEN;
  qualify exit 0, 1678 passed/3 skipped). Native assess: medium, large writer,
  self-verification stands; reviewDue (slice budget) deferred to PR-slice boundary.
  Parent spot-check: focused tests 5 passed. Route: delegated writer, test-first.
- [x] T6: Deterministic renderer `access.documentation.report/1` — delivered in commit
  5774c33 (application/documentation_render.py + 9 tests; writer GREEN 14 combined,
  qualify exit 0 1687/3; orchestrator RED replay: import collection error without module).
  Native assess: medium, large writer, self-verification stands; reviewDue deferred.
  Incident note: worker shells lack `python`; repo interpreter is .venv/Scripts/python.exe.
- [x] T7: Save service — delivered in commit 39e584d (application/documentation_save.py
  + 16 tests; RED import → GREEN; qualify exit 0, 1703 passed/3 skipped; parent spot-check
  16 passed). Native assess: medium, large writer, self-verification stands; reviewDue
  false (under budget).
- [x] T8: Inspect service — delivered in commit c66ddb3 (application/documentation_inspect.py
  + 10 tests incl. fake-signature conformance guard; RED import → GREEN; qualify exit 0,
  1713 passed/3 skipped; parent spot-check 10 passed). Native assess: medium, large
  writer, self-verification stands; reviewDue (slice budget) deferred. Known residual:
  schema_inspector suppresses the original SQL-read exception upstream; service maps blank
  SQL to a failed fact honestly (Access QueryDef SQL is never legitimately empty).
- [x] T9: Frontends — delivered in commit e94428b (analysis tools, prompts, CLI group,
  pin updates, test_documentation_frontends.py; RED 9 failures → GREEN 15; qualify exit 0,
  1718 passed/3 skipped; parent spot-check 15 passed). Native assess: medium, large
  writer, self-verification stands; reviewDue false (under budget).
- [x] T10: Spec sync — inline (small mechanical status flips from verified facts): spec
  banner + section statuses now "IMPLEMENTED (unit level) — RUNTIME NOT QUALIFIED",
  qualification matrix cells stay NOT QUALIFIED; README row updated. Route: inline.
- [x] T11: Runtime qualification — EXECUTED 2026-10-08 against db/northwind.accdb
  (ACE/DAO, closed fixture, sha f7140e9a… unchanged across every check). Results:
  Q1 integrity PASS; Q2 no lock residue PASS; Q3 schema fidelity PASS vs independent DAO
  reference (8/8 tables; fixture reality: Products and OrderDetails carry NO PK — evidence
  honestly observed-empty); Q4 relationships PASS after fixes; Q5 queries PASS (0 observed);
  Q6 render+save E2E PASS after envelope fix (permission deny exit 1, --yes exit 2,
  overwrite guard, stale-fingerprint rejection, correct-fingerprint overwrite success,
  on-disk sha == report sha); Q7 read-only PASS-with-caveat (bytes+mtime untouched;
  dao.py:135 still opens read_only=False — engine-level protection remains unqualified);
  Q8 non-execution PASS-with-scope (no queries/macros/modules exist in fixture).
  Qualification found and fixed three defects: CLI envelope seam (282e0b2), child-side
  cardinality derivation (fae04b8), evidence parent/child inversion + Access relation
  auto-indexes Unique=True defeating uniqueness gates (71152e7; probe-verified).
  Final real-DB render: all four edges `}o--||` child-first; catalogue parent/child
  correct; auto-indexes still listed. NOT COVERED by this fixture: linked-table isolation,
  VBA/macro non-execution, ODBC backend, filtered inspection. Independent verifier PASS
  on 282e0b2 and 71152e7 E2E; fae04b8 verifier FAIL led to the 71152e7 root-cause fix.
- [x] T12: Engine-level read-only inspection open — delivered (uncommitted). RED
  `tests/unit/test_dao_adapter.py::test_documentation_inspection_open_requests_engine_read_only`
  failed against current code: the recording dispatcher showed the only DAO-boundary open
  call as `('C:\\db\\core.accdb', '', False)` (read_only=False) with no read-only reopen,
  and `test_reopen_read_only_reuses_recorded_password` failed `AttributeError`. Fix: the
  documentation-inspect wiring now routes through
  `application/documentation_inspect.py::open_inspection_port` →
  `backends/dispatcher.py::ComDispatcher.reopen_dao_database_read_only`, which reopens the
  same handle with `OpenDatabase(..., read_only=True)` and reuses the recorded password;
  `backends/dao.py` `DaoAdapter.connect` is unchanged, so every other tool keeps its
  read-write open (preserve case
  `test_backend_dao.py::TestLifecycle::test_connect_opens_database` green). GREEN: unit 16
  passed, exit 0. Qualification:
  `tests/integration/test_documentation_pipeline_e2e.py::test_inspect_open_is_engine_read_only_and_refuses_write`
  (public `documentation inspect` on core.accdb exits 0 with complete metadata readback;
  the production inspection open refuses `UPDATE Orders ...` with DAO `Operation must use
  an updateable query.`; core.accdb sha256 unchanged) — pipeline 15 passed, construction 4
  passed, env-unset 19 skipped, `scripts/qualify.py` exit 0 (ruff/pyright OK, unit 1759
  passed / 3 skipped). Route: delegated writer, test-first. No commits.

## Checks (implementation)

- Test-first per task with RED/GREEN evidence recorded; focused pytest per unit;
  `python scripts/qualify.py` green before each work-unit commit.
- `git status` scope proof per task; Conventional Commits; commit identities recorded here.

## Delivery

- Docs slice committed: 287a186 on `docs/documentation-workflows` (PR candidate 1).
- Forecast S1 ~700-900 authored lines across three work units — chain applies; slice
  boundaries recorded per commit as they land.

## Log

- T12 (engine-level read-only inspection open): RED showed the inspection path recorded
  `read_only=False` at the DAO boundary and exposed no read-only reopen; GREEN after
  routing the wiring through `open_inspection_port` → `reopen_dao_database_read_only`
  (unit 16 passed, exit 0). Qualification via
  `test_inspect_open_is_engine_read_only_and_refuses_write`: the read-only handle refuses
  an `UPDATE` with DAO `Operation must use an updateable query.`, `documentation inspect`
  still exits 0 with complete metadata readback, and core.accdb sha256 is unchanged.
  Pipeline 15 passed; construction 4 passed; env-unset 19 skipped; `scripts/qualify.py`
  exit 0. No commits.
- T12 verification-finding fix round (B1, N1, N2, N3, N4). B1: the CLI
  documentation-inspect path now opens the inspection port inside the command's `try`
  (`src/ms_access_mcp/cli.py`), so a failing read-only reopen returns the formatted `Err`
  envelope (exit 1) instead of an unhandled traceback; probe
  `tests/unit/test_cli_documentation.py::test_inspect_reopen_failure_returns_err_envelope_without_raising`
  and preserve `...::test_inspect_success_still_returns_ok_envelope` pass. N1:
  `ComDispatcher.reopen_dao_database_read_only` refuses with `RuntimeError` when
  `_db_path is None or not is_connected()`, so closing the DAO handle cannot resurrect a
  read-only connection; probe
  `tests/unit/test_dao_adapter.py::test_reopen_read_only_refuses_when_connection_is_not_live`
  passes. N2: `AccessAdapter.connect` records the password
  (`src/ms_access_mcp/backends/access/__init__.py`) so the read-only reopen carries real
  credentials for encrypted databases, restoring DAO parity; probes
  `...::test_access_com_connect_records_password_for_read_only_reopen` and preserve
  `...::test_access_com_connect_keeps_empty_password_for_read_only_reopen` pass. N4:
  `tests/integration/test_documentation_pipeline_e2e.py::test_inspect_open_is_engine_read_only_and_refuses_write`
  now pins the DAO engine error code `excepinfo[4] == 5003073` (description `Operation
  must use an updateable query.`) instead of the loose regex alone. N3: the §0 banner no
  longer folds non-execution into the fixture-scope claim, so the workflows status
  matches `docs/specs/documentation-fixtures.md`; the NOT-QUALIFIED list names
  startup-code non-execution. B1/N1/N2/N4 were already applied by the interrupted round
  (their RED was captured then, not re-observable now) and were verified by running the
  probes; N3 was completed this round as a passive documentation edit (no RED applicable).
  Checks (repo root, `.venv` interpreter): (1) unit
  `test_dao_adapter.py test_cli_documentation.py test_documentation_inspect.py` = 21
  passed, exit 0; (2) `ACCESS_DOCUMENTATION_FIXTURES_TEST=1` pipeline = 15 passed, exit 0;
  (3) construction suite = 4 passed, exit 0; (4) both integration files env-unset = 19
  skipped, exit 0; (5) `scripts/qualify.py` exit 0 (ruff OK, pyright OK, unit 1764 passed /
  3 skipped, budget OK); (6) no `db/fixtures/e2e-*` leftovers, no `.laccdb`/`.ldb` under
  `db/fixtures`, `db/fixtures/documentation` intact, `db/northwind.accdb` sha256
  `f7140e9af87312f7d53dee9feb56a4bcb8b9a68b707b3400a974e5fde71ac5cb` unchanged. No commits.
