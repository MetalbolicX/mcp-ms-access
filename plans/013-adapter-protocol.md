# Plan 013: Type the adapter seam and centralize _ensure_adapter

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> If anything in the "STOP conditions" section occurs, stop and report —
> do not improvise. When done, update the status row for this plan in
> `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2178509..HEAD -- src/ms_access_mcp/application/automation.py src/ms_access_mcp/application/maintenance.py src/ms_access_mcp/application/versioning.py src/ms_access_mcp/application/database.py src/ms_access_mcp/application/protocols.py`
> On any mismatch with the "Current state" excerpts, treat as STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `2178509`, 2026-09-09

## Execution mode

**Direct edit (typing refactor).** The existing unit suites (fake adapters
in `test_application_*.py`) pin the connect/reconnect behavior, and pyright
is the primary gate for the typing change. Strict TDD does not apply — no
runtime behavior changes. SDD is not warranted.

## Why this matters

Every application use case injects its backend adapter as `Any`
(`application/automation.py:28,40`, `maintenance.py:39,43`,
`versioning.py:35,39`, `database.py:77`), even though the repo already has a
Protocol idiom (`application/protocols.py`, `backends/protocols.py`, and the
consumer-Protocol precedent from commit `ef43cec`). Result: fake/real
adapter drift is caught only at runtime, and pyright cannot check ~18
`self._adapter.<method>` call sites. Additionally, `_ensure_adapter()`
(reuse-or-reconnect via `getattr(self._adapter, "connected", False)`,
`db_path` guard, lazy `BackendSelector.get_adapter(...)`) is duplicated
near-identically in three modules (`automation.py:48`, `maintenance.py:51`,
`versioning.py:43`) — connect-retry semantics can silently diverge.

## Current state

- Pattern excerpt (`application/automation.py:28-48`; maintenance/versioning
  are the same shape):

```python
def __init__(self, adapter: Any = None, ...) -> None:
    self._adapter: Any = adapter
    ...
def _ensure_adapter(self):
    if getattr(self._adapter, "connected", False):
        return None
    if not self._db_path:
        return err("...")   # exact message varies per module — preserve each
    ...lazy BackendSelector.get_adapter...
```

- `_ensure_adapter` is invoked via `if err := self._ensure_adapter():`
  ~18 times across the three modules (8/2/8). The per-module error message
  strings DIFFER — do not unify messages; only unify the mechanism.
- Precedent to mirror: commit `ef43cec` added consumer-specific Protocols
  (`application/protocols.py`) and changed `AccessAdapter` from
  `_dispatcher: Any` to `AccessDispatcherProtocol`. Match that style:
  structural Protocol, minimal method set actually used by callers.
- `backends/db_operations.py:9` and `backends/schema_inspector.py:9` also
  take `dispatcher: Any` — secondary; include only if the primary change is
  green (Step 4).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| App tests | `uv run pytest tests/unit/test_application_automation.py tests/unit/test_application_maintenance.py tests/unit/test_application_versioning.py tests/unit/test_database.py -q` | all pass |
