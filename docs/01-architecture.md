# 01 — Architecture (as-built)

> **As-built snapshot** of `main @ 7642414` (2026-10-08). This document describes
> what exists in code, with file references. It is derived from the source tree,
> not a design proposal. History lives in `plans/`; change specs in `openspec/`.

## Purpose and boundaries

**mcp-ms-access** is a local stdio MCP server and CLI for Microsoft Access on
Windows x64. One Python package (`ms_access_mcp`) serves both frontends with the
same typed application layer. Deterministic operations only — no LLM runs in
the runtime path; workflow *prompts* are exposed to clients, but execution is
plain Python over COM/DAO/ODBC.

- Local, single-machine: stdio transport, no network listener.
- COM automation requires desktop Access; some operations additionally use
  ODBC, a 32-bit JRO bridge (Jet 3), or `msaccess /compact` subprocesses.
- Destructive surface is gated: raw SQL and VBA/macro writes require explicit
  confirmation or opt-in environment flags (see `docs/03`, forthcoming).

## Layered architecture

Dependency contract is executable: `tests/unit/test_architecture_boundaries.py`
enforces `cli/mcp_tools -> application -> backends`, a shared kernel
(`errors`, `models`, `safety`, `profiles`) that imports nothing from layers,
and "no win32com in application".

```
┌────────────────────────────────────────────────────────────┐
│ Frontends                                                   │
│   cli.py (argparse CLI, entry: ms-access-mcp)               │
│   server.py (FastMCP stdio; `mcp` CLI subcommand, lazy)     │
├────────────────────────────────────────────────────────────┤
│ mcp_tools/          profile-gated tool modules + prompts    │
│   _registry, _session_registry, core/automation/ddl/...     │
├────────────────────────────────────────────────────────────┤
│ application/        use cases, AdapterLifecycle,            │
│                     session_backup, sql_builder             │
├────────────────────────────────────────────────────────────┤
│ backends/           dispatcher (STA), dao, odbc,            │
│                     schema_inspector, jet3_bridge, ...      │
├────────────────────────────────────────────────────────────┤
│ Kernel: errors, models, safety, profiles                    │
└────────────────────────────────────────────────────────────┘
```

| Layer | Responsibility | Representative evidence |
|---|---|---|
| Frontends | Argument/session entry, output formatting, MCP registration | `cli.py:1944` `main()`; `server.py:82-105` `build_server` |
| `mcp_tools/` | One module per profile; tools wrap application use cases; per-call session closing | `mcp_tools/core.py:485-507`; `_session_registry.py:29` |
| `application/` | Business use cases (database, analysis, automation, migration, maintenance, versioning, export); adapter lifecycle; automatic session backup | `application/database.py`; `_adapter_lifecycle.py:26`; `session_backup.py` |
| `backends/` | Access-specific execution: COM dispatcher, DAO/SQL adapters, ODBC reader, schema inspection, format detection, Jet 3 bridge | `backends/dispatcher.py:35`; `dao.py`; `odbc.py` |
| Kernel | Error envelope + hierarchy, path safety, profile resolution, shared models — imports nothing upward | `errors.py:34-122`; `safety.py:10-76`; `profiles.py:14-95` |

### Known edge cases (documented, not guarded)

The boundary test does not cover these; treat them as accepted caveats:

- `server.py:13` — frontend imports `backends.dispatcher` directly (lifespan teardown).
- `mcp_tools/maintenance.py:223` — mcp_tools imports `backends.jet3_bridge` (doctor-style lazy import).
- `application/db_export.py:13` — concrete `AccessAdapter`, bypassing `BackendSelector`.
- `mcp_tools/ddl.py:16`, `migration.py:16` — mcp_tools imports models directly.

## Frontends

- **CLI**: console script `ms-access-mcp = ms_access_mcp.cli:main`
  (`pyproject.toml:21`); `python -m ms_access_mcp` funnels to the same `main`.
  Subcommand dispatch via `set_defaults(func=...)`; `doctor` and `configure`
  are wired here too.
- **MCP stdio**: the `mcp` subcommand lazily imports `server.main`
  (`cli.py:1462-1481`), keeping `--help/--version` import-light. Server:
  `FastMCP("ms-access", lifespan=...)`, dynamic per-profile module
  registration, prompts always registered (`server.py:82-105, 143-161`).
- Both frontends construct the same application use cases and read the same
  `ACCESS_MCP_ALLOWED_DIRS` safety env (`cli_format.py:11`, `server.py:17`).

## COM lifecycle and session model

- **Single STA owner**: `ComDispatcher` (`backends/dispatcher.py:35`) runs a
  dedicated daemon thread (`ComDispatcher-STA`). All COM handles are created
  and released on that thread only; other threads enqueue calls with a 120 s
  dispatch timeout.
