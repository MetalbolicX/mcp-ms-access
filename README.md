# MS Access MCP Server

Local stdio MCP server and CLI for Microsoft Access (Windows x64).

## Quick start

```bash
uv sync --extra dev
uv run ms-access-mcp --version
```

## Development

Fast local gate (mirrors CI): `uv run python scripts/qualify.py`.
Full 13-gate qualification (requires local MS Access): `uv run python scripts/step5_qualify.py`.

## Documentation

Engineering reference (as-built) lives under `docs/`:

| Document | Purpose |
|---|---|
| [Architecture](docs/01-architecture.md) | Layers, COM lifecycle, sessions, tool surface by profile |
| [Contracts](docs/02-contracts.md) | Result envelope, error hierarchy, authorization gates, limits |
| [Safety and configuration](docs/03-safety-and-configuration.md) | Environment variables, backend selection, crash-resilience model |
| [Access documentation workflows (proposed; not implemented)](docs/specs/documentation-workflows.md) | v1 design for deterministic database documentation workflows |

Operational how-to stays in this README. The crash-recovery specification is
`docs/specs/crash-recovery.md`; implementation history is in `plans/`.

## Password-protected databases

The server resolves database passwords **server-side** so they never appear as MCP tool arguments or reach the LLM. Register a password once via the CLI, and all subsequent connections use it automatically.

### CLI usage

```bash
# Store credentials globally (user-level)
ms-access-mcp credentials set --db C:\path\to\database.accdb

# Store credentials alongside the database (local file)
ms-access-mcp credentials set --db C:\path\to\database.accdb --local

# List stored entries (passwords are never shown)
ms-access-mcp credentials list

# Remove an entry
ms-access-mcp credentials remove --db C:\path\to\database.accdb
```

### Credential file locations

| Scope | Path |
|-------|------|
| Global | ``%USERPROFILE%\.ms-access-mcp\credentials.json`` (Windows) |
| Global | ``~/.ms-access-mcp/credentials.json`` (POSIX) |
| Local | ``<db_dir>\.ms-access-mcp.json`` |

### Lookup order

1. **Local** (highest priority): ``<db_dir>/.ms-access-mcp.json``
2. **Global** (lowest priority): ``~/.ms-access-mcp/credentials.json``

A local entry shadows a global entry for the same database.

Local credential files are stored beside the database and can travel with folders that are backed up, shared, or synchronized. Prefer the global store unless local placement is intentional; ``ms-access-mcp doctor`` warns when its current working directory's local store falls beneath a recognized OneDrive or Dropbox root. Detection is heuristic and is not a guarantee that a store is outside synchronized storage.

### File format

```json
{
  "version": 1,
  "credentials": {
    "C:\\Users\\you\\databases\\app.accdb": "your_password"
  }
}
```

Paths are stored as normalized absolute paths. The global file location can be overridden with the ``ACCESS_MCP_CREDENTIALS_FILE`` environment variable.

### Security notes

- **Forbidden characters**: passwords containing ``;``, ``{``, or ``}`` are rejected by the CLI because ODBC connection strings use these as delimiters. Edit the JSON file directly if you must store such a password (at your own risk).
- **File permissions**: credential files are created with mode ``0o600`` (owner read/write only) on POSIX and a protected owner-only DACL on Windows (owner, SYSTEM, and Administrators). Files written before this hardening retain their old ACL; re-run ``ms-access-mcp credentials set`` once to re-harden them.
- **The MCP/LLM layer never sees passwords**: credentials are resolved inside the server process and passed directly to the database adapter; they never appear in tool-call results or error messages exposed to the LLM.

## External database credentials

The server also manages credentials for **external databases** accessed via migration tools (``upload_schema`` / ``transfer_data``) and linked-table tools (``create_linked_table`` / ``refresh_linked_table`` / ``upsert_linked_table``). Connection strings for these targets are stored in **named profiles** rather than passed as tool arguments.

### Profile flow

```bash
# Register a connection string under a profile name
ms-access-mcp credentials profile set --name prod_dw
# (prompts for the connection string twice, hidden via getpass)

# Use the profile in an MCP tool call
# upload_schema(..., connection_ref="prod_dw")
# create_linked_table(..., connect_ref="prod_dw")

# List profile names (values are never shown)
ms-access-mcp credentials profile list

# Remove a profile
ms-access-mcp credentials profile remove --name prod_dw
```

### Hard-reject rule

