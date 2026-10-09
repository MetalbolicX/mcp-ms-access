# ODD feature: documentation-workflows (v1)

Status: DOCS CYCLE CLOSED — spec agreed and committed on branch `docs/documentation-workflows`;
source implementation NOT authorized yet.

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

## Delivery

- Forecast: ~350 authored changed lines (spec ~280, README ~5, this doc). Under the ~400
  budget; strategy `ask-on-risk` (default), no chaining menu expected.
- Delivered as one work-unit commit on `docs/documentation-workflows` (branched from
  main b824a9c); commit identity recorded in the Engram mirror. Push/PR/merge remain
  user decisions.
