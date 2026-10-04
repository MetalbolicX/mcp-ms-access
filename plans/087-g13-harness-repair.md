# Plan 087: Repair the G13 kill-mid-write chaos gate and its stale documentation

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- scripts/g13_kill_mid_write.py src/ms_access_mcp/mcp_tools/core.py README.md`
> Plans 078–086 may have changed `mcp_tools/core.py` (helpers) — the tool
> NAMES and ARGUMENT SCHEMAS are what this plan depends on; re-verify them
> against the live registry, not against this file.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW (harness and docs only — no product code)
- **Depends on**: none (but land after 078 for a truthful budget gate)
- **Category**: tests / dx
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

`scripts/g13_kill_mid_write.py` is the crash-durability chaos gate behind
plan 047's recorded "3/3 CLEAN" evidence — but it invokes MCP tools that do
not exist (`open_database`, `list_tables`), sends `insert_data` with the
wrong argument key (`values` instead of `data`) and without the required
`path`/`confirm`, never checks responses for errors, and — decisively —
returns exit 1 only for `FATAL` rounds, so rounds that FAIL (including
failing before any write happens) exit 0 as `[PASS]`. A harness that never
successfully writes can classify itself CLEAN. The durability evidence is
unreliable until the harness is repaired, and README claims about CI
coverage of this test are wrong.

## Current state

- `scripts/g13_kill_mid_write.py` — audit-verified specifics:
  - ~line 320: calls tool `open_database` — no such tool; the real tool is
    `connect_access` (`src/ms_access_mcp/mcp_tools/core.py:477-478`).
  - ~lines 335–339: `insert_data` with `{"table": ..., "values": ...}` —
    the typed signature is `insert_data(path, table, data, confirm)`
    (`mcp_tools/core.py:371-384`).
  - ~line 429: calls `list_tables` — the real tool is `get_tables`
    (`core.py:116`).
  - ~lines 330–342: responses discarded; no `isError`/`success` checks.
  - ~lines 594–599: `_main` returns 1 only on `FATAL`; `FAIL` rounds print
    `[PASS] all rounds recovered or retryable` and exit 0.
  - Verdict logic (~lines 458–495) classifies on file state alone, so
    "never wrote anything" can yield CLEAN.
- `git log -S` (audit): `open_database` was never an MCP tool name in
  `src/` history — the harness was born incompatible with the registry.
- `plans/README.md` row 047 records "g13 harness 3/3 CLEAN" — provenance
  now unverifiable; do not delete history, but mark it superseded.
- `README.md` ~line 157 — claims the kill-mid-write test "runs 5 rounds in
  CI" with a stray trailing ` ``` ` fence; CI runs `pytest tests/unit/`
  only (`.github/workflows/ci.yml:51`).
- Sibling scripts are CORRECT references: `scripts/stdio_smoke.py`,
  `g10`, `g11`, `g12` use `get_tables`+`path` and check responses.
- Conventions: MCP stdio JSON-RPC framing helpers already exist inside
  g13 and the sibling smoke scripts; reuse them.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Conformance test | `uv run pytest tests/unit/test_g13_conformance.py -q` | pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Harness syntax | `uv run python -m py_compile scripts/g13_kill_mid_write.py` | exit 0 |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `scripts/g13_kill_mid_write.py`
- `tests/unit/test_g13_conformance.py` (create)
- `README.md` (the ~line 157 claim + stray fence)
- `plans/README.md` (row 047 supersession note — one line)

**Out of scope**:
- Any product source under `src/`.
- Other gates (g10–g12, step5) — verified correct.
- Re-running the live chaos matrix (operator decision; see Done criteria).

## Git workflow

- Branch: `plan/087-g13-harness-repair` from `main`.
- Conventional commits: `fix(scripts): g13 chaos gate uses real tool contracts and fails on failed rounds`.

## Steps

### Step 1: Extract the call plan into a module-level constant

Refactor g13's MCP invocations into an inspectable constant (e.g.
`TOOL_CALLS` or per-phase small structures naming tool + argument keys),
keeping the JSON-RPC helpers as-is. This is what the unit test pins; no
behavior change yet.

**Verify**: `uv run python -m py_compile scripts/g13_kill_mid_write.py` → exit 0.

