# Plan 043: Path-guard output semantics + allowed-dirs coverage for versioning/maintenance/migration

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat dde0998..HEAD -- src/ms_access_mcp/safety.py src/ms_access_mcp/application/versioning.py src/ms_access_mcp/application/maintenance.py src/ms_access_mcp/application/migration.py src/ms_access_mcp/application/database.py src/ms_access_mcp/mcp_tools/versioning.py src/ms_access_mcp/mcp_tools/maintenance.py src/ms_access_mcp/mcp_tools/migration.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `dde0998`, 2026-09-16

## Why this matters

Two sides of the same guard are broken. (1) The path guard is applied
inconsistently: core DB tools confine file access to `ACCESS_MCP_ALLOWED_DIRS`,
but the versioning, maintenance, and migration tool groups construct their use
cases WITHOUT `allowed_dirs` — so `versioning_export_all(out_dir)` can write
full database contents (including VBA source) to any path the process can
write, and `maintenance_backup(path)` reads any file on disk into a predictable
temp dir. A prompt-injected client defeats the guard simply by switching tools.
(2) The guard itself rejects every not-yet-existing path (`safety.py:25-26`),
which breaks the normal "export to a NEW file" flow wherever the guard IS
applied — the guard must distinguish "target must exist" (reads) from "parent
must be allowed" (writes).

## Current state

`src/ms_access_mcp/safety.py:10-34` — the guard as it exists today (verbatim):

```python
def is_path_allowed(path: str | Path, allowed_dirs: list[Path]) -> bool:
    p = Path(path).resolve(strict=False)

    # Reject network paths
    if str(p).startswith("\\\\") or str(p).startswith("//"):
        return False

    # Reject nonexistent paths without raising
    if not p.exists():
        return False

    for allowed in allowed_dirs:
        try:
            p.relative_to(Path(allowed).resolve(strict=False))
            return True  # per-directory try/except — the oracle bug fix
        except ValueError:
            continue
    return False
```

Unguarded construction sites (verified):

```python
# mcp_tools/versioning.py:15-16
def _new_versioning(readonly: bool = True) -> VersioningUseCases:
    return VersioningUseCases(readonly=readonly, db_path=None)

# mcp_tools/maintenance.py:21-22
def _new_maintenance(readonly: bool = True) -> MaintenanceUseCases:
    return MaintenanceUseCases(readonly=readonly, db_path=None)
```

`application/versioning.py:14` imports only `WritePolicy` from `..safety` —
`is_path_allowed` never appears in the file. `application/maintenance.py:158-160`
checks only `os.path.exists(path)` before `shutil.copy2` into
`%TEMP%\ms_access_backups` (:162-167). `application/migration.py:177` guards
`database_path` with `os.path.isfile` only.

Guarded precedent to mirror — `application/database.py:93` (and `:838`):
`if not is_path_allowed(db_path, self._allowed_dirs): return _err(...)`.
`DatabaseUseCases.__init__` accepts `allowed_dirs`; `mcp_tools/core.py:26-28`
shows the wiring pattern:

```python
def _new_db(readonly: bool = True) -> DatabaseUseCases:
    """Construct a DatabaseUseCases honoring ACCESS_MCP_ALLOWED_DIRS."""
    return DatabaseUseCases(readonly=readonly, allowed_dirs=allowed_dirs_from_env())
```

Tool-entry path params that must become guarded:
`versioning_export_object(kind, name, out_dir)`, `versioning_export_all(out_dir)`,
`versioning_compare(export_dir)`, `versioning_export_schema_ddl(out_dir)`,
`versioning_import_object(kind, name, file_path, confirm)`,
`versioning_import_all(input_dir, confirm)`; maintenance: `maintenance_backup(path)`,
dev-copy `source`/`dev_path`/`target` params, `compact_repair(path)`; migration:
`extract_schema(database_path, ...)` and any other path-taking entry
(verify in Step 0).

## Commands you will need

| Purpose    | Command                                | Expected on success |
|------------|----------------------------------------|---------------------|
| Lint       | `uv run ruff check .`                  | exit 0              |
| Typecheck  | `uv run pyright src/ms_access_mcp/`    | 0 errors            |
| Unit tests | `uv run pytest tests/unit/ -q`         | all pass            |
| Targeted   | `uv run pytest tests/unit/test_safety_paths.py tests/unit/test_mcp_versioning.py -q` | all pass |

## Scope

**In scope**:
- `src/ms_access_mcp/safety.py` — output-path semantics
- `src/ms_access_mcp/application/versioning.py`, `application/maintenance.py`,
  `application/migration.py` — constructor `allowed_dirs` + per-entry guards
- `src/ms_access_mcp/mcp_tools/versioning.py`, `mcp_tools/maintenance.py`,
  `mcp_tools/migration.py` — pass `allowed_dirs_from_env()`
- `tests/unit/test_safety_paths.py` (extend), versioning/maintenance/migration
  use-case and tool tests (extend existing files)

**Out of scope**:
- Changing the GUARDED behavior of `database.py`/`db_export.py`/`db_analysis.py`
  beyond what Step 1 requires (their call sites may need the new parameter —
  mechanical only).
- The CLI path handling (`cli.py`) — separate sweep; note findings in the step
  report if CLI bypasses appear.
- `backends/trusted_locations.py` (registry writes — flagged in audit as
  unreviewed; NOT part of this plan).

## Git workflow

- Branch: `plan/043-path-guard-coverage`
- Conventional commits; one per step. Do NOT push unless instructed.

## Steps

### Step 0: Establish the default-semantics baseline (recon, no edits)

