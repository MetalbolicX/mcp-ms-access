# Plan 039: Server-side password resolution for password-protected Access databases

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 9de32d0..HEAD -- src/ms_access_mcp/application/database.py src/ms_access_mcp/cli.py src/ms_access_mcp/mcp_tools/core.py src/ms_access_mcp/backends/odbc.py src/ms_access_mcp/backends/dao.py src/ms_access_mcp/backends/ado.py src/ms_access_mcp/backends/access.py src/ms_access_mcp/backends/dispatcher.py src/ms_access_mcp/safety.py README.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `9de32d0`, 2026-09-15

## Why this matters

Password-protected Access databases **cannot be opened at all today**: every
backend adapter's `connect()` already accepts `password: str = ""`, but
`DatabaseUseCases.open_database` calls `self._adapter.connect(db_path)` without
it, and no resolution layer exists. The product rule ("plaintext passwords
never appear as MCP tool arguments" — `plans/README.md` line 17) forbids the
obvious fix of adding a `password` tool argument, because anything sent as a
tool argument lands in the LLM's context. This plan closes the gap the
rule-compliant way: the user registers the password once via a new
`credentials` CLI subcommand into a JSON file the LLM never sees, and the
server resolves it by db path at connect time. The LLM keeps sending only
`path` and stays unaware passwords exist.

## Current state

Relevant files and their roles:

- `src/ms_access_mcp/application/database.py` — `DatabaseUseCases.open_database`;
  the single seam where a resolved password must be forwarded (line 101).
- `src/ms_access_mcp/credentials.py` — **does not exist**; this plan creates it.
- `src/ms_access_mcp/cli.py` — argparse CLI; gains a `credentials` subcommand group.
- `src/ms_access_mcp/mcp_tools/core.py` — MCP tool wrappers; docstring-only edit.
- `src/ms_access_mcp/backends/{odbc,dao,ado,access,dispatcher}.py` — **out of
  scope**; all already accept `password` (verified below).
- `src/ms_access_mcp/backends/dao_sql.py:131-134` — `_strip_password()` regex
  already strips passwords from linked-table connect strings. Keep as-is.
- `src/ms_access_mcp/safety.py` — `allowed_dirs_from_env()`; pattern reference
  for env-var reading. Not modified.

The seam — `open_database` as it exists today (`database.py:83-109`):

```python
    def open_database(self, db_path: str) -> Result:
        """Open a database connection. ..."""
        if not is_path_allowed(db_path, self._allowed_dirs):
            return _err(f"Path not allowed: {db_path}")

        try:
            self._adapter = BackendSelector.get_adapter(
                db_path=db_path,
                backend=cast(Literal["odbc", "com", "dao", "auto"] | None, self._backend_name),
                capabilities=DATA_READ_CAPS,
            )
            self._adapter.connect(db_path)
            self._db_path = db_path
            self._connections[self._active_connection] = self._adapter
            return _ok(data={"path": db_path, "connection": self._active_connection})
        except Exception as e:
            import traceback

            traceback.print_exc()
            return _err(f"connect failed: {e!r}")
```

Adapter `connect` signatures (verified — all accept `password: str = ""`):

```python
# backends/odbc.py:39
def connect(self, db_path: str | None = None, password: str = "") -> None:
    # builds: DBQ=...;PWD=<password>;DRIVER={Microsoft Access Driver (*.mdb, *.accdb)}

# backends/ado.py:33
def connect(self, db_path: str | None = None, password: str = "") -> None:
    # builds: ...;Jet OLEDB:Database Password=<password>;

# backends/dao.py:68-73 (DaoSession._open_database)
engine = win32com.client.Dispatch("DAO.DBEngine.120")
connect_str = f";PWD={self._password}" if self._password else ""
return engine.OpenDatabase(self.path, False, self.read_only, connect_str)
# DaoAdapter forwards an adapter-level `password` param (dao.py:132,140).

# backends/access.py:87-98
def connect(self, db_path: str | None = None, password: str = "") -> None:
    # app.OpenCurrentDatabase(target, False, password)

# backends/dispatcher.py:132-158 — open_dao_database(db_path, password="", read_only=False)
```

MCP tool layer — the LLM only ever sends `path`; `params` flows to operation
kwargs, **never** to `open_database` (`core.py:138-147`):

```python
def connect_access(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    """Connect to an Access database."""
    db = _new_db(readonly=True)
    result = db.open_database(path)
    dict_result = _result_to_dict(result)
    return to_mcp_result(dict_result)
```

Env-var reading precedent (`safety.py:37-48`):

```python
def allowed_dirs_from_env() -> list[Path]:
    raw = os.environ.get("ACCESS_MCP_ALLOWED_DIRS", "")
    if not raw:
        return []
    sep = ";" if os.name == "nt" else ":"
    return [Path(p).expanduser() for p in raw.split(sep) if p.strip()]
```

CLI global-vs-project path precedent (`cli.py:797-811`) — follow this pattern
for the credentials-file locations:

```python
def _resolve_opencode_target(global_scope: bool, cwd: Path) -> Path:
    if global_scope:
        if os.name == "nt":
            return Path(os.environ["USERPROFILE"]) / ".config" / "opencode" / "opencode.json"
        return Path.home() / ".config" / "opencode" / "opencode.json"
    return cwd / "opencode.json"
```

Conventions to match:

- **CLI tests**: model after `tests/unit/test_cli_configure.py` — plain
  functions, `tempfile` + `patch.dict(os.environ, {"USERPROFILE": ...})` for an
  isolated home, `unittest.mock.patch` for I/O seams. No conftest fixtures.
- **Architecture guard** (`tests/unit/test_architecture_boundaries.py`): never
  name a new file with a leading underscore (known guard off-by-one — lesson
  recorded in `plans/README.md`). `credentials.py` complies.
- **Docstrings**: ruff rules D/EM/TRY003 are enforced on new modules
  (`pyproject.toml` select list) — write full docstrings with Args/Returns
  sections on every public function, and use the repo's raise mechanics
  (this module must not raise at all — see Step 1).
- Commits: conventional commits, English, no AI attribution. Branch per plan.

## Commands you will need

| Purpose   | Command                                  | Expected on success        |
|-----------|------------------------------------------|----------------------------|
| Tests     | `uv run pytest tests/unit/ -q`           | all pass                   |
| Lint      | `uv run ruff check src/ tests/`          | exit 0, no findings        |
| Typecheck | `uv run pyright src/ms_access_mcp/`      | 0 errors, 0 warnings       |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS (counts unchanged)    |

## Scope

**In scope** (the only files you should modify):

- `src/ms_access_mcp/credentials.py` (create)
- `src/ms_access_mcp/application/database.py` (wire-through in `open_database`)
- `src/ms_access_mcp/cli.py` (new `credentials` subcommand group)
- `src/ms_access_mcp/mcp_tools/core.py` (one docstring line on `connect_access`)
- `tests/unit/test_credentials.py` (create)
- `tests/unit/test_cli_credentials.py` (create)
- `README.md` (new "Password-protected databases" section)
- `plans/README.md` (status row)

**Out of scope** (do NOT touch, even though they look related):

- `src/ms_access_mcp/backends/**` — all adapters already accept `password`;
  verified above. If any `connect` does NOT accept a `password` kwarg, that is
  a STOP condition, not a license to edit backends.
- `src/ms_access_mcp/server.py`, `src/ms_access_mcp/profiles.py` — no new MCP
  tools are added; profile wiring is untouched.
- `src/ms_access_mcp/safety.py`, `pyproject.toml`, `uv.lock` — no new
  dependencies (`getpass`, `json`, `pathlib` are stdlib).
- `src/ms_access_mcp/backends/migration/**` — external-DB connectors have their
  own credential model (`application/migration.py:77`); not part of this plan.
- Windows Credential Manager / `keyring`, an `ACCESS_MCP_DB_PASSWORD` env var —
  explicitly deferred; the file format reserves room (see Step 1).

## Git workflow

- Branch: `plan/039-server-side-db-credentials` from `main`.
- Commit per step; conventional commits (example from `git log`:
  `feat(cli): add credentials subcommand group`).
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Credentials resolver — RED then GREEN

Create `tests/unit/test_credentials.py` FIRST (RED). Cases, in this order:

1. `resolve_password` returns `""` when neither file exists.
2. Global file hit: write `{"version": 1, "credentials": {"<abs path>": "pw"}}`
   to a temp global file (isolate via `ACCESS_MCP_CREDENTIALS_FILE` env var or
   patched `USERPROFILE`), assert `resolve_password("<abs path>") == "pw"`.
3. Normalization matrix: entry stored as absolute path resolves when queried
   with (a) forward slashes, (b) different case on Windows, (c) a relative path
   pointing at the same file.
4. Local shadow: `<db_dir>/.ms-access-mcp.json` entry wins over a different
   global entry for the same db.
5. Malformed JSON in either file → `""` (never raises).
6. Valid JSON with wrong structure (list, or dict without `credentials`) → `""`.
7. Missing entry for the db (file exists, other dbs listed) → `""`.

**Verify RED**: `uv run pytest tests/unit/test_credentials.py -q` → all 7 fail
with `ModuleNotFoundError: No module named 'ms_access_mcp.credentials'`.

Then create `src/ms_access_mcp/credentials.py` (GREEN):

- `resolve_password(db_path: str) -> str` — normalize via
  `Path(db_path).resolve()` + `os.path.normcase`; check
  `<db_dir>/.ms-access-mcp.json` first, then the global file; return `""` on
  any miss. Never raises; never logs the password. File format:
  `{"version": 1, "credentials": {"<normalized abs path>": "<password>"}}`.
- Global path: `%USERPROFILE%\.ms-access-mcp\credentials.json` on Windows
  (`Path(os.environ["USERPROFILE"])`), `Path.home() / ".ms-access-mcp"` on
  POSIX; overridable via `ACCESS_MCP_CREDENTIALS_FILE` env var (test seam —
  mirror `allowed_dirs_from_env` style).
- `write_entry(db_path, password, *, local) -> None`,
  `remove_entry(db_path, *, local) -> bool`,
  `list_entries() -> list[tuple[Path, str]]` (path + which file) — used by the
  CLI in Step 3. Writes are atomic: write temp file in the same directory, then
  `os.replace`. On POSIX set mode `0o600` on create.
- Cache parsed files by `(path, mtime)` in module-level dicts.

**Verify GREEN**: `uv run pytest tests/unit/test_credentials.py -q` → 7 passed.

### Step 2: Wire the password through `open_database` — RED then GREEN

Add to `tests/unit/test_credentials.py` (RED):

8. Stub `BackendSelector.get_adapter` (monkeypatch) to capture the `connect`
   call; put an entry in a temp credentials file; call
   `DatabaseUseCases().open_database(path)`; assert the stub adapter received
   `connect(<path>, password="pw")` (or the equivalent positional form).
9. No credentials entry → stub adapter received `connect(<path>)` with an empty
   password — existing behavior preserved.
10. **Error-leak guard**: stub `connect` to raise
    `Exception("foo;PWD=hunter2;bar")`; assert the returned `Err.message` does
    NOT contain `hunter2` (the resolved password must be scrubbed from the
    error string the LLM receives).
11. **params guard**: call the MCP `connect_access` tool with
    `params={"password": "hunter2"}`; assert the tool result contains no
    `hunter2` and the connection path never saw it.

**Verify RED**: `uv run pytest tests/unit/test_credentials.py -q -k "wire or leak or params"` → new cases fail.

Edit `application/database.py` `open_database` (GREEN):

```python
from ..credentials import resolve_password
...
            password = resolve_password(db_path)
            if password:
                self._adapter.connect(db_path, password=password)
            else:
                self._adapter.connect(db_path)
        ...
        except Exception as e:
            import traceback

            traceback.print_exc()
            message = f"connect failed: {e!r}"
            if password:
                message = message.replace(password, "***")
            return _err(message)
```

(`password` initialized to `""` before the `try` so the `except` can scrub it.)

**Verify GREEN**: `uv run pytest tests/unit/test_credentials.py -q` → 11 passed.
`uv run pytest tests/unit/ -q` → entire suite still passes (no regressions).

### Step 3: CLI `credentials` subcommand — RED then GREEN

Create `tests/unit/test_cli_credentials.py` (RED), modeled after
`tests/unit/test_cli_configure.py` (tmp `USERPROFILE`/`HOME` via
`patch.dict(os.environ, ...)`, `unittest.mock.patch` on `getpass.getpass`):

1. `credentials set --db <path>` with mocked `getpass` returning `"pw"` twice →
   global file created with the entry; output names the file written; password
   never printed.
2. Mismatched getpass entries → exit 2, no file written.
3. Empty password → exit 2.
4. Password containing `;`, `{`, or `}` → exit 2 with an explanation (ODBC
   connection-string safety; manual file editing is documented as
   at-your-own-risk).
5. Existing entry → overwrite requires interactive confirm; `--yes` skips it.
6. `credentials set --db <path> --local` → writes `<db_dir>/.ms-access-mcp.json`.
7. `credentials list` → prints db paths and their source file; never prints
   passwords; reports malformed files as invalid.
8. `credentials remove --db <path>` → entry gone; `remove` on missing entry is
   a no-op exit 0.

**Verify RED**: `uv run pytest tests/unit/test_cli_credentials.py -q` → collection
errors / all fail (no `credentials` subcommand).

Edit `cli.py` (GREEN), following the existing subcommand pattern
(`build_parser` + `set_defaults(func=...)`, handlers named
`_cmd_credentials_set/list/remove`, exit code 2 for user errors):

- `credentials set --db PATH [--local] [--yes]` — `getpass.getpass` prompt
  twice, reject empty/mismatch/forbidden chars, then `write_entry`.
- `credentials list` — `list_entries()`; validate both files and flag malformed.
- `credentials remove --db PATH [--local]` — `remove_entry`.

**Verify GREEN**: `uv run pytest tests/unit/test_cli_credentials.py -q` → 8 passed.

### Step 4: Docs + tool description

- `mcp_tools/core.py` `connect_access` docstring gains one line:
  *"Password-protected databases resolve credentials server-side; if connection
  fails with an auth error, ask the user to run
  `ms-access-mcp credentials set --db <path>` — never request passwords."*
- `README.md`: new "Password-protected databases" section — CLI usage, both
  file locations and lookup order (local shadows global), forbidden characters,
  file permissions, and the explicit statement that the MCP/LLM layer never
  sees passwords.

**Verify**: `uv run python scripts/tool_budget.py` → PASS (no new tools).

### Step 5: Full gates + index

1. `uv run ruff check src/ tests/` → clean.
2. `uv run pyright src/ms_access_mcp/` → 0 errors.
3. `uv run pytest tests/unit/ -q` → entire suite passes, including the 11
   resolver/wire tests and 8 CLI tests.
4. `rg -n "password" src/ms_access_mcp/mcp_tools/` → no `password` parameter on
   any tool signature (existing checklist from `plans/006`).
5. Update the 039 row in `plans/README.md` to DONE with gate evidence.

## Test plan

All new tests live in `tests/unit/test_credentials.py` (resolver + wire-through
+ leak guards, 11 cases) and `tests/unit/test_cli_credentials.py` (CLI, 8
cases), enumerated per step above. Structural patterns: `test_cli_configure.py`
for CLI isolation; monkeypatched `BackendSelector.get_adapter` for the
wire-through (no real Access/COM needed — no `com_integration` markers).
Verification: `uv run pytest tests/unit/ -q` → all pass, +19 new tests.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `uv run pytest tests/unit/ -q` exits 0 with the 19 new tests passing
- [ ] `uv run ruff check src/ tests/` exits 0
- [ ] `uv run pyright src/ms_access_mcp/` reports 0 errors
- [ ] `uv run python scripts/tool_budget.py` → PASS
- [ ] `rg -n "password" src/ms_access_mcp/mcp_tools/` shows no tool-level
      `password` parameter
- [ ] `rg -n "hunter2"` over any captured tool result in the leak tests → no match
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` row 039 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The `open_database` excerpt in "Current state" doesn't match the live code
  (the seam has moved — e.g. plan-038-style cohort extraction relocated it).
- Any adapter's `connect` does NOT accept a `password` kwarg (would force a
  backends edit — out of scope).
- `BackendSelector.get_adapter` must change to thread the password (backends
  package — out of scope).
- A step's verification fails twice after a reasonable fix attempt.
- The DAO adapter stores the password at construction time in a way that
  `connect(db_path, password=...)` cannot override (then the wire-through point
  needs redesign).

## Maintenance notes

- **Deferred**: Windows Credential Manager / `keyring` as an additional
  resolution layer (API-compatible: add a third lookup step in
  `resolve_password`); per-backend connection-string escaping of `;`/`{`/`}`
  (the CLI rejects them, but a hand-edited file can still contain them — ODBC
  `PWD=` strings are built by naive f-string join in `odbc.py:41-46`);
  `ACCESS_MCP_DB_PASSWORD` env var for single-password setups.
- **Reviewer scrutiny**: the scrub in `open_database`'s `except` (Step 2) —
  confirm no other error path can surface the password; the atomic-write and
  `0o600` handling in `credentials.py`; that `list` can never print values.
- **Interactions**: if `DatabaseUseCases` is further split (follow-up to plan
  038), the `resolve_password` call must move with `open_database`. If a future
  plan adds reconnect/refresh logic, the mtime cache in `credentials.py` keeps
  password rotation working without a server restart.
