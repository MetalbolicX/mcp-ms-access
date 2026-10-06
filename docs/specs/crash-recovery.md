# Spec: Crash-Resilience Testing & Recovery for MS Access MCP Server

> **Status**: APPROVED (2026-09-16) — backup policy ruled: automatic
> pre-session snapshot with auto-delete on clean close. Execution plan:
> `plans/047-crash-resilience-recovery.md`.
> **Created**: 2026-09-16.
> **Related**: `scripts/g10_repeated_shutdown.py` (graceful-shutdown gate),
> `src/ms_access_mcp/application/maintenance.py` (`compact_repair`, `backup`,
> `recover_access`), `plans/README.md` product decisions (read-only default,
> path guard, fixture hygiene).

## 1. Intention

The server performs writes against MS Access databases through ACE
(ODBC / COM DAO / ADO). ACE uses **write-behind caching with no write-ahead
log**: a sudden process death or power loss can leave committed pages
unflushed, stale `.ldb` lock files, orphaned COM processes, or a torn/corrupt
`.accdb`. Today nothing verifies behavior under these conditions — plan 008 /
G10 only tests *graceful* shutdown (stdin close, Ctrl-C), and the only
recovery path is a single confirm-gated `compact_repair`.

Why we are doing this:

1. **Know the truth**: empirically determine what happens when the server is
   killed mid-write — before users discover it against real databases.
2. **Guarantee recoverability**: every failure class observed must have a
   defined, tested recovery rung. Unrecoverable corruption must be
   impossible-by-policy (backups), not by luck.
3. **Complete the safety posture**: the server already ships read-only
   default + path allowlist; crash resilience is the missing third leg.

Explicit non-goal: making ACE corruption-proof. It cannot be — there is no
journal to replay. The goal is **detect → classify → recover or restore**,
with data loss bounded by the last backup.

## 2. Behaviour

### 2.1 Chaos harness — `scripts/g13_kill_mid_write.py`

Black-box, through the real stdio MCP protocol, reusing G10's `_LineReader`,
`_Watchdog`, and orphan-kill accounting.

1. Copy `tests/fixtures/test_db.accdb` into a temp dir per round; snapshot
   baseline (row counts + ordered-value checksum per table).
2. Spawn the server via the venv python directly (never `uv run` — its
   wrapper muddies kill semantics), with `ACCESS_MCP_ALLOWED_DIRS` scoped to
   the temp dir.
3. Drive a sustained write workload via MCP tool calls: interleaved single
   inserts/updates plus one large multi-row batch (the "long transaction").
4. Hard-kill the server process (`proc.kill()` / `TerminateProcess` — the
   closest Windows analogue to power loss) at a seeded-random phase:
   - mid-batch,
   - immediately after a commit response (the ACE write-behind window),
   - mid-read.
5. Kill and account for orphaned `MSACCESS.EXE` / `python.exe` processes
   (G10 baseline-snapshot logic).
6. Spawn a **fresh server** against the same file and classify the outcome:
   - `CLEAN` — opens; checksum matches all ops committed before the kill;
     no partial uncommitted rows.
   - `LOCK_STALE` — `.ldb` residue blocks or delays opening.
   - `REPAIRABLE` — only opens after compact/repair.
   - `FATAL` — no recovery rung opens the file.
7. N=20 rounds; RNG seed printed per run so any failing round is
   re-runnable. g10-style report table; non-zero exit on any `FATAL` that no
   rung recovers.

### 2.2 Recovery ladder

Rungs execute in order; each runs only if the previous failed.

1. **Rung 1 — Reopen with retry**: detect stale `.ldb`, backoff, retry open.
   Automatic and transparent.
2. **Rung 2 — `compact_repair` (existing)**: DAO `DBEngine.CompactDatabase`;
   confirm gate retained. The harness validates it against genuinely killed
   databases.
3. **Rung 3 — CLI repair escalation**: spawn
   `msaccess.exe "<db>" /compact "<target>"` (documented Microsoft switch)
   when DAO repair fails; capability-gated on Access being installed.
4. **Rung 4 — Salvage**: create a fresh `.accdb`; copy tables row-by-row via
   DAO, skipping unreadable pages/tables; rename the original to
   `<name>.corrupt-<timestamp>.accdb`; report per-table salvaged/lost row
   counts. Recovers **data** even when the file catalog is damaged.
5. **Rung 5 — Restore from backup**: restore replaces the corrupt file,
   bounding worst-case data loss.
   - **Decision (2026-09-16, operator)**: automatic pre-session backup.
     When a write-enabled session opens a database, the server takes a
     rolling snapshot using the existing backup machinery (manifest tagged
     `kind: "auto_session"`). On clean close the snapshot is deleted
     automatically — zero user friction, no accumulation. On crash/kill the
     snapshot survives; the next `open_database` of that file detects it,
     reports `crash_detected` with the backup path, and keeps it protected
     until the operator restores or dismisses it. Read-only sessions take no
     snapshot. Auto-session snapshots do not count against the manual-backup
     retention budget.