If an LLM attempts to pass a connection string **literal** containing a password (``PWD=``, ``Password=``, ``Jet OLEDB:Database Password=``) as a tool argument, the tool returns an error directing the user to the profile CLI. The literal is rejected at the **use-case layer** before any connector is invoked.

### Refresh semantics

When a linked table is refreshed, the full (unstripped) connection string is passed to the connector at refresh time so the re-authentication succeeds. After the refresh, the persisted ``Connect`` value is stripped of all password tokens so stored table definitions never retain credentials in plain text.

### Known limitation: SQL-text credential smuggling

SQL text sent via ``query_data`` / ``execute_raw_sql`` may legally embed credentials in ODBC connection strings embedded in SQL (e.g., ``OPENROWSET('MSDASQL', 'Driver={ODBC Driver 18};PWD=secret;', 'SELECT 1')``). The server cannot inspect or redact SQL text. Profiles exist so users **never need to** embed credentials in SQL — store them once via ``credentials profile set`` and reference by name instead.

### Profile file location

Profiles are stored in the same global credentials file as database passwords (``%USERPROFILE%\.ms-access-mcp\credentials.json``). There is no per-database local file for profiles — they are always global. The file format is version 2:

```json
{
  "version": 2,
  "credentials": {
    "C:\\path\\to\\app.accdb": "db_password"
  },
  "profiles": {
    "prod_dw": "Driver={ODBC Driver 18};Server=db.example.com;PWD=hunter2;"
  }
}
```

## Core tool parameter types

The `core` profile MCP tools expose **typed parameters** instead of an opaque `params` dict. Client code that calls core tools should pass individual named arguments (e.g. `sql="SELECT 1"`, `table="MyTable"`) rather than a `params={"sql": "..."}` dictionary. Write tools (`insert_data`, `update_data`, `delete_data`, `execute_raw_sql`, `create_query`, `set_query_sql`, `delete_query`, `save_query`) require `confirm=True` to execute — pass `confirm=False` explicitly for read-only operations. The wire request schema has changed; response payloads are byte-compatible with the previous implementation.

## VBA and macro write opt-in

VBA and macro code-object writes are disabled by default. Set `ACCESS_MCP_ENABLE_VBA_WRITES=1` in the server or CLI process environment to enable them; `1`, `true`, and `yes` are accepted case-insensitively. This additional opt-in reduces accidental code rewrites and is separate from the existing `confirm=True` and write-policy checks.

The opt-in gates only `set_vba_code`, `add_vba_procedure`, `vba_replace_procedure`, `create_module`, `rename_module`, `delete_module`, `create_macro`, `rename_macro`, `delete_macro`, and `set_control_event_procedure`. It does not gate `run_macro` or form/report design writes.

## Crash resilience

MS Access (ACE) uses write-behind caching with no write-ahead log. If the server process dies mid-write, committed pages may never be flushed, leaving stale `.ldb` lock files or a torn `.accdb`. The server installs multiple recovery rungs to bound data loss.

### Automatic session backup

When a **write-enabled** session opens a database, the server takes an automatic rolling snapshot (copy + SHA-256 manifest tagged `kind: "auto_session"`). On **clean close**, the snapshot is deleted automatically — zero user friction, no accumulation. On **crash/kill**, the snapshot survives; the next `open_database` of that file detects it and returns `crash_detected: true` with the backup path. The operator decides whether to restore.

Read-only sessions take no snapshot.

### Recovery ladder

| Rung | Tool / Mechanism | When it fires |
|------|-----------------|---------------|
| 1 | `.ldb`-aware open retry (≤3 × 250 ms backoff) | Lock signature + stale `.ldb`/`.laccdb` present |
| 2 | `compact_repair` (DAO `DBEngine.CompactDatabase`) | Rung 1 exhausted or non-lock open failure |
| 3 | `repair_with_access_cli` (`msaccess.exe /compact`) | DAO repair fails; Access CLI available |
| 4 | `salvage_database` (rename → fresh DB → row-by-row copy) | Catalog damaged; salvage readable tables |
| 5 | Restore from backup | All rungs failed |

**Honest limits**: ACE has no WAL. Recovery is repair/restore, never replay. Documentation must not claim otherwise.

### Chaos testing

The G13 harness (`scripts/g13_kill_mid_write.py`) runs N rounds of: spawn server → drive write workload → hard-kill (`TerminateProcess`) at a random phase → classify result. Run it locally on Windows with ACE:

```bash
python scripts/g13_kill_mid_write.py --rounds 20 --seed 1
```

The kill-mid-write harness is a local operator gate and requires Windows with ACE; CI runs the unit suite.
