# Plan 063: Harden CLI startup and recovery verification (lazy server import, repair-output proof, ladder guards, backup cleanup filter)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/cli.py src/ms_access_mcp/application/maintenance.py src/ms_access_mcp/application/session_backup.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M (six small strict-TDD steps)
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug | perf
- **Planned at**: commit `a7f2edb`, 2026-09-24

## Why this matters

Every CLI invocation — `--version`, `--help`, `db list`, `credentials list` —
pays ~5 seconds importing the FastMCP/pydantic chain that only the `mcp`
subcommand needs (measured with `python -X importtime`: 5,020,938 µs total,
2,332,740 µs from `ms_access_mcp.server`). Separately, the recovery ladder has
three correctness gaps: `repair_with_access_cli` trusts msaccess.exe's exit
code (notoriously unreliable for a GUI app) and never verifies the compacted
output exists, so the ladder can halt with a false positive while the corrupt
DB is never escalated; `recover_access` raises `AssertionError` when no
adapter was ensured; and `cleanup_session_backup` ignores its `db_path`
argument, deleting every snapshot for the PID — including snapshots belonging
to a different database in the same process.

## Current state

- `src/ms_access_mcp/cli.py` — 1,929-line argparse CLI. Line 32:
  ```python
  from .server import main as mcp_main
  ```
  imported at module top; the ONLY use site is the `mcp` subcommand wiring at
  cli.py:1475:
  ```python
  mcp_parser.set_defaults(command="mcp", func=lambda opts: mcp_main(["--profile", opts.profile] if opts.profile else None))
  ```
  `server.py:10` does `from mcp.server.fastmcp import FastMCP` (the heavy chain).
  `--version` is handled around cli.py:1826; all other subcommands never need
  `mcp_main`.

- `src/ms_access_mcp/application/maintenance.py` — maintenance use cases.
  `repair_with_access_cli` (lines 519–538):
  ```python
  try:
      proc = subprocess.run(
          [exe_path, f'"{path}"', "/compact", f'"{target}"'],
          capture_output=True,
          timeout=120,
          check=False,
      )
  except subprocess.TimeoutExpired:
      return _err("msaccess.exe /compact timed out after 120s")

  if proc.returncode != 0:
      stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
      return _err(f"msaccess.exe /compact failed (exit {proc.returncode}): {stderr}")

  return _ok(data={"original_path": path, "exe_used": exe_path, "target_path": target})
  ```
  Two defects: (1) the args are pre-quoted (`f'"{path}"'`) AND passed in
  list form, so subprocess quotes them again — paths with spaces are mangled;
  (2) success is decided on `returncode == 0` alone with no
  `os.path.isfile(target)` check.

  The correct ensure-adapter pattern exists in `compact_repair`
  (maintenance.py:409–411):
  ```python
  if err := self._ensure_adapter():
      return err
  assert self._adapter is not None  # noqa: S101 - _ensure_adapter establishes the application invariant
  ```
  But `recover_access` (line ~448–452) does `kill_access_processes()` then:
  ```python
  reconnected = False
  assert self._adapter is not None  # noqa: S101 - recover requires an initialized adapter
  ```
  with NO `_ensure_adapter()` call before it — on a fresh use case
  `self._adapter` is `None` → `AssertionError` (or silent skip under `python -O`
  via the `hasattr` guard on the next line).

- `src/ms_access_mcp/application/session_backup.py` — crash-recovery session
  backups. `cleanup_session_backup` (lines 107–124):
  ```python
  def cleanup_session_backup(db_path: str, pid: int | None = None) -> None:
      """Delete the auto-session snapshot (and manifest) for this session.

      Best-effort: never raises. Idempotent — safe to call in finally blocks.
      """
      target_pid = pid if pid is not None else os.getpid()
      pattern = os.path.join(_AUTO_SESSION_DIR, f"{_FILENAME_PREFIX}_{target_pid}_*")
      for backup_path in glob.glob(pattern):
          ...
  ```
  `db_path` is never referenced — cleanup deletes EVERY snapshot for the PID.
  Sibling `detect_orphaned_backup` already filters by db (its test
  `test_different_db_not_orphan` passes), so the basename-filter pattern is
  established. `detect_orphaned_backup` probe (lines 166–184):
  ```python
  try:
      result = subprocess.run(["tasklist", "/FI", f"PID eq {session_pid}", ...], timeout=3, check=False)
      output = result.stdout.decode("utf-8", errors="replace").strip()
      if str(session_pid) in output:
          continue
  except Exception:
      pass

  return manifest
  ```
  If the `tasklist` probe itself fails (timeout, missing binary), the code
  falls through to `return manifest` — treating an UNVERIFIED session as an
  orphan (false positive in exactly the degraded conditions where crashes
  happen).

