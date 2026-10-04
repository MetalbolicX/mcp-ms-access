# Plan 085: Production versioning wiring (backend delegation, real exports, module round-trip)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/application/versioning.py src/ms_access_mcp/backends/versioning.py src/ms_access_mcp/backends/access/__init__.py src/ms_access_mcp/application/protocols.py tests/unit/test_versioning_backend.py tests/unit/test_application_versioning.py`
> Plans 082 will already have changed `mcp_tools/versioning.py` (the `path`
> parameter) — expected drift; this plan starts below the wrapper layer.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/082-*.md (MCP tools connected and taking `path`)
- **Category**: bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

The complete `VersioningBackend` implementation (`backends/versioning.py`)
is referenced only by tests — production never instantiates it. The
production adapter (`AccessAdapter`) implements only form/report text
export/import, so every macro/query/module operation and all batch
operations (`export_all`, `import_all`, `compare`, `export_schema_ddl`)
raise `AttributeError` (surfacing as `Err`). Worse, the single-object
`export_object` use case returns an `output_path` it never writes — the
tool advertises "Export … to a text file" and no file appears. And once the
backend is wired, a latent asymmetry becomes reachable: modules export as
`modules_*.bas` while `import_all`/`compare` glob only `*_*.txt`, so VBA
modules silently never round-trip.

## Current state

- `src/ms_access_mcp/application/protocols.py` — the versioning adapter
  protocol (~lines 72–86) plus the kind-method surface the use case
  requires (macro/query/module text methods).
- `src/ms_access_mcp/application/versioning.py` — the use case. Verified:
  `VersioningUseCases` with `backend="com"` reaches `AccessAdapter`;
  `export_object` (~lines 106–110) returns `output_path` in the `Ok` data
  without writing any file. All batch methods (`export_all_versioning`,
  `import_all_versioning`, `compare_versioning`, `export_schema_ddl`) are
  declared in the protocol and missing on `AccessAdapter`.
- `src/ms_access_mcp/backends/access/__init__.py` — `AccessAdapter` has
  ONLY `export_form_to_text`/`import_form_to_text` and
  `export_report_to_text`/`import_report_to_text` (~lines 660–664,
  720–724). No `__getattr__` delegation exists anywhere in `src/` (audit
  grep).
- `src/ms_access_mcp/backends/versioning.py` — the complete
  `VersioningBackend` (~line 23): SaveAsText/LoadFromText per kind,
  `export_all_versioning` / `import_all_versioning` /
  `compare_versioning` / `export_schema_ddl`. Instantiated ONLY in
  `tests/unit/test_versioning_backend.py`. Known sub-facts: modules write
  `modules_{n}.bas` (~lines 351–355, default `module_ext=".bas"`);
  `import_all_versioning`/`compare_versioning` glob `{sub}_*.txt`
  (~lines 410, 456). Temp-file cleanup in `finally` EXISTS (~lines 79–80,
  103–104) — an earlier audit claim that temps leak was refuted; the
  UTF-16 encoding choice remains deliberately deferred (ledger).
- `src/ms_access_mcp/cli.py` — `versioning *` subcommands (~line 1738+)
  construct `VersioningUseCases(_backend="com")` — same gap.
- Filename sanitization (~lines 245–246) maps illegal chars to `_` —
  collision risk is known-deferred (do not redesign the scheme here).
- Conventions: use cases resolve adapters through `AdapterLifecycle`
  (`application/_adapter_lifecycle.py`); COM work happens on the
  dispatcher's STA thread (`backends/dispatcher.py`); path-guard for any
  written output via `safety.is_path_allowed(..., must_exist=False)`
  (plan 043 wiring precedent in maintenance/versioning use cases).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused unit | `uv run pytest tests/unit/test_versioning_backend.py tests/unit/test_application_versioning.py tests/unit/test_mcp_versioning.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Budget | `uv run python scripts/tool_budget.py` | exit 0; versioning count unchanged (6) |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/backends/access/__init__.py` (delegate versioning surface)
- `src/ms_access_mcp/application/versioning.py` (`export_object` file write)
- `src/ms_access_mcp/backends/versioning.py` (import/compare glob fix for `.bas`)
- `tests/unit/test_application_versioning.py`,
  `tests/unit/test_versioning_backend.py`, `tests/unit/test_backends_access.py`

**Out of scope**:
- UTF-16 temp encoding and LoadFromText format semantics (known-deferred;
  requires live Access verification).
