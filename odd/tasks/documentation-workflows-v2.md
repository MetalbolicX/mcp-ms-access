# ODD feature: documentation-workflows-v2 — IMPLEMENTATION AUTHORIZED

Status: COMPLETE 2026-10-18 (L18) � all nine tasks closed (T5 blocked-with-evidence by design). Runtime qualification is fixture-scope on the measured Access 16.0 host. Merge/push/PR are the user�s decisions.
Branch: `feat/documentation-v2-app-logic` from `main` @ `7a51681`.

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

## Feasibility result (2026-10-17): fixture-scope PASS, production safety unqualified

The authorized matrix completed with process exit **0** in **290.2 seconds**.
An independent read-only verifier accepted the protocol PASS with no blockers;
this is a feasibility result, not approval to implement or a general security
claim. A purpose-built equivalent fixture carried separate AutoExec and
startup-form VBA marker functions (rather than modifying the canonical fixture).

| Cell | N | Security readback | AutoExec VBA marker | Form OnLoad VBA marker | Observed dialogs | Cell cleanup |
| --- | --- | --- | --- | --- | --- | --- |
| Positive control | 3 | 1 | Fired 3/3 | Fired 3/3 | None | Graceful 3/3 |
| ForceDisable, unchanged copy | 3 | 3 | Absent 3/3 | Absent 3/3 | None | Graceful 3/3 |
| ForceDisable, DAO StartupForm deletion | 3 | 3 | Absent 3/3 | Absent 3/3 | None sampled | Graceful 3/3 |

All nine `child_returncode` values were 0; their stdout/stderr files were
empty. No cell timed out. No lock files or new MSACCESS processes remained;
all 11 original fixture/Northwind file hashes matched. The builder alone
needed an identity-verified PID kill after its 20-second Quit grace period;
this must not be reported as nine-plus-one graceful exits. The watcher saw
owned windows in positive-control/unchanged-copy runs; the deletion runs
started the observer without sampling any windows (weaker dialog evidence).

### What this allows us to conclude

- A copy opened under `AutomationSecurity=3` suppressed the two tested VBA
  entry functions on this host. Both positive controls were non-vacuous.
- DAO can read the real `StartupForm` database property and remove it on a
  disposable copy; deletion succeeded in all three deletion runs. Startup
  properties therefore belong in the Tier 1 candidate surface, not in the
  earlier blanket assertion that they require Access.Application.
- Tier 2 remains a viable planning candidate. The unmodified-copy recipe
  was clean at the measured scope, so destructive neutralization of startup
  metadata is not justified as a default by this experiment.

### Missing proof: do not promote PASS into a no-execution guarantee

- AutoExec contained **RunCode calling VBA**; OnLoad was an expression
  calling VBA. Neither used a pure macro-action canary. A macro engine
  could execute actions while blocking VBA and leave these markers absent.
  General macro-action non-execution remains unresolved.
- AutoKeys was not triggered (no keyboard input); named macro absence was
  not a positive control. Form/report SaveAsText, VBProject/trust access,
  table data-macro reads, and cross-version behavior were not qualified.
- The References probe failed in all nine cells with COM scode
  `0x800A01C4` (invalid ordinal). Its Count/item operations shared one
  exception boundary; this is an unresolved probe/dispatch issue, not
  evidence that ForceDisable denies References.
- No deadline/crash cell ran. Cleanup after killing the Python owner is
  still unproven: its `finally` cannot reap Access once that owner is dead.
  Future runtime ownership must cover the captured Access PID outside the
  child, not merely promise child-side cleanup.

At the first-spike closure, the next planning gate was pure macro-action
suppression: the two VBA markers alone were insufficient. L8 authorized
that follow-up; its bounded result is recorded below. Production
implementation remains unauthorized.

Authorized follow-up (L8; experiment only, not implementation): an
AutoExec macro performs a non-VBA action, for example OpenQuery on a saved
append query that adds one known row to a dedicated log table in the
throwaway copy. A matching low-security positive control must demonstrate
the row insertion; under ForceDisable the row count/content must stay
unchanged. An independent DAO read observes the row rather than relying on
another VBA callback. Macro action syntax and startup-form embedded-macro
coverage must be qualified rather than assumed. Keep the same unique D:
copy, ownership, timeout, dialog, original-integrity, and cleanup boundaries.
If a non-VBA action executes, ForceDisable alone is not a passive-reader
recipe; stop Tier 2 at the planning gate instead of weakening the contract.

