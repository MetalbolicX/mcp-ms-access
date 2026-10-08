# 02 — Contracts (as-built)

> **As-built snapshot** of the contracts surface at branch
> `docs/as-built-consolidation` (after `42f29bd`, 2026-10-08). Describes what
> the code enforces today, with file references. Architecture context lives in
> `docs/01-architecture.md`.

## Result envelope

Kernel result type (`errors.py:37-87`): `Ok(data=None, rowcount=0)` and
`Err(message)` frozen dataclasses sharing `map/bind/fold`; `Result = Ok | Err`
with `ok()/err()` helpers.

| Surface | Rendering | Evidence |
|---|---|---|
| MCP tool | Single `TextContent` with `json.dumps(dict)`; `isError = not success` | `mcp_tools/core.py:30-51` |
| MCP dict shape | Ok → `{"success": true, "data", "rowcount"}`; Err → `{"success": false, "error"}` | `core.py:54-59` |
| CLI | Same dict shape (JSON or human mode); `format_result` returns exit code 0 (Ok) / 1 (Err) | `cli_format.py:17-42` |

### CLI exit codes

| Code | Meaning | Evidence |
|---|---|---|
| 0 | Ok; also bare `ms-access-mcp` prints help | `cli_format.py:23-42`; `cli.py:1954-1957` |
| 1 | Err envelope; `mcp` with unknown profile; doctor failure | `server.py:149-157`; `doctor.py:425-435` |
| 2 | Missing required arguments; credentials/profile set validation | `cli.py:58,94,1031,1018-1055,1134,1148` |

## Error hierarchy and scrubbing

`AccessMcpError` (`errors.py:90-131`) with five subclasses:

| Exception | Trigger | Evidence |
|---|---|---|
| `BackendUnavailableError` | DAO requested off-Windows; COM on Linux; connector lacks migration support | `backends/__init__.py:156,174`; `application/migration.py:54,69,81` |
| `BackendCapabilityMismatchError` | `ACCESS_MCP_BACKEND=odbc` vs COM-only capability; ODBC unsupported ops (incl. `execute_raw_sql`, `execute_script`) | `backends/__init__.py:147,183`; `odbc.py:344-473,526,531` |
| `DaoOperationError` (kernel) | **No raise site found — appears dead**; a *separate* `DaoOperationError(Exception)` in `dao.py:93` is raised with scrubbed messages and falls into generic handlers | `errors.py:102`; `dao.py:152,187,440,489` |
| `AccessObjectNotFoundError` | `"{kind} '{name}' not found"` | `errors.py:112`; `object_kinds.py:214-218` |
| `AccessBackendError` | `"{op} '{name}' failed: {detail}"` | `errors.py:122` |

Backend exceptions surface as `Err(f"Backend unavailable: {e}")` through the
adapter lifecycle (`application/_adapter_lifecycle.py:69-77`).

**Password scrubbing guarantee.** `strip_password` removes `PWD=`,
`Password=`, `Jet OLEDB:Database Password=` (case-insensitive) and trailing
`;` (`errors.py:10-31`). Applied at DAO/ODBC error boundaries, connect
failures, linked-table and doctor paths (representative: `dao.py:489,860`;
`odbc.py:124,144`; `application/database.py:196-200,1135-1137`).
**Known hole:** free SQL text cannot be scrubbed (`README.md:104`) — see the
credentials hard-reject rule below.

## Authorization gates

Write safety is layered (checked before any write executes):

1. **Path guard** — `is_path_allowed` rejects UNC (`\\`, `//`) and paths
   outside `ACCESS_MCP_ALLOWED_DIRS` (`safety.py:11-78`).
2. **Write policy** — `WritePolicy` defaults `readonly=True`;
   `require_acknowledgment(op, confirm=False)` returns an error string unless
   `confirm` (`safety.py:77-97`). Checked by every write use case
   (`application/database.py:485-958` passim; `automation.py:64-66`).
3. **Tool-level confirm** — write cohort tools take `confirm: bool = False`;
   `execute_raw_sql` with `confirm=False` fails immediately with
   `"write policy blocks this; pass confirm=True and enable writes"`
   (`mcp_tools/core.py:270-281`; use case `application/database.py:598-601`).
   The ODBC backend hard-refuses raw SQL regardless (`odbc.py:523-526`).
4. **VBA/macro opt-in** — `ACCESS_MCP_ENABLE_VBA_WRITES` must casefold to
   `1|true|yes` (`application/automation.py:68-76`), default off. Gates 10
   tools (set_vba_code, add/replace_vba_procedure, create/rename/delete_module,
   create/rename/delete_macro, set_control_event_procedure). Does **not** gate
   `run_macro` or form/report design writes.

## Limits and budgets

