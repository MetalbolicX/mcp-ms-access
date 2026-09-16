# MS Access MCP Server

Local stdio MCP server and CLI for Microsoft Access (Windows x64).

## Quick start

```bash
uv sync --extra dev
uv run ms-access-mcp --version
```

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
- **File permissions**: on POSIX systems the global credentials file is created with mode ``0o600`` (owner read/write only). On Windows, use NTFS ACLs to restrict access.
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
