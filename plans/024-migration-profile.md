# Plan 024: External-database migration profile (SDD cycle)

> **Executor instructions**: This plan is executed as a full SDD cycle, not as
> direct steps. The "Steps" section names the SDD phases in order, the skills
> to invoke, and the decisions already made (do NOT re-litigate them in the
> explore/propose phases — they are inputs). Surface to the operator before
> each phase dispatch (interactive SDD pacing, same as plan 005). When done,
> update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat c764e12..HEAD -- src/ms_access_mcp/ plans/README.md`
> If the tree has drifted (especially plans 022/023 landing), note it in the
> SDD proposal's context section.

## Status

- **Priority**: P2
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: plans/022-ddl-profile.md, plans/023-xlsx-iisam-export.md (sequence only — this plan adds a profile and edits `pyproject.toml`; no concurrent edits to `profiles.py`/`server.py`/`pyproject.toml`)
- **Category**: feature
- **Planned at**: commit `c764e12`, 2026-09-11

## Execution mode

**SDD cycle**: explore → propose → spec (scenarios) → design → tasks → apply
(per-task `@medium`, strict TDD inside apply) → verify → archive, using the
`sdd-*` skills. Plan 005 is the exemplar for pacing and artifact layout
(`openspec/changes/`, archive folder on completion). Automated SDD artifacts
are saved to memory with `capture_prompt: false`.

Why SDD and not direct TDD: this is a new feature FAMILY — new profile, new
optional dependencies, a job/status model, and per-dialect connectors — the
same class of work as plan 005 (installer) and plans 006/007 (profiles). Per
the plans/README rationale: "SDD remains the right tool for the next FEATURE."

## Why this matters

The reference server can migrate an Access database to postgres, mysql,
mariadb, sqlite, or sqlserver (`extract_schema`, `upload_schema`,
`transfer_data`, `get_migration_status`). This repo has zero migration code
(repo-wide search: no matches). Users moving off Access need this; it was
deferred backlog, not rejected scope. This plan ports the capability with the
reference's credential-vault defect deliberately NOT ported.

## Pre-decided decisions (inputs to the SDD cycle — fixed)

1. **Profile**: new `migration` profile in `profiles.py`; module
   `ms_access_mcp.mcp_tools.migration`; included in `"full"`.
2. **Tool names**: verbatim oracle names — `extract_schema`, `upload_schema`,
   `transfer_data`, `get_migration_status`. Rationale: MCP-contract parity
   with the oracle is the point of this feature (diverges deliberately from
   plan 007's `versioning_*`/`maintenance_*` prefixing).
3. **Dependencies**: new optional extra in `pyproject.toml`:
   `migrate = ["psycopg2-binary>=2.9.0", "mysql-connector-python>=8.0.0"]`
   (same drivers as the oracle). sqlite via stdlib `sqlite3`, sqlserver via
   the already-present `pyodbc` — no extra needed for those two. Connectors
   for postgres/mysql import their driver lazily and fail with a clear
   "install ms-access-mcp[migrate]" message when absent.
4. **NO credential vault / no `server_id`** (oracle must-not-port defect:
   `store_credential` took plaintext passwords as tool arguments). Passwords
   live only inside the caller-provided `connection_string`; docs warn about
   shell-history/log exposure. No secret values in any artifact.
5. **Synchronous, in-process job model**: `transfer_data` runs to completion
   and registers the finished job in a module-level tracker;
   `get_migration_status` reads it. No threads, no background workers, no
   persistence across restarts (documented limitation).
6. **Layering** (plan 014 architecture guard applies): MCP tools thin →
   `application/migration.py` use cases → connectors under
   `backends/migration/` (one module per target dialect + registry). Pydantic
   models in `src/ms_access_mcp/models/migration.py` (pydantic is already a
   core dep).
7. **Access-side reads** go through the existing backend seam (DAO/ODBC
   `execute_query` / schema getters) — no new Access backend code.

## Current state (oracle behavioral spec — inline for the SDD phases)

Oracle repo (READ-ONLY): `D:\code\python\ms-access-mcp-server`.

### The four oracle tools — `mcp/migration.py`

- `extract_schema(database_path)` (:38-68): validates path, reuses an open
  connection to the same DB if present, else opens an ODBC adapter with schema
  capabilities; returns `{"success": True, "schema": ExtractedSchema dict}`.
  NEW-SERVER ADAPTATION: no connection pool exists — open per call through the
  existing `DatabaseUseCases` seam.
- `upload_schema(target_type, connection_string, schema_json, server_id=None)`
  (:71-106): resolves connector by `target_type`; creates tables in FK
  topological order; skips existing tables into `tables_failed`. DROP the
  `server_id` param entirely.
- `transfer_data(target_type, connection_string, database_path, schema_json=None,
  transfer_mode="auto", verification_mode="full", table_overrides=None,
  odbc_connection_string=None, server_id=None)` (:109-205): extracts schema if
  not provided; per-table transfer with overrides
  (`{"columns": [...], "where": "...", "order_by": [...]}`); job tracked;
  returns `{"success": True, "job_id": ...}`. DROP `server_id`; keep the rest.
- `get_migration_status(job_id)` (:208-216): returns the tracked job dict.

### Engine shape — `services/migration.py` (oracle)

`MigrationService` (:20-39) composes `SchemaExtractor`, `SchemaMapper`,
`TransferStrategySelector`, `VerificationService`, `JobTracker`,
`ConnectorRegistry`. `upload_schema` (:121-156): `sort_tables_by_fk` → per
table `table_exists`/`create_table` → `{tables_created, tables_failed}`.
`transfer_data` (:177-274): create job → connect → per-table loop updating
progress → strategy transfer → `TableResult` accumulation → mark completed.

### Models — `models/migration.py` (oracle, port with pydantic)

`ExtractedSchema` (:107-113): `source, version="1.0", extracted_at, tables,
relationships, unknown_metadata`. `TableSchema` (:94-99): `name, columns,
primary_key, foreign_keys, indexes`. `TableTransferConfig` (:116-120):
`columns?, where?, order_by?`. `MigrationJob` (:25-34): `id, status
(pending|running|completed|failed), phase (extract|upload|transfer),
current_table, progress, started_at, completed_at, results, errors`.
`TableResult` (:13-22): `table, source_rows, rows_transferred, duration_ms,
success, error, strategy_used, verification`.

### Oracle drivers — `pyproject.toml:12-13`

`psycopg2-binary>=2.9.0`, `mysql-connector-python>=8.0.0` (CORE deps in the
oracle; OPTIONAL extra here).

### What the spec phase must define with scenarios

- Schema extraction coverage: tables, columns (name/type/nullable/default/
  autoincrement), PKs, FKs, indexes; what lands in `unknown_metadata`.
- Type-mapping table Access→{postgres,mysql/mariadb,sqlite,sqlserver} (the
  design phase owns the concrete mapping; spec pins the invariants).
- Transfer modes: `batch` (fetch+insert) is the baseline; `linked`/`passthrough`
  (oracle's ODBC-linked fast path) — evaluate during design; cutting them is
  acceptable if spec'd as a limitation (batch-only v1) — recommend batch-only.
- Verification modes: `full` (row sampling/content) vs `count-only`.
- Failure semantics: per-table isolation, rollback, job marked failed with
  per-table errors.
- Security scenarios: no plaintext-password tooling; connection-string-only
  credentials; error messages sanitized (oracle used `ConnectPolicy().sanitize`).
- Confirmation/write policy: `upload_schema`/`transfer_data` write to EXTERNAL
  databases — they require `confirm=True` like other destructive tools, and
  `extract_schema` path-guards the Access path.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Unit suite | `uv run pytest tests/unit -q` | all pass, 0 failed |
| Full suite | `uv run pytest -q` | 0 failed |
| Lint | `uv run ruff check .` | `All checks passed!` |
| Typecheck | `uv run pyright` | `0 errors, 0 warnings, 0 informations` |
| Install extra (verify) | `uv run pytest tests/unit -q` WITHOUT the extra installed | migration unit tests skip/pass per their design (lazy-import tests must not hard-fail when drivers absent) |

## Scope

**In scope**:

- SDD artifacts under `openspec/changes/<change-name>/` (proposal, specs,
  design, tasks) and the archive folder on completion
- New: `src/ms_access_mcp/mcp_tools/migration.py`,
  `src/ms_access_mcp/application/migration.py`,
  `src/ms_access_mcp/backends/migration/` (connectors + registry),
  `src/ms_access_mcp/models/migration.py`, matching `tests/unit/` files
- Edits: `src/ms_access_mcp/profiles.py`, `src/ms_access_mcp/server.py`
  (help text), `pyproject.toml` (the `migrate` extra ONLY), `plans/README.md`
- CLI migration group — ONLY if the spec phase decides parity requires it
  (recommend YES: `migration extract-schema|upload-schema|transfer-data|status`,
  matching the product's CLI/MCP equivalence gate from plan 008)

**Out of scope** (do NOT touch, even though they look related):

- Credential vault, `store_credential`, `server_id` — must-not-port
- `backends/` existing files (Access-side reads use the existing seam)
- `uv.lock`: editing `pyproject.toml` optional-dependencies invalidates the
  lock — the apply phase MUST run `uv lock` once, review the diff (only
  psycopg2-binary/mysql-connector-python added), and commit `uv.lock` TOGETHER
  with the pyproject change (this is the sanctioned exception to the standing
  "preserve uv.lock" convention; the pre-existing uncommitted modification
  must remain uncommitted — keep the two changes in separate commits)
- `.opencode/` — never commit
- Async/background jobs, REST/HTTP anything, migration OF the MCP server itself
- Real postgres/mysql/sqlserver qualification — fake-based unit tests +
  sqlite end-to-end only (see Test plan)

## Git workflow

- Branch: `plan/024-migration-profile`
- Conventional commits, English, no AI attribution; one commit per SDD apply
  task (plan 005 pattern).
- Do NOT push. `.opencode/` never committed. `uv.lock`: only the
  migrate-extra commit touches it (see Scope).

## Steps (SDD phases)

### Phase 1: Explore (skill: sdd-explore)

Inputs: this plan's "Pre-decided decisions" + "Current state". Read the oracle
files listed (read-only). Output: requirement-clarification notes appended to
the change folder. Resolve ONLY what is not pre-decided (connector module
layout, batch-only vs linked mode, CLI parity scope).

**Verify**: change folder exists with exploration notes; every pre-decided
decision is reflected, none contradicted.

### Phase 2: Propose (skill: sdd-propose)

Proposal: intent (migration profile), scope (in/out from this plan),
approach (layered port with fakes-first connectors).

**Verify**: `openspec/changes/<name>/proposal.md` exists and names the 4
tools, the `migrate` extra, and the no-vault decision.

### Phase 3: Spec (skill: sdd-spec)

Delta specs with scenarios covering everything in "What the spec phase must
define" above. Each requirement gets ≥1 scenario; destructive external writes
get confirm-gate scenarios.

**Verify**: spec files exist; every bullet of "What the spec phase must
define" maps to a scenario (checklist in the verify phase).

### Phase 4: Design (skill: sdd-design)

Technical design: connector interface (`connect/create_table/table_exists/
insert_batch/rollback_table/disconnect`), registry, type-mapping module,
JobTracker (in-process dict + lock-free single-threaded use), ExtractedSchema
models, MCP/CLI wiring, lazy driver imports.

**Verify**: `design.md` exists; layering matches plan 014's guard
(application→backends direction; no MCP→backends imports).

### Phase 5: Tasks (skill: sdd-tasks)

Ordered task list with per-task acceptance. Apply tasks must each be sized for
one `@medium` dispatch with an `[acceptance]` block.

**Verify**: `tasks.md` checklist exists; every task names files and a
verification command.

### Phase 6: Apply (skill: sdd-apply; per-task `@medium`, strict TDD)

Execute tasks in order. RED→GREEN per task. The `pyproject.toml`+`uv lock`
commit is its own task (see Scope). Orchestrator re-verifies each task's
acceptance in-shell before starting the next (standing rule: subagent gate
claims have been false before).

**Verify**: after each task, its acceptance command run IN YOUR SHELL is green.

### Phase 7: Verify (skill: sdd-verify)

Spec-scenario → test mapping; run the full gate set (unit, ruff, pyright,
full pytest). Record PASS/WARNINGS honestly (plan 005 archived with warnings;
that is acceptable, hiding them is not).

**Verify**: verification report exists; gate outputs quoted.

### Phase 8: Archive + index (skill: sdd-archive)

Archive the change; update `plans/README.md` row 024 → DONE with the verdict.

**Verify**: archive folder exists; index row updated.

## Test plan

- Unit (fakes): connector registry, type mapping, FK topological sort,
  job tracker lifecycle, per-table failure isolation, confirm gates, lazy
  driver import error message, sanitized error strings (no connection-string
  echo in errors).
- End-to-end WITHOUT external servers: Access fixture copy → sqlite target
  (stdlib `sqlite3`) full extract→upload→transfer→verify cycle on a temp dir.
- No real postgres/mysql/sqlserver tests; the verify phase documents a manual
  qualification checklist for environments that have them.
- `tests/unit/test_mcp_migration.py` modeled on `test_mcp_core.py` (registry +
  name parity assertions for the 4 verbatim tool names).

## Done criteria

ALL must hold:

- [ ] `migration` profile exists; `load_profile("migration")` and `"full"`
  include it; the 4 verbatim tool names are registered
- [ ] `migrate` extra in `pyproject.toml`; drivers lazily imported; product
  works with the extra absent (unit suite green without it)
- [ ] sqlite end-to-end migration test green on a fixture copy
- [ ] No credential-vault/server_id/store_credential anywhere in new code
  (`grep -rn "server_id\|credential" src/ms_access_mcp/` → no migration hits)
- [ ] `uv run pytest -q` 0 failed; `ruff` clean; `pyright` 0/0/0
- [ ] SDD archive folder complete (proposal→specs→design→tasks→verify)
- [ ] `plans/README.md` row 024 + deferred-backlog migration line updated

## STOP conditions

Stop and report to the operator (do not improvise) if:

- A pre-decided decision turns out to be infeasible (e.g. pyodbc cannot serve
  the sqlserver connector shape the design needs).
- The oracle's `TransferStrategySelector` proves load-bearing for correctness
  (not just speed) — that changes the batch-only recommendation.
- `uv lock` pulls anything beyond the two drivers + their transitive deps.
- Any spec scenario cannot be tested without a real external server AND no
  fake can pin its contract.
- Two consecutive SDD phases fail their verify gates.

## Maintenance notes

- Batch-only v1 (if spec'd) leaves `transfer_mode="linked"/"passthrough"` as
  documented no-ops or rejected values — the spec must say which; do not
  silently accept-then-ignore.
- Reviewer focus: error sanitization (never echo connection strings), confirm
  gates on external writes, lazy imports keeping the base install lean.
- The pre-existing uncommitted `uv.lock` modification and this plan's
  migrate-extra lock change MUST land as separate commits — future bisects
  depend on it.
- If real-DB qualification later happens, append results to the SDD
  verification report rather than rewriting it.