| Limit | Value | Evidence |
|---|---|---|
| Profile tool budget | 8 profiles must register ≥1 tool; **core ≤ 30** (hard fail); cross-profile warnings | `scripts/tool_budget.py:12,56-87` |
| COM dispatch timeout | 120 s per call (`TimeoutError`) | `backends/dispatcher.py:43,101` |
| Dispatcher shutdown | 15 s join; 5/10 s tasklist/taskkill subprocess timeouts | `dispatcher.py:272,286,305` |
| Query row cap | **None by default** — `limit: int \| None = None`; DAO rewrites `SELECT … TOP {n}` and slices results | `application/database.py:557`; `dao.py:361-371,401-402` |
| Export caps | Sheet name ≤ 31 chars; guarded out-path; **no row cap** | `database.py:978,1005-1006` |
| Connections | No count cap; reopening disconnects the previous handle | `database.py:92-93,116-122` |

Session semantics: `connect_access` persists until `disconnect_access`;
other core tools open per-call sessions closed by decorator
(`mcp_tools/core.py:73-97,115-119`).

## CLI command catalogue

`build_parser` (`cli.py:1826-1860`) registers 22 command families. Global
flags: `--db --backend --json --yes --allow-write` (`cli.py:40-46`).

| Command | Purpose |
|---|---|
| `doctor` | Diagnostics (`--json/--deep`) |
| `configure opencode` | Register/remove opencode client config |
| `credentials` | set/list/remove db passwords; profile set/list/remove connection refs |
| `db` | open/close/list/active connections |
| `tables` `schema` `indexes` `relationships` `stats` `props` | Introspection |
| `queries` `query` | Query CRUD; SELECT with `--param/--limit` |
| `insert` `update` `delete` | Data mutations |
| `ddl` | create/alter/drop table, indexes, relationships |
| `export` | csv/json/xlsx export |
| `links` | Linked tables listing |
| `automation` | forms / reports / macros / vba |
| `versioning` | export/import objects, compare, schema DDL |
| `maintenance` | backup, dev-copy pipeline, compact-repair, diagnose, recover |
| `mcp` | stdio server with `--profile` |

## MCP tool parameter conventions

- Core tools are **individually typed** (no params dict; `core.py:1-9,9-10`);
  automation-profile tools use `params: dict` + `confirm`
  (`mcp_tools/automation.py:329-333`).
- Database reference is `path: str` (absolute `.accdb`/`.mdb`); the use-case
  layer adds `connection_name="default"`. Linked tables use `connect_ref`;
  migration tools use `connection_ref` (`database.py:558,1095-1107`;
  `migration.py:181+`).
- Representative shapes (`core.py:245-420`):
  - `query_data(path, sql, params: list | None, limit)`
  - `insert_data(path, table, data: dict, confirm)`
  - `update_data(..., where: str | None = None)` — WHERE clause **without**
    the `WHERE` keyword
  - `export_data(path, source, out, format ∈ {csv,json,xlsx}, sheet_name="Sheet1")`

## Credentials contract

- **Password resolution** (`credentials.py:244-278`): local
  `<db_dir>/.ms-access-mcp.json` first, then global
  (`ACCESS_MCP_CREDENTIALS_FILE` override, else
  `%USERPROFILE%\.ms-access-mcp\credentials.json`). Returns `""` if absent;
  never raises, never logs.
- **Named connection profiles** (`resolve_profile`, `:415-430`): global file
  only; `None` if missing; never raises.
- **Hard-reject rule** — SQL text matching `PWD=`, `Password=`,
  `Jet OLEDB:Database Password=` is rejected, never executed: linked tables
  (`database.py:1077-1082`), migration (`migration.py:194-199`); ambiguous
  ref+string is rejected (`migration.py:181-185`); unknown profile yields Err
  with remediation (`migration.py:187-193`).
- **Storage guarantees**: atomic writes with NT DACL hardening
  (`credentials.py:52-98`); sync-root warning (`:157-170`).

## Claims not verified at snapshot time

1. Argparse usage errors exiting with code 2 is standard behavior, not
   observed in source (`SystemExit` path).
2. Kernel `errors.DaoOperationError` appears dead (no raise site found);
   absence is not proof across dynamic dispatch.
3. Exact per-profile tool counts require building the server
   (`tool_budget.py` execution out of scope for this snapshot).
4. `_classify_object_failure` not-found condition only partially reviewed
   (`object_kinds.py:207-218`).
5. CLI `mcp --profile` help lists 5 values (`cli.py:1478`) while validation
   accepts 8 (`profiles.py:14`, `server.py:149`) — help-text drift,
   intended state unconfirmed.

## Reading map

- Architecture → `docs/01-architecture.md`
- Safety and configuration model → `docs/03-safety-and-configuration.md`
- Operational quick start and credentials usage → `README.md`
- Error-contract defect history → `plans/` (plans 010, 017)
