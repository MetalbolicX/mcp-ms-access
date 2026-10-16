# Query parameters

`query_data`, the MCP `query_data` tool, and the CLI `query` command all accept
an optional parameter list for parameterized `SELECT` statements.

## Contract

| Caller input | Behaviour |
|---|---|
| `params` omitted, `None`, `[]`, or `()` | The SQL runs with no bind values. This is the plain-SQL path and is unchanged. |
| non-empty `params` on the **ODBC** backend | Each value binds to its `?` placeholder. A `None` element binds as SQL `NULL` (it is still a parameter, not "no parameters"). |
| non-empty `params` on the **DAO** backend | The request fails closed with an explicit error directing callers to ODBC. No SQL runs. |

The DAO (COM) backend cannot bind parameters. Rather than silently dropping the
values and running unparameterized SQL, it rejects a non-empty `params`
sequence before any SQL operation and reports:

```
DAO backend does not support query parameters; use the ODBC backend for parameterized queries
```

`None` and `[]` are not rejected: they preserve the historical no-parameter
DAO behavior, so existing callers keep working.

The DAO backend checks `params` **before** the connection state. A non-empty
`params` reached through a disconnected DAO backend therefore reports the
unsupported-bind error above, not `Not connected`; only `None` and `[]` (or an
empty tuple) on a disconnected backend fall through to `Not connected`.

## Failure propagation

A backend `QueryResult` that reports `success=false` (including the DAO
parameter rejection) becomes an `Err` in `DatabaseUseCases.query_data`. It is
never converted into a successful empty row set. The sanitized message travels
through every public surface:

- **CLI** `query` exits `1` and prints `Error: ...` to stderr.
- **MCP** `query_data` returns `isError: true` with the error in the JSON payload.
- **Application** callers receive `Err`, not `Ok(data=[])`.

Credential tokens (`;PWD=...`, `;Password=...`) are stripped from these messages
by the shared sanitizer.

## CLI usage

```console
# no parameters (works on both backends)
ms-access-mcp query "SELECT COUNT(*) FROM [Orders]"

# ODBC parameters bind to ? placeholders
ms-access-mcp query --backend odbc "SELECT * FROM [Orders] WHERE [CustomerID] = ?" --param 7

# DAO rejects a non-empty parameter list with guidance to use ODBC
ms-access-mcp query --backend dao "SELECT * FROM [Orders] WHERE [CustomerID] = ?" --param 7
# Error: DAO backend does not support query parameters; use the ODBC backend for parameterized queries
```

The `--param` CLI help text, as shipped:

```
Query parameters; a non-empty binding list requires the ODBC backend (DAO rejects parameter bindings)
```

## MCP usage

```json
{ "path": "C:/data/app.accdb", "sql": "SELECT * FROM [Orders] WHERE [CustomerID] = ?", "params": [7] }
```

With the DAO backend selected, this returns `isError: true` and the message
above; with ODBC it binds `7` and returns the matching rows.

## Non-goals

- No `QueryDef` binding and no SQL string substitution.
- No change to CRUD builders or write paths.
- Row limits, disconnected behavior, and recordset cleanup are preserved.
