# Plan 016: Functional-composition refactor (Result combinators, error policy, adapter mirror)

> **Executor instructions**: Follow this plan phase by phase, step by step. Run
> every verification command and confirm the expected result before moving to
> the next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 980efcc..HEAD -- src/ms_access_mcp/errors.py src/ms_access_mcp/backends/access.py src/ms_access_mcp/application/database.py src/ms_access_mcp/application/automation.py src/ms_access_mcp/mcp_tools/ tests/unit/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1 (Phases 1–2) / P2 (Phases 3–4)
- **Effort**: L
- **Risk**: MED
- **Depends on**: 012 (errors.py owns the envelope; DONE), 010 (typed-error
  pattern exists; DONE). 015 should land AFTER this plan (it is cheaper once
  `access.py` shrinks again).
- **Category**: tech-debt
- **Planned at**: commit `980efcc`, 2026-09-10

## Execution mode

**Phased, mode per phase:**

| Phase | Content | Mode |
|---|---|---|
| 1 | `Result.map/bind/fold` + consolidate `_result_to_dict` | Direct edit + new unit tests |
| 2 | Uniform typed error policy in `access.py` mutations | **Strict TDD** (error-contract change, bool API preserved — same regime as plan 010) |
| 3 | Purity warts in `application/` + `cli_format.py` | Direct edit (behavior-preserving) |
| 4 | Collapse form/report setter+control mirror (~16 methods) | Characterization tests FIRST, then direct edit (009 + 014 hybrid) |

No SDD: no user-facing surface changes. The CLI parser-tree registry rewrite
is explicitly NOT in this plan — it stays deferred per `plans/README.md`
(needs per-subparser characterization first).

## Why this matters

The codebase has a shared `Ok`/`Err` envelope (`errors.py`) but uses it as a
passive container: every consumer branches with `isinstance`, and the same
fold is hand-written three times in `mcp_tools/`. Error handling at the COM
adapter is inconsistent — `_rename_object`/`_delete_object` classify and log
(plan 010) while `_create_object`, `open_form`, `close_form`, and
`set_report_property` still swallow failures silently into `False`/`{}`.
And `backends/access.py` (2,225 lines) still carries ~16 report-only methods
(`1262–1626`) that mirror their form twins structurally — the table-driven
dispatch that already fixed this problem at the application layer
(`application/automation.py:139–209`) was never ported to the adapter.

This plan makes failure a composable value, makes the error policy uniform,
and collapses the mirror — smaller adapter, one place per semantic, and a
`Result` API future code can build on instead of re-branching.

## Current state

### Files and roles

- `src/ms_access_mcp/errors.py` — shared kernel: envelope + exception
  taxonomy. Lines 13–38 (verbatim):

```python
@dataclass(frozen=True)
class Ok:
    """Success result envelope."""

    data: Any = None
    rowcount: int = 0


@dataclass(frozen=True)
class Err:
    """Error result envelope."""

    message: str


Result = Ok | Err


def ok(data: Any = None, rowcount: int = 0) -> Ok:
    """Construct a success result."""
    return Ok(data=data, rowcount=rowcount)


def err(message: str) -> Err:
    """Construct an error result."""
    return Err(message=message)
```

  Note: `application/database.py` re-exports the envelope (plan 012 shim);
  sibling application modules import as `from ..errors import err as _err`.
  Do not break the shim or the aliases.

- `src/ms_access_mcp/mcp_tools/core.py:56-60` — one of THREE identical
  `_result_to_dict` helpers (verbatim):

```python
def _result_to_dict(result: Ok | Err) -> dict[str, Any]:
    """Convert Ok/Err to use-case dict."""
    if isinstance(result, Ok):
        return {"success": True, "data": result.data, "rowcount": result.rowcount}
    return {"success": False, "error": result.message}
```

  The other two live at `mcp_tools/maintenance.py:21` and
  `mcp_tools/automation.py:19`. Verify all three are identical before
  consolidating (STOP if any differs).

