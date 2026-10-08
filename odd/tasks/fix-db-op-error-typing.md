# ODD Feature: database.py operation-path error typing

## Goal

Close the recorded follow-up from fix-dao-error-unification: the ~30
operation-path handlers in application/database.py catch generic
Exception and return bare `_err(str(e))`, losing the typed
DaoOperationError, labeling operations inconsistently, and relying on
backends to scrub passwords at raise time. Give every operation-path
envelope typed dispatch plus boundary scrubbing, via one shared helper.

## Tasks

- [x] T1 RED tests (tests/unit/test_database.py): (a) adapter op raising
  errors.DaoOperationError -> message startswith "DAO operation failed:";
  (b) generic Exception containing "PWD=..." -> envelope scrubbed at
  boundary (no PWD=, no secret); (c) plain generic Exception -> message
  preserved verbatim, no false DAO prefix
- [x] T2 GREEN: module-level `_operation_error(e)` helper in database.py
  (strip_password + DaoOperationError isinstance dispatch); rewire the
  generic operation-path `except Exception` sites to it; keep custom
  handlers (connect block, NotImplementedError branch, preconditions,
  best-effort :122) unchanged
- [x] T3 Verification: focused pytest + full gate qualify.py
- [x] T4 Docs sync (docs/02 boundary resolved, refs re-verified) +
  ledger + work-unit commit

## Constraints

- Stacked on fix/dao-error-unification (791eb03); kernel typed class is
  the semantic prerequisite.
- Zero expected breakage of existing tests (mocks use generic Exception
  with exact messages; strip_password is a no-op for PWD-free text and
  no test raises DaoOperationError through operations today). Any break
  must be reported with cause before adapting.
- Edit surfaces: src/ms_access_mcp/application/database.py,
  tests/unit/test_database.py only.
- Other application modules (automation, maintenance, migration,
  db_admin, db_export) keep their handlers; kernel-level helper reuse
  is a possible later refactor, not this feature.
- Work-unit commit(s) on fix/db-op-error-typing; merge/push user calls.

## Evidence

- 33 `except Exception` in database.py (grep count 2026-10-08);
  representative generic sites :223, :287, :466, :588, :651.
- Custom handlers to preserve: connect :195-200 (strip + mask),
  execute_action_sql typed branch :611-615, best-effort :122 (Plan 068).
- test_database.py: 64 tests; assertions :184/:202/:215 exact generic
  messages, :475 "boom" in message, :238-239 connect scrubbing already
  covered. No DaoOperationError raised in operations today.
- strip_password patterns: PWD=, Password=, Jet OLEDB:Database Password=
  (errors.py:10-31).

## Evidence (delivery)

- T1+T2 via gentle-ai-worker (surfaces: database.py, test_database.py).
  RED: 2 of 3 new tests failed (typed prefix, boundary scrubbing); the
  third (plain message verbatim) passed at baseline as expected-behavior
  regression guard — honestly noted, not forced RED. GREEN: focused 78
  passed; ruff clean. Helper at database.py:47-52; 30 handler sites
  rewired; `grep -c '_err(str(e))'` = 0 remaining. Custom handlers
  preserved (connect :212-216, NotImplementedError branch, best-effort
  :132-140 Plan 068, preconditions). Diff: database.py 78 lines, tests +46.
- T3 via gentle-ai-verify: qualify.py exit 0 (ruff OK, pyright 0 errors,
  unit 1666 passed / 3 skipped — includes the 3 new tests, budget PASS
  core 21). Full unit suite separate run exit 0. No unexpected mutations.
- T4: docs/02 synced (helper evidence + surfacing paragraph with new
  boundary = other application modules; scrubbing refs 47-52/212-216;
  write-policy range 501-1000; raw-SQL 614-617; limit 573; export 1006/1021;
  connections 108/132-140; connect_ref 573/1094-1122; hard-reject 1094-1100;
  claim 2 extended). All refs re-verified by grep after edit.

## Status: CLOSED (all 4 tasks done; merge/push remain user decisions)
