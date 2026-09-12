# Plan 021: Re-verify the CurrentDb.Name fix and unblock plan 008

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**:
> `git merge-base --is-ancestor c955258 HEAD` (must exit 0 — if not, the
> `plan/cleanup` commit is not in your tree; STOP and report "merge plan/cleanup first").
> Then `git diff --stat c955258..HEAD -- tests/integration/test_access_adapter.py src/ms_access_mcp/backends/access.py src/ms_access_mcp/backends/dispatcher.py docs/release/0.1.0-report.md plans/README.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S (GREEN path) / M (RED path fallback)
- **Risk**: LOW (GREEN path) / MED (RED path fallback)
- **Depends on**: none technically; tree must contain commit `c955258` (branch `plan/cleanup` — merge it into `main` before executing)
- **Category**: bug (verification-first; conditional fix)
- **Planned at**: commit `c955258` (branch `plan/cleanup`), 2026-09-11

## Why this matters

Plan 008 (Windows x64 release qualification) is the only unfinished plan and is
BLOCKED on G3+G5 RED: one failing integration test,
`test_no_msaccess_orphans_after_disconnect`, reading `CurrentDb.Name` via COM.
The release report (`docs/release/0.1.0-report.md`, verdict line 7) still says
the product is un-releasable and `v0.1.0` cannot be tagged.

However, git history shows the bug was **already fixed ~3 hours after the
failing run** by commit `6e6c585` ("fix(backends): get_current_db_name() typed
accessor for apartment-bound DAO Database", 2026-09-09 22:17), and the fix was
**never re-verified**: no session re-ran the test, updated the report, or
flipped plan 008. This plan closes that loop. Expected outcome: the test
passes (GREEN path) and the remaining work is documentation + gate re-runs.
Only if it still fails does a real diagnosis/fix cycle run (RED path).

## Current state

All excerpts below are verbatim from commit `c955258` (the newest code state).

### Relevant files

- `src/ms_access_mcp/backends/access.py` — COM adapter for Access.Application; owns `connect`, `get_current_db_name`, `connect_hidden`
- `src/ms_access_mcp/backends/dispatcher.py` — STA COM dispatcher thread; `call(fn)` runs `fn` on the apartment thread that created the COM handles
- `tests/integration/test_access_adapter.py` — the failing integration test (marker `com_integration`)
- `docs/release/0.1.0-report.md` — plan 008 qualification report; verdict BLOCKED on this single failure
- `plans/README.md` — plan index; row 008 says BLOCKED

### The failing test (`tests/integration/test_access_adapter.py`)

Module markers (lines 10–16) — no class decorators:

```python
pytestmark = [
    pytest.mark.skipif(
        __import__("os").name != "nt",
        reason="Windows-only test",
    ),
    pytest.mark.com_integration,
]
```

The test (class `TestAccessAdapterHidden`, lines 34–54). **Note line 50 calls
`get_current_db_name()`, not the old `get_current_db().Name`**; the docstring
on line 35 is stale (describes the old API):

```python
    def test_no_msaccess_orphans_after_disconnect(self, temp_db_copy):
        """Integration: connect → read CurrentDb().Name → disconnect; zero orphans within 10s."""
        pytest.importorskip("win32com")
        from ms_access_mcp.backends.access import connect_hidden
        ...
        adapter = connect_hidden(temp_db_copy)
        try:
            db_name = adapter.get_current_db_name()   # line 50
            assert db_name
            assert db_name.lower().endswith(".accdb")
        finally:
            adapter.disconnect()