- `src/ms_access_mcp/backends/access.py` — COM adapter, 2,225 lines:
  - `_create_object` (448–467): `except Exception: return False` — swallowed,
    untyped, unlogged. Only per-property rejections are logged (462–463).
  - `_rename_object`/`_delete_object` (471–519): the plan-010 regime —
    heuristic on message/`0x8004` → `AccessObjectNotFoundError` /
    `AccessBackendError`, caught, logged, collapsed to `False`.
  - `open_form` (418) / `close_form` (435): swallowed to `False`/`{}` with
    zero logging.
  - `set_form_property` (581) vs `set_report_property` (1262): same skeleton,
    different COM tokens — `OpenForm(name, 1)` vs `OpenReport(name, 1)`;
    `app.Forms(name)` vs `app.Reports(name)`; `Close(2, name, 1)` vs
    `Close(4, name, 1)`. `set_report_property` swallows at 1282–1283.
  - The mirror block `1262–1626`: `set_*_property(ies)`,
    `get/set_*_control_property(ies)`, `add/remove_*_control`,
    `set_*_section_property(ies)` — form side mostly unprefixed
    (`set_control_property`, `add_control`), report side prefixed
    (`set_report_control_property`, `add_report_control`); see the name maps
    in `application/automation.py:139-191` for the exact public surface.
  - `_OBJECT_KINDS` registry at 206–209 (from plan 009):
    `{"form": {"collection": "AllForms", "ac_type": 2}, "report": {...}}` —
    the extension point for Phase 4.
  - Plan-009 delegator style to match (521–531):
    `def create_form(...): return self._create_object("form", ...)`.
  - Loop-scoped lazy import at 358: `from ms_access_mcp.models import
    FormInfo, ReportInfo` INSIDE the collection walk. Repo convention: lazy
    imports stay method-scoped (never hoist to module top — plan 009
    out-of-scope rule), but loop-body is gratuitous.

- `src/ms_access_mcp/application/database.py`:
  - `export_data` (781–829): builds SQL inline at 808 —
    `f"SELECT * FROM [{table_or_query}]"` — bypassing
    `application/sql_builder.py:11` `build_select`.
  - `query_data` (444–451): `inspect.signature` introspection per call.
- `src/ms_access_mcp/safety.py:44` — `allowed_dirs_from_env()` (public since
  plan 012), reads `ACCESS_MCP_ALLOWED_DIRS`.
- `src/ms_access_mcp/cli_format.py:16` — duplicates that env read instead of
  importing from `safety`.
- `src/ms_access_mcp/application/automation.py:139-209` — the in-house
  exemplar for Phase 4: `_FORM_METHODS`/`_REPORT_METHODS` dicts + `_dispatch()`.

### Conventions to match

- `from __future__ import annotations` at module top; ruff clean; pyright
  0 errors; lazy model imports stay method-scoped in `access.py`.
- Fakes/dispatcher doubles: model after `tests/unit/test_backend_access.py`.
- Strict TDD (Phase 2): RED (quote the failing output in the step report) →
  GREEN (minimal) → REFACTOR. No implementation before its failing test.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Backend tests | `uv run pytest tests/unit/test_backend_access.py -q` | all pass |
| Result tests | `uv run pytest tests/unit/test_result_composition.py -q` | all pass (Phase 1+) |
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | 0 errors |

## Scope

**In scope** (the only files you should modify):

- `src/ms_access_mcp/errors.py` (Phase 1: add methods to `Ok`/`Err` only)
- `src/ms_access_mcp/mcp_tools/core.py`, `maintenance.py`, `automation.py`
  (Phase 1: consolidate `_result_to_dict`)
- `src/ms_access_mcp/backends/access.py` (Phases 2, 3, 4)
- `src/ms_access_mcp/application/database.py` (Phase 3)
- `src/ms_access_mcp/cli_format.py` (Phase 3)
- `tests/unit/test_result_composition.py` (new, Phase 1)
- `tests/unit/test_backend_access.py` (Phase 2 tests; Phase 4
  characterization may go in a new `tests/unit/test_backend_object_mirror.py`)

