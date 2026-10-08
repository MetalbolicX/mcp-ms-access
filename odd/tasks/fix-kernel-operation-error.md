# ODD Feature: kernel operation_error — complete application-layer contract

## Goal

Promote the envelope helper to the kernel and rewire the remaining
application modules so every operation-path error gets typed DAO
labeling and guaranteed boundary scrubbing. Closes the recorded
boundary from fix-db-op-error-typing (docs/02): automation,
maintenance, migration, db_admin, db_export, versioning (and
analysis) still catch generically.

## Design

- errors.py gains PUBLIC `operation_error(e: BaseException,
  label: str | None = None) -> Err`: scrub first (strip_password);
  label is None -> DaoOperationError gets "DAO operation failed:"
  prefix, otherwise verbatim; label given -> f"{label}: {message}"
  (custom module labels preserved, still scrubbed; the DAO typed
  label applies only where no custom label exists — documented
  trade-off).
- database.py `_operation_error` becomes an alias/identity to the
  kernel function (no behavior change).
- Bare `_err(str(e))` sites -> kernel helper. Custom-label sites ->
  helper with their existing label (observable change: scrubbing
  only; messages unchanged for PWD-free text).
- except blocks with complex logic (classification, retries,
  re-raise) are preserved as-is and reported, not force-rewired.

## Tasks

- [x] T1 RED: kernel tests in test_models_errors.py (verbatim pass-
  through, DAO label, boundary scrubbing, label param) + one module-
  level test: versioning use case with adapter raising
  errors.DaoOperationError -> "DAO operation failed:" + identity test
  database._operation_error is errors.operation_error
- [x] T2 GREEN: errors.py helper; database.py alias; rewire bare sites
  (versioning ~8, db_admin ~3, automation ~3, db_export ~2,
  maintenance ~2) and custom-label generic tails (scrubbing gain,
  messages unchanged)
- [x] T3 Verification: focused suites + full gate qualify.py
- [x] T4 Docs sync (docs/02 boundary resolved, docs/01 kernel refs)
  + ledger + work-unit commit

## Constraints

- Stacked on fix/cli-profile-help (3a85cc8); merge/push user calls.
- Existing tests expected green (no message change for PWD-free
  text); any break reported with cause before adapting.
- Kernel purity: errors.py imports nothing from layers (boundaries
  test polices).
- If a needed test file is outside the surfaces, report instead of
  editing.

## Evidence (measurements, 2026-10-08)

- Bare sites: versioning 8, db_admin 3, automation 3, db_export 2,
  maintenance 2 (grep raw-str-e). Generic-with-label catches:
  maintenance 17, migration 14, analysis 3, versioning/db_admin/etc.
- database.py helper at :47-52 re-exports kernel names already
  (import line :18 pattern, noqa F401 backward-compat convention).
- test_models_errors.py is the kernel envelope test home.

## Evidence (delivery)

- T1+T2 via gentle-ai-worker. RED observed: 6 failed (4 kernel behavior
  tests in TestOperationError, identity alias, versioning integration
  bare "vba boom"). GREEN: 328 focused passed; ruff clean; git diff
  --check clean. errors.py public operation_error at :134-140; database
  alias at :48 (identity); rewired: versioning 8 bare, automation 3
  bare, db_admin 3 bare + 1 labeled, db_export 2 bare, maintenance 2
  bare + 6 labeled, migration 5 labeled. Preserved complex blocks
  (reported): migration transfer job-state/result mutation, maintenance
  process-kill/recovery aggregation, analysis structured result
  payloads. Diff: 11 files, +104/-42.
- T3 via gentle-ai-verify: qualify.py exit 0 (ruff OK, pyright 0
  errors, unit 1673 passed / 3 skipped incl. 6 new, budget PASS core
  21); test_architecture_boundaries.py 6/6 green (kernel purity:
  errors.py imports only stdlib); full unit suite exit 0.
- T4: docs/02 boundary paragraph rewritten (all application modules
  rewired; preserved blocks listed), hierarchy row evidence + kernel
  refs updated; docs/01 kernel range 34-140.

## Status: CLOSED (all 4 tasks done; merge/push remain user decisions)