- **Adapters own or inject the dispatcher** (`dao.py:122-168`);
  `AdapterLifecycle` (`application/_adapter_lifecycle.py:26`) implements
  reuse-or-reconnect with idempotent close for use cases.
- **MCP session registry**: `SessionRegistry` (`mcp_tools/_session_registry.py:27`)
  holds strong references and closes idempotently; core tools open per-call
  sessions closed by decorator (`core.py:82-96`); server lifespan teardown
  closes db, shuts the dispatcher down, and drains sessions (`server.py:43-77`).
- **Supervised teardown**: shutdown flushes queued futures, joins the STA
  thread (15 s), releases COM handles, then verifies Access PID liveness and
  force-kills stragglers via `taskkill /F /PID` (`dispatcher.py:246-308`).
- **Crash resilience**: write-opens create an automatic backup copy with a
  sha256 manifest (`application/session_backup.py`), cleaned on clean close;
  orphans are detected on next open. The chaos harness and the five-rung
  recovery ladder are specified in `docs/specs/crash-recovery.md`.

## Tool surface by profile

`profiles.py:14` defines 8 profile names; resolution is explicit argument >
`ACCESS_MCP_PROFILE` > `core`; `full` is the union of all tool modules
(`profiles.py:54, 84-95`). Approximate tool counts from each module's
`_TOOLS` listing:

| Profile | Tools | Scope |
|---|---|---|
| core | 21 | connections, schema introspection, parameterized query/data CRUD, gated raw SQL, export |
| automation | ~70 | forms/reports/macros/VBA CRUD; VBA & macro writes gated by `ACCESS_MCP_ENABLE_VBA_WRITES` (default off) |
| ddl | 18 | table/index/relationship DDL, linked tables, db properties, SQL scripts |
| maintenance | 10 | backup, dev-copy pipeline, compact/repair, diagnose, salvage, Jet 3 conversion |
| versioning | 6 | object export/compare/import, schema DDL export |
| migration | 4 | extract/upload schema, transfer data, status |
| analysis | 3 | ER diagram, query analysis, SQL generation |

Prompts are profile-independent (`mcp_tools/prompts.py:56-71`).

## Cross-cutting kernel and helpers

- `errors.py` — `Ok/Err` envelope (`:34/:54`), `strip_password` scrubbing
  (`:10`), `AccessMcpError` hierarchy (`:90-122`).
- `safety.py` — path allow-list (`ACCESS_MCP_ALLOWED_DIRS`, `;`-separated),
  UNC rejection, per-dir oracle, `WritePolicy`.
- `credentials.py` — server-side password resolution so secrets never appear
  as tool arguments; atomic store with NT ACL hardening; named external
  connection profiles; credential scrubbing on errors.
- `profiles.py`, `models/`, `doctor.py` (diagnostics), `opencode_config.py`
  (client registration with timestamped backup), `cli_format.py` (shared
  CLI/MCP result formatting).

## Repository layout

```
src/ms_access_mcp/        application/ backends/ mcp_tools/ models/ + root modules
tests/                    unit/ (~80 files) · integration/ (COM-live) ·
                          powershell/ · fixtures/test_db.accdb
scripts/                  qualify.py (fast gate: ruff, pyright, unit, budget) ·
                          step5_qualify.py (full 13-gate, needs local Access) ·
                          g10-g13 chaos/stdio harnesses · install/build ps1
plans/                    001-110 implementation ledger (history)
openspec/                 capability specs + change archive
docs/                     release reports · specs/crash-recovery.md · this file
```

Fast gate mirrors CI: `uv run python scripts/qualify.py`. Full qualification
requires local Access: `uv run python scripts/step5_qualify.py` (see README).

## Claims not verified at snapshot time

Recorded so this document does not overstate; verify before relying on them:

1. The `ComDispatcher` created in `server.py:54` lifespan — its role beyond
   `shutdown()` is unclear; adapters construct their own dispatchers.
2. No out-of-process supervised COM host exists for main automation: STA
   thread in-process; subprocesses only for PID kill, `/compact`, Jet 3.
3. `BackendSelector.get_adapter` selection order and capability flags only
   partially reviewed (`backends/__init__.py:25-115`).
4. ODBC backend dispatcher usage not verified.
5. Multi-connection fan-out (`application/database.py` `_connections`) not
   exercised against the MCP surface.
6. Help-text drift: `cli.py:1476-1478` lists 5 profiles vs 8 in `server.py:118`.

## Reading map

- Operations and quick start → `README.md`
- Command, error, and limits contracts → `docs/02-contracts.md` (forthcoming)
- Safety and configuration model → `docs/03-safety-and-configuration.md` (forthcoming)
- Implementation history and evidence → `plans/`
- Change specifications → `openspec/`
- Crash recovery detail → `docs/specs/crash-recovery.md`