**Out of scope** (do NOT touch, even though they look related):

- `cli.py` — the parser-tree registry rewrite stays deferred (README
  "Findings considered and rejected/deferred").
- `backends/dao.py`, `backends/versioning.py`, `backends/protocols.py`,
  `application/protocols.py`, `_adapter_lifecycle.py`.
- `application/sql_builder.py` — read-only reference; only `database.py`
  changes to call it.
- Generic-typing redesign of `Ok`/`Err` (`Generic[T]`) — keep `Any`-typed;
  pyright-0 is the gate, not elegance.
- The `application/database.py` re-export shim and `_err` aliases.
- Public method names/signatures/return types anywhere — all public
  contracts are frozen. Phase 2 preserves the `bool`/`{}` return API exactly
  as plan 010 did.
- MCP-layer structured error surfacing (deferred in README).

## Git workflow

- Branch: `plan/016-fp-maintainability`
- Commit per phase (or per step group inside a phase); conventional commits,
  e.g. `refactor(errors): add map/bind/fold to Result envelope`,
  `feat(backends): classify and log _create_object failures [plan 016]`,
  `refactor(backends): collapse form/report property mirror via kind registry`.
- Do NOT push or open a PR unless instructed.

## Steps

### Phase 1: Result composition

#### Step 1: Add `map`/`bind`/`fold` to `Ok` and `Err`

In `errors.py`, add three methods to each dataclass (keep them frozen,
`Any`-typed, docstrings one-liners):

```python
# on Ok
def map(self, fn: Callable[[Any], Any]) -> "Ok":
    """Transform success data, preserving rowcount."""
    return Ok(data=fn(self.data), rowcount=self.rowcount)

def bind(self, fn: Callable[[Any], "Result"]) -> "Result":
    """Chain a Result-returning function on success data."""
    return fn(self.data)

def fold(self, on_ok: Callable[[Any, int], Any], on_err: Callable[[str], Any]) -> Any:
    """Consume the envelope: on_ok(data, rowcount) or on_err(message)."""
    return on_ok(self.data, self.rowcount)

# on Err — all three short-circuit
def map(self, fn): return self
def bind(self, fn): return self
def fold(self, on_ok, on_err): return on_err(self.message)
```

(Use real signatures mirroring the `Ok` ones; `Callable` from `typing`.)

**Verify**: `uv run ruff check src/ms_access_mcp/errors.py` → exit 0;
`uv run pyright` → 0 errors.

#### Step 2: Unit tests for the combinators

Create `tests/unit/test_result_composition.py`. Cases (pure, no fixtures):
`map` transforms data and preserves rowcount; `map`/`bind` on `Err` return
the same `Err` (identity, not a copy of behavior — assert equality);
`bind` chains `Ok → Ok` and short-circuits `Err → Err`; `fold` picks `on_ok`
with both args and `on_err` with the message; laws spot-check:
`Ok(x).map(f).map(g) == Ok(x).map(lambda v: g(f(v)))`.

**Verify**: `uv run pytest tests/unit/test_result_composition.py -q` → all pass.

#### Step 3: Consolidate the three `_result_to_dict` helpers

First diff all three copies (`mcp_tools/core.py:56`, `maintenance.py:21`,
`automation.py:19`). If any differs beyond its name, STOP and report.
Otherwise: keep ONE canonical implementation in `mcp_tools/core.py`, built on
`fold`:

```python
def _result_to_dict(result: Ok | Err) -> dict[str, Any]:
    """Convert Ok/Err to use-case dict."""
    return result.fold(
        lambda data, rowcount: {"success": True, "data": data, "rowcount": rowcount},
        lambda message: {"success": False, "error": message},
    )
```

Replace the copies in `maintenance.py` and `automation.py` with
`from .core import _result_to_dict` (position the import to match each
module's existing import style; if this creates a circular import, STOP and
report).

**Verify**: `uv run pytest tests/unit/ -q` → all pass, same count as before;
`uv run ruff check .` → exit 0.

### Phase 2: Uniform error policy (STRICT TDD)

