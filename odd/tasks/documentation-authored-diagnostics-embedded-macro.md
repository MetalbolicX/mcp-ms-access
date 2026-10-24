# Documentation follow-ups: authored diagnostics and the embedded event-macro vector

Feature branch `feat/documentation-authored-diagnostics-embedded-macro` from `main` `c579c79`.
Develops the justified optional follow-ups and reopens T5 (embedded event macros, L14).
Merge/push/PR remain the user's decisions.

## Specs

- S1 — "Develop the optional follow upset and the macro vector. Justify why they are needed": every
  pending follow-up gets a written justification; justified items are developed, unjustified items
  are dropped with the reason recorded here, never silently.
- S2 — Authored detail: an exception type the project defines may carry authored, compile-time
  literal detail that the documentation diagnostic formatter renders as `<Class>: <detail>`. Runtime
  text (`str(error)`, `args`, OS strings, file names, database values) never passes. The detail is
  accepted only when it is exactly `str` (`type(detail) is str`, the same exact-type rule as OS codes).
  A chained cause (`__cause__`) is rendered through the existing class-only formatter, one level only.
- S3 — Adoption: the six `ContainmentError` sites in `documentation_app_logic.py`, the
  `InspectionCapabilityUnavailableError` sites in `documentation_inspect.py`, and the worker request
  validation in `documentation_worker.py` carry their authored detail through S2, so the user-facing
  message (runtime `documentation_runtime.py:506`) and evidence facts say which check failed.
- S4 — Durable pin: an `int` subclass overriding `__format__`, `__str__`, and `__repr__` as an OS
  code never reaches a reason, whichever formatting path renders it.
- S5 — Property-omission freshness: `database_property_omissions()` describes only the latest
  property read; a failed read after a successful read with omissions yields no omission warnings.
- S6 — Embedded event-macro canary: build a disposable fixture whose startup form carries a real
  `OnLoadEmMacro` block (grammar from Access SaveAsText ground truth) running `qryAppendCanary`,
  prove non-vacuity with a positive control (the macro fires on an unprotected open), then run the
  protected Tier 2 open and record whether the canary row appears. Safety contract unchanged:
  original never opened by `Access.Application`, disposable copies, `AutomationSecurity=3` read back
  before opening, owned PID+creation kill only, no Trust Center changes or synthetic input.
- S7 — Docs: spec §8 diagnostics bullet, the coverage matrix, and README reflect S2 and the S6
  outcome, with the evidence archive path and SHA-256.

## Tasks

- T1 | S2,S3,S4,S7 | worker + parent, strict TDD; Risk item 2 → independent verify PASS | commit: `f441778` `fix(documentation): restore authored diagnostic detail without runtime text` (S4 pin rides here: it is a formatter test)
- T2 | S5 | worker, test-first | commit: `fix(documentation): reset property omissions at each read`
- T3 | S6,S7 | worker live run (D: scratch `db/fixtures/embedded-macro-requal/`, removed) + parent spot check; QUALIFIED (fixture scope) | commit: `docs(odd): re-qualify the startup-form embedded event macro with evidence`
- T4 | S1 | close: qualification, assess/review, ledger | commit: —

## Justification (S1)

Developed:
- T1 — needed. The strict pass (`0b76964`) made every documentation failure class-only, including
  authored safety messages. `documentation_runtime.py:506` surfaces the worker error verbatim, so a
  containment failure now reads `ContainmentError` without saying which of the six containment checks
  (AutomationSecurity arm/readback, identity, copy, …) failed, and ODBC capability facts read
  `InspectionCapabilityUnavailableError` instead of the authored reason. Authored literals carry no
  runtime data, so restoring them recovers diagnosability without reopening the leak class.
- T2 S4 — needed, cheap: `type(value) is int` is pinned only through `__format__`; a later refactor to
  `str(code)` or `repr` would bypass the current test. One test pins the rule against every path.
- T2 S5 — needed, cheap: freshness currently depends on the failure path in another function
  discarding warnings (`documentation_inspect.py:1061-1068`); resetting at the read makes the
  invariant local (advisories R3-stale-omissions-after-failed-reread, R3-failure-path-omission-warnings-untested).
