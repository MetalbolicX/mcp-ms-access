# ODD feature: documentation-workflows-v2 — DEFINED (awaiting implementation authorization)

Status: DEFINITION COMPLETE 2026-10-17 — user accepted all four decisions
(D1=A, D2=all families, D3=offline copy, D4=degrade-to-unavailable).
Agreements below are ACCEPTED. Implementation starts only on explicit
user authorization (definition-first preference, Engram obs-34abb3cda74c8c4a).

## Goal

Extend the delivered v1 documentation workflows with **application reverse
engineering**: VBA modules, forms, reports, named macros (incl. AutoKeys), and
(probe-first) table data macros — as additional fact categories in the same
evidence → render → save pipeline (`access.documentation.evidence/1` /
`access.documentation.report/1`), with the same per-fact honesty
(`observed | unavailable | failed | excluded`), coverage matrix, and
deterministic offline rendering.

## What already exists (reuse surface, no new invention)

- **Primitives**: `backends/access/` automation backend already reads
  VBA (`vba.py`: `VBProjects`/`VBComponents`, `SaveAsText` UTF-16-LE BOM),
  forms/reports (design-view machinery, `_with_design_view`), macros
  (`macros.py`). Proven live in plans 092/106/109/110 (ACXL dialect,
  `DoCmd.Save(5)`, compile honesty).
- **Pipeline**: `documentation_inspect` / `documentation_render` /
  `documentation_save` (analysis profile) with typed envelopes, evidence IDs,
  bounded resources (§6.3: 120 s deadline, 32/16 MiB caps via the T4b worker).
- **Safety proofs**: engine-level read-only opens (DAO `excepinfo[4]==5003073`,
  ODBC), startup/macro/data-macro non-execution fixtures, dialog-watcher,
  deadline supervisor (`documentation_runtime.py`).
- **Generator discipline**: `AutomationSecurity=3` (force-disable) +
  open-only-while-no-auto-run-object + rename-last, verified by
  `test_generate_documentation_fixtures.py`.

## The central design tension (must be decided before implementation)

v1's non-execution guarantee holds because the inspection path **never
instantiates `Access.Application`** (DAO `DBEngine.120` / ODBC driver only).
App-logic facts are **only** reachable at instantiation level: `VBProject`
reads require the operator trust flag ("Trust access to the VBA project
object model"); forms/reports/macros text requires `SaveAsText` on
`Access.Application`. There is no engine-level (DAO/ODBC) route — this is the
documented unreachable-by-class boundary (spec §7.2). v2 must therefore
either (A) introduce a disciplined instantiation-level **reader**, or
(B) stay engine-only and declare app-logic facts permanently out of scope
(which collapses v2), or (C) not integrate (leave app-logic reads in the
automation profile, outside the report pipeline).

Proposed resolution: **(A)** — a dedicated read-only app-logic reader that
opens an **offline copy** of the source under `AutomationSecurity=3`, with
no design writes, read-only DoCmd paths (`SaveAsText`, `VBComponents`
read), close-without-save, dialog supervision, and per-fact `excluded`/
`unavailable` honesty when trust is refused. The instantiation-level reader
is recorded per-fact (`method` metadata), so evidence consumers can
distinguish engine-level from UI-level facts. The existing boundary guard
note (a documentation path calling generic
`get_object_metadata`/`get_database_statistics`) is enforced by test.

## Accepted agreements (2026-10-17)

1. v2 adds fact categories **additively** to `access.documentation.evidence/1`
   (schema version unchanged; new categories: `vba_module`, `form`,
   `report`, `macro`, optional `data_macro`, `reference`, `startup_property`).
   Breaking layout changes would bump to `/2` — none planned.
2. App-logic extraction uses the disciplined reader of the central decision:
   offline copy + `AutomationSecurity=3` + no writes + close-without-save +
   supervision. The offline **copy** rule inherits v1's "source must be
   closed/offline" and strengthens it: the reader never opens the original.
3. VBProject trust refusal is `unavailable` with the operator prerequisite
   named in coverage; it never blocks schema/query facts (per-category
   isolation, v1 agreement 4).
4. VBA/macro text is **untrusted evidence** (v1 §5.3): recorded as source
   text, labelled as such in the report, passed through the existing
   `strip_password` boundary scrub; no execution, no client-side instruction
   status.
5. Table data macros are **probe-first**: reading via `SaveAsText` on a table
   carrying a data macro is unproven (the recorded blocker was
   `LoadFromText` build-side, L23 of safety-hardening). A live probe on a
   disposable fixture decides included vs. blocked-with-evidence; the probe
   result is recorded either way.
6. Non-goals: no source mutations, no `run_macro`/VBA write tools (the
   `ACCESS_MCP_ENABLE_VBA_WRITES` gate stays separate), no `.mdb`, no
   live/in-use databases, no MCP pre-decode transport bounds, no hidden
   object exports.
7. Report gains labeled sections (application-logic catalogue, form/report
   inventory, macro catalogue) with the same deterministic-render and
   coverage rules; Mermaid ER stays schema-only.
8. Forms/reports enter as **inventory + declared text** (control/section/
   event listing + `SaveAsText` source), not as rendered semantics; deep
   control-tree semantics stay out of v2.

## Resolved decisions (user, 2026-10-17)

- D1 = **A**: disciplined instantiation-level reader (offline copy,
  `AutomationSecurity=3`, no writes, close-without-save, supervision).
- D2 = **all four families** in the first slice: VBA modules; forms +
  reports inventory; named macros + AutoKeys; references + startup
  properties (DAO-readable). Table data macros remain probe-gated and
  separate (agreement 5).
- D3 = **offline copy only**: the reader never opens the original.
- D4 = **degrade to `unavailable`**: missing VBProject trust never blocks
  other categories; the prerequisite is named in coverage.

## Proposed slices (definition-level; execution order S1→S4, TDD)

- S1 — Reader skeleton: offline-copy acquisition, `AutomationSecurity=3`
  open, close-without-save, supervision hooks, per-fact `method` metadata,
  boundary-guard test (no `get_object_metadata`/`get_database_statistics`
  in the documentation path).
- S2 — VBA modules + references/startup properties (D4 degrade path first:
  trust-refusal → `unavailable`; then observed facts).
- S3 — Forms + reports inventory + declared text (SaveAsText, UTF-16-LE);
  named macros + AutoKeys action trees via the proven AXL dialect.
- S4 — Report sections + spec sync + coverage matrix rows; table data-macro
  read probe (disposable fixture, gate recorded either way).

Runtime qualification is operator-gated, as in v1 (T11 pattern).

## Log

- L1 (2026-10-17): "Get the memory of the project to continue with the next
  features" — user selected v2 definition as next feature; definition-only
  authorization (no implementation). Delivered local 28-commit stack merged
  ff to main at `ed7277b`, pushed to `origin/main`; 5 branches deleted
  (safe -d); qualify green post-merge after one-line pytest `pythonpath`
  fix (collection of `test_dialog_watcher.py` under `uv run pytest`).
- L2 (2026-10-17): user resolved D1=A (disciplined reader), D2=all four
  families + probe-gated data macros, D3=offline copy only, D4=degrade to
  `unavailable`. Agreements ACCEPTED; slices S1–S4 proposed; implementation
  remains unstarted pending explicit authorization.