- Repo conventions: strict TDD RED→GREEN per step; conventional commits
  (`test: ...` then `fix: ...`), English, no AI attribution; branch
  `plan/063-cli-startup-recovery-hardening`. Test exemplars:
  `tests/unit/test_maintenance_recovery.py` (classes `TestRepairWithAccessCli`
  at :118, `TestLdbAwareRetry` at :20; fakes are method-nested like
  `FakeAdapter`/`_FakeDBEngine`), `tests/unit/test_session_backup.py`
  (`TestCleanupSessionBackup` at :67, `TestDetectOrphanedBackup` at :102).
  No import-cost test exists today.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | exit 0 (baseline error count must not increase) |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS (tool counts unchanged) |
| Arch guard | `uv run pytest tests/unit/test_architecture_boundaries.py -q` | pass |
| Import timing (manual sanity) | `uv run python -X importtime -c "import ms_access_mcp.cli" 2>&1 \| Measure-Object -Line` | runs; server no longer in chain (see Step 1 verify) |

## Scope

**In scope** (the only files you should modify):
- `src/ms_access_mcp/cli.py` (remove top-level server import; add local import)
- `src/ms_access_mcp/application/maintenance.py` (repair verification + arg quoting + `recover_access` ensure-adapter)
- `src/ms_access_mcp/application/session_backup.py` (cleanup db filter + probe-failure skip)
- `tests/unit/test_cli_import_cost.py` (create)
- `tests/unit/test_maintenance_recovery.py` (extend)
- `tests/unit/test_session_backup.py` (extend)

**Out of scope** (do NOT touch):
- `src/ms_access_mcp/server.py` and `src/ms_access_mcp/__main__.py` — server startup cost is a separate concern (profile loading).
- PID-reuse hardening of `detect_orphaned_backup` (manifest start-time/hostname) — deferred; needs live validation.
- Any recovery-ladder reordering; only the three targeted gaps.

## Git workflow

- Branch: `plan/063-cli-startup-recovery-hardening`
- One RED commit (`test: ...`) then one GREEN commit (`fix: ...`) per step,
  or combined per step if the repo's recent style (see `git log --oneline -10`)
  prefers atomic test+fix commits — match what plans 056–062 did.
- Do NOT push.

## Steps

### Step 1: Lazy-import the MCP server in cli.py

RED: create `tests/unit/test_cli_import_cost.py`:

```python
"""Importing the CLI must not pull the FastMCP server chain.

The `mcp` subcommand is the only consumer of ms_access_mcp.server; every
other CLI verb (including --version) must stay import-light.
"""

import subprocess
import sys


def test_cli_import_does_not_load_mcp_server() -> None:
    code = "import sys; import ms_access_mcp.cli; assert 'ms_access_mcp.server' not in sys.modules, 'server imported eagerly'"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
```

**Verify**: `uv run pytest tests/unit/test_cli_import_cost.py -q` → FAILS
(the assertion fires because cli.py:32 imports eagerly).

GREEN: delete line 32 (`from .server import main as mcp_main`) and change the
`mcp` subcommand wiring at cli.py:1475 to import inside the lambda's body via
a small local function:

```python
def _run_mcp(opts: Any) -> Any:
    from .server import main as mcp_main  # lazy: keeps --version/--help import-light

    return mcp_main(["--profile", opts.profile] if opts.profile else None)


mcp_parser.set_defaults(command="mcp", func=_run_mcp)
```

(Place `_run_mcp` next to `_build_mcp_parser`; follow the local-import sort
lesson: `import X` lines before `from X import Y` inside function bodies.)

**Verify**: `uv run pytest tests/unit/test_cli_import_cost.py -q` → 1 passed.
`uv run python -c "import sys, ms_access_mcp.cli; print('ms_access_mcp.server' in sys.modules)"` → `False`.
`uv run ms-access-mcp --version` → prints version, noticeably faster.

### Step 2: repair_with_access_cli must verify the output file exists

RED: extend `TestRepairWithAccessCli` in `tests/unit/test_maintenance_recovery.py`
with a test that fakes `subprocess.run` returning `returncode=0` while the
target file does NOT exist, and asserts an Err envelope comes back (study the
existing fake-adapter pattern in that class first and reuse its setup).

**Verify**: `uv run pytest tests/unit/test_maintenance_recovery.py::TestRepairWithAccessCli -q` → new test FAILS (current code returns `_ok`).