- T3 — needed most. Embedded event macros are the dominant automation style in real Access
  databases (Microsoft templates use them throughout), and a startup form's `OnLoad` macro fires on
  open without VBA, so `AutomationSecurity=3` (which disables VBA) is not by itself evidence that
  they are suppressed. It is the only open execution vector in the Tier 2 safety claim. The L14 block
  was a fixture-syntax hypothesis error, not an Access limit: all five candidates used invented
  keywords (`Begin Embedded Macro`, `EmbeddedMacro = Begin`, `Begin Macro`, bare `Begin`), and Access
  rejected each at the keyword ("Expected: 'Begin'. Found: Embedded."). Ground truth now available:
  Microsoft Q&A ("each embedded macro starts with text like ' EmMacro = Begin'") and the `accdt`
  SaveAsText parser fixture (`OnClickEmMacro = Begin`, `Version =196611`, anonymous `Begin` action
  blocks with `Action`/`Argument`, a closing `Comment ="_AXL:…"` block).

Dropped (with reason):
- R3-filename2-branch-untested, R3-scratch-warning-path-leak, documentation_app_logic.py:394-398 leak
  site — superseded: `_path_free_os_reason` was removed by `0b76964`; all four boundaries are class-only.
- R3-image-name-host-path / `ntpath.basename` portability — not needed: the product is Windows-only
  (Access COM); `Path().name` on Windows already splits both separators.
- R3-classname-metaclass-note — not reachable: class names come from in-process classes, never data.
- R3-exact-int-drops-subclass-codes — intended behavior (S4 pins it), not a defect.
- Coverage nits (R3-digest-size-error-precedence, R3-form-branch-message-coverage,
  R3-render-digest-gap-contract, R3-strict-debug-suppression, R3-lister-adapter-coverage,
  R3-evidence-limit-interaction, R3-regex-import-dependency, R4-001) — each asks for a test of behavior
  already pinned at another seam or a branch unreachable from the public interface; adding them grows
  the suite without catching a new regression class (prior decision: skip coverage nits).
- R1-sanitized-detail-dependency, R3-detail-sanitization-unproved — closed by `8eaadb6`/`0b76964`
  (cleanup detail is class-only and pinned).

## Log

- L1 (2026-10-23): user: "Develop the optional follow upset and the macro vector. Justify why they are needed"
- L2 (2026-10-23): exploration — L14 archive shows all five embedded-macro candidates (E0-E4) used
  invented block keywords; Access errors name the keyword (`errors*.txt`). Public ground truth found
  for the real `<Event>EmMacro = Begin` grammar. Runtime `:506` confirmed worker error is user-facing.