| Full unit | `uv run pytest tests/unit/ -q` | all pass |
| Types | `uv run pyright` | 0 errors |
| Lint | `uv run ruff check .` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/application/protocols.py`
- `src/ms_access_mcp/application/automation.py`
- `src/ms_access_mcp/application/maintenance.py`
- `src/ms_access_mcp/application/versioning.py`
- `src/ms_access_mcp/application/database.py` (only the `_get_adapter` Any
  annotations at :77 and :179)
- Step 4 only: `src/ms_access_mcp/backends/db_operations.py`,
  `src/ms_access_mcp/backends/schema_inspector.py`

**Out of scope**:
- Changing connect/reconnect BEHAVIOR (messages, retry policy, lazy
  BackendSelector timing) — mechanism only.
- `backends/access.py`, `backends/dao.py`, `backends/odbc.py`,
  `backends/ado.py`, `backends/dispatcher.py` type-ignore cleanups.
- `mcp_tools/**`, `cli.py`.
- Constructor injection redesign (a fuller DI refactor is deferred; this
  plan types the seam that exists).

## Git workflow

- Branch: `plan/013-adapter-protocol`
- Commit per step; e.g.
  `refactor(application): consumer AdapterProtocol for use-case adapter seam`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Define the consumer Protocol

In `application/protocols.py`, add `class AdapterProtocol(Protocol):`
declaring exactly the members the three use-case modules actually call on
`self._adapter` (discover with
`grep -n "self._adapter\." src/ms_access_mcp/application/*.py` and union the
attribute/method names: at minimum `connected`, `connect`, `disconnect`,
plus the operation methods each module uses). Follow the existing Protocol
style in that file (commit `ef43cec`). If the union is large, prefer three
narrow per-module protocols (`AutomationAdapterProtocol`, etc.) over one
fat protocol — match the consumer-specific precedent.

**Verify**: `uv run pyright src/ms_access_mcp/application/protocols.py` → 0 errors

### Step 2: Replace Any at the seam

In each of `automation.py`, `maintenance.py`, `versioning.py`: change
`adapter: Any = None` / `self._adapter: Any` annotations to the protocol
type (with `| None` where None is a real state). In `database.py`, change
`_get_adapter(...) -> Any` at :77 and :179 to the protocol. Fake adapters
in tests are structural — they should satisfy the Protocol without edits;
if pyright flags a fake, extend the FAKE (add the missing stub member), not
the protocol.

**Verify**: `uv run pyright` → 0 errors;
`uv run pytest tests/unit/test_application_automation.py tests/unit/test_application_maintenance.py tests/unit/test_application_versioning.py tests/unit/test_database.py -q` → all pass

### Step 3: Centralize _ensure_adapter

Extract the shared mechanism into one helper in `application/protocols.py`
(or a new `application/_adapter_lifecycle.py` if protocols.py is purely
type declarations — match the existing file's character):

```python
def ensure_adapter(adapter, db_path, *, not_connected_error: str): ...
```

parameterized by the per-module error message (preserved verbatim) and
returning the existing `Err|None` convention. Replace the three copies with
calls. Behavior must be identical: same lazy import of BackendSelector, same
`getattr(..., "connected", False)` check order, same messages.

**Verify**: full gate —
`uv run pytest tests/unit/ -q` → all pass (unchanged count);
`uv run ruff check .` → exit 0; `uv run pyright` → 0 errors

### Step 4 (optional, only if Steps 1-3 green): backend dispatcher annotations

Change `backends/db_operations.py:9` and `backends/schema_inspector.py:9`
from `dispatcher: Any` to the existing dispatcher protocol from
`backends/protocols.py`. If pyright reports ANY new error here, revert this
step and note it in the plan row — do not expand scope.

**Verify**: `uv run pyright` → 0 errors; `uv run pytest tests/unit/ -q` → all pass

## Test plan

- No new tests: behavior is pinned by the existing fake-adapter suites.
- The pyright run IS the primary verification for this plan — 0 errors
  before and after, with the `Any` annotations gone.
- Verification: `uv run pytest tests/unit/ -q` → all pass, unchanged count.

## Done criteria

- [ ] `grep -n "adapter: Any\|_adapter: Any\|-> Any" src/ms_access_mcp/application/*.py`
      returns no matches for the adapter seam
- [ ] `_ensure_adapter` defined exactly once; three call sites
- [ ] `uv run pyright` → 0 errors
- [ ] `uv run pytest tests/unit/ -q` exits 0 with unchanged test count
- [ ] `uv run ruff check .` exits 0
- [ ] Per-module `_ensure_adapter` error messages unchanged (`git diff`
      shows message strings moved/parameterized, not rewritten)
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The union of adapter members is so large (> ~25 methods) that a consumer
  Protocol becomes a copy of the concrete adapter — that means the use cases
  are a pass-through layer and the plan needs redesign, not typing.
- Fakes in tests CANNOT satisfy the protocol structurally (e.g. they rely on
  dynamic `__getattr__`) — report; do not loosen the protocol to `Any`.
- `_ensure_adapter` copies turn out to have behavioral differences beyond
  the message string (different reconnect order, extra side effects) —
  preserve each locally and report instead of unifying.

## Maintenance notes

- The new Protocol is the contract MCP/CLI fakes must honor; when adding
  adapter methods, update the protocol and let pyright find the fakes.
- Reviewers should scrutinize: that Step 3 preserved the lazy
  `BackendSelector` import timing (module-level import would re-introduce
  COM import cost at startup — the reason the lazy pattern exists).
- Deferred: full constructor-injection DI for use cases (would let tests
  drop the lazy-connect path entirely); the duplicate `DatabaseUseCases`
  wiring in `mcp_tools/core.py:27`.