### Retained evidence and cleanup

The archive contains 100 text evidence/harness files, including both failed
pre-matrix build attempts; it contains no Access databases or bytecode:
[`documentation-v2-feasibility-20261017.zip`](../evidence/documentation-v2-feasibility-20261017.zip)
(76,979 bytes; SHA-256
`e07f536d70d25ce7323d6507097c887ea2d2955281080d3ca0287ef9a00120de`).
The exact scratch root was removed after independent verification and archive
integrity checking. No production files changed; no commit or push performed.

## Non-VBA follow-up result (L8-L9): fixture-scope PASS

The authorized follow-up completed exit **0** in **143.7 seconds** on
Access **16.0**. Independent read-only verifier `mvd9o49p-5-wtks` accepted
PASS with no blockers (Engram #9960); no live rerun was needed.

The actual SaveAsText export confirms AutoExec's sole action was OpenQuery
on `qryAppendCanary`. DAO independently confirmed the saved append SQL
inserts exactly `(ProbeID=1, Label='NON_VBA_AUTOEXEC')` into a local table.
There were no VBA modules, forms, StartupForm property, RunCode actions,
SetWarnings actions, or automatic dialog acceptance. The builder loaded a
neutral macro name, captured/inspected its real export, then armed AutoExec
last; it never reopened the armed fixture through Access.

| Cell | N | Security readback | DAO rows before | DAO rows after |
| --- | --- | --- | --- | --- |
| Positive control | 3 | 1 | Empty 3/3 | Exact single canary row 3/3 |
| ForceDisable, unchanged copy | 3 | 3 | Empty 3/3 | Empty 3/3 |

All six Access child exits and all 12 independent DAO observer exits were
0. Access stdout/stderr and DAO stderr were empty; DAO stdout contained
structured observations. AutoExec was still present after every run, so
absence of the macro cannot explain the ForceDisable result. Six cell
instances exited gracefully; no timeout, lock, residual owned process,
new MSACCESS process, or changed original hash was observed (11 files).
The window observers started without error but sampled no windows; report
"no dialogs observed", not a guarantee that every possible dialog was
observable. The builder's final identity lookup raced with process exit;
its guarded kill was skipped. Do not count it as a proven graceful exit or
as an actual kill.

### Planning consequence and remaining boundaries

- The earlier VBA-only gap is now addressed by a **non-VBA OpenQuery
  canary**, with a non-vacuous positive control. Together, the experiments
  support continuing Tier 2 design for this measured runtime.
- This is **not** universal suppression of arbitrary macro actions,
  embedded event macros, AutoKeys keyboard triggers, external side effects,
  or other Access versions/settings. No host Trust Center settings were
  changed. Absence of action-query dialogs is a host observation, not a
  portable contract.
- An untouched-copy ForceDisable recipe passed both experiments. Removing
  startup metadata is not supported as a necessary default; retaining
  original metadata also preserves what the reader is supposed to inspect.
- Content extraction (SaveAsText for forms/reports, VBProject trust,
  References) and cleanup after owner death remain unqualified. In
  particular, no claim about killing a hung owner or opening the copy
  engine-read-only follows from these successful startup tests.

Remaining actionable planning steps (no production implementation authorized):
1. Specify the worker/supervisor ownership contract: original never opened
   by Access, security readback before open, exact Access PID ownership,
   graceful exit and supervisor-owned cleanup after owner death, D: copy
   lifecycle, and explicit unsupported-runtime handling.
2. Define catalogue/source-read acceptance scenarios for each accepted
   family, including missing-trust degradation and independent References
   diagnostics; no raw source by default and no source execution.
3. Pin default-off CLI/MCP arguments, source-flag precedence, per-fact
   evidence schema compatibility, size caps, and catalogue-only rendering.
4. Reconcile this evolving definition into one execution-ready plan only
   after the remaining contracts are agreed. These experiments do not
   authorize a production writer or additional live probes automatically.

Evidence archive:
[`documentation-v2-nonvba-feasibility-20261017.zip`](../evidence/documentation-v2-nonvba-feasibility-20261017.zip)
contains 83 text/harness files, no databases or bytecode; 66,107 bytes;
SHA-256 `da65dbf51411be90da283244d03532bdf1a2d2687af4505ef432e5dce87a3b49`.
The exact D: scratch root was removed after verification and archive
integrity checking. No production changes, commit, or push.

## Specs (implementation anchors)

- S1 (authorization): user: "Execute the recommended order" — the six-phase
  order of record: (1) supported safety contract; (2) process containment
  first; (3) qualify content-reading operations; (4) close targeted
  startup-coverage gaps; (5) implement behind agreed opt-ins; (6) final
  release gates. Order is mandatory: 5 requires 2+3+4 gates passed.
- S2 (contract): the "v2 safety and support contract" section below is
  normative for all implementation tasks.
- S3 (agreements): D1–D4, R1–R3, and agreements 1–12 above are binding.
- S4 (v1 preservation): default-off app-logic arguments; a default v1
  `documentation_inspect` call emits byte-identical evidence; existing caps
  (120 s / 32 MiB / 16 MiB) and pinned `Err` texts/exit semantics unchanged
  (`tests/unit/test_documentation_runtime.py:314-393`).
- S5 (qualification evidence): every live gate needs a non-vacuous positive
  control, unchanged original hashes, zero owned survivors/locks, and
  PID+creation-scoped kills only; no `/IM`, no baseline-diff kills, no
  Trust Center changes, no synthetic input, no dialog auto-acceptance.

## v2 safety and support contract (T1, normative)

1. **Supported runtime.** App-logic qualification applies to the measured
   Access 16.0 runtime on this host (build/bitness recorded in evidence).
   Any other runtime reports app-logic facts `unavailable` naming the
   prerequisite; it is never attempted (D4).
2. **Source handling.** The original is never opened by `Access.Application`.
   Each inspection uses one unique disposable copy under the D: scratch
   convention, removed after evidence capture; qualification verifies
   original hashes unchanged.
3. **Open discipline.** `AutomationSecurity=3` is set and read back before
   `OpenCurrentDatabase`; mismatch aborts without opening. `Visible=False`,
   `UserControl=False`. No Trust Center, registry, or trusted-location
   changes; no synthetic input; dialogs are observed, never accepted.
4. **Object access.** Read-only APIs only (`SaveAsText`, `CurrentProject`
   enumerations, DAO properties). No design-view opens, no mutating `DoCmd`.
   VBA/macro text is untrusted evidence: opt-in and labeled (R1); secret
   scrubbing applies at the error/reason boundary (as in v1), while embedded
   opt-in source stays raw untrusted text — the include_query_sql precedent.
5. **Process ownership.** The worker captures the Access PID, creation time,
   and image immediately after dispatch and acknowledges the supervisor
   BEFORE opening the armed copy. The supervisor owns the kill net for
   deadline, crash, and exit paths. Kills are PID+creation-scoped only.
6. **Degradation.** Security or cleanup uncertainty produces `Err`, never a
   partial `Ok`. Missing VBProject trust degrades to `unavailable` (D4).
7. **Claims boundary.** Qualification is fixture-scope on the supported
   runtime. No claim of universal macro suppression or hostile-database
   safety is made anywhere in code, docs, or reports.

## Tasks

- T1 | S2 | inline | author the contract section (this file).
- T2 | S2,S4,S5 | worker | ownership protocol in
  `application/documentation_runtime.py` + `documentation_worker.py`:
  worker→parent Access-identity ack before armed open; parent-held kill net;
  strict TDD on unit/fake level; no live Access. Delivered `12965a8`
  (parent-verified: 41 passed, ruff clean; new `process_identity.py`).
- T3 | S2,S5 | worker-prep + parent-launched live gates | containment
  research: Job Object assignment for COM-launched MSACCESS; crash/deadline/
  supervisor-death matrix on scratch copies. GATE PASSED 2026-10-17 (L12):
  attempt-4 exit 0, 9/9 cells, 11/11 checks; close-after-assign and
  sacrificial-parent job containment PROVEN; production kill net reaped
  Access in crash/hang cells with pinned Err texts, no kill in graceful
  cells; zero owned survivors, originals unchanged. Evidence in
  `odd/evidence/documentation-v2-containment-20261017.zip` (sha d5eb52eb…f81,
  includes attempts 1-3); scratch roots removed.
- T4 | S2,S5 | worker + live | content-read qualification: corrected 1-based
  References enumeration with separated Count/Item diagnostics; SaveAsText
  form/report/macro reads; VBProject trust refusal → `unavailable`. GATE
  PASSED 2026-10-17 (exit 0, 9/9 cells, 13/13 checks): References fully
  readable under ForceDisable (name/path/GUID/version; the archived spike
  failure was the 0-based indexing defect); SaveAsText exports for form,
  report, and macro all succeed under ForceDisable (UTF-16-LE BOM, hashed);
  this host has VBProject trust enabled (trust_enabled_observed=true, no
  settings changed) so the refusal-degradation path is proven by unit fakes
  in T7, not live. Evidence archived in
  `odd/evidence/documentation-v2-content-reads-20261017.zip` (104 text files,
  206,889 bytes, SHA-256 `7d4006a6e539721b3f0efe375261e234ee253a096ef53f15b6863546d9f9ce64`);
  scratch root removed, no locks or survivors, originals hash-matched.
- T5 | S2,S5 | worker + live | embedded event-macro canary fixture with
  positive control; rerun startup matrix; AutoKeys stays unsupported
  (no input synthesis). **BLOCKED-with-evidence 2026-10-17 (L14)**: every
  body-bearing synthesized form-text framing was rejected by LoadFromText
  (scode -2146826160 "errors while importing"); the placeholder-only
  framing imports but is vacuous, and the harness correctly refused it.
  Ground-truth search found none (local Northwind carries 0 forms; no
  Office sample/template .accdb/.accdt on this host). Disposition: the
  embedded event-macro vector stays explicitly UNQUALIFIED in the coverage
  matrix — never claimed suppressed. Reopenable when an operator exports a
  real form with an embedded event macro (SaveAsText) as ground truth;
  harness archived in
  `odd/evidence/documentation-v2-embedded-blocked-20261017.zip` (42 text
  files, 73,339 bytes, SHA-256 `c37f76401d29bf850cef5fb195b3a271877a4915c9290c6c2088db029173a0d4`).
  Tier 2 proceeds; AutoKeys remains unsupported by policy (no input).
- T6 | S3,S4 | worker | Tier 1 DAO inventory (Containers + startup
  properties) behind default-off args; v1 byte-identity pins. Delivered
  `2615178`: `include_app_logic` (CLI `--include-app-logic`, MCP param,
  default False) routes CLI/MCP→runtime→worker→service; five additive
  categories (form/report/macro/module inventories + allowlisted startup
  properties) via DAO only with per-fact states and engine-level method
  metadata; ODBC reports unavailable; default-off byte-identity pinned at
  five seams (110 focused tests, ruff clean, pyright 0/0/0). Surface note:
  parent expanded the delegated surfaces mid-task with
  documentation_runtime.py + its test (mechanically required by R2).
- T7 | S2,S3,S4 | worker | Tier 2 reader using T2 protocol + T3–T5 gates:
  offline copy, ForceDisable, opt-in source, caps, catalogue facts. Delivered
  `de714c2`: reader orchestrator with injectable COM boundary; COM session in
  `backends/access/app_logic_session.py` (architecture guard compliance —
  parent relocation after the guard caught win32com in application/);
  `include_app_logic_source` wired CLI/MCP→runtime→worker with usage-Err
  precedence; caps enforced; default-off byte-identity preserved; 2009 unit
  tests green, ruff clean, pyright 0/0/0. Live qualification deferred to T9.
- T8 | S3 | worker | rendering catalogue sections, prompts, spec sync (R3).
  Delivered `a33fb5b`: "Application logic" sections for both report kinds,
  catalogue-only rendering with evidence-ID + sha256 references (never
  inlined source), visible limitation/coverage lines for degraded states;
  prompts updated; spec §8 implemented-vs-not-qualified matrix with the
  T3/T4/T5 archive references; README row extended. Parent closed the
  agreement-9 gap the worker surfaced: VBA module facts now always carry
  UTF-8 sha256 + bytes (validator + reader + tests), raw source still
  opt-in. Byte-identity and fingerprint pins green; full suite 2020
  passed / 3 skipped.
- T9 | S4,S5 | parent + independent verify | final gates: qualify.py,
  opt-in integration, independent review, coverage matrix docs. COMPLETED
  2026-10-18 (L18): qualify exit 0 (ruff/pyright/unit 2020 passed/budget);
  live CLI round trip on a startup-fixture copy — inspect `--include-app-logic
  [--include-app-logic-source]` status complete with all catalogue facts
  observed (3 macros + VBA module with always-digest, 3 references) and
  render producing catalogue-only Markdown with zero source inlining; live
  e2e fixtures suite 34 passed (v1 unregressed); originals hash-unchanged,
  zero process/lock residue. The live gate caught and fixed three real
  defects (`af03b5f`): `hWndAccessApp` is a method on the dynamic dispatch;
  CurrentProject AccessObject collections are 0-based; per-property DAO
  isolation so one unreadable property cannot erase the catalogue (absent
  startup properties are omitted, not failed). Independent verifier
  `mvdg53l3-f-5nx0`: ACCEPT-with-advisories, zero blockers; A1 stale spec
  wording fixed in this commit; A3 recorded: production scratch defaults to
  the system temp with `ACCESS_MCP_DOC_SCRATCH_DIR` override (the D:
  convention was experiment-scoped); A4: the branch carries 14 commits
  809ffe3..af03b5f. Live evidence archived in
  `odd/evidence/documentation-v2-live-final-20261017.zip` (6 JSON files,
  11,410 bytes, SHA-256 `be42383cf938e79e9546c272dd48c03d26fd221391540bbe8f3b4ece4530b743`).
  FEATURE COMPLETE on `feat/documentation-v2-app-logic`; push/PR remain the
  user's decisions.

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
- L6 (2026-10-17): user authorized the live feasibility test: "Yes, do it".
  Experiment only; no production implementation, commits, or push authorized
  by this reply. Scratch root: `db/fixtures/e2e-v2-spike-20261017-db3efa7e`.
  DAO-only preflight observed the Scripts/Modules inventories and successfully
  created/read/deleted `StartupForm` on a disposable copy (original had no
  StartupForm property); this does not yet establish Access startup behavior.
  Parent pre-launch audit corrected fail-open process ownership and vacuous
  verdict gates in the scratch harness. First launch exited 20 during fixture
  creation: DAO 5003292, "Syntax error in field definition." Zero matrix cells
  ran; canonical hashes unchanged; no Access process remained. Preserved the
  full attempt under `attempt-1-ddl-failure`; bracketed the scratch-only
  reserved `[Note]` column. Second build passed DAO DDL but exited 20 on
  "CoInitialize has not been called" before Access creation; preserved it
  under `attempt-2-com-init-failure`. Balanced a new COM apartment around
  the Access build phase (DAO had already uninitialized its own apartment)
  and launched the fresh build/matrix. These are harness-preparation failures,
  not startup-vector results. Results pending at that point; see L7.
- L7 (2026-10-17): live matrix completed exit 0, fixture-scope PASS;
  independent verifier `mvd7n3md-3-u5h5` accepted with no blockers and the
  limitations recorded above (Engram #9957). User: "Review the delivered
  subagent output and continue." Parent verified observations, archived
  text evidence/harness, and removed the exact disposable scratch root.
  Tier 2 planning remains viable; general macro-action non-execution,
  References, content reads, and owner-death cleanup remain unqualified.
  No production implementation, commit, or push.
- L8 (2026-10-17): user: "yes" to authorizing the additional isolated
  non-VBA AutoExec marker-row test. Scope: a new disposable D: fixture with
  no VBA callbacks; AutoExec invokes a saved append query to a local canary
  table. Three low-security positive controls must insert the known row;
  three ForceDisable runs must leave the DAO-observed row set unchanged
  to support this recipe. Preserve exact exits/streams, dialogs, captured
  process identities, hashes, and cleanup evidence. No Trust Center changes,
  external queries, production changes, commit, or push. Scratch root:
  `db/fixtures/e2e-v2-spike-nonvba-20261017-90be035e`. Prepared the harness
  from the prior verified archive, added independent pre/post DAO row
  observers, and observed 18 synthetic verdict checks pass. Pure-macro
  fixture: `CanaryRows`, `qryAppendCanary`, and AutoExec's sole OpenQuery
  action; no VBA, StartupForm, RunCode, SetWarnings, or automatic dialog
  acceptance. Live matrix launched; results were pending at that point.
- L9 (2026-10-17): follow-up completed exit 0, 143.7 seconds, six runs:
  non-VBA OpenQuery canary inserted exactly one expected row in all three
  low-security controls and zero rows in all three ForceDisable runs.
  Independent verifier accepted fixture-scope PASS (Engram #9960), with
  remaining claims boundaries recorded above. User: "Review the delivered
  subagent output and continue." Parent archived the text evidence,
  removed the exact scratch root, and recorded the next planning steps.
  No production implementation, commit, or push.
- L10 (2026-10-17): user: "Execute the recommended order" — the six-phase
  remediation order is authorized as implementation. Branch
  `feat/documentation-v2-app-logic` from `main` @ `7a51681`. ODD tracking
  added (Specs S1–S5, contract, Tasks T1–T9). Work-unit commits on the
  feature branch; push/PR remain the user's decisions.
- L11 (2026-10-17): T1 completed in `3b33b61` (repairs `809ffe3`'s
  overclaim: the atomic Specs/contract/Tasks edit had been rejected and only
  the L10 log line landed). T2 delivered in `12965a8`: worker ack frame
  protocol + runtime kill net + fail-closed `process_identity.py`
  (taskkill /F /PID only, never /IM); parent re-observed 41 passed,
  ruff clean, and reviewed kill-net placement (after child reap, pre-spawn
  baseline, refusal never kills, pinned error text byte-identical).
  Slice boundary recorded: commits 809ffe3+3b33b61 = tracking/docs slice;
  12965a8 = T2 code slice. Next: T3 containment live research gates
  (Job Object for COM-launched MSACCESS; crash/deadline/supervisor-death
  matrix on scratch copies).
- L12 (2026-10-17): T3 GATE PASSED. Attempt-1 crashed on a harness defect
  (Thread subclass attribute `_stop` shadowed `threading.Thread._stop()`;
  renamed, traceback capture added — Engram #9966). Attempts 2-3 evidence
  already showed correct production behavior; only verdict predicates failed
  (forced-kill lock residue is contract-bounded to the disposable copy; the
  pinned crash message never claims a grace; the lock list carries bare
  filenames globbed from the copy's own directory). Attempt-4: PASS exit 0,
  9/9 cells, both job modes PROVEN including supervisor-death containment
  via kill-on-close job; kill-net crash/hang reaps verified; graceful cells
  issued no kill; zero owned survivors; 11 canonical hashes unchanged; no
  MSACCESS residue at close. Archived
  `documentation-v2-containment-20261017.zip` (101 text files, 133,512
  bytes, sha d5eb52eb…f81) including all failed attempts; exact scratch
  roots removed after verification. Design consequence for T7: BOTH layers
  are available — Job Object kill-on-close for supervisor death AND the T2
  PID-scoped kill net for deadline/crash; the reader will register both.
- L13 (2026-10-17): T4 GATE PASSED (exit 0, 9/9 cells, 13/13 checks,
  116.8s). References: readable under ForceDisable with 1-based
  enumeration and separated Count/Item diagnostics — the archived
  0x800A01C4 error is confirmed as the spike probe's 0-based indexing
  defect, not a ForceDisable denial. SaveAsText: form/report/macro all
  export under ForceDisable (UTF-16-LE BOM; form 5,130 B; report 156,382 B;
  macro captured earlier). Trust: host has VBProject trust enabled
  (trust_enabled_observed=true, settings_changed=false); the D4 refusal
  path moves to T7 unit fakes. Archived
  `documentation-v2-content-reads-20261017.zip` (sha 7d4006a6…e64);
  scratch removed with zero locks/survivors; 11 canonical hashes matched.
  The conditional low-security positive control was unnecessary because
  the ForceDisable exports themselves are the positive reads (non-vacuity
  via DAO fixture-inventory proof). Next: T5 embedded event-macro canary.
- L14 (2026-10-17): T5 BLOCKED-with-evidence, disposition accept-blocked
  pending optional operator ground truth (plan-110/L25 precedent). Attempt-1
  failed on a stale-fixture DAO 5003045; attempt-2 (with traceback capture)
  exposed the real blocker: the 5-candidate embedded-macro form-text ladder —
  E0 placeholder-only imports and round-trips, every body-bearing framing
  E1-E4 is rejected by LoadFromText with "Microsoft Access encountered
  errors while importing frmCanary" (scode -2146826160). A placeholder-only
  event would make the suppression test vacuous, so the build stopped rather
  than fall back to VBA/expressions. Ground-truth probe on a Northwind COPY
  under ForceDisable: 0 forms exist locally (data-only fixture); bounded
  Office/user-template search found no .accdb samples or .accdt templates.
  Northwind hash unchanged; no processes/locks left; harness + both attempt
  evidence archived (sha c37f7640…0d4). Coverage consequence: embedded
  event-macro suppression is recorded UNQUALIFIED, not claimed; T7 proceeds
  with the qualified vectors (AutoExec RunCode→VBA, OnLoad expression→VBA,
  AutoExec OpenQuery non-VBA) and the T3/T4 containment/read gates.
- L15 (2026-10-17): T6 delivered in `2615178` (RED 43 failures → GREEN
  110 focused tests; two dispatch rounds after the parent expanded the
  allowed surfaces with the runtime facade + its test, mechanically
  required by R2 and within the authorized scope). Tier 1 ships regardless
  of Tier 2 per L5. Remaining: T7 Tier 2 reader (both containment layers,
  References 1-based, SaveAsText, trust-refusal unit fakes, embedded-macro
  vector recorded unqualified), T8 rendering/spec, T9 final gates.
- L16 (2026-10-17): T7 delivered in `de714c2`. Worker delivered the reader
  with fakes-only strict TDD (RED 20 failures → GREEN 144 focused). Parent
  corrections folded in: (a) two strict fakes in test_odbc_inspection_port.py
  refreshed for the T6/T7 facade kwargs (a T6 regression its focused set
  missed); (b) the architecture guard caught win32com in application/ — the
  production COM session moved to backends/access/app_logic_session.py with
  VBA_TRUST_PREREQUISITE/VbaTrustRefusedError re-exported through the
  application seam; (c) ruff D docstrings added for the backends module.
  Final gates: full unit suite 2009 passed / 3 skipped, ruff clean, pyright
  0/0/0 on changed modules. Remaining: T8 rendering catalogue sections +
  prompts + spec sync; T9 live qualification gates + independent review +
  coverage matrix + README.
- L18 (2026-10-18): T9 COMPLETED and the feature closed. qualify exit 0;
  full unit 2020 passed / 3 skipped; live opt-in round trip complete and
  catalogue-only; live e2e 34 passed; three live-only defects found+fixed
  (`af03b5f`); independent review ACCEPT-with-advisories with zero
  blockers (A1 fixed here; A2 is this entry; A3/A4 recorded in the T9
  row). All nine tasks closed (T5 blocked-with-evidence by design).
  Runtime qualification remains fixture-scope on the measured Access 16.0
  host per the claims boundary; embedded event macros UNQUALIFIED;
  AutoKeys unsupported. Branch `feat/documentation-v2-app-logic` is 16
  commits ahead of `origin/main` (unpushed `7a51681` + 15 feature
  commits `809ffe3..ea4ef81`), NOT pushed — merge/push/PR are the
  user's decisions.
- L17 (2026-10-17): T8 delivered in `a33fb5b` (worker RED 8 → GREEN;
  parent added the agreement-9 RED test `KeyError: 'sha256'` → fix:
  always-digest VBA module facts over UTF-8, validator allowed-keys
  updated; round-trip test caught the validator mismatch before commit).
  Recorded decision: app-logic sections render for BOTH report kinds when
  app-logic evidence exists; er without app-logic stays byte-identical.
  Remaining: T9 final gates (qualify.py, live opt-in integration on
  fixtures, independent review, coverage matrix honesty rows).
- L19 (2026-10-18): post-completion adversarial security code review
  (user: "Did you review it?"). Native assess remains structurally
  unavailable (RDD off -> terminal stop; inspect confirmed the 29-path
  surface). Full line-level review executed by independent reviewer
  `mvdstwc5-g-dygm` over origin/main..HEAD: **zero blockers, 9
  advisories**. Verified clean: original-never-opened, ack-before-open,
  security readback gate, PID-scoped kill net (no /IM, baseline/creation/
  image refusals, deadline-path Access reap), IPC frame anti-spoofing,
  flag-precedence Err, evidence ingress validation + cell escaping, cap
  arithmetic, af03b5f residual-indexing sweep. Advisories recorded:
  A1 verify->taskkill PID-recycle TOCTOU (ms window, inherent); A2 job
  assignment lacks creation re-verification (fixable: re-probe creation
  before OpenProcess); A3 tasklist substring liveness parse (fail-safe
  direction); A4 pre-cap memory materialization (deadline-bounded); A5
  job-failure proceeds with warning (documented contract-6 tension);
  A6 scratch default system temp (env override documented); A7 scratch
  residue after hard kill (copy-bounded); A8 agreement-4 scrub wording vs
  raw opt-in source (v1 include_query_sql precedent); A9 PATH-resolved
  tools + cheap inline pid guard in read_process_identity. Pre-merge
  hardening candidates: A2, A9 (small), A3 (medium). Merge decision is
  the user's.
