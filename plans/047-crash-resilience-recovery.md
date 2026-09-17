# Plan 047: Crash resilience — kill-mid-write chaos harness, recovery ladder, automatic session backup

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat e34697d..HEAD -- src/ms_access_mcp/application/ src/ms_access_mcp/mcp_tools/maintenance.py src/ms_access_mcp/server.py scripts/g10_repeated_shutdown.py tests/integration/conftest.py docs/specs/crash-recovery.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: HIGH (deliberately corrupts databases in a sandbox; touches the
  open/close lifecycle every session depends on)
- **Depends on**: none. (Plan 041 — verification baseline — recommended but
  not required; the chaos harness is itself a gate.)
- **Category**: security
- **Planned at**: commit `e34697d`, 2026-09-16
- **Spec**: `docs/specs/crash-recovery.md` (APPROVED 2026-09-16) — read it
  first; it is the contract this plan implements.

## Why this matters

ACE (the Access engine) has **write-behind caching and no write-ahead log**:
if the MCP server process dies mid-write (power loss, kill, OS crash),
committed pages may never be flushed, leaving stale `.ldb` files, orphaned
`MSACCESS.EXE` COM processes, or a torn `.accdb`. Today nothing tests this:
plan 008 / G10 (`scripts/g10_repeated_shutdown.py`) only verifies *graceful*
shutdown, and the only recovery path is a single confirm-gated
`compact_repair`. This plan (a) builds empirical evidence of what a hard kill
mid-write actually does, (b) installs an automatic pre-session backup so
worst-case data loss is bounded to the current session with ZERO user
friction, and (c) completes the recovery ladder (retry → compact/repair →
CLI escalation → salvage → restore) so every observed failure class has a
tested rung.

## Current state

The facts the executor needs, inlined:

- `scripts/g10_repeated_shutdown.py` (653 lines) — the pattern to mirror.
  Provides `_LineReader` (stdout line queue), `_Watchdog` (heartbeat kill),
  `_snapshot_baseline`/`_new_orphans`/`_kill_pids` (process-table accounting
  via `tasklist`/`taskkill /F /PID`), a per-round stdio JSON-RPC driver
  (initialize → notifications/initialized → tools/list), and a
  g10-style report table with PASS/WARN/FAIL verdicts. It copies
  `tests/fixtures/test_db.accdb` into a temp dir per round and spawns the
  server via `uv run` (close-stdin rounds) or the venv python directly with
  `CREATE_NEW_PROCESS_GROUP` (Ctrl-C rounds). **For kill rounds, always
  spawn the venv python directly** — `uv run` wraps the process and muddies
  kill semantics.
- `src/ms_access_mcp/backends/odbc.py:106-113` — multi-row insert loops then
  ONE `self._conn.commit()` (:113); rollback in except handlers
  (:117/:146/:169). Per-op commits at :141 (update) and :164 (delete). No
  `BeginTrans`; SELECTs are untransactioned. The corruption window is
  BETWEEN `commit()` returning and ACE's lazy flush.
- `src/ms_access_mcp/application/database.py:98-135` — `open_database`:
  path-guard (`is_path_allowed`), server-side credential resolution, then
  `BackendSelector.get_adapter(...).connect(db_path)`. Sets `self._db_path`.
  `:137-156` — `close_database`: pops from `self._connections`, calls
  `adapter.disconnect()`.
- `src/ms_access_mcp/application/maintenance.py:119-152` — `_preflight_backup`:
  copies DB to `<tempdir>/ms_access_backups/<timestamp>_<basename>` +
  `<file>.manifest.json` (sha256, size, `kind: "backup"`).
  `:158-219` — public `backup(path, *, retention=None)`: same layout plus
  retention pruning (`DEFAULT_RETENTION`, glob `*_<basename>`, keeps newest
  N with manifests). **Reuse this machinery for auto-session backups** — add
  a `kind: "auto_session"` tag, do not duplicate the copy/hash/manifest code.
- `src/ms_access_mcp/application/maintenance.py:375-410` — `compact_repair`
  (Rung 2, EXISTS): confirm-gated (`_confirm_gate`), preflight backup for
  non-disposable paths, delegates to `self._adapter.compact_repair(path, mode)`.
- `src/ms_access_mcp/server.py:51` — server constructs
  `DatabaseUseCases(readonly=True, ...)` — read-only default; write sessions
  are opt-in. `:57-71` — lifespan `finally` closes DB + COM dispatcher: this
  is the RELIABLE clean-close hook (see Known trap below).
- `tests/integration/conftest.py:59-85` — `temp_db_copy` fixture:
  `shutil.copy2` of the fixture DB per destructive test, 0.25 s settle on
  cleanup. Integration tests skip unless Windows + pywin32 + fixture DB
  (`conftest.py:52-56`). Markers `com_integration`, `odbc_integration`,
  `slow` in `pyproject.toml:28-33`.