### 2.3 Regression gate

Promote the harness to a `slow`-marked pytest in `tests/integration/`,
gated like the existing COM integration tests (Windows + pywin32 + fixture
DB), so the corruption matrix stays green as recovery code evolves.

### 2.4 Recorded 20-round outcome matrix (2026-10-10)

Evidence from **one machine**: Windows 10.0.19044.7725; Microsoft Access
16.0. Fixed RNG seed: `20261010`. The harness's per-round `Exit` is the
killed server process exit code; the final harness exit code is `0`. The
harness does not report a separate recovery-path or backup-state field, so
neither is inferred here.

| Round | Kill phase | Outcome | Exit | Duration |
| ---: | --- | --- | ---: | ---: |
| 1 | mid_batch | LOCK_STALE | 1 | 6.3s |
| 2 | post_commit | LOCK_STALE | 1 | 5.5s |
| 3 | mid_read | LOCK_STALE | 1 | 6.1s |
| 4 | mid_batch | LOCK_STALE | 1 | 5.6s |
| 5 | post_commit | LOCK_STALE | 1 | 4.6s |
| 6 | mid_read | LOCK_STALE | 1 | 10.4s |
| 7 | mid_batch | LOCK_STALE | 1 | 11.0s |
| 8 | post_commit | LOCK_STALE | 1 | 4.7s |
| 9 | mid_read | LOCK_STALE | 1 | 4.7s |
| 10 | mid_batch | LOCK_STALE | 1 | 5.7s |
| 11 | post_commit | LOCK_STALE | 1 | 4.8s |
| 12 | mid_read | LOCK_STALE | 1 | 4.9s |
| 13 | mid_batch | LOCK_STALE | 1 | 4.8s |
| 14 | post_commit | LOCK_STALE | 1 | 5.3s |
| 15 | mid_read | LOCK_STALE | 1 | 5.6s |
| 16 | mid_batch | LOCK_STALE | 1 | 4.7s |
| 17 | post_commit | LOCK_STALE | 1 | 5.3s |
| 18 | mid_read | LOCK_STALE | 1 | 5.4s |
| 19 | mid_batch | LOCK_STALE | 1 | 4.8s |
| 20 | post_commit | LOCK_STALE | 1 | 5.2s |

Outcome distribution: **20 LOCK_STALE (retryable), 0 CLEAN, 0 REPAIRABLE, 0 FATAL, 0 FAIL**. Final harness exit code: **0**.

## 3. Constraints

- **Windows-only execution**: COM/ACE paths require Windows + pywin32; the
  harness skips elsewhere (same gating as existing integration tests).
- **No change to default safety posture**: server stays read-only-by-default;
  recovery tools keep confirmation gates (`_confirm_gate` pattern,
  `maintenance.py`).
- **No new third-party dependencies**: harness and recovery use stdlib +
  existing `pyodbc` / `pywin32` / `pytest`.
- **Never mutate the original file destructively**: salvage writes to a new
  file; repair works on a copy or renames the original to `.corrupt-*` first;
  restore requires explicit confirmation.
- **Kill fidelity**: `TerminateProcess` is the power-loss analogue; no
  graceful signals in kill rounds.
- **Fixture hygiene**: chaos rounds run only on temp copies — never on
  `tests/fixtures/test_db.accdb` itself or any user path.
- **Reproducibility**: RNG seed printed per run; a failing round must be
  re-runnable with the same seed.
- **Honest limits**: ACE has no WAL; recovery is repair/restore, never
  replay. Documentation must not claim otherwise.

## 4. Scope and out of scope

**In scope:**

- `scripts/g13_kill_mid_write.py` chaos harness (kill mid-write,
  classification, report).
- Rung 1 retry/`.ldb` handling.
- Rung 3 CLI-repair escalation (`msaccess /compact`), capability-gated.
- Rung 4 salvage tool (new MCP tool + use case).
- Rung 5 backup policy wiring (shape pending the open decision in §2.2).
- Validation of existing `compact_repair` (Rung 2) against genuinely killed
  databases.
- `slow` integration test promoting the harness to CI.
- Documentation of the failure classes and the recovery ladder.

**Out of scope:**

- Making ACE crash-proof / adding a journal or WAL (impossible without
  replacing the engine).
- VSS / Previous-Versions automation (OS-level; restores stale whole-file
  copies; documented as manual last resort only).
- `esentutl` or any ESE tooling (wrong engine — ESE ≠ ACE).
- Migration connectors (sqlite/postgres/mysql/sqlserver targets have their
  own transaction semantics).
- Multi-user / network-share corruption scenarios (single-process kills
  only).
- Graceful-shutdown testing (already covered by G10).
- Performance optimization of compact/repair.
