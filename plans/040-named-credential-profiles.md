# Plan 040: Named credential profiles for external databases (migration + linked tables)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 19098f2..HEAD -- src/ms_access_mcp/credentials.py src/ms_access_mcp/application/database.py src/ms_access_mcp/application/migration.py src/ms_access_mcp/backends/dao.py src/ms_access_mcp/backends/dao_sql.py src/ms_access_mcp/backends/migration/connectors/__init__.py src/ms_access_mcp/mcp_tools/migration.py src/ms_access_mcp/mcp_tools/ddl.py src/ms_access_mcp/cli.py README.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: plans/039-server-side-db-credentials.md (landed at `19098f2`)
- **Category**: security
- **Planned at**: commit `19098f2`, 2026-09-15

## Why this matters

The product rule "plaintext passwords never appear as MCP tool arguments"
(`plans/README.md` line 17) is violated by design in exactly two flows, where
the LLM must *author* credential-bearing strings: **migration** (`upload_schema`
/`transfer_data` take a literal `connection_string` for MySQL/Postgres/SQLServer/
SQLite targets) and **linked tables** (`create/refresh/upsert_linked_table`
take a literal `connect_string` for any ODBC-linked source). Beyond the
arguments themselves there are three confirmed leak paths: `get_linked_tables`
returns `tdef.Connect` raw on read; `_strip_password` is case-sensitive and
misses `pwd=`/`Password=`/`Jet OLEDB:Database Password=`; and linked-table
error paths return `str(e)` unsanitized. This plan extends plan 039's
server-side resolution from "path → Access password" to "name → external
connection string": the user registers a profile once via CLI, the LLM sends
only the profile name, and secret literals are hard-rejected at the use-case
layer.

**Verified scope**: two full-tree sweeps confirm migration targets and linked
tables are the ONLY credential entry points for external databases (export,
execute_sql_script, create_access_database, doctor, automation, versioning,
maintenance accept none).

## Current state

Relevant files and their roles:

- `src/ms_access_mcp/credentials.py` — plan-039 module (path → password
  resolver); this plan extends it to file format v2 with a `profiles` section.
- `src/ms_access_mcp/application/database.py` — linked-table use cases at
  lines 873/897 (create), 907/930 (refresh), 972/996 (recreate); already has
  the 039 `resolve_password` wiring in `open_database`.
- `src/ms_access_mcp/application/migration.py` — `MigrationUseCases`;
  `_sanitize_error` at 74-81; unsanitized bypasses at 310 and 554-555.
- `src/ms_access_mcp/backends/dao_sql.py:131-144` — `_strip_password` (the
  regex to harden).
- `src/ms_access_mcp/backends/dao.py` — linked-table backend: read at
  713-747, write/refresh/recreate at 749-812, unsanitized errors at
  744/767/785/810.
- `src/ms_access_mcp/backends/migration/connectors/__init__.py:39-45` —
  `BaseConnector.__init__` stores plaintext `self.connection_string`.
- `src/ms_access_mcp/backends/odbc.py:346-384` — linked-table methods are
  reject-stubs (`BackendCapabilityMismatchError`). DAO is the only real path.
- `src/ms_access_mcp/mcp_tools/{migration,ddl}.py` — tool wrappers extracting
  `connection_string`/`connect_string` from the free-form `params` dict.
- `src/ms_access_mcp/cli.py` — plan-039 `credentials set/list/remove`
  subcommand group (getpass-based); this plan adds the `profile` group.
- `src/ms_access_mcp/models/__init__.py:103` — `connect_string: str` result
  field for linked tables (keep the field; ensure its value is stripped).

039 resolver as it exists today (`credentials.py:144-178`) — the pattern the
new functions must mirror (never raises, never logs values):