### Step 2: Conformance unit test (RED for the current names)

Create `tests/unit/test_g13_conformance.py` loading the script via
importlib (pattern: `tests/unit/test_qualify_script.py`) and building the
REAL core tool registry in-process (`from ms_access_mcp.server import
build_server` with the core profile, no client connection — same approach
as `scripts/tool_budget.py`). Assert every g13 call's tool name exists in
the registry and its argument keys ⊆ the registered schema properties
(required params present). Expected: RED on `open_database`, `list_tables`,
`values`.

**Verify**: `uv run pytest tests/unit/test_g13_conformance.py -q` → RED as described.

### Step 3: Fix the calls (GREEN)

- `open_database` → `connect_access` (with the `path` argument the tool
  takes — read the live schema).
- `list_tables` → `get_tables(path=...)`.
- `insert_data`: `table`, `data` (not `values`), `path`, `confirm=True`.
- After EVERY call: check the JSON-RPC response for `isError` / payload
  `success: false` and treat it as round failure (not silent continue);
  specifically, the insert must SUCCEED before the kill is scheduled —
  a round that never wrote must be classified `FAIL`, never `CLEAN`.

**Verify**: conformance test GREEN; `py_compile` exit 0.

### Step 4: Verdict/exit-code honesty

- `FAIL` rounds (including bootstrap-open failures that are not
  retryable-per-existing-policy) must make `_main` return non-zero, or at
  minimum print an explicit `[FAIL]` summary line the qualify/step5
  wrappers can detect — match g10's failure semantics (read
  `scripts/g10_repeated_shutdown.py` exit handling and mirror it).
- Keep `FATAL` as the harsher class; do not relax retry classification —
  only fix that FAIL ≠ PASS.

**Verify**: reading the diff, `FAIL` paths cannot reach exit 0; add a tiny unit case driving `_classify_round`/verdict function with a fake "no successful writes" state asserting non-CLEAN.

### Step 5: Documentation truth

- `README.md:157`: replace the CI claim with the factual statement
  (kill-mid-write is a LOCAL operator gate; CI runs the unit suite) and
  remove the stray ` ``` ` fence.
- `plans/README.md` row 047: append a one-line note — "g13 tool-name/exit
  defects found 2026-10-04 audit; 3/3 CLEAN evidence superseded until the
  repaired harness re-runs" (this is the only edit to that file besides
  your own plan row).

**Verify**: `grep -n 'runs 5 rounds in CI' README.md` → no hits; `grep -cn '```' README.md` → even count.

### Step 6: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run ruff check .` → exit 0 (scripts are lint-covered); `uv run python scripts/qualify.py` → exit 0.

## Test plan

- `test_g13_conformance.py`: registry-name/schema conformance (the exact
  class of drift that rotted this harness — it now can't recur silently).
- One verdict-function unit test: zero-successful-writes ⇒ not CLEAN.
- Live chaos run is explicitly an OPERATOR gate: the executor does not run
  Access; record "awaiting operator re-run" in the plan row.

## Done criteria

- [ ] Conformance test passes against the live registry and runs in CI's unit scope
- [ ] g13 calls `connect_access`/`get_tables`/`insert_data(path, table, data, confirm)` with response checks
- [ ] FAIL rounds cannot exit 0 (unit-pinned)
- [ ] README CI claim corrected; stray fence gone; plans/README row 047 annotated
- [ ] Unit suite + qualify green; plan row updated with operator-gate status

## STOP conditions

- If the live registry's tool schemas differ from what this plan lists
  (drift after 078–086), the conformance test IS the authority — fix the
  harness to the registry, not the reverse.
- If changing FAIL exit semantics breaks `scripts/step5_qualify.py`'s
  expectations of g13 (read its gate invocation), stop and report the
  coupling before editing step5 (out of scope here).
- If a real chaos run is demanded as part of this plan, stop — that is an
  operator decision requiring Access.

## Maintenance notes

- The conformance-test pattern (script call plan vs live registry) is
  reusable for g10–g12 if they ever gain new calls.
- When the operator re-runs the chaos matrix, record rounds/seed/result in
  the plan row exactly like plan 047 did — that is the replacement
  evidence.
