# Plan 078: Honest exit codes, startup argv, gate behavior, and config hygiene

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/cli_format.py src/ms_access_mcp/server.py src/ms_access_mcp/cli.py scripts/tool_budget.py pyproject.toml .gitignore`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/077-*.md (green baseline)
- **Category**: dx / bug
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

Five small dishonesty defects make failures invisible: `--json` CLI failures
exit 0; a bare `ms-access-mcp mcp` invocation crashes before startup because
the server parser re-reads the outer CLI's argv; the CI tool-budget gate
reports "OK: 0 tools" when tool registration itself throws and never checks
the `ddl`/`analysis`/`migration` profiles; `pyproject.toml` carries a
per-file-ignore for a path that no longer exists; and `.ruff_cache/` is not
gitignored. Individually small; together they let real breakage pass gates.

## Current state

- `src/ms_access_mcp/cli_format.py` — `format_result` (lines 33–46). Exact
  current code:

```python
    if json_output:
        print(json.dumps(result_to_dict(result)))
        return 0
```

  An `Err` in JSON mode prints `{"success": false, ...}` but returns 0. The
  docstring above it promises "Exit code: 0 for success, 1 for error".
- `src/ms_access_mcp/cli.py` — the `mcp` subcommand handler (`_run_mcp`,
  ~line 1463) calls `server.main(None)` (via `mcp_main`) when no
  `--profile` is given.
- `src/ms_access_mcp/server.py` — `_parse_args`/`parse_args` receives that
  `None` (~line 150); `argparse` then re-reads `sys.argv[1:]`, which still
  contains `mcp`, producing `error: unrecognized arguments: mcp` (exit 2).
  Observed during the audit with a mocked argv. Profile resolution via
  `ACCESS_MCP_PROFILE` (`profiles.py:79-95`) is never reached.
  Configured clients (opencode configure) always inject `--profile`, so only
  bare manual invocations break.
- `scripts/tool_budget.py` — profile list (~line 32) is
  `["core", "automation", "maintenance", "versioning", "full"]`, omitting
  `ddl`, `analysis`, `migration` (all valid per `src/ms_access_mcp/profiles.py`).
  Build failures are swallowed (~lines 36–41):
  `except Exception: results[profile] = (0, [])` → prints
  `OK: core profile has 0 tools (<=30)` and exits 0. The gate-2/gate-3 WARN
  lines for `full` are informational by design (comment in the script) — do
  NOT turn those into failures.
- `pyproject.toml` — per-file-ignores contains
  `"src/ms_access_mcp/sql_builder.py" = ["EM", "TRY003"]`; the file lives at
  `src/ms_access_mcp/application/sql_builder.py` since the plan-038
  extraction and is already covered by the `application/**` entry.
- `.gitignore` — no `.ruff_cache/` entry (recon-confirmed).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | exit 0 |
| Budget gate | `uv run python scripts/tool_budget.py` | exit 0, all 8 profiles listed |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/cli_format.py`
- `src/ms_access_mcp/server.py` (argv handling only)
- `src/ms_access_mcp/cli.py` (only the `_run_mcp`/`mcp_main` call, if needed to pass `[]`)
- `scripts/tool_budget.py`
- `tests/unit/test_cli_dispatch.py`, `tests/unit/test_server_build.py`, `tests/unit/test_tool_budget.py` (create)
- `pyproject.toml` (remove the dead ignore entry; add packaging metadata fields)
- `.gitignore`

**Out of scope**:
- `scripts/step5_qualify.py`, `scripts/qualify.py` (their gate lists mirror CI correctly).
- Any change to the `full`-profile WARN semantics.
- Tool registration code itself (`mcp_tools/**`).

## Git workflow

- Branch: `plan/078-cli-gate-honesty` from `main` (after 077 lands).
- Conventional commits, one per logical unit, e.g.
  `fix(cli): return exit 1 for Err results in JSON mode`.

## Steps

### Step 1: JSON-mode exit code (strict TDD)

RED: add to `tests/unit/test_cli_dispatch.py` (format_result is already
imported there):

```python
def test_format_result_json_err_exit_code():
    assert format_result(Err("boom"), json_output=True) == 1
    assert format_result(Ok(data={"a": 1}), json_output=True) == 0
```

GREEN: in `cli_format.py`, print the JSON payload for both `Ok` and `Err`,
then `return 0 if isinstance(result, Ok) else 1`. Keep stderr text-mode
behavior unchanged.

**Verify**: `uv run pytest tests/unit/test_cli_dispatch.py -q` → pass.

### Step 2: Bare `mcp` startup (strict TDD)

RED: add a test in `tests/unit/test_server_build.py` asserting
`server._parse_args([])` (or the equivalent private helper) succeeds and
resolves the default profile without touching `sys.argv` (monkeypatch
`sys.argv` to `["ms-access-mcp", "mcp"]` to prove independence).

GREEN: make the `mcp` subcommand path pass an empty argv (`[]`) into the
server entry instead of `None` — either in `cli.py`'s `_run_mcp` call or at
the top of `server.main`/`_parse_args` (`argv if argv is not None else []`).
Do not rewrite the parser.

**Verify**: `uv run python -m ms_access_mcp.cli mcp --help` prints usage (exit 0); new test passes.

### Step 3: Tool-budget gate honesty (strict TDD)

RED: create `tests/unit/test_tool_budget.py` loading `scripts/tool_budget.py`
via importlib (pattern: `tests/unit/test_qualify_script.py`) with a fake
server object, covering:
1. build raising → script must exit non-zero (call its main with
   `SystemExit` expected, monkeypatching `build_server` to raise);
2. a profile registering 0 tools while marked enabled → non-zero;
3. the profile list includes `ddl`, `analysis`, `migration` (assert on the
   module-level constant).

GREEN: in `scripts/tool_budget.py` — wrap per-profile build so an exception
records `("ERROR", [str(e)])` and the runner exits 1; extend the profile
list to all eight profile names from `profiles.py`; keep the ≤30 budget
check applying to `core` only (that is the documented budget).

**Verify**: `uv run pytest tests/unit/test_tool_budget.py -q` → pass; `uv run python scripts/tool_budget.py` → exit 0 with 8 profile lines.

### Step 4: Config hygiene (direct edit)

- `pyproject.toml`: delete the `"src/ms_access_mcp/sql_builder.py"` per-file-ignore entry.
- `.gitignore`: add `.ruff_cache/`.
- `pyproject.toml` `[project]`: add the publishing metadata that a released
  wheel should carry — `readme = "README.md"`, `license`, `authors`, and a
  `[project.urls]` block pointing at the repository. Use the repo's actual
  remote (`git remote get-url origin`) and keep values factual.

**Verify**: `uv run ruff check .` → exit 0; `git check-ignore .ruff_cache` → prints the path; `uv run python -m build --help` NOT required (no build step in this plan).

### Step 5: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → exit 0; `uv run python scripts/qualify.py` → exit 0.

## Test plan

- New: `tests/unit/test_tool_budget.py` (3 cases above).
- Extended: one exit-code case in `test_cli_dispatch.py`; one argv case in
  `test_server_build.py`.
- Existing pins to check (not blindly keep): any test asserting
  `format_result(...) == 0` for an `Err` must be refreshed with rationale.

## Done criteria

- [ ] `format_result(Err(...), json_output=True) == 1` (test exists and passes)
- [ ] `python -m ms_access_mcp.cli mcp --help` exits 0
- [ ] `scripts/tool_budget.py` exits non-zero on build failure / zero-tool profile; lists 8 profiles
- [ ] `grep -n "sql_builder.py" pyproject.toml` returns no per-file-ignore hit
- [ ] `.gitignore` contains `.ruff_cache/`
- [ ] `uv run python scripts/qualify.py` exit 0
- [ ] `plans/README.md` row updated

## STOP conditions

- If fixing the exit code breaks existing CLI tests that legitimately rely on
  exit 0 for `--json` errors, stop and report the list — that is a product
  decision (documented scripts may depend on it), not something to silently
  rewrite.
- If `server.py`'s arg parsing has drifted such that `[]` changes
  `--profile` semantics, stop and report.

## Maintenance notes

- The exit-code change is user-visible; mention it in the next release notes.
- When a new profile is added to `profiles.py`, `scripts/tool_budget.py`
  must be updated in the same commit — its profile list is now tested, so CI
  will catch drift.
