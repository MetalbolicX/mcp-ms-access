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
- [ ] T8: Inspect service — offline DAO extraction with honest failure semantics (PKs from
  indexes; no blank-SQL/empty-list collapse → `failed` states), bounded protected
  session closed before return, credential-ref only. Unit tests with fakes +
  conformance guard; real-DB integration deferred to T11. Route: delegated writer.
- [ ] T9: Frontends — analysis-profile MCP tools `documentation_inspect/render/save`, CLI
  `documentation` group, prompts `document_database`/`generate_er_documentation`;
  intentional updates to test_tool_schemas / test_cli_surface / test_mcp_prompts /
  tool_budget. Route: delegated writer.
- [ ] T10: Spec sync — flip implemented sections' proposal status in
  `docs/specs/documentation-workflows.md`; README row update. Route: inline or writer.
- [ ] T11: Runtime qualification — operator-gated real-Access checks (engine read-only,
  no execution, linked-source isolation, format/runtime matrix). NOT scheduled here.

## Checks (implementation)

- Test-first per task with RED/GREEN evidence recorded; focused pytest per unit;
  `python scripts/qualify.py` green before each work-unit commit.
- `git status` scope proof per task; Conventional Commits; commit identities recorded here.

## Delivery

- Docs slice committed: 287a186 on `docs/documentation-workflows` (PR candidate 1).
- Forecast S1 ~700-900 authored lines across three work units — chain applies; slice
  boundaries recorded per commit as they land.