GREEN: after the returncode check, add:

```python
if not os.path.isfile(target):
    return _err(
        f"msaccess.exe /compact exited 0 but produced no output at {target}; "
        "escalating (output verification failed)"
    )
```

**Verify**: new test passes; full class passes.

### Step 3: Stop hand-quoting subprocess args

RED: extend the same test class with a test that captures the argv passed to
`subprocess.run` (fake it) and asserts the elements are the RAW paths:
`[exe_path, path, "/compact", target]` — no embedded `"` characters.

**Verify**: new test FAILS (current argv contains `f'"{path}"'`).

GREEN: change the argv to `[exe_path, path, "/compact", target]` (list-form
subprocess quoting handles spaces).

**Verify**: new test passes; `uv run pytest tests/unit/test_maintenance_recovery.py -q` → all pass.

### Step 4: recover_access must ensure an adapter, not assert

RED: add a test (in `TestLdbAwareRetry` or a new small class) constructing a
fresh `MaintenanceUseCases` with NO db_path, calling `recover_access`
(confirm=True path; monkeypatch `kill_access_processes` to return `([], [])`),
and asserting the result is an Err envelope (`.success is False`), not an
`AssertionError` raise.

**Verify**: new test FAILS with `AssertionError`.

GREEN: in `recover_access`, before the assert, mirror `compact_repair`:

```python
if err := self._ensure_adapter():
    return err
assert self._adapter is not None  # noqa: S101 - _ensure_adapter establishes the application invariant
```

**Verify**: new test passes.

### Step 5: cleanup_session_backup must filter by database

RED: extend `TestCleanupSessionBackup` in `tests/unit/test_session_backup.py`:
create two snapshots for the same PID but different `db_path` values (follow
the naming used by `create_session_backup` — inspect the manifest/pattern
first), call `cleanup_session_backup(db_a, pid)` and assert db B's snapshot
and manifest still exist.

**Verify**: new test FAILS (both snapshots deleted).

GREEN: derive a per-db discriminator (the same basename/suffix filter
`detect_orphaned_backup` uses for its `test_different_db_not_orphan` contract)
and restrict the glob to it. Keep best-effort/never-raise semantics.

**Verify**: new test passes; existing cleanup tests still pass.

### Step 6: detect_orphaned_backup must skip on probe failure, not assume orphan

RED: extend `TestDetectOrphanedBackup`: monkeypatch `subprocess.run` (the
tasklist probe) to raise `OSError`; a snapshot with a live-looking
`session_pid` in its manifest must return `None` (cannot determine), not the
manifest.

**Verify**: new test FAILS (returns manifest today).

GREEN: in the `except Exception:` handler around the tasklist probe, `continue`
(skip this candidate — unverified) instead of falling through to
`return manifest`. Keep the pre-`session_pid` legacy branch ("treat as
orphan") unchanged.

**Verify**: new test passes; `uv run pytest tests/unit/test_session_backup.py -q` → all pass.

## Test plan

- New: `test_cli_import_cost.py` (1), repair-verification + argv-quoting tests
  (2), recover-ensure test (1), cleanup-filter test (1), probe-failure test (1)
  — 6 new tests total.
- Structural pattern: method-nested fakes from `test_maintenance_recovery.py`.
- Verification: `uv run pytest tests/unit/ -q` → all pass including the 6 new.

## Done criteria

- [ ] `uv run ruff check .` exits 0
- [ ] `uv run pyright` exits with baseline error count (no new errors)
- [ ] `uv run pytest tests/unit/ -q` all pass; 6 new tests exist and pass
- [ ] `uv run python scripts/tool_budget.py` PASS
- [ ] `rg -n "from .server import" src/ms_access_mcp/cli.py` → no module-level match (only inside `_run_mcp`)
- [ ] `rg -n 'f.\{path\}' src/ms_access_mcp/application/maintenance.py` → no quoted-arg subprocess pattern remains
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- The code at the cited locations doesn't match the excerpts (drift).
- A step's verification fails twice after a reasonable fix attempt.
- The fix appears to require touching an out-of-scope file (e.g. server.py).
- `detect_orphaned_backup`'s manifest format doesn't contain what the db
  filter needs (then report the actual format — do not invent a new one).

## Maintenance notes

- The lazy import makes `ms-access-mcp --version` fast; if a future flag needs
  the server at module scope, put it behind another lazy local import.
- The `]`-in-path gap for xlsx export is plan 074's scope — do not fix here.
- PID-reuse orphan hardening (manifest start-time/hostname) remains deferred;
  revisit with live crash-recovery validation.
