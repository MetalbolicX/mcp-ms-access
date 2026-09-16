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
