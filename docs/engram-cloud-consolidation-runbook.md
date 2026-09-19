# Engram Cloud Consolidation Runbook

Consolidate stray cloud projects into `mcp-ms-access`, harden ingest, and prevent
recurrence. This is a corrected revision of the original 5-phase plan, amended after
verifying local client state (engram v1.20.0, `C:\Users\MetalbolicX\.engram\engram.db`)
and the cloud service (`http://100.69.159.29:18080`, healthy) on 2026-09-18.

## Incident

The cloud ingest accepted sessions under wrong or path-like project names. Verified
examples (`engram doctor` → `session_project_directory_mismatch`):

| Session | Stored project | Actual directory |
| --- | --- | --- |
| `01a0a014-6209-7040-ac05-ef1d8d4b740f` | `windows-system` | `D:\code\python\mcp-ms-access` (misattributed) |
| `ses_f7bac0a37ffepeP1p1yxUgoZVf` | `d:\code\python\mcp-ms-access` | `D:\code\python\mcp-ms-access` (legacy path-like name) |

## Verified local state (2026-09-18)

Local projects (9) and mutation counts from `sync_mutations`:

| Project | Mutations | Sessions | Observations | Prompts | Disposition |
| --- | --- | --- | --- | --- | --- |
| `ms-access-mcp-server` | 4570 | 155 | 1306 | — | consolidate → `mcp-ms-access` (old name of the same project) |
| `mcp-ms-access` | **800** | 34 | 195 | — | target |
| `d:\code\typescript\mcp-ms-access` | 16 | 3 | 0 | 10 | consolidate (content is mcp-ms-access: COM/ReScript migration) |
| `d:\code\python\mcp-ms-access` | 6 | 1 | 0 | 5 | consolidate (SDD plans/workers prompts) |
| `windows-system` | 1 | 1 | 0 | 0 | consolidate (its single session is misattributed mcp-ms-access) |
| `c:\users\metalbolicx` | 4 | 1 | 0 | 3 | **EXCLUDED** — content is syncthing/Windows-home, not mcp-ms-access |
| `pi-smart-router` | — | — | 118 | — | keep |
| `tipviz` | — | — | 66 | — | keep |
| `visc-line` | — | — | 56 | — | keep |

Notes:

- The original plan expected the target at ~528 mutations; it is already 800 and
  autosync is active. Recompute expected totals at execution time.
- Local `projects consolidate --all --dry-run` already exists in CLI v1.20.0; the
  client-side runbook may not require an upgrade.
- Cloud has no admin HTTP endpoints (404 on `/projects`, `/stats`, ...) — server-side
  consolidation must go through the CLI, as planned.

## Phase 0 — Pre-flight verification (read-only, on the server)

1. Check `cloud_project_grants` for the 5 source projects. If rows exist, update them
   to the canonical name during merge (or refuse consolidation until resolved).
2. Confirm `cmd/engram/cloud.go` `cmdCloudRepair` (~line 244) DSN/config resolution;
   the new commands must resolve the Postgres DSN the same way.
3. Confirm `internal/cloud/cloudstore/cloudstore.go` materialize path (~309-348,
   715-721): how `cloud_project_sessions` is rebuilt, and whether relation payloads
   embed a project field (observations/sessions/prompts do; relations likely do not —
   make payload rewrite conditional on `payload->>'project' = from`).
4. Detect duplicate `(entity, entity_key)` pairs across source→target. Survivor rule:
   highest seq wins. **Delete losers before the UPDATE** (unique-constraint safety),
   or apply the project UPDATE with `ON CONFLICT` handling.
5. Recompute the expected post-consolidation counts from live data; do not reuse the
   numbers in this document.

## Phase 1 — CloudStore primitives (`internal/cloud/cloudstore/cloudstore.go`)

Each in its own transaction, following the existing audit-log pattern
(`cloud_sync_audit_log` append with action `project_consolidate` / `project_delete`):

- `ConsolidateProject(ctx, from, to)`:
  - Validate: non-empty, `from != to`, `to` not path-like.
  - Dedupe collisions against the target (keep max seq), **deleting losers before
    the project UPDATE**.
  - `UPDATE cloud_mutations SET project = to,
    payload = jsonb_set(payload,'{project}','"<to>"') WHERE project = from` — apply
    `jsonb_set` only when `payload->>'project' = from` (relations may not embed it).
  - `DELETE FROM cloud_chunks WHERE project_name IN (from, to)`; same for
    `cloud_project_sessions`; re-materialize chunks + session index for `to` by
    reusing the materialize-mutations core logic, inside the same transaction.
  - Invalidate the dashboard read-model cache (`dashboardReadModelMu` guard).
  - Return counts: mutations moved, duplicates dropped, chunks rebuilt, sessions
    reindexed.
