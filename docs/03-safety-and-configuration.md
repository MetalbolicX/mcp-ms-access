# 03 — Safety and configuration (as-built)

> **As-built snapshot** at branch `docs/as-built-consolidation` (2026-10-08).
> Engineering reference for the safety and configuration model. Operational
> how-to (commands to type) stays in `README.md`; this document explains what
> is enforced, where, and why. Contract shapes are in `docs/02-contracts.md`.

## Configuration model

There is **no general configuration file**. Configuration is environment
variables + CLI flags + the two credential stores (below).

### Environment variables (complete inventory)

| Variable | Values / format | Default | Effect | Enforcement |
|---|---|---|---|---|
| `ACCESS_MCP_ALLOWED_DIRS` | `;`-separated absolute dirs (Windows) | unset → path guard rejects writes outside CWD-scoped checks | Path allowlist for all database access | `safety.py:15,62`; `cli_format.py:11`; `server.py:17` |
| `ACCESS_MCP_PROFILE` | one of 8 profile names | unset → `core` | Tool surface for MCP server / `mcp` subcommand | `profiles.py:84-95,130`; `cli.py:1479` |
| `ACCESS_MCP_BACKEND` | `auto \| odbc \| com \| dao` | `auto` | Backend adapter selection; invalid value raises at normalization | `backends/__init__.py:80,121-181` |
| `ACCESS_MCP_ENABLE_VBA_WRITES` | `1 \| true \| yes` (casefolded) | unset → **off** | Opt-in gate for 10 VBA/macro write tools | `application/automation.py:68-76` |
| `ACCESS_MCP_CREDENTIALS_FILE` | filesystem path | unset → `%USERPROFILE%\.ms-access-mcp\credentials.json` | Overrides global credential store location | `credentials.py:13,260-276` |

### CLI global flags

`--db --backend --json --yes --allow-write` (`cli.py:40-46`). Profile
resolution precedence: explicit argument > `ACCESS_MCP_PROFILE` > `core`.

## Safety invariants

Four layered gates, each checked before a write executes (contract shapes in
`docs/02-contracts.md` § Authorization gates):

1. **Path guard** — `is_path_allowed` rejects UNC paths (`\\`, `//`) and
   anything outside `ACCESS_MCP_ALLOWED_DIRS` (`safety.py:11-65`).
2. **Write policy** — `WritePolicy` defaults to read-only;
   `require_acknowledgment(op, confirm)` blocks unconfirmed writes
   (`safety.py:76-97`).
3. **Tool-level confirm** — write cohort requires `confirm=True`
   (`mcp_tools/core.py:270-281`).
4. **VBA/macro opt-in** — environment flag, default off
   (`application/automation.py:68-76`).

Honest boundaries of the model:

- Free **SQL text cannot be scrubbed** of credentials; compensated by the
  hard-reject rule (`credentials.py:401-412`; enforced
  `application/database.py:1077-1082`, `migration.py:194-199`).
- **No default row caps** on queries/exports and no connection-count cap —
  bounding is the caller's responsibility (`docs/02-contracts.md` § Limits).

## Backend selection and capabilities

`BackendSelector` (`backends/__init__.py`) validates and normalizes
`ACCESS_MCP_BACKEND` (invalid values fail fast with the accepted list).
Capability model:

- `BackendCapabilities` flag set: read/write data, introspect schema, VBA,
  forms, reports, macros, compact, linked tables, import/export text
  (`backends/__init__.py:25-40`).
- Preset bundles: `SCHEMA_CAPS`, `DATA_READ_CAPS`, `DATA_WRITE_CAPS`,
  `VBA_CAPS`, `AUTOMATION_CAPS`, `MAINTENANCE_CAPS`.
- **COM-only capabilities** (VBA, forms, reports, macros, compact,
  import/export text) cannot be served by ODBC: requesting them under
  `ACCESS_MCP_BACKEND=odbc` raises `BackendCapabilityMismatchError`
  (`backends/__init__.py:143-183`).
- DAO constructability requires Windows + `win32com` (`:185-196`);
  otherwise `BackendUnavailableError`.

## Credentials and secret storage

Resolution contract (order, never-raises, hard-reject) is specified in
`docs/02-contracts.md` § Credentials contract. Storage security model:

- Store writes are **atomic** and hardened with a **NT DACL** restricting
  access (`credentials.py:52-98`).
- A **sync-root warning** fires when the store sits inside a synced folder
  (`credentials.py:157-170`).
- `resolve_password` / `resolve_profile` never raise and never log
  (`credentials.py:251-252,415-430`).
- Operational usage (CLI commands, file formats, lookup examples) →
  `README.md` § Password-protected databases, § External database
  credentials.

## Crash resilience model

Premise (from the approved spec `docs/specs/crash-recovery.md`, 2026-09-16):
ACE uses **write-behind caching with no write-ahead log** — sudden death can
leave unflushed pages, stale `.ldb` locks, orphaned COM processes, or torn
files. The goal is **detect → classify → recover or restore**, with data loss
bounded by the last backup; making ACE corruption-proof is an explicit
non-goal.

**Automatic pre-session snapshot invariants** (operator decision, in spec
§2.2 rung 5):

- Write-enabled session opens take a rolling snapshot (`kind: auto_session`,
  sha256 manifest; `application/session_backup.py`).
- Clean close deletes it automatically — no accumulation.
- Crash leaves it protected; the next `open_database` reports
  `crash_detected` with the backup path until the operator restores or
  dismisses (`application/database.py:133,182,187,229`).
- Read-only sessions take no snapshot; auto-snapshots do not count against
  manual-backup retention.

**Recovery ladder** (rungs execute only when the previous failed; operational
detail in the spec and `README.md` § Recovery ladder):

1. Reopen with retry (stale `.ldb` backoff) — automatic.
2. `compact_repair` (DAO, confirm-gated).
3. CLI escalation: `msaccess.exe /compact` (requires installed Access).
4. Salvage: fresh file, row-by-row copy, original renamed
   `<name>.corrupt-<timestamp>.accdb`, per-table loss report.
5. Restore from the surviving auto-session backup.

**Verification**: chaos harness `scripts/g13_kill_mid_write.py` (real stdio
protocol, seeded kill phases, classification `CLEAN / LOCK_STALE /
REPAIRABLE / FATAL`, N=20 rounds); a recorded 20-round outcome matrix exists
(spec §2.4, 2026-10-10, seed `20261010`); graceful-shutdown gate
`scripts/g10_repeated_shutdown.py`.

## Diagnostics

`doctor` runs seven checks (`doctor.py:40-290`): package versions, Access COM
probe, ACE provider, DAO engine, ODBC driver, deep Access check, temp dir
writability. Output plain or `--json`; exit 0/1. Deep probe requires
`--deep` and an installed Access.

## Distribution and client registration

- Installer `scripts/install.ps1`; portable zip build
  `scripts/build_portable.ps1` (plan 032 lineage).
- OpenCode client registration: `configure opencode` writes config with a
  timestamped backup (`opencode_config.py:173-307`; spec
  `openspec/specs/opencode-config`).

## Documentation split (dedupe map)

| Topic | Reference (this docs/ set) | Operational how-to |
|---|---|---|
| Architecture | `docs/01-architecture.md` | — |
| Envelope, errors, gates, limits | `docs/02-contracts.md` | — |
| Env vars, backend model, storage security, crash model | this document | `README.md` § Quick start, § Development |
| Credentials usage | `docs/02` § contract | `README.md` § Password-protected databases, § External database credentials |
| VBA opt-in | this document § Safety invariants | `README.md` § VBA and macro write opt-in |
| Crash recovery | this document § Crash resilience model | `README.md` § Crash resilience; spec `docs/specs/crash-recovery.md` |

## Claims not verified at snapshot time

1. `ACCESS_MCP_ALLOWED_DIRS` empty-value semantics (guard behavior when set
   but blank) not exercised.
2. Doctor check internals beyond names/signatures not reviewed.
3. g13 outcome-matrix rows beyond the header not read; classification counts
   not restated here.
4. Installer/portable scripts not read; distribution claims derive from
   script names and plan lineage only.

## Reading map

- Architecture → `docs/01-architecture.md`
- Contracts → `docs/02-contracts.md`
- Crash-recovery spec → `docs/specs/crash-recovery.md`
- Plans history → `plans/` (047 crash resilience, 032 portable zip, 106 VBA gate)