#### Step 4: Locate the plan-010 classification helper

Grep `access.py` for the error-classification helper plan 010 introduced in
`_rename_object`/`_delete_object` (heuristic on message text / `0x8004` →
`AccessObjectNotFoundError` / `AccessBackendError`). If it is inlined rather
than shared, first extract it into one private method (behavior-preserving,
suite green) so Phase 2 can reuse it.

**Verify**: `uv run pytest tests/unit/test_backend_access.py -q` → all pass.

#### Step 5: `_create_object` — RED then GREEN

RED: add a failing test asserting that a COM failure inside `_create_object`
(via the existing fake dispatcher) logs a warning naming the operation and
still returns `False`. GREEN: route the catch through the classifier + logger,
mirroring `_rename_object`, return value unchanged (`False`).

**Verify**: RED run quoted in the step report, then
`uv run pytest tests/unit/test_backend_access.py -q` → all pass.

#### Step 6: `open_form` / `close_form` — RED then GREEN

Same treatment: failing test (failure logged, return stays `False`/`{}` as
today), then classify+log in the catch. One commit for both.

**Verify**: RED quoted; `uv run pytest tests/unit/test_backend_access.py -q`
→ all pass.

#### Step 7: `set_report_property` (and its form twin if it also swallows)

RED: failing test that a property-write COM failure logs and preserves the
current falsy return. GREEN: classify+log. If plan 010 already covered the
form side, touch only the report side.

**Verify**: RED quoted; `uv run pytest tests/unit/test_backend_access.py -q`
→ all pass; `uv run pytest tests/unit/ -q` → all pass.

### Phase 3: Purity warts (direct edit)

#### Step 8: `export_data` uses `sql_builder.build_select`

Read `application/sql_builder.py:11`. If `build_select(<table>)` produces
byte-identical SQL to `f"SELECT * FROM [{table_or_query}]"` for a bare table
name (bracket quoting included), replace the f-string at
`application/database.py:808` with the builder call. If output differs in any
way, STOP and report — do not "fix" the builder.

**Verify**: `uv run pytest tests/unit/ -q` → all pass (export tests included).

#### Step 9: Hoist the loop-scoped import

In `access.py:358`, move `from ms_access_mcp.models import FormInfo,
ReportInfo` from inside the collection-walk loop to the top of the enclosing
method. Do NOT hoist to module top (plan 009 rule).

**Verify**: `uv run pytest tests/unit/test_backend_access.py -q` → all pass.

#### Step 10: Deduplicate the env read

In `cli_format.py:16`, replace the local `ACCESS_MCP_ALLOWED_DIRS` parsing
with a call to `safety.allowed_dirs_from_env()` (public since plan 012).
Check the two parsers agree (separator, default) before deleting the local
copy; on disagreement, STOP and report.

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0.

#### Step 11: Cache the `inspect.signature` lookup

`application/database.py:444-451`: resolve the signature once (module-level
cache or `functools.lru_cache` on a small helper) instead of per call.
Behavior-preserving.

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0.

### Phase 4: Adapter mirror collapse (characterization first)

#### Step 12: Characterization tests for the mirror

Create `tests/unit/test_backend_object_mirror.py`. Using the existing fake
dispatcher pattern from `test_backend_access.py`, write characterization
tests that capture, for BOTH kinds of each pair in the `1262–1626` block —
`set_{form,report}_property`, `set_{form,report}_properties`,
`{get,set}_{,report_}control_property(ies)`, `{add,remove}_{,report_}control`,
`set_{form,report}_section_property(ies)` — the exact `DoCmd` verb sequence:
open verb + args, live collection accessor (`Forms`/`Reports`), close
constant (`2`/`4`). These tests pin CURRENT behavior; they must pass BEFORE
any refactor and stay green unchanged AFTER.

**Verify**: `uv run pytest tests/unit/test_backend_object_mirror.py -q` →
all pass (against unmodified code).

#### Step 13: Extend `_OBJECT_KINDS` and extract shared helpers