Before touching anything, answer in the step report: when
`ACCESS_MCP_ALLOWED_DIRS` is unset (empty list), what does
`DatabaseUseCases.__init__` default to, and what does `is_path_allowed(path, [])`
return today? (Per the excerpt, an empty list means the loop never runs →
`False`.) Determine how `connect_access` succeeds today with an unset env var —
there must be a default (e.g. cwd, db-dir, or a None-means-allow-all path).
The new guards in Steps 2–4 MUST preserve that default behavior for users who
never set the env var. If the default is "empty list denies everything" and
some other code path makes tools work anyway, STOP and report — the guard model
is different from what this plan assumes.

### Step 1: Output-path semantics in `is_path_allowed` (RED→GREEN)

Add a keyword-only parameter, e.g. `must_exist: bool = True`. When
`must_exist=False` and the resolved target does not exist: require the resolved
PARENT directory to exist and pass the same allowed-dirs walk; keep the UNC
rejection first. Do not change behavior for existing paths or for
`must_exist=True` callers.

Failing tests first, in `tests/unit/test_safety_paths.py`:
- new file inside an allowed dir → True (with `must_exist=False`)
- new file outside allowed dirs → False
- new file whose PARENT does not exist → False
- new file under a UNC path → False
- existing behavior matrix unchanged (default `must_exist=True`)

**Verify**: `uv run pytest tests/unit/test_safety_paths.py -q` → all pass;
`uv run ruff check .` → exit 0.

### Step 2: Fix existing output-path callers

`application/db_export.py:115` and `application/db_analysis.py:93` pass OUTPUT
paths — switch them to `is_path_allowed(path, allowed_dirs, must_exist=False)`.
Confirm with a unit test that exporting to a nonexistent file inside an allowed
dir now passes the guard (use tmp_path).

**Verify**: targeted export/generate_sql tests pass; full unit suite green.

### Step 3: Wire `allowed_dirs` into versioning + maintenance + migration

- `VersioningUseCases.__init__` and `MaintenanceUseCases.__init__` gain
  `allowed_dirs: list[Path] | None = None` (store as `self._allowed_dirs`;
  follow the `DatabaseUseCases` default semantics established in Step 0).
- Guard every caller-supplied path at use-case entry: export/import `out_dir`/
  `file_path`/`input_dir`/`export_dir` (exports/writes → `must_exist=False`;
  reads/imports → `must_exist=True`); maintenance `backup(path)` source (read),
  dev-copy paths, `compact_repair(path)`.
- Migration: guard `extract_schema`'s `database_path` (read) and any other
  filesystem-path parameter found in Step 0 (connection strings/refs are NOT
  paths — do not guard those).
- MCP factories: `_new_versioning`/`_new_maintenance` (and the migration
  equivalent) pass `allowed_dirs_from_env()` exactly like `_new_db`.

**Verify**: new tests — tool call with `out_dir` outside allowed dirs returns
`Err` mentioning "not allowed"; inside allowed dirs succeeds (use tmp_path and
set `allowed_dirs` explicitly on the use case, not the env var). Suite green.

### Step 4: Regression sweep

`rg -n "is_path_allowed" src/` — every caller-supplied filesystem path that
crosses the MCP boundary must now hit the guard or have a one-line comment
justifying why not. List the final inventory in the step report.

**Verify**: `uv run pytest tests/unit/ -q` → all pass;
`uv run pyright src/ms_access_mcp/` → 0 errors;
`uv run python scripts/tool_budget.py` → PASS (no tool shape changes).

## Test plan

- `tests/unit/test_safety_paths.py`: the Step-1 matrix (model after its existing
  cases).
- Versioning/maintenance/migration: one allowed + one denied case per guarded
  entry point; use `tmp_path` and explicit `allowed_dirs=[tmp_path]`.
- Negative security case: `versioning_export_all(out_dir=<outside>)` with
  `ACCESS_MCP_ALLOWED_DIRS` set must return Err and create NO files.

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0 (≥ baseline; no deletions)
- [ ] `uv run ruff check .` exit 0; `uv run pyright src/ms_access_mcp/` 0 errors
- [ ] `rg -n "VersioningUseCases(readonly=readonly, db_path=None)" src/` → no matches
- [ ] Export-to-new-file inside an allowed dir succeeds (tested)
- [ ] Versioning/maintenance/migration paths outside allowed dirs are denied (tested)
- [ ] Default behavior with unset `ACCESS_MCP_ALLOWED_DIRS` is unchanged (Step-0 answer + test)
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- Step 0 reveals the default guard model differs from this plan's assumption
  (e.g. empty list = deny-all is load-bearing somewhere) — stop and report.
- A tool's path parameter is ALSO used as a connection string / non-filesystem
  identifier by the same code path — stop; do not guard ambiguous params by
  guesswork.
- Wiring `allowed_dirs` into a use case requires changing its public
  constructor in a way that breaks the CLI (`cli.py` constructs the same use
  cases) — update the CLI call sites mechanically IF trivial; otherwise stop.
- The existing unit suite is red before you start — stop.

## Maintenance notes

- The threat model sentence for reviewers: the guard exists because the MCP
  client (an LLM) is not fully trusted; every tool that touches the filesystem
  must pass through it. Keep this invariant when adding future tools.
- Deferred: `backends/trusted_locations.py` registry-write review (separate
  security finding); CLI-side path guard sweep; Windows DACL hardening of the
  credentials file (separate finding, small).
- `is_path_allowed` now has two modes — future callers must pick consciously;
  the docstring must state which mode fits reads vs writes (add it in Step 1).