```python
def resolve_password(db_path: str) -> str:
    normalized = _normalize_path(db_path)

    # 1. Check local file first
    local_path = _local_credentials_path(normalized)
    local_data = _read_credentials_file(local_path)
    if local_data is not None:
        return local_data["credentials"].get(normalized, "")

    # 2. Check global file
    env_override = os.environ.get("ACCESS_MCP_CREDENTIALS_FILE", "")
    global_path = Path(env_override) if env_override else _global_credentials_path()
    global_data = _read_credentials_file(global_path)
    if global_data is not None:
        return global_data["credentials"].get(normalized, "")

    return ""
```

039 file reader requires the `credentials` key (`credentials.py:130-132`) —
this plan relaxes that for v2:

```python
    if not isinstance(raw, dict):
        _cache[(path, mtime)] = None
        return None
    if "credentials" not in raw:
        _cache[(path, mtime)] = None
        return None

    data: _CredentialsDict = {"version": raw.get("version", 1), "credentials": raw["credentials"]}
```

`_strip_password` as it exists today (`dao_sql.py:131-144`) — case-sensitive:

```python
def _strip_password(connect_string: str) -> str:
    """Strip PWD=... from a DAO connect string."""
    # Strip PWD=... (handles both ;PWD= and PWD= forms)
    stripped = re.sub(r"(?<=[;])PWD=[^;]*", "", connect_string)
    stripped = re.sub(r"\bPWD=[^;]*", "", stripped)
    ...  # returns stripped
```

DAO linked-table read path (`dao.py:713-747`) — raw echo on read:

```python
        connect_str = tdef.Connect or ""        # line 725
        ...
        "connect_string": connect_str,           # line 738 — NO strip
```

DAO write-time strip pattern (`dao.py:749-769`) — transient raw, persisted
stripped; **keep this pattern**, the use-case layer injects the full string:

```python
                tdef.Connect = connect_string          # 761 raw
                db.TableDefs.Append(tdef)              # 763
                tdef.Connect = _strip_password(connect_string)  # 764 stripped persist
```

Migration tool arg extraction (`mcp_tools/migration.py:86`, and :129
identically):

```python
    connection_string = p.get("connection_string", "")
    ...
    if not connection_string:
        dict_result = {"success": False, "error": "connection_string is required"}
```

Migration sanitizer (`application/migration.py:74-81`): `_sanitize_error`
always returns `"[sanitized]"`; applied on connection/transfer failures but
NOT on the ImportError path (l.554-555: `return _err(str(e))`) or the
invalid-schema path (l.310-311: `return _err(f"Invalid schema: {e}")`).

Conventions to match:

- **CLI tests**: model after `tests/unit/test_cli_credentials.py` (plan 039) —
  tmp `USERPROFILE`/`ACCESS_MCP_CREDENTIALS_FILE` isolation,
  `unittest.mock.patch` on `getpass.getpass`. Plain functions, no fixtures.