- `src/ms_access_mcp/mcp_tools/maintenance.py:74-84` — thin MCP wrappers over
  maintenance use cases, registered via `mcp_tools/_registry.py`
  `register(mcp)` data-driven table. New tools follow this pattern.
- **Verified Microsoft facts** (do not re-research):
  `msaccess.exe "<db>" /compact "<target>"` compacts AND repairs then closes
  (documented switch). `esentutl` is ESE, NOT Access — never use it. VSS
  restores stale whole-file copies — manual last resort only, out of scope.
- **Known trap (2026-09-16 audit, deferred finding)**: `mcp_tools/core.py`
  builds a fresh `DatabaseUseCases` per call and `disconnect_access` closes
  a new empty instance — the MCP disconnect path is structurally a no-op.
  Therefore auto-backup cleanup MUST hook the server lifespan shutdown
  (`server.py:57-71`) in addition to `close_database`; do not rely on the
  MCP `disconnect_access` tool alone.
- Repo conventions: conventional commits, branch `plan/047-crash-resilience-recovery`;
  error handling via Result envelope (`_ok`/`_err` from `..errors`);
  destructive ops are confirm-gated; canonical fixtures are NEVER mutated
  (`plans/README.md` product decisions).

## Commands you will need

| Purpose     | Command                                          | Expected on success        |
|-------------|--------------------------------------------------|----------------------------|
| Lint        | `uv run ruff check .`                            | exit 0                     |
| Typecheck   | `uv run pyright src/ms_access_mcp/`              | 0 errors                   |
| Unit tests  | `uv run pytest tests/unit/ -q`                   | all pass                   |
| Budget      | `uv run python scripts/tool_budget.py`           | PASS                       |
| Targeted    | `uv run pytest tests/unit/test_session_backup.py -q` | all pass               |
| Chaos       | `uv run python scripts/g13_kill_mid_write.py --rounds 3 --seed 1` | exit 0, report table |

## Scope

**In scope** (the only files you should modify):

- `scripts/g13_kill_mid_write.py` (create) — chaos harness
- `src/ms_access_mcp/application/session_backup.py` (create) — auto-session
  backup lifecycle (create / clean-close delete / orphan detection)
- `src/ms_access_mcp/application/database.py` — wire session backup into
  `open_database` (create + orphan detect) and `close_database` (delete)
- `src/ms_access_mcp/server.py` — lifespan shutdown: flush session-backup
  cleanup for any connection still open
- `src/ms_access_mcp/application/maintenance.py` — Rung 3 (`msaccess
  /compact` escalation) and Rung 4 (`salvage_database`); reuse
  `_preflight_backup`/`backup` internals for the auto-session snapshot
- `src/ms_access_mcp/mcp_tools/maintenance.py` — register `salvage_database`
  tool (maintenance profile; confirm-gated)
- `tests/unit/test_session_backup.py` (create)
- `tests/unit/test_maintenance_recovery.py` (create)
- `tests/integration/test_kill_mid_write.py` (create — `slow` +
  `com_integration` marked promotion of the harness)
- `docs/specs/crash-recovery.md` — status updates only (outcome matrix after
  harness runs)
- `README.md` — short "Crash resilience" section (auto-backup behavior,
  recovery tools)

**Out of scope** (do NOT touch, even though they look related):

- `src/ms_access_mcp/backends/odbc.py` transaction semantics — do NOT add
  explicit `BeginTrans` or change commit cadence; the harness measures
  current behavior first.
- Migration connectors (`backends/migration/`) — different engines, own
  transaction semantics.
- VSS / Previous-Versions automation, `esentutl` — out of scope per spec §4.
- The deferred "per-call connection lifecycle" refactor — note interaction
  in Maintenance notes; do not fix it here.
- Multi-user / network-share scenarios.
- `tests/fixtures/test_db.accdb` — never mutate; harness works on temp copies.

## Git workflow

- Branch: `plan/047-crash-resilience-recovery` from `main`.
- Commit per step; conventional commits, English, no AI attribution
  (e.g. `test(scripts): add g13 kill-mid-write chaos harness`).
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Chaos harness `scripts/g13_kill_mid_write.py`

Create the script mirroring g10's structure (copy `_LineReader`, `_Watchdog`,
`_ProcSnapshot`, orphan helpers verbatim — duplication across scripts is
accepted house style, see g10/g11/g12). New logic:

1. CLI args: `--rounds N` (default 20), `--seed S` (default: random, printed).
2. Per round: copy fixture DB to temp dir; snapshot baseline per table
   (row count + sha256 of ordered `repr(row)` dump via pyodbc directly —
   the harness may use pyodbc itself for verification, separate from the
   server under test).