- L3 (2026-10-23): T1 (S2/S3/S7 diagnostics bullet) implemented inline with strict TDD. RED observed
  twice: (1) collection `ImportError: cannot import name 'AuthoredDiagnostic'` once the tests existed;
  (2) after the class alone was added, 4 formatter assertions failed with class-only output (authored
  detail not rendered, cause not appended, cause's own detail not rendered, `__context__` not
  rendered). GREEN: `test_documentation_evidence.py` 63 passed, `test_documentation_app_logic.py`
  104 passed, `test_documentation_inspect.py` + `test_odbc_inspection_port.py` 92 passed,
  `test_documentation_worker.py` 44 passed; focused documentation suites 436 passed; full
  `pytest tests/unit -q` 2244 passed / 3 skipped (baseline 2231 / 3, +13 = the 13 new T1 tests);
  `ruff check` (all touched files, then repo-wide) "All checks passed!"; `pyright` 0 errors / 0
  warnings with the venv interpreter (`--pythonpath .venv/Scripts/python.exe`; the bare invocation
  reports 26 pre-existing `reportMissingImports` for `mcp`/`pydantic` because it resolves no venv).
  LiteralString enforcement proven, not assumed: temporarily replacing one `ContainmentError`
  literal with an f-string produced `documentation_app_logic.py:461:36 - error: Argument of type
  "str" cannot be assigned to parameter "detail" of type "LiteralString"`, reverted to 0 errors.
  Adoption: `AuthoredDiagnostic` in `documentation.py` (`__init__(detail: LiteralString)`); the six
  `ContainmentError` raises are now pure literals (`AutomationSecurity=3` written literally, readback
  mismatch says "read back a different value" instead of `{readback!r}`, cause kept via `from`);
  `InspectionCapabilityUnavailableError(AuthoredDiagnostic, RuntimeError)` with `Final[LiteralString]`
  module constants, `dict[str, LiteralString]` for `_ODBC_UNAVAILABLE`, and a literal
  `list_container_objects` refusal (no `{container_name}`); new
  `WorkerRequestError(AccessMcpError, AuthoredDiagnostic)` for child-side `decode_request` with the
  backends spelled out and one literal per `tables`/`queries` key. Deliberately unchanged: the
  over-cap `decode_request` message (needs the runtime byte budget), `encode_request`/supervisor-side
  parsing, and `documentation_worker.py` `AccessMcpError(opened.message)` (runtime backend text may
  hold the db path) - a new PRESERVE test pins that last one class-only. Spec: §8 diagnostics bullet
  retitled "class-only, never runtime exception text" plus a new "Authored detail is the one
  exception, and it is literal-only" bullet. Commits: not created by this writer (git is
  parent-owned); the two commands are in the Tasks commit column.
- L4 (2026-10-23): T2 (S4/S5) implemented. S4 was test-only as planned: `_HostileFormattingCode`
  (an `int` subclass overriding `__format__`, `__str__`, and `__repr__`) used as errno and as
  winerror, 3 params, assert the class-only fallback and absence of the hostile path - all 3 passed on
  the base behavior, so no RED was fabricated (the rule already held; only `__format__` was pinned
  before). S5 RED observed: `test_a_failed_property_reread_discards_the_previous_omission_warnings`
  failed with `assert ['1 database property could not be read ... (readable names: Connection)'] == []`
  (a failed re-read left the earlier read's omission warning behind); GREEN after resetting
  `self._property_omissions = []` at the start of `_read_database_properties`: 4 omission tests
  passed, `test_documentation_inspect.py` + `test_documentation_evidence.py` 121 passed, full
  `pytest tests/unit -q` 2248 passed / 3 skipped, `ruff check` clean, `pyright` 0 errors.
- L5 (2026-10-23): parent review of the worker diff. Found the formatter read `error.authored_detail`
  directly, so an `AuthoredDiagnostic` subclass that skipped `__init__` made the formatter itself
  raise at a failure boundary; RED `AttributeError` at `documentation.py:194`, fixed with `getattr`
  (+1 test). Independent verifier (mvlxavqd-2-23q6): S2, S3, S4, S5, S7 MET, blockers none; 13-case
  leak hunt (str-subclass detail, hostile cause fields and codes, authored cause, `__context__`,
  `__notes__`, skipped `__init__`, OSError+authored MRO) found no leak; base-vs-change differential
  showed only the intended S3/S5 differences. Advisories: in-process reassignment of
  `authored_detail`/`__name__` is the trust domain that writes the literals (no production path
  does it); pre-existing Job-assignment skip reason carries OS process metadata (unchanged). Its two
  probe pins added (`__notes__` never rendered; hostile int codes on a chained cause → class-only).
  Corrected counts: full unit suite 2251 passed / 3 skipped after the parent's +3 tests (L3/L4 counts
  were mid-implementation snapshots). S4 commits with T1 (formatter test); T2 commits S5 alone.
  Risk: item 2 (sanitization boundary: what text may leave the process) → verified independently.
- L6 (2026-10-23): committed T1 `f441778` (617+/71-, 10 files; one behavior kept whole because a
  high-risk unit gets its own commit — over the ~400-line guideline, disclosed) and T2 `fad30e7`
  (33+, S5 alone). T1 alone (S5 stashed) 2250 passed / 3 skipped; both 2251 / 3. Slice boundary:
  the hook's 9,783-line count is measured from `origin/main` and includes the 41 unpushed v2 commits
  already merged into local `main`; this branch's slices are T1 and T2 (and T3's evidence commit).