- **Use-case tests**: fake/monkeypatched adapter seam (same pattern as plan
  039 step 2's `BackendSelector.get_adapter` stub).
- **Architecture guard** (`tests/unit/test_architecture_boundaries.py`): never
  name a new file with a leading underscore. This plan adds NO new files.
- Docstrings: ruff D/EM/TRY003 enforced; full Args/Returns on public functions.
- Commits: conventional commits, English, no AI attribution. Branch per plan.

## Commands you will need

| Purpose     | Command                                   | Expected on success        |
|-------------|-------------------------------------------|----------------------------|
| Tests       | `uv run pytest tests/unit/ -q`            | all pass                   |
| Lint        | `uv run ruff check src/ tests/`           | exit 0                     |
| Typecheck   | `uv run pyright src/ms_access_mcp/ <changed files>` | 0 errors, 0 warnings |
| Tool budget | `uv run python scripts/tool_budget.py`    | PASS (counts unchanged)    |

## Scope

**In scope** (the only files you should modify):

- `src/ms_access_mcp/credentials.py` (v2 format, profile functions, `contains_credential`)
- `src/ms_access_mcp/backends/dao_sql.py` (harden `_strip_password`)
- `src/ms_access_mcp/backends/dao.py` (read-time strip in `get_linked_tables`; error scrub at 744/767/785/810)
- `src/ms_access_mcp/application/database.py` (linked-table use cases: `connect_ref`, hard-reject)
- `src/ms_access_mcp/application/migration.py` (`connection_ref`, hard-reject, error-path routing)
- `src/ms_access_mcp/backends/migration/connectors/__init__.py` (redacted `__repr__`)
- `src/ms_access_mcp/mcp_tools/migration.py` (passthrough + docstring rewrite)
- `src/ms_access_mcp/mcp_tools/ddl.py` (passthrough + docstring rewrite)
- `src/ms_access_mcp/cli.py` (`credentials profile` subcommand group)
- `tests/unit/test_credentials.py` (extend)
- `tests/unit/test_cli_credentials.py` (extend)
- `tests/unit/test_dao_sql.py` or the nearest existing dao_sql test file (extend; create only if none exists)
- `README.md` (new "External database credentials" section)
- `plans/README.md` (status row)

**Out of scope** (do NOT touch, even though they look related):

- `src/ms_access_mcp/backends/migration/connectors/{mysql,postgres,sqlite,sqlserver}.py` — connection mechanics unchanged; only the base class `__repr__` is in scope.
- `src/ms_access_mcp/backends/odbc.py` — linked-table methods are reject-stubs; leave them.
- `src/ms_access_mcp/models/__init__.py` — keep the `connect_string` field; only its VALUE changes (stripped).
- `src/ms_access_mcp/server.py`, `profiles.py`, `safety.py`, `pyproject.toml`, `uv.lock` — no new tools, no new deps.
- SQL-text credential smuggling (`OPENROWSET(...;PWD=x)` in query SQL) — unfixable at the server layer; README documents it as a known limitation.
- Keyring/DPAPI, field-by-field credential prompts, `--local` profiles — deferred (Maintenance notes).

## Git workflow

- Branch: `plan/040-named-credential-profiles` from `main`.
- Commit per step; conventional commits (example: `feat(credentials): add v2 named profiles`).
- Do NOT push or open a PR unless the operator instructed it.
- Working-tree caveat: `.atl/*` artifacts and untracked `plans/033..039.md`
  files belong to other sessions — never stage them.

## Steps

### Step 1: Credential file v2 — profiles + `contains_credential` (RED then GREEN)

Extend `tests/unit/test_credentials.py` FIRST (RED):

1. v2 round-trip: `write_profile("prod_dw", "Driver={ODBC};PWD=hunter2;")` then
   `resolve_profile("prod_dw")` returns the exact string.
2. `resolve_profile` returns `None` (NOT `""`) when the profile is absent.
3. Back-compat: a v1 file `{"version": 1, "credentials": {...}}` with no
   `profiles` key → `resolve_profile` returns `None`, and 039's
   `resolve_password` keeps working on the same file.
4. Forward-compat: a file with `profiles` but no `credentials` key is accepted
   (relax the reader: accept either section, default missing sections to `{}`).
5. Profile names are NOT path-normalized (unlike 039 keys): the name is an
   exact-match dict key; a name like `prod_dw` must never pass through
   `_normalize_path`.
6. `list_profiles()` returns names only — never values.
7. `remove_profile` returns True/False; removing the last profile leaves the
   `profiles` section as `{}` (does NOT delete the file — unlike 039's
   `remove_entry`, because the same file may still hold path credentials).
8. `contains_credential` matrix — True for: `PWD=x`, `pwd=x`, `Pwd=x`,
   `Password=x`, `Jet OLEDB:Database Password=x` (any position, any case).
   False for: `Trusted_Connection=yes`, `C:\data\db.sqlite`,
   `DSN=mydsn;UID=me`, `Driver={ODBC Driver 18 for SQL Server}`.

**Verify RED**: `uv run pytest tests/unit/test_credentials.py -q -k "profile or contains"` → failures (missing functions).

Edit `credentials.py` (GREEN):

- Extend the file format: `{"version": 2, "credentials": {...}, "profiles": {"<name>": "<connection string>"}}`.
- Relax `_read_credentials_file`: accept a dict with EITHER section; default
  missing sections to `{}`; keep the mtime cache.
- New exports: `resolve_profile(name: str) -> str | None` (global file ONLY —
  profiles have no `--local` file), `write_profile(name, connection_string)`,
  `remove_profile(name) -> bool`, `list_profiles() -> list[str]`.
- `contains_credential(text: str) -> bool` — one case-insensitive regex
  covering `PWD=`, `Password=`, `Jet OLEDB:Database Password=`.
- Writes reuse 039's atomic-write pattern (tempfile + `os.replace`, `0600`).
- Never raise from resolve; never log values.
- Add the four names to `__all__` alongside `contains_credential`.

**Verify GREEN**: `uv run pytest tests/unit/test_credentials.py -q` → all pass
(039's cases included — no regressions).

### Step 2: Strip hardening + read-strip + DAO error scrub (RED then GREEN)

Extend the dao_sql test file (RED):

9. `_strip_password` removes: `pwd=` lowercase, `Password=`, mixed case,
   `Jet OLEDB:Database Password=x`, token at string start, token mid-string,
   multiple password tokens in one string.
10. Non-credential strings pass through byte-identical: `ODBC;DSN=x`,
    `;DATABASE=C:\data\db.accdb`, `Driver={Microsoft Access Driver (*.mdb, *.accdb)}`.

Then RED tests for the DAO adapter (fake/record the win32com surface the way
existing dao tests do — look at `tests/unit/test_dao_adapter.py` for the
pattern):

11. `get_linked_tables` returns `connect_string` values with password tokens
    stripped even when the stored `tdef.Connect` contains `pwd=secret`
    (simulates an externally-created link).
12. `create_linked_table` error path: raise a fake DAO error whose message
    CONTAINS the full connect string; assert the returned `error` string does
    NOT contain the password value.

**Verify RED**: run the new cases → failures.

GREEN edits:

- `dao_sql.py`: add `re.IGNORECASE`; extend patterns to cover `PWD=`,
  `Password=`, `Jet OLEDB:Database Password=` (each a `;`-delimited token;
  strip from the token start to the next `;` or end of string).
- `dao.py:738`: `connect_str = _strip_password(tdef.Connect or "")` at line
  725's assignment (strip ONCE at read).
- `dao.py` error returns at 744/767/785/810: build the error as
  `str(e)` with the full `connect_string` replaced by `[sanitized]` when
  present, then pass the result through `_strip_password` as a fallback
  (error text may split the string).

**Verify GREEN**: new cases pass; `uv run pytest tests/unit/ -q` → no
regressions.

### Step 3: Linked-table use cases — `connect_ref` + hard-reject (RED then GREEN)

Extend a new or existing linked-table use-case test file (RED), fake adapter
capturing the connect string:

13. `create_linked_table(name, source_table, connect_ref="prod_dw")` →
    adapter receives the RESOLVED full string (with `PWD=hunter2`), not the ref.
14. Ref not found → Err mentioning
    `ms-access-mcp credentials profile set --name prod_dw`; message must NOT
    contain any credential value.
15. Literal `connect_string="...;PWD=hunter2"` → Err (hard-reject) with the
    same profile-flow guidance.
16. Literal `connect_string="ODBC;DSN=mydsn;Trusted_Connection=yes"` → allowed
    (clean literal).
17. Both `connect_ref` and `connect_string` given → Err (ambiguous).
18. `refresh_linked_table` with NEITHER → current behavior preserved (adapter
    called with no new connect string).
19. Error scrub at the use-case seam: fake adapter raises
    `Exception("...;PWD=hunter2;...")`; the Err.message must not contain
    `hunter2`.

**Verify RED**: run `-k "linked"` → failures.

Edit `application/database.py` (GREEN) — in the three use cases at 873/907/972:

```python
from ..credentials import contains_credential, resolve_profile
...
def create_linked_table(self, name, source_table, connect_string=None, *, connect_ref=None, ...):
    resolved, err = _resolve_connect_arg(connect_string, connect_ref)
    if err is not None:
        return _err(err)
    ...
```

Implement one shared helper `_resolve_connect_arg(connect_string, connect_ref)`
in `application/database.py` applying the 5-rule resolution (both → ambiguous
Err; ref → resolve_profile, None → not-found Err with the CLI guidance and the
instruction "never supply the connection string"; literal with
`contains_credential` → hard-reject Err with the same guidance; clean literal →
allowed; both None → allowed, reuse existing link). Use-case error returns
from the adapter path: scrub the resolved string from any `Err` message the
way `open_database` already does (`str.replace(password, "***")` — here the
full resolved connect string; fall back to a `contains_credential`-aware scrub).

**Verify GREEN**: cases 13-19 pass.

### Step 4: Migration — `connection_ref` + error hardening (RED then GREEN)

RED tests (extend a migration use-case test file; sqlite connector is the
zero-extra-dependency fake target — see plan 024's test style):

20. `upload_schema(..., connection_ref="prod_dw")` → `get_connector` receives
    the resolved string.
21. Ref not found / ambiguous / hard-reject literals — same Err behavior as
    Step 3 (reuse `_resolve_connect_arg` semantics; factor it into
    `credentials.py` if both use cases import it, or duplicate the small
    helper in `migration.py` — executor's choice, one of the two, documented).
22. ImportError path (missing driver) → error message passes through
    `_sanitize_error` (assert the output does not echo the connection string
    even if the ImportError message somehow contained it).
23. `BaseConnector.__repr__` → `"<SqlServerConnector [redacted]>"` style;
    assert `repr(connector)` never contains the connection string.

**Verify RED** → failures. GREEN:

- `application/migration.py`: add `connection_ref` param to
  `upload_schema`/`transfer_data`; apply the same 5-rule resolution before
  `_get_connector`; route l.554-555 and l.310-311 through `_sanitize_error`.
- `connectors/__init__.py`: `__repr__` returning `f"<{type(self).__name__} [redacted]>"`.
  Do NOT change `self.connection_string` storage (out-of-scope refactor).

**Verify GREEN**: cases 20-23 pass; full suite green.

### Step 5: MCP tool layer — passthrough + docstrings

`mcp_tools/migration.py` and `mcp_tools/ddl.py`:

- Extract `connection_ref`/`connect_ref` from `params` alongside the existing
  literals and forward to the use cases. The required-field check
  (`connection_string is required`) becomes: require `connection_ref` OR
  `connection_string`.
- Rewrite the WARNING docstrings (migration.py:79-82, 122-125): remove
  "logs parameters to shell history"; document the protocol: *"Credentials
  resolve server-side from named profiles. If the connection fails with an
  auth error, ask the user to run `ms-access-mcp credentials profile set
  --name <name>` — never request or transmit the connection string."* Same
  one-liner on the three ddl.py linked-table tools.

**Verify**: `uv run python scripts/tool_budget.py` → PASS (params are
free-form dict keys — no tool-count or schema change).

### Step 6: CLI `credentials profile` group (RED then GREEN)

Extend `tests/unit/test_cli_credentials.py` (RED), plan-039 style (mocked
`getpass.getpass`, tmp `ACCESS_MCP_CREDENTIALS_FILE`):

24. `credentials profile set --name prod_dw` with mocked getpass → profile
    written to the global file; success output names the file, never the value.
25. Invalid name (contains `/`, `\\`, `:`, spaces) → exit 2.
26. Empty string → exit 2. Mismatched confirmation → exit 2.
27. Existing profile → overwrite confirm; `--yes` skips.
28. `credentials profile list` → names only, never values.
29. `credentials profile remove --name x` → gone; missing name → no-op exit 0.

GREEN: add `_cmd_credentials_profile_set/list/remove` in `cli.py` under the
existing `credentials` subparsers (`profile set|list|remove`), mirroring the
039 handlers (getpass twice, exit 2 user errors, name charset `[A-Za-z0-9_.-]`).
No forbidden-char validation on the connection string value — ODBC strings
legitimately contain `;` and `{...}`.

**Verify GREEN**: 24-29 pass.

### Step 7: README + gates + index

- README "External database credentials" section: profile flow, hard-reject
  rule, refresh semantics (password present in the string at refresh time,
  stripped from what persists), and the known limitation: SQL text sent via
  `query_data`/`execute_raw_sql` may legally embed credentials
  (`OPENROWSET(...;PWD=x)`) — the server cannot prevent that, profiles exist
  so users never need to.
- Gates: `ruff` clean; `pyright` on changed files 0/0/0; full unit suite;
  `tool_budget.py` PASS.
- Leak greps (see Done criteria).
- Update the 040 row in `plans/README.md` to DONE with gate evidence.

## Test plan

All new tests land in the three files listed in Scope (~20 new cases, numbered
1-29 above, several being single assertions). Patterns: plan 039's
`test_credentials.py`/`test_cli_credentials.py` are the direct exemplars;
dao adapter tests model after `tests/unit/test_dao_adapter.py`; migration
use-case tests model after plan 024/027's test files. Verification:
`uv run pytest tests/unit/ -q` → all pass, suite grows from the 1181 baseline
with zero deletions.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `uv run pytest tests/unit/ -q` exits 0; new cases 1-29 present and passing
- [ ] `uv run ruff check src/ tests/` exits 0
- [ ] `uv run pyright` on changed files → 0 errors, 0 warnings
- [ ] `uv run python scripts/tool_budget.py` → PASS
- [ ] `rg -n "PWD=|Password=" src/ms_access_mcp/mcp_tools/` → docstrings only,
      no tool-argument schema or parameter named `password`/`connection_string` added
- [ ] Leak-guard tests pass: no `hunter2` in any captured tool result or error
      across the exercised flows
- [ ] `rg -n "pwd=" src/ms_access_mcp/backends/dao_sql.py` (lowercase) appears
      only inside regex/tests comments — i.e., the strip covers case variants
- [ ] No files outside the in-scope list modified (`git status`); `.atl/*` and
      untracked `plans/03x.md` files untouched
- [ ] `plans/README.md` row 040 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Any "Current state" excerpt mismatches the live code (drift — especially
  `credentials.py` if 039 landed differently, or `application/database.py`
  cohort extraction moved the linked-table methods).
- The DAO adapter's linked-table methods changed signature (would invalidate
  the use-case wiring assumption).
- `_strip_password` has new callers not enumerated here (write at 764/782/807,
  this plan adds read at 725 + error scrub at 744/767/785/810).
- Relaxing the v2 reader breaks any plan-039 test (must stay green).
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- **Deferred**: keyring/DPAPI profile storage (add a third resolution step in
  `resolve_profile`); field-by-field credential prompts (host/user/db then a
  hidden password, server builds the string per dialect); `--local` profiles;
  per-dialect string builders/validators.
- **Reviewer scrutiny**: the regex set in `_strip_password` (must not corrupt
  legitimate `;`-delimited tokens containing the substring "pwd"); the
  hard-reject message wording (must instruct the profile flow, never echo the
  submitted string); `resolve_profile` returning `None` vs `""` (distinct
  sentinel from 039 — profile-absent vs password-empty).
- **Interactions**: if migration gains async jobs or per-job credential
  pinning later, the resolution must happen ONCE at job creation and the
  resolved string must never land on the `MigrationJob` model (it already
  doesn't — keep it that way). If a `get_linked_tables`-style read tool is
  added for ODBC in the future, it must strip on read from day one.