```

### The already-landed fix (`src/ms_access_mcp/backends/access.py`)

`connect` (lines 68–87) creates ALL COM handles on the STA thread — late-bound
`win32com.client.Dispatch`, no gencache/EnsureDispatch anywhere in `backends/**`:

```python
        def _do() -> None:
            import win32com.client
            app = win32com.client.Dispatch("Access.Application")   # late binding
            app.Visible = False
            app.OpenCurrentDatabase(target, False, password)
            self._dispatcher._access_app = app
            self._dispatcher._current_db = app.CurrentDb()         # called as METHOD
            self._dispatcher._ado_conn = app.CurrentProject.Connection
```

`get_current_db_name` (lines 104–116) routes the `.Name` read onto that same
STA thread via `dispatcher.call`:

```python
    def get_current_db_name(self) -> str:
        """... COM objects vended by Access.Application are apartment-bound to the
        dispatcher's STA thread. Reading their attributes from any other thread
        raises AttributeError (RPC_E_WRONG_THREAD surfaced by pywin32 dynamic
        dispatch). Typed accessors like this one dispatch the read onto the
        STA thread; never expose raw COM handles across threads. """
        def _do() -> str:
            return str(self._dispatcher.current_db.Name)
        return self._dispatcher.call(_do)
```

`connect_hidden` (lines 1995–2002, end of file) — plain delegation:

```python
def connect_hidden(db_path: str) -> AccessAdapter:
    """Create and connect an AccessAdapter in hidden mode.

    Returns the connected adapter. Caller must call disconnect() when done.
    """
    adapter = AccessAdapter(db_path)
    adapter.connect()
    return adapter
```

Dispatcher threading model (`src/ms_access_mcp/backends/dispatcher.py`):
`call()` at lines 83–95 enqueues `fn` on `_call_queue` and blocks on
`future.result(...)`; the `ComDispatcher-STA` thread (line 79) executes it.
`current_db` property at line 60.

### Git history (the evidence chain)

| commit | when | what |
|--------|------|------|
| `8afd916` | 2026-09-08 22:55 | introduced the test with `adapter.get_current_db().Name` (raw handle, caller thread) |
| `8ff5a38` | 2026-09-09 19:32 | release report written; G3+G5 RED against the code above |
| `6e6c585` | 2026-09-09 22:17 | **the fix**: `get_current_db_name()` typed STA-dispatched accessor |
| `2178509` | 2026-09-09 22:30 | deleted the raw `get_current_db` handle accessor entirely |

The report predates the fix by ~3 hours; its line 66 quotes
`db_name = adapter.get_current_db().Name` — code that no longer exists.

### Original failure mechanism (for the RED path, if needed)

`AttributeError: CurrentDb.Name` raised from
`.venv/Lib/site-packages/win32com/client/dynamic.py:631` — pywin32's
`CDispatch.__getattr__` signature `raise AttributeError("%s.%s" %
(self._username_, attr))`. Two candidate mechanisms:

- **(A) Cross-thread apartment violation** (the committed fix's theory):
  `RPC_E_WRONG_THREAD` during `GetIDsOfNames` on a handle touched from the
  wrong thread, surfaced as AttributeError. The `6e6c585` fix addresses this.
- **(B) Late-binding name-resolution failure**: `GetIDsOfNames` cannot resolve
  `"Name"` on the `CurrentDb`-vended DAO Database without a generated makepy
  typelib (none exists — zero `gencache`/`EnsureDispatch`/`makepy` hits in
  `src/`). If (A) is ruled out and (B) confirmed, the fix differs (see Step 3).

### Conventions that apply

- All checks run through `uv` (uv-managed Python 3.11 venv).
- COM hygiene (from `plans/README.md` execution conventions): kill stray Access
  before and after COM test runs (`taskkill /F /IM MSACCESS.EXE` — "not found"
  is fine); never mutate canonical fixtures under `tests/fixtures/` — mutating
  tests operate on temp copies (the failing test uses the `temp_db_copy`
  fixture) and the suite's G4 fixture-hygiene checks enforce this.
- Error-handling contract (plans 010/016/017): typed errors + logging, public
  `bool` APIs preserved. Any new `raise` must satisfy Ruff EM101/EM102/TRY003
  (enabled by plan 020): assign the message to a variable first, use
  `logger.error` before raising typed errors — see existing patterns in
  `src/ms_access_mcp/backends/access.py` (e.g. the plan-010 classified raises).
- Docstrings must satisfy Ruff D on `backends/**` (plan 019).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Kill stray Access | `taskkill /F /IM MSACCESS.EXE` | exit 0 or "not found" — either is fine |
| Single failing test | `uv run pytest "tests/integration/test_access_adapter.py::TestAccessAdapterHidden::test_no_msaccess_orphans_after_disconnect" -q` | `1 passed` (GREEN) or `1 failed` (RED) |
| Unit suite | `uv run pytest tests/unit -q` | all pass, 0 failed |
| G5 marker subset | `uv run pytest -q -m "com_integration or odbc_integration"` | 14 passed, 0 failed |
| G3 full suite | `uv run pytest -q` | 0 failed (2 skipped is normal) |
| Lint | `uv run ruff check .` | `All checks passed!` |
| Typecheck | `uv run pyright` | `0 errors, 0 warnings, 0 informations` |

## Scope

**In scope — GREEN path** (test already passes; docs only):

- `tests/integration/test_access_adapter.py` — fix the stale docstring on line 35 only
- `docs/release/0.1.0-report.md` — append a "Resolution" section; update the verdict and done-criteria lines
- `plans/README.md` — add/mark row 021 DONE; flip row 008 to DONE with evidence

**In scope — RED path only** (test still fails; add to the above):

- `src/ms_access_mcp/backends/access.py` — minimal fix at the accessor seam
- `src/ms_access_mcp/backends/dispatcher.py` — only if the root cause is in `call()`/handle storage
- `tests/unit/test_backend_access.py` — unit pin for the accessor (model after existing fakes in that file)

**Out of scope** (do NOT touch, even though they look related):

- Any behavior change on the GREEN path — it is documentation + verification only
- `uv.lock` (carries a deliberate uncommitted modification — preserve it exactly) and `.opencode/` (local router override — do not commit either)
- Swapping `Dispatch` → `EnsureDispatch` anywhere outside the single seam the evidence names (that is a cross-cutting change needing its own plan)
- Full 13-gate re-qualification, `v0.1.0` tagging, CI work — separate decisions after this plan
- The doctor, DAO, ADO, ODBC backends — untouched by this failure

## Git workflow

- Branch: `plan/021-currentdb-name-reverify` (from a tree containing `c955258`)
- Conventional commits, English, no AI attribution. Suggested split:
  - GREEN path: one commit `docs: re-verify CurrentDb.Name fix, unblock plan 008`
  - RED path: `fix(backends): <root cause> for CurrentDb.Name access` then the docs commit
- Do NOT push. Do NOT commit `uv.lock` or `.opencode/`.

## Steps

### Step 1: Pre-flight

1. Run the drift check from the executor block (ancestry + diff). Ancestry
   must exit 0.
2. `git status --porcelain` — expect at most ` M uv.lock` and `?? .opencode/`.
   Anything else modified: STOP.
3. `taskkill /F /IM MSACCESS.EXE` (either outcome fine).
4. Create the branch: `git checkout -b plan/021-currentdb-name-reverify`.

**Verify**: ancestry command exit 0; `git branch --show-current` → `plan/021-currentdb-name-reverify`.

### Step 2: Reproduce (the decision point)

Run the single-test command from the table. Quote the full output (pytest
tail + traceback if any) into your step report.

- **`1 passed`** → GREEN path: skip Steps 3–4, go to Step 5.
- **`1 failed`** → RED path: capture the traceback verbatim; if the error is
  still `AttributeError: CurrentDb.Name` from `dynamic.py`, proceed to
  Step 3. A *different* error is a STOP condition.

**Verify**: you have a quoted pytest result and a chosen path.

### Step 3: RED path — root cause with probes (no repo edits)

Write a probe script **outside the repo** (e.g.
`%TEMP%\opencode\probe_currentdb.py`) and run it with `uv run python` from the
repo root (so the venv resolves). Each probe opens a hidden Access session on
a **temp copy** of a fixture (never a canonical fixture path) and quits
afterwards. Run `taskkill /F /IM MSACCESS.EXE` between probes.

Probes, in order — stop at the first decisive result:

1. **V1 current shape** (mirrors the test; sanity baseline):
   `connect_hidden(tmp_path)` then `adapter.get_current_db_name()` then `adapter.disconnect()`.
2. **V2 raw STA read** (isolates the accessor): on a connected adapter,
   `adapter._dispatcher.call(lambda: str(adapter._dispatcher.current_db.Name))`.
3. **V3 early binding** (isolates late-binding resolution): fresh process;
   `app = win32com.client.gencache.EnsureDispatch("Access.Application")`;
   `app.OpenCurrentDatabase(tmp_path)`; read `app.CurrentDb().Name`; `app.Quit()`.
4. **V4 alternative source**: on a connected adapter,
   `adapter._dispatcher.call(lambda: str(adapter._dispatcher._access_app.CurrentProject.Name))`.

Decision rules:

- V2 fails like V1 → the typed accessor is not the problem; the handle or
  thread model is → examine `connect`/`dispatcher.call`; fix at that seam.
- V2 fails, V3 succeeds → mechanism (B): late-binding resolution; fix =
  obtain the name through a resolvable member (e.g. `CurrentProject.Name`, V4)
  inside the existing `_do` closure — one-line-ish change in
  `get_current_db_name`; do NOT convert the whole adapter to EnsureDispatch.
- V3 also fails → environment problem (broken Access/DAO typelib
  registration on this machine) → STOP and report; a product fix cannot help.
- V1 passes but Step 2 failed → harness/interference issue → re-run Step 2
  once after `taskkill`; if it still fails, STOP and report.

**Verify**: probe outcomes quoted verbatim; one decision rule matched and named.

### Step 4: RED path — fix under strict TDD

1. **RED (unit pin first)**: in `tests/unit/test_backend_access.py`, add a
   unit test pinning `get_current_db_name` semantics with the existing fake
   dispatcher pattern from that file: the `.Name` read must happen inside
   `call()` on the STA thread and return `str(...)`. If your fix changes the
   member read (V4 route), pin THAT member. Run it — it must fail against the
   current code (or, if purely additive, assert the new behavior). Quote output.
2. **GREEN**: apply the minimal fix named by Step 3's decision rule, at the
   accessor seam only. Respect the error contract (typed errors, EM101/EM102/
   TRY003-clean raises, D-compliant docstrings — update the docstring if the
   mechanism changes).
3. **Re-run**: the unit pin passes; the Step 2 single-test command passes.

**Verify**: `uv run pytest tests/unit/test_backend_access.py -q` → all pass
incl. the new pin; single-test command → `1 passed`.

### Step 5: Gates (both paths)

In order, cheap-first; `taskkill /F /IM MSACCESS.EXE` before each COM-touching
run (G5, G3):

1. `uv run ruff check .` → `All checks passed!`
2. `uv run pyright` → `0 errors, 0 warnings, 0 informations`
3. `uv run pytest tests/unit -q` → 0 failed
4. `uv run pytest -q -m "com_integration or odbc_integration"` → 0 failed (was the G5 gate)
5. `uv run pytest -q` → 0 failed (was the G3 gate; ~2 skipped is normal)
6. After the final run: `taskkill /F /IM MSACCESS.EXE`, wait ~10s, then
   `tasklist /FI "IMAGENAME eq MSACCESS.EXE"` → `INFO: No tasks are running...`
   (zero orphans).

**Verify**: all six quoted green; no MSACCESS.EXE remains.

### Step 6: Documentation close-out

1. `tests/integration/test_access_adapter.py` line 35 — replace the stale
   docstring with: `"""Integration: connect → read get_current_db_name() → disconnect; zero orphans within 10s."""`
2. `docs/release/0.1.0-report.md`:
   - Append a `## Resolution (<today's date>, plan 021)` section: the git
     history chain (`8ff5a38` report → `6e6c585` fix landed same night,
     never re-verified), the Step 2/5 gate outputs quoted, and (RED path
     only) the root cause + fix.
   - Update the verdict line (line 7) and the G3/G5 matrix rows and
     done-criteria rows to reflect the green re-run, referencing the
     Resolution section. Do not falsify history — keep the original findings,
     mark them superseded.
3. `plans/README.md`: mark row 021 DONE (with gate evidence); flip row 008
   from BLOCKED to DONE citing the Resolution section. Note in row 008 that
   full re-qualification/tagging remains a separate decision if the team
   wants a fresh 13-gate cycle.

**Verify**: `git diff --stat` shows only in-scope files; report renders (no
broken markdown); `uv run ruff check .` still green (docstring edits are
Python file changes).

## Test plan

- The regression test already exists: the Step 2 single-test command. No new
  integration tests.
- RED path only: one unit pin in `tests/unit/test_backend_access.py` (Step 4),
  modeled on the existing fake-dispatcher tests in that file.
- Verification: the Step 5 gate list, all green.

## Done criteria

ALL must hold:

- [ ] Step 2 single-test command → `1 passed` (quoted in the step report)
- [ ] `uv run ruff check .` → `All checks passed!`
- [ ] `uv run pyright` → `0 errors, 0 warnings, 0 informations`
- [ ] `uv run pytest -q` → 0 failed
- [ ] `uv run pytest -q -m "com_integration or odbc_integration"` → 0 failed
- [ ] Zero MSACCESS.EXE processes after the final run
- [ ] `docs/release/0.1.0-report.md` has a Resolution section; verdict no longer BLOCKED
- [ ] `tests/integration/test_access_adapter.py` docstring no longer mentions `CurrentDb().Name`
- [ ] `plans/README.md`: row 021 DONE, row 008 DONE
- [ ] `git status` shows no modifications outside the in-scope list; `uv.lock` modification and `.opencode/` preserved, uncommitted

## STOP conditions

Stop and report back (do not improvise) if:

- The drift-check ancestry fails (tree lacks `c955258`) or in-scope excerpts
  don't match the live code.
- Step 2 fails with an error OTHER than `AttributeError: CurrentDb.Name`
  (different bug → different plan).
- Step 3's V3 probe also fails (environment/typelib problem, not a product bug).
- The Step 4 fix wants to touch any file outside the RED-path in-scope list,
  or wants to convert COM creation to `EnsureDispatch` beyond the accessor seam.
- Any Step 5 gate fails twice after one reasonable fix attempt.
- Canonical fixture files change hash/size, or `tests/fixtures/*.tmp_*`
  artifacts appear and persist.
- Stray MSACCESS.EXE processes survive `taskkill` + 10s settle twice in a row.

## Maintenance notes

- After this lands, the only plan-008 residue is the release decision itself:
  full 13-gate re-qualification and tagging `v0.1.0` (or rolling to `0.2.0`
  with the accumulated 009–020 work — see the report's maintenance note that
  the gate matrix is reusable across cycles).
- The report's "Root cause" section describes the pre-`6e6c585` world; keep it
  for history but every future reader should hit the Resolution section first.
- If a future plan ever re-introduces exposing raw COM handles across threads
  (a `get_current_db()`-style accessor), the architecture guard from plan 014
  and this plan's history are the reasons not to.
- The `plan/cleanup` branch holds the 018–020 residual commit (`c955258`);
  once merged to `main` it can be deleted (its purpose is captured).
