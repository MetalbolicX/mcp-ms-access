# Plan 007: Versioning and maintenance profiles

> **Executor instructions**: Follow step by step; verify each command result.
> STOP conditions are hard. Update `plans/README.md` when done.
>
> **Drift check (run first)**: oracle HEAD `c8beaab`; new repo has plans
> 002–006 DONE.

## Status

- **Priority**: P2
- **Effort**: L
- **Risk**: MED
- **Depends on**: plans/004-mcp-core-profile.md, plans/006-automation-profile.md
- **Category**: feature
- **Planned at**: oracle commit `c8beaab`, 2026-09-08

## Execution mode

`@medium` + strict TDD (fakes first); maintenance integration gates run
in-shell by the orchestrator (destructive paths on disposable copies only).

## Why this matters

Versioning (SaveAsText text export/import, compare, export-all) turns an
Access DB into git-manageable source; maintenance (backup, dev-copy,
compact/repair, recovery) keeps production files safe. All are dangerous-
adjacent filesystem+engine operations: they need preflight backups,
confirmation gates, and profile isolation — the old server exposed them with
free-string parameters and triple-duplicated families.

## Current state (oracle facts)

- `adapters/versioning_io.py` (657 lines): SaveAsText/LoadFromText per object kind (form/report/module/macro/query), export-all with SHA-256 dedup, batch import ordering. The oracle's git-versioning SDD cycle (verified: 13/13 tasks, 1158 tests) is the behavioral spec.
- `mcp/persistence.py` (16 tools) + `mcp/dev_copy.py` (15) contain THREE overlapping export/import families (persistence `*_to_text`, dev_copy `*_backup`, CLI `export-all`) — consolidate into ONE file-based family + in-DB read tools.
- `compact_repair` via `DBEngine` (oracle `wincom.py:879-939`): DAO path, NO Access instance needed. Oracle defect to avoid: free-string `action` parameter (can overwrite files) — replace with explicit named operations.
- `copy_database` is file-level (`shutil`-style); dev-copy lifecycle (`create_dev_copy`/`deploy_dev_copy`/status) overwrote production on deploy — confirm + preflight backup mandatory.
- `recover_access` (oracle `mcp/recovery.py:14`): kills processes/repairs — CLI-first only.
- DANGER list (not ported): `store_credential`/`clear_credentials` tool arguments (plaintext secrets); free-string `action` knobs.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Unit | `uv run pytest tests/unit -q` | all pass |
| Integration | `uv run pytest tests/integration -q` | all pass; fixture hash unchanged |
| CLI smoke | `uv run ms-access-mcp versioning export-all --help` | exit 0 |

## Scope

**In scope**: `src/ms_access_mcp/application/versioning.py`, `src/ms_access_mcp/application/maintenance.py`, `src/ms_access_mcp/backends/versioning.py` (port), `src/ms_access_mcp/mcp_tools/versioning.py`, `src/ms_access_mcp/mcp_tools/maintenance.py`, CLI command groups, `tests/**`.

**Out of scope**: external-DB migration (backlog), git hook generator beyond a thin `git-hook-init` (port as-is if trivial), credential vault of any kind.

## Git workflow

Branch `plan/007-versioning-maintenance`. Conventional commits. No push.

## Steps

### Step 1: Port versioning IO

`backends/versioning.py` from oracle `versioning_io.py` (dispatcher-call pattern, SaveAsText/LoadFromText, kind mapping incl. `acQuery=5`). Strict TDD with fakes; integration on fixture copies asserting export tree + re-import round-trip stability (same SHA-256 set).

**Verify**: `uv run pytest tests/unit/test_versioning.py -q` → pass.

### Step 2: Versioning use cases + tools + CLI

`application/versioning.py`: `export_object(kind, name, out_dir)`, `export_all(out_dir)` (SHA-256 dedup preserved), `import_object(kind, name, path, confirm)`, `import_all(dir, confirm)`, `compare(dir)` (structured diff), `export_schema_ddl(out_path)`. MCP `versioning` profile + CLI `versioning` group share them. Imports are destructive → confirm gate.

**Verify**: unit green; budget script shows `versioning` contributes only when enabled.

### Step 3: Maintenance use cases

`application/maintenance.py`:
- `backup(path) -> BackupInfo` (file copy + manifest with SHA-256; retention: keep N=5, configurable).
- `create_dev_copy(source) / deploy_dev_copy(dev, target, confirm, preflight_backup=True) / dev_copy_status()`.
- `compact_repair(path, mode: Literal["compact","repair","compact_and_repair"], confirm)` — named modes ONLY (no free strings); DAO `DBEngine` path; requires exclusive access error surfaced verbatim.
- `diagnose_environment()` → doctor reuse; `recover_access(confirm)` CLI-only (never an MCP tool).
- Preflight rule: `deploy` and `compact_repair` on a path outside temp/disposable ALWAYS take a backup first and fail if the backup fails.

**Verify**: `uv run pytest tests/unit/test_maintenance.py -q` → pass (fakes: confirm, preflight, retention).

### Step 4: MCP maintenance profile + CLI group

`mcp_tools/maintenance.py` under `maintenance` profile: backup/dev-copy status/create/deploy, compact_repair (guarded), diagnose_environment. `recover_access` NOT registered (CLI-only). CLI `maintenance backup|dev-copy|compact-repair|recover|diagnose` with `--yes`.

**Verify**: `uv run ms-access-mcp maintenance --help` → exit 0.

### Step 5: Destructive-path qualification (orchestrator in-shell)

On disposable copies: deploy-overwrite leaves a preflight backup; compact_repair on a locked file surfaces the engine error cleanly (no hang, no orphan); 5-round backup retention prunes to N; canonical fixture hash unchanged after everything.

**Verify**: all green in orchestrator shell log.

## Test plan

- Unit: versioning fakes (kind mapping, dedup, ordering), maintenance confirm/preflight/retention, profile registration.
- Integration: real SaveAsText round-trip, real compact on copies, locked-file behavior.
- Hash discipline everywhere.

## Done criteria

- [ ] One export/import family (grep shows no duplicate `*_to_text`/`*_backup` families)
- [ ] Deploy/compact always preflight-backup (automated test proves failure path blocks the operation)
- [ ] `compact_repair` has named modes only; no free-string actions
- [ ] `recover_access` absent from MCP tool registry (automated assertion)
- [ ] All tests green; fixture hash unchanged; `plans/README.md` row 007 updated

## STOP conditions

- A destructive path can bypass preflight backup.
- SaveAsText/LoadFromText round-trip is unstable (import ≠ export) after one fix attempt.
- Compact on locked DB hangs (> 60 s).

## Maintenance notes

- Backup retention default N=5 is a `ponytail:`-style ceiling — make it a config value, not a framework.
- Reviewer focus: preflight-backup failure path and confirm gates on deploy.
