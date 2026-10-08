# ODD Feature: DAO error unification (kernel DaoOperationError)

## Goal

Resolve recorded discrepancy #2 (docs/02 claim 2): the kernel
`errors.DaoOperationError` has no raise site while `backends/dao.py:93`
defines a live same-named plain Exception. Unify on the kernel class and
give DAO connect failures a typed, honestly-labeled envelope.

## Tasks

- [x] T1 RED tests: class identity (backends.dao.DaoOperationError IS
  errors.DaoOperationError) + typed catch in _adapter_lifecycle
  (DaoOperationError from connect -> "DAO operation failed:", not
  "Backend unavailable:")
- [x] T2 GREEN: dao.py imports kernel DaoOperationError and deletes the
  local class (5 raise sites unchanged, same constructor signature);
  _adapter_lifecycle gains a DaoOperationError except before generic
- [x] T3 Verification: focused pytest + full fast gate qualify.py
- [x] T4 Docs sync: docs/02 hierarchy row + claim 2 + flow; docs/01 dao.py
  line refs; ledger + work-unit commit

## Constraints

- Source change authorized by user ("Ok, lets do it", Option A).
- Test-first: observe RED before GREEN.
- Operation-path typing in database.py handlers is OUT of scope (possible
  follow-up); connect-path labeling is in scope.
- test_backend_dao.py imports from backends.dao must keep working via
  re-export; do not edit that file unless the import breaks.
- Boundaries contract: backends may import kernel errors (legal direction).
- Work-unit commit(s) on fix/dao-error-unification; push remains user call.

## Evidence

- Discrepancy recorded: docs/02-contracts.md claim 2 (commit 2bec675/0658390).
- Kernel class: errors.py:102 (message, cause, __cause__), since b679995.
- Local class: dao.py:93-100, raises at :152,187,440,489,860 (scrubbed at
  489/860), since 4f8cbe0.
- Connect path: _adapter_lifecycle.py:69-78 three excepts all -> "Backend
  unavailable: {e}"; NOT_CONNECTED_ERROR constant at :23 unaffected.
- Automation tests asserting "Backend unavailable" (test_application_
  automation.py:458,475) raise generic exceptions -> unaffected by typed catch.
- Out-of-scope note: database.py operation handlers (except Exception at
  :195..651) not retyped in this feature.

## Evidence (delivery)

- T1+T2 delegated to gentle-ai-worker (surfaces: dao.py,
  _adapter_lifecycle.py, test_adapter_lifecycle.py, test_models_errors.py).
  RED observed: 2 failed (test_dao_operation_error_is_shared_with_kernel,
  test_lazy_adapter_dao_connect_error_has_typed_message), 106 passed.
  GREEN: 108 passed focused; ruff clean. Diff: dao.py +1/-10 (kernel import,
  local class removed, raise sites untouched), _adapter_lifecycle.py +4/-1
  (typed except before generic), test_adapter_lifecycle.py +25 (+26 stat).
  Deviation: noqa N813 for lowercase test aliases (ruff), no behavior change.
- T3 via gentle-ai-verify: qualify.py exit 0 — ruff OK, pyright 0 errors,
  unit 1663 passed / 3 skipped, budget PASS (core 21). Full unit suite
  separate run: exit 0, same counts. No unexpected mutations.
- T4: docs/02 updated (unified hierarchy row with new lines dao.py:143,178,
  431,480,851; typed surfacing + honest database.py boundary; scrubbing refs
  480/851; TOP/slice refs 352-362/392-393; claim 2 resolved); docs/01
  dao.py:113-151. New line refs verified by grep after edit.

## Status: CLOSED (all 4 tasks done; merge/push remain user decisions)