3. Spawn server (venv python direct, `ACCESS_MCP_ALLOWED_DIRS=<tmpdir>`),
   handshake, connect DB via MCP `connect_access`/`open_database` tool,
   then drive a workload thread: loop of single `insert_data` calls plus,
   at a random point, one large multi-row batch insert (the "long
   transaction").
4. Kill `proc.kill()` at a seeded-random phase: `mid_batch` (during the big
   insert), `post_commit` (within 50 ms after a write tool response), or
   `mid_read` (during a SELECT of a large table). Log the phase per round.
5. Orphan accounting identical to g10.
6. Verification pass with a FRESH server spawn on the same file:
   classify `CLEAN` / `LOCK_STALE` (`.ldb` present and open fails or
   delays) / `REPAIRABLE` (opens only after `compact_repair` tool
   succeeds) / `FATAL` (nothing opens it). Compare checksums: every write
   whose tool response was received must be durable; no partial rows from
   the in-flight batch.
7. g10-style report table + summary; exit 1 on any `FATAL` (recovery rungs
   land in later steps; until then, FATAL rounds are REPORTED and the exit
   code may be 0 with a "unrecovered FATAL" warning — flip to exit 1 in
   Step 6).

**Verify**: `uv run python scripts/g13_kill_mid_write.py --rounds 3 --seed 1`
→ completes, prints table, no unkillable orphans. (Requires Windows + ACE;
run locally, not in CI.)

### Step 2: Automatic session backup (strict TDD)

New module `src/ms_access_mcp/application/session_backup.py`:

- `create_session_backup(db_path) -> Ok|Err` — reuse maintenance's
  copy/hash/manifest logic (extract shared helper if needed); manifest gets
  `"kind": "auto_session"` and `"session_pid": os.getpid()`; filename
  `autosession_<pid>_<timestamp>_<basename>` so it never collides with or
  counts against manual-backup retention.
- `cleanup_session_backup(db_path, pid) -> None` — delete snapshot +
  manifest for THIS session. Best-effort, never raises.
- `detect_orphaned_backup(db_path) -> dict | None` — find a surviving
  `autosession_*` manifest for this DB whose `session_pid` is dead
  (or simply: any surviving auto-session snapshot = crashed session, since
  clean close deletes it); return manifest data.

Wiring:

- `database.py::open_database`: after successful connect, if NOT readonly →
  `create_session_backup`; BEFORE that, `detect_orphaned_backup` → if found,
  include `crash_detected: true` + `backup_path` in the Ok data (never
  auto-restore, never auto-delete an orphaned snapshot).
- `close_database`: on successful disconnect → `cleanup_session_backup`.
- `server.py` lifespan `finally`: iterate open connections and cleanup
  (the reliable hook — see Known trap).
- Readonly sessions: no snapshot, no detection (skip both).

**Verify**: `uv run pytest tests/unit/test_session_backup.py -q` → all pass
(create/delete/orphan-detect/readonly-skip/crash-kept cases, tmp_path
fixtures, no real Access needed).

### Step 3: Rung 1 — `.ldb`-aware open retry (strict TDD)

In `open_database`'s connect except-path: if the error indicates a lock
(substring match on the pyodbc/COM message, e.g. "Could not use"; "file
already in use") and a `<db>.ldb`/`.laccdb` file exists, retry the connect
up to 3 times with 250 ms backoff before returning Err. Keep it scoped:
only on the lock signature, only for the initial connect.

**Verify**: unit test with a fake adapter whose `connect` raises the lock
signature N times then succeeds; assert retry count and Ok result; assert
non-lock errors fail immediately.

### Step 4: Rung 3 — `msaccess /compact` escalation (strict TDD)

`maintenance.py`: new use case `repair_with_access_cli(path, *, confirm) ->
Result`. Confirm-gated; refuses non-Windows; locates `msaccess.exe` (probe
standard Office16 paths + `shutil.which`); runs
`msaccess.exe "<path>" /compact "<target>"` via `subprocess.run` with a
timeout; on non-zero exit or missing executable → Err with a clear message.
Capability-gated like `CAN_COMPACT` (`backends/__init__.py:33` pattern).

**Verify**: unit tests mock `subprocess.run` and the exe probe — success,
timeout, missing exe, non-zero exit, confirm gate. No real Access needed.

### Step 5: Rung 4 — `salvage_database` (strict TDD + live check)

`maintenance.py`: new use case `salvage_database(path, *, confirm) -> Result`:

1. Confirm-gated. Refuse if file doesn't exist.
2. Rename original to `<name>.corrupt-<timestamp>.accdb` (NEVER mutate in
   place).
3. Create a fresh `.accdb` at the original path (DAO
   `DBEngine.CreateDatabase` via the dispatcher).
4. Copy tables row-by-row from the corrupt file via DAO; per-table try/except
   — a table that fails to open is recorded as lost, not fatal.
5. Return Ok with per-table `{salvaged_rows, lost_tables}` report.

MCP wrapper `salvage_database` in `mcp_tools/maintenance.py` via
`register(mcp)` (maintenance profile — token budget unaffected for core).

**Verify**: unit tests with fake adapter/DAO layer (happy path, one lost
table, missing file, confirm gate). Live check deferred to Step 6's harness:
a `REPAIRABLE`/`FATAL` round must be exercisable through salvage manually;
record the outcome in the spec's outcome matrix.

### Step 6: Promote harness to regression gate

Create `tests/integration/test_kill_mid_write.py`: `pytest.mark.slow` +
`com_integration`-style skip guards (Windows + pywin32 + fixture DB), runs
the harness logic for `--rounds 5 --seed 42` via subprocess and asserts exit
code 0 and zero `FATAL`-without-recovery. Now flip the harness: FATAL
unrecovered = exit 1.

**Verify**: `uv run pytest tests/integration/test_kill_mid_write.py -q` →
passes (or skips cleanly off-Windows).

### Step 7: Gates, docs, index

1. Fill the outcome matrix in `docs/specs/crash-recovery.md` (counts per
   class from the full 20-round harness run; which rung recovered each).
2. README "Crash resilience" section: auto-backup behavior (create on write
   session, delete on clean close, `crash_detected` on next open), recovery
   ladder, honest no-WAL note.
3. Run all gates (Commands table). Update `plans/README.md` row 047.

## Test plan

- `tests/unit/test_session_backup.py`: create-on-write-open;
  delete-on-clean-close; orphan survives kill → `crash_detected` in next
  open's Ok data; readonly session → no snapshot; orphan never auto-deleted;
  manual retention pruning ignores `autosession_*` files.
- `tests/unit/test_maintenance_recovery.py`: Rung 1 retry (lock signature
  retried ≤3, non-lock immediate fail); Rung 3 (mocked subprocess: success /
  timeout / missing exe / confirm gate / non-Windows refusal); Rung 4
  (salvage: renames original, per-table loss recorded, confirm gate).
- Structural pattern: model after `tests/unit/test_credentials.py`
  (tmp_path + fake adapters) and `tests/unit/test_facade_adapter_contract.py`
  (fake adapter seam).
- Verification: `uv run pytest tests/unit/ -q` → all pass, including new
  files.

## Done criteria

- [ ] `uv run ruff check .` exits 0
- [ ] `uv run pyright src/ms_access_mcp/` reports 0 errors
- [ ] `uv run pytest tests/unit/ -q` exits 0 with the new test files included
- [ ] `uv run python scripts/tool_budget.py` PASS (core profile unchanged)
- [ ] `uv run python scripts/g13_kill_mid_write.py --rounds 20 --seed 1`
  exits 0; outcome matrix recorded in the spec; no unkillable orphans
- [ ] `uv run pytest tests/integration/test_kill_mid_write.py -q` passes or
  skips cleanly off-Windows
- [ ] `rg -n "autosession_" src/ms_access_mcp/` shows create/cleanup/detect
  wiring; `close_database` and server lifespan both call cleanup
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` row 047 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The code at the locations in "Current state" doesn't match the excerpts
  (drift since `e34697d`).
- The harness produces a `FATAL` round that NEITHER `compact_repair` NOR
  salvage NOR backup-restore can recover — that is a spec-level finding,
  not something to patch around in this plan.
- `msaccess.exe` cannot be located on the qualification machine (Rung 3
  becomes docs-only; report and continue).
- Wiring the auto-backup into `open_database` requires changing the
  `BackendSelector.get_adapter` seam — that seam is out of scope.
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- **Interaction with the deferred per-call connection lifecycle finding**
  (2026-09-16 audit): when that refactor lands, `close_database` becomes a
  real per-session hook — re-verify the cleanup wiring then; the lifespan
  hook in this plan is the belt-and-suspenders that must stay regardless.
- **What a reviewer should scrutinize**: the orphan-detection heuristic
  (any surviving auto-session snapshot ⇒ crash). Confirm clean close ALWAYS
  deletes — including when `close_database` errors mid-disconnect
  (cleanup must be in a `finally`, and must not delete ANOTHER live
  session's snapshot — hence the `session_pid` in filename + manifest).
- **Follow-ups deferred**: VSS integration (manual last resort, spec §4);
  automatic restore on `crash_detected` (deliberately NOT done — restore
  destroys post-crash state; operator decides); network-share/multi-user
  kill scenarios.