Extend the registry at `access.py:206-209` per kind with `open_verb`
(`"OpenForm"`/`"OpenReport"`), `live_collection` (`"Forms"`/`"Reports"`),
`close_ac` (`2`/`4`). Then extract one private helper per mirror family
(`_set_object_property(kind, ...)`, `_set_object_control_property(kind, ...)`,
`_add_object_control(kind, ...)`, `_set_object_section_property(kind, ...)`,
…), resolving COM tokens from the registry. Public methods become plan-009
style 3-line delegators; public names and docstrings unchanged (mind the
asymmetric names — form `set_control_property` vs report
`set_report_control_property` both delegate to
`_set_object_control_property("<kind>", ...)`).

**Verify**: `uv run pytest tests/unit/test_backend_object_mirror.py
tests/unit/test_backend_access.py -q` → all pass, zero test edits.

#### Step 14: Full gate

**Verify**: `uv run ruff check .` → exit 0; `uv run pyright` → 0 errors;
`uv run pytest tests/unit/ -q` → all pass with the Phase-12 count + prior
count. Record `access.py` line count (expect roughly −300 to −450 from 2,225).

## Test plan

- Phase 1: new `tests/unit/test_result_composition.py` (combinator laws +
  short-circuits); consolidation verified by unchanged suite count.
- Phase 2: RED→GREEN tests in `test_backend_access.py` per mutation site
  (log asserted via `caplog`, return-value invariance asserted).
- Phase 3: no new tests; existing export/format/query suites are the pin.
- Phase 4: characterization tests written FIRST and never edited after the
  refactor; a characterization failure post-refactor = behavior change =
  revert the step.
- Optional integration confirmation (only if Access available locally):
  `uv run pytest tests/integration -m com_integration -q` → same results as
  before. Kill stray `MSACCESS.EXE` before/after (repo COM hygiene rule).

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0 (count = pre-plan count + new
      Phase 1/2/4 tests)
- [ ] `uv run ruff check .` exits 0; `uv run pyright` reports 0 errors
- [ ] `Ok`/`Err` expose `map`/`bind`/`fold`; exactly ONE `_result_to_dict`
      exists in `mcp_tools/` (`grep -rn "_result_to_dict" src/` → one def,
      two imports)
- [ ] `_create_object`, `open_form`, `close_form`, `set_report_property`
      failures are classified and logged; public return values unchanged
- [ ] `export_data` builds SQL via `sql_builder.build_select`;
      `grep -n "SELECT \* FROM" src/ms_access_mcp/application/database.py`
      returns no f-string match
- [ ] `access.py` mirror block collapsed; line count reduced ~300–450
- [ ] No public signature/name/return-type changed anywhere (`git diff`)
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The three `_result_to_dict` copies are not identical (Phase 1 Step 3).
- Importing `_result_to_dict` across `mcp_tools/` modules creates a cycle.
- `build_select` output differs from the inline f-string SQL (Step 8).
- The two `ACCESS_MCP_ALLOWED_DIRS` parsers disagree (Step 10).
- A mirror pair turns out NOT to be a true structural clone (different
  control flow or return shapes) — leave that pair and report it.
- Any characterization test (Step 12) fails after Step 13 — revert the
  extraction for that family; do not edit the test.
- A RED test in Phase 2 cannot be turned GREEN without changing a public
  return value.
- Line drift beyond ±25 lines in any "Current state" location, or a step's
  verification fails twice after a reasonable fix attempt.

## Maintenance notes

- Future consumer code should prefer `result.map/bind/fold` over
  `isinstance` branching; reviewers should reject new hand-rolled folds.
- Phase 2 makes the adapter's failure regime uniform — the still-deferred
  "MCP-layer structured error surfacing" (README deferred list) can now be
  planned on top of it.
- Phase 4's registry is the extension point for any future object kind
  (e.g. modules): one table entry, delegators follow.
- Plan 015 (ruff per-file-ignores narrowing) is cheapest AFTER this plan
  shrinks `access.py` again — resequence if 015 started first.
- The CLI registry rewrite remains deferred; its prerequisite
  (per-subparser characterization) is still unmet.