- `DeleteCloudProject(ctx, name)`: deletes mutations/chunks/sessions rows; refuses if
  a grant exists.

## Phase 2 — CLI (`cmd/engram/cloud.go`)

- `engram cloud projects list` — distinct projects with counts.
- `engram cloud projects consolidate <from> <to> (--dry-run|--apply)` — mirror the
  `cloud repair` safety flags (`--project <name> (--dry-run|--apply)`).
- `engram cloud projects delete <name> (--dry-run|--apply)`.
- Tests in `cmd/engram/main_test.go` style (dry-run prints, apply flag required, arg
  validation).

## Phase 3 — Ingest validation (partial prevention)

- `internal/cloud/cloudserver/mutations.go` (~line 93, BR2-1 block): reject entries
  whose project contains `\` or `/` with `400 invalid_project`, reusing a shared
  helper extracted from `internal/project/detect.go:504-510`.
- Leave `store.NormalizeProject` unchanged (read-path compat with legacy rows).
- Handler tests: path-like rejected, normal names accepted.

**Known gap (this does NOT fix the reported incident):** `windows-system` is not
path-like — it is produced by the client-side detection fallback when project
detection fails. Phase 3 will not stop a recurrence under a new non-path-like
fallback name. Follow-up needed in `internal/project/detect.go` (client): either fix
the fallback (never invent a platform-default project name when a session has a real
directory) or flag such sessions for review instead of syncing them.

## Phase 4 — Execute on the server (walnutpi)

1. **Pause client sync**: stop the local engram daemon / autosync on this machine for
   the duration of apply + verify (prevents new mutations landing mid-consolidation).
2. Backup:
   `docker exec engram-postgres-1 pg_dump -U engram engram_cloud > /tmp/opencode/engram_cloud_backup_$(date +%F).sql`
3. Build the new binary; Postgres (5432) is unpublished, so deploy via `docker cp`
   into `engram-cloud-1` and run the CLI inside the container with the container's
   DSN env (verify env first).
4. Dry-run **four** consolidations into `mcp-ms-access`
   (`ms-access-mcp-server`, `d:\code\typescript\mcp-ms-access`,
   `d:\code\python\mcp-ms-access`, `windows-system`); review dedupe previews, then
   `--apply` each. **Do not consolidate `c:\users\metalbolicx`** (excluded decision).
5. Verify:
   - `projects list` shows exactly: `visc-line`, `tipviz`, `pi-smart-router`,
     `mcp-ms-access`, **plus `c:\users\metalbolicx` (expected to remain)**.
   - Target mutation count ≈ live recomputed total (local reference at draft time:
     800 target + 4570 + 16 + 6 + 1, minus duplicates).
   - Chunks rebuilt; `curl 127.0.0.1:18080/health` still ok.
6. Resume client sync; check `engram doctor` afterwards.

## Phase 5 — Quality gates + client runbooks

- Server: `go test ./...`, `go vet ./...`; follow repo skills (engram-server-api,
  business-rules, testing-coverage, commit-hygiene, docs-alignment — document the new
  commands).
- This Windows machine: run local `engram projects consolidate --all --dry-run`, then
  `--apply` (CLI v1.20.0 already supports it). Before trusting it, verify in code
  that renaming already-synced mutations does **not** reset their synced markers
  (otherwise ~4570 mutations would re-upload).
- **Audit other tailnet machines** (`metalbolicx-lenovo-yoga-x390`,
  `metalbolicx-soyo-alpine`): if they run engram, their per-project sync cursors
  become orphaned for renamed projects — apply the same local consolidation there, or
  their clients will silently miss merged history.
- Ingest rejection (Phase 3) makes the server return `400 invalid_project` for
  path-like names; until local cleanup completes on every client, those clients will
  see sync errors — expected transitional state, resolves after local consolidation.

## Rollback

Restore the Phase 4.2 dump via `psql` while clients remain paused; restart sync after
verification.

## Follow-ups (post-consolidation)

1. Client-side detection fix for `windows-system`-style fallback misattribution
   (`internal/project/detect.go`) — the actual root cause of the reported incident.
2. Decide the final fate of `c:\users\metalbolicx` (keep as-is, or
   `engram cloud projects delete` after explicit confirmation).
3. Optional: server-side visibility (read-only admin endpoint for project counts) so
   Phase 0 checks no longer require direct DB access.