- Filename collision-proofing (deferred).
- `mcp_tools/versioning.py` (plan 082 owns the wiring/`path` parameter).
- Any new MCP tool or schema.

## Git workflow

- Branch: `plan/085-versioning-wiring` from `main` (after 082).
- Conventional commits: `feat(versioning): wire VersioningBackend into AccessAdapter`.

## Steps

### Step 1: RED — conformance test

Add to `tests/unit/test_backends_access.py` (or a new
`tests/unit/test_versioning_conformance.py`) a conformance test: construct
the production `AccessAdapter` with a fake dispatcher (pattern:
`test_backend_object_mirror.py`) and assert EVERY method the versioning
protocol + kind-method surface requires exists and accepts the documented
signature (introspect with `inspect.signature`). Iterate the protocol's
method list so future additions are auto-checked.

**Verify**: RED — missing macro/query/module/batch methods listed.

### Step 2: GREEN — delegate to `VersioningBackend`

In `AccessAdapter.__init__`, compose `self._versioning =
VersioningBackend(self._dispatcher)` (it already takes a dispatcher — read
its constructor and match). Add explicit delegating methods for every
missing protocol method (explicit methods, not `__getattr__` — the
architecture guard and reviewers prefer explicit surfaces). Each delegate
must run COM work through the dispatcher exactly as `VersioningBackend`
already does internally — do not move work off the STA thread.

**Verify**: conformance test GREEN; `uv run pytest tests/unit/test_backends_access.py -q` → pass.

### Step 3: RED→GREEN — `export_object` writes the file

- RED: `tests/unit/test_application_versioning.py` with a fake adapter
  returning exported text — assert `export_object(...)` writes the bytes
  to `output_path` (tmp_path fixture; output must be inside
  `allowed_dirs`) and returns them in `Ok`.
- GREEN: in `application/versioning.py`, after obtaining the text, guard
  the output path with `is_path_allowed(output_path, self._allowed_dirs,
  must_exist=False)` (match the existing maintenance pattern) and write
  via `pathlib` (the repo's enforced convention). On guard failure return
  `Err("Output path not allowed")`.

**Verify**: new test GREEN; path-guard rejection case also tested.

### Step 4: Module round-trip glob fix (strict TDD)

- RED: `tests/unit/test_versioning_backend.py` — with a fake exporter
  writing `modules_1.bas`, `import_all_versioning` and
  `compare_versioning` must include modules.
- GREEN: extend the import/compare globs to include the module extension
  (e.g. `{sub}_*.txt` plus `modules_*.bas`, driven by the existing
  `module_ext` constant — avoid hardcoding `.bas` twice).

**Verify**: focused tests GREEN.

### Step 5: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0; `uv run ruff check .` → 0; `uv run python scripts/tool_budget.py` → exit 0.

## Test plan

- Conformance test (protocol-driven, auto-expanding).
- `export_object` writes + path-guard rejection.
- Module round-trip (fake exporter/importer).
- CLI `versioning` tests (`tests/unit/test_cli_versioning.py`) refreshed if
  they pinned `AttributeError`-derived `Err`s — document each refresh.
- Live SaveAsText/LoadFromText behavior is NOT verifiable on fakes — record
  the operator gate (run `versioning export-all` round-trip on a real
  database) in the plan row; do not fake it.

## Done criteria

- [ ] Protocol conformance test passes against the production `AccessAdapter`
- [ ] `export_object` writes a real file inside allowed dirs (test)
- [ ] `import_all`/`compare` include `.bas` modules (test)
- [ ] Unit suite + qualify + budget green; `plans/README.md` row updated
  (including the operator round-trip gate result or its pending status)

## STOP conditions

- If `VersioningBackend`'s constructor or COM call pattern does not fit
  `AccessAdapter`'s dispatcher ownership (double dispatch, thread
  mismatch), stop and report the conflict — do not restructure the
  dispatcher.
- If writing `output_path` from the use case breaks the CLI's formatting
  contract (CLI currently only formats the returned string —
  `cli.py:712-715`), stop and report; the CLI display may need a
  coordinated tweak.
- If the UTF-16 temp format blocks module export on fakes in a way the
  plan cannot test honestly, keep the unit on file-level behavior and mark
  the COM part as operator-gated.

## Maintenance notes

- The architecture guard (`tests/unit/test_architecture_boundaries.py`)
  must stay green — `backends/access` may import from `backends/`
  siblings; application may not import backend internals directly.
- When the deferred UTF-16 decision is revisited, the conformance test and
  round-trip glob tests here are the safety net.