- L7 (2026-10-23): T3 (S6) live re-qualification — **QUALIFIED (fixture scope)**. The 2026-10-17
  harness was extracted to `db/fixtures/embedded-macro-requal/` (paths re-pointed, safety envelope
  unchanged) and the invented-keyword ladder E0-E4 was replaced by the real SaveAsText grammar:
  F0 placeholder-only diagnostic, F1 `OnLoadEmMacro = Begin` property block (`Version =196611`,
  `ColumnsShown =8`, one anonymous `Action ="OpenQuery"` / `Argument =` block, and a
  `Comment ="_AXL:…"` block carrying `For="frmCanary" Event="OnLoad"` wrapped onto the live-captured
  80-character quoted continuations), F2 the same AXL on one line, F3 without the AXL block, F4 with
  `ColumnsShown =0`. Insert order is macro-block-then-placeholder because the archived evidence
  shows a property assignment placed after the placeholder is rejected (`Expected: 'End'. Found:
  EmbeddedMacro.`, E2) while a block opener there is accepted (E0). All five candidates imported and
  read back `"[Embedded Macro]"`; F1 was installed by the stricter first-match bar (import +
  design-view readback + intact form + `OnLoadEmMacro`/`OpenQuery`/`qryAppendCanary` in Access' own
  re-export, matrix-build roundtrip sha256 `03ac8e6a…`; installed form text `37d614b3…`). F1 and F2
  re-exported byte-identically (Access
  normalised the single-line AXL to the wrapped form), F3 kept legacy rows only, F4 kept
  `ColumnsShown =0`. `--self-check` clean (28 synthetic verdict cases); `--build-only` ok (its
  fixture and evidence preserved as `fixture-buildonly/` and `evidence/build_only_*`); `--all`
  PASS exit `0` in 176.6 s: POS 3/3 exactly `[{ProbeID:1, Label:'EMBEDDED_MACRO'}]`, FD 3/3 `[]`,
  and the new PRODUCT cell (the shipped `AppLogicReader` behind `read_tier2_content`, own
  `AutomationSecurity=3` readback 3, own identity proof and Job Object, `scratch_root` under the run
  directory) 3/3 `[]` on the copy the reader itself opened (retained after it closed and reaped
  Access), with the source copy still untouched and the trigger present on every retained copy.
  Safety: baseline and final `MSACCESS.EXE` PID sets both empty, zero survivors, zero kills (every
  reap graceful), zero `.laccdb` residue, canonical hashes unchanged (11 files), zero observed
  dialogs. Evidence archive `odd/evidence/documentation-v2-embedded-macro-requal-20261023.zip`
  (text only): 177 files, 739,941 bytes uncompressed, 203,217 zip bytes, sha256
  `939dbfc64a98805d4b286c6d94d568eca5b96dd67ea00aeeb4b7d1e1284f2e1e`. S7 doc updates
  (spec §8, coverage matrix, README) are not part of this entry.
- L8 (2026-10-23): parent spot check of T3 — archive SHA-256 `939dbfc6…e1e` recomputed and matched;
  no `MSACCESS.EXE` running; zero `.laccdb`/`.ldb` in the scratch root; `evidence/summary.json`
  `status PASS`, per-run `rows_after_count` POS 1/1/1 (exact canary row), FD 0/0/0, PRODUCT 0/0/0,
  every `security_readback` equal to the requested level. Correction to L7: on the PRODUCT runs the
  harness could NOT read the source copy afterwards (`rows_after_source_copy_read_ok false`); the
  verdict rests on the retained file the reader itself opened (`is_the_product_opened_file true`,
  readable, 0 rows), which is the observation that decides suppression. S7: spec §8.2 records the
  re-qualified gate beside the 2026-10-17 block, §8.3 now claims only the measured trigger (startup-
  form embedded `OnLoad` macro QUALIFIED; embedded macros on other events/controls UNQUALIFIED), and
  README names the embedded `OnLoad` macro under startup-code non-execution. Scratch root removed.
