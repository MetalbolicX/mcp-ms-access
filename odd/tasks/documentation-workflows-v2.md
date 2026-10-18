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
9. **Raw source text is opt-in** (R1, mirrors the v1 `include_query_sql`
   contract): by default, app-logic evidence carries the catalogue — names,
   modules, procedure listings, control/section/event inventories, macro
   action trees where cheap — plus a content hash per source object; full
   `SaveAsText`/VBA source enters the evidence payload only when the caller
   explicitly opts in (proposed: `include_app_logic_source`, CLI
   `--include-app-logic-source`). Opt-in source still counts against the
   existing 32 MiB evidence / 16 MiB Markdown caps — above-limit stays
   `Err`, never truncation.
10. **Surface is the existing `documentation_inspect` operation extended**
    (R2): new arguments gate app-logic families (default OFF keeps every v1
    call byte-identical); the `analysis` profile does not grow a new tool
    and CLI/MCP keep the same single inspect entrypoint and permission
    semantics.
11. **Markdown reports show catalogues, not code** (R3): application-logic
    sections list modules/procedures, form/report inventories, and macro
    action trees; opt-in raw source is referenced by evidence ID (and
    hash), never inlined in the Markdown. Reports stay readable and bounded
    regardless of code volume.
12. **Child-shutdown invariant for the spawned `MSACCESS.EXE`** (hardening
    discovery, L3): the T4b supervisor kills the owned child on deadline;
    once the child hosts `Access.Application`, a kill can orphan `MSACCESS.
    EXE` holding a lock on the offline copy (the v1 "unreachable in
    production" argument no longer holds). The v2 child must reap its own
    Access instance by captured PID on every exit path — graceful
    close/quit first, then PID-scoped kill (precedent: plan-030 PID capture
    at connect, plan-031 `kill_access_processes` PID-scoped, never `/IM`);
    the offline copy is scratch-owned so lock residue is bounded to the
    disposable copy, never the original.

## Resolved decisions (user, 2026-10-17)

- D1 = **A**: disciplined instantiation-level reader (offline copy,
  `AutomationSecurity=3`, no writes, close-without-save, supervision).
- D2 = **all four families** in the first slice: VBA modules; forms +
  reports inventory; named macros + AutoKeys; references + startup
  properties. Correction (L4): references/startup are NOT engine-level —
  no reader exists in this codebase and project references have no DAO
  path; both come from the same instantiation-level reader
  (`Application.References` / `CurrentProject.AllProperties`). Table data
  macros remain probe-gated and separate (agreement 5).
- D3 = **offline copy only**: the reader never opens the original.
- D4 = **degrade to `unavailable`**: missing VBProject trust never blocks
  other categories; the prerequisite is named in coverage.
- R1 = **catalogue + opt-in source** (agreement 9).
- R2 = **extend `documentation_inspect`**, default-off arguments, no new
  tool (agreement 10).
- R3 = **catalogues in Markdown**, raw source only via evidence-ID
  reference (agreement 11).

## Proposed slices (definition-level; execution order S1→S4, TDD)

- S1 — Reader skeleton: offline-copy acquisition, `AutomationSecurity=3`
  open, close-without-save, supervision hooks, per-fact `method` metadata,
  **child-shutdown invariant (agreement 12): PID-captured Access reap on
  every exit path incl. the deadline-kill path**, boundary-guard test (no
  `get_object_metadata`/`get_database_statistics` in the documentation
  path).
- S2 — Inspect extension wiring (R2): default-off app-logic arguments on
  `documentation_inspect` + CLI/MCP surfaces + pins proving a
  default-off call emits byte-identical v1 evidence; VBA modules +
  references/startup properties (D4 degrade path first: trust-refusal →
  `unavailable`; then observed facts); catalogue+hash shape per R1.
- S3 — Forms + reports inventory + declared text (SaveAsText, UTF-16-LE);
  named macros + AutoKeys action trees via the proven AXL dialect; opt-in
  source payload path with cap arithmetic (R1).
- S4 — Report catalogue sections (R3) + evidence-ID/hash referencing +
  spec sync + coverage matrix rows; table data-macro read probe
  (disposable fixture, gate recorded either way).

Runtime qualification is operator-gated, as in v1 (T11 pattern).

## Agreed execution order (L5, supersedes slice order above)

1. **Feasibility spike (gate, no production code).** Decides whether
   Tier 2 exists at all.
2. **Tier 1 — engine-level inventory (ships regardless of the spike).**
   DAO `Containers` (Forms, Reports, Scripts, Modules): names and dates;
   startup DAO database properties (`StartupForm`, `AppTitle`, ...) if the
   spike confirms DAO readability. Runs in the existing worker; never
   instantiates Access, so every v1 safety proof still applies.
3. **Tier 2 — instantiation-level content reader (only if the spike
   passes).** VBA source, form/report controls and events, macro action
   trees, project references (S1–S4 reshaped).
4. **Settle open agenda items with spike data** (copy lifecycle, dialog
   supervision, flag precedence, acceptance scenarios).
5. **Single coherent rewrite of this document** (two tiers + spike
   outcome), replacing incremental patches.

Why the spike gates Tier 2: the fixture generator never opens
`Access.Application` while an auto-run object exists
(`scripts/generate_documentation_fixtures.py:35-45`: "AutoExec runs at
startup"); `AutomationSecurity=3` was only ever a second guard. Tier 2
must open apps that DO carry AutoExec/startup forms, so non-execution under
Access.Application is unproven and is the premise of the whole tier.

## Spike protocol (Step 1, proposed)

- **Fixture:** disposable copies of `db/fixtures/documentation/startup.accdb`
  (AutoExec -> `RunCode StartupMarker()`, ManualMarker, AutoKeys), plus a
  startup form whose `OnLoad` is `=StartupMarker()` (expression call into
  the existing module; no VBProject trust needed to set it). The marker is
  copy-relative (`startup_executed.marker` beside the opened file,
  generator :40-58), so each copy is its own detector.
- **Copy location:** a unique per-run directory under the D: repo scratch
  convention (`db/fixtures/e2e-<uuid>`), never `%TEMP%` on C: (standing
  user preference); removed after evidence is captured.
- **Matrix (each cell observed, N=3 runs):**
  - Positive control: `AutomationSecurity=1` (low) MUST fire the marker;
    otherwise every "did not fire" result is vacuous (SQLite-PWD lesson).
  - `AutomationSecurity=3`, copy untouched.
  - `AutomationSecurity=3` + DAO neutralization of the copy before opening
    (delete `StartupForm` property; AutoExec neutralization route is an
    open spike question — DAO cannot rename Scripts documents).
  - AutoKeys is input-triggered: recorded as not-reachable without
    keyboard input, no synthetic keystrokes sent.
- **Observed per cell:** marker present/absent; any modal/trust dialog
  (dialog watcher); `MSACCESS.EXE` PID captured, reaped, no orphan; no
  lock residue; startup properties readable via DAO (Tier 1 confirmation);
  `Application.References` readable after open (Tier 2 data point).
- **Exit:** PASS = positive control fires AND a recipe with zero markers,
  zero dialogs, clean PID reap across all runs. Otherwise STOP recorded
  with evidence; v2 ships Tier 1 only.
- **Safety:** copies only, original fixtures and `db/northwind.accdb`
  hash-checked unchanged; no commits from the spike.

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
- L3 (2026-10-17): refinement round. Design gap found by parent: the T4b
  deadline-kill path can orphan the child-spawned `MSACCESS.EXE` (v1's
  "unreachable in production" no-grandchildren argument breaks once the
  worker hosts Access.Application) → agreement 12 (child-shutdown PID reap
  invariant). User accepted R1 catalogue+opt-in source, R2 extend
  `documentation_inspect` (default-off, v1 byte-identical), R3 catalogues
  in Markdown (no inlined code). Slices rewritten to carry these.
  Delivered stack + fix + this definition pushed: main `9330821` on
  origin.
- L4 (2026-10-17): parent correction — D2's "(DAO-readable)" note on
  references/startup was wrong (grep: no reader in the codebase;
  `Access.Application.References` has no DAO equivalent). Both families
  move under the instantiation-level reader; their fact-read paths join the
  S1 validation probes. Remaining refinement agenda for the user: copy
  lifecycle/location + cleanup, production dialog supervision (v1 kept the
  watcher test-only), catalogue evidence layout (hash/ID format, flag
  precedence `include_app_logic_source` without `include_app_logic`), and
  v2 runtime-acceptance scenarios (v1 Q1–Q7 pattern).
- L5 (2026-10-17): user: "I agree" — the feasibility spike gates Tier 2,
  and Tier 1 (engine-level inventory) ships on its own regardless of the
  spike result. Parent recorded the execution order and spike protocol;
  copy location corrected to the D: scratch convention (bare `mkdtemp`
  would land on C:). L4's startup-property claim partially reversed:
  startup options are believed to be DAO database properties (Tier 1),
  references are not — confirmation is a spike observation, not a fact
  yet. Spike execution NOT yet authorized.
