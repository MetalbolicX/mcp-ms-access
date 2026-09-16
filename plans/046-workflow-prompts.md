# Plan 046: MCP prompts for the three flagship workflows (analyze / develop / migrate)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat dde0998..HEAD -- src/ms_access_mcp/server.py src/ms_access_mcp/mcp_tools/ README.md tests/unit/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: plans/045-typed-core-tools.md (prompt recipes reference tool
  parameters; the typed core signatures must be settled first)
- **Category**: direction / dx
- **Planned at**: commit `dde0998`, 2026-09-16

## Why this matters

The server registers ~140 tools and ZERO MCP prompts — so every client session
re-derives multi-step orchestration from scratch: which tools, in what order,
with what gotchas. The workflows already exist implicitly in the code
(analyze: `get_tables` → `get_table_schema` → `get_relationships` →
`get_er_diagram` → `analyze_query`; develop: `create_access_database` →
`execute_sql_script` → `create_table` → `compile_vba`; migrate:
`extract_schema` → `upload_schema` → `transfer_data` → `get_migration_status`).
MCP prompts are the protocol-native mechanism to ship exactly this guidance:
cheap, additive, and they appear in every MCP client's slash-command UX. This
is the highest-leverage pure-DX addition available.

## Current state

- `src/ms_access_mcp/server.py:88-103` — `build_server(profile)` dynamically
  imports profile modules and calls each module's `register(mcp)`. Only tools
  are ever registered; a grep for `@mcp.prompt` / `add_prompt` /
  `mcp.types.Prompt` across `src/` returns zero hits (verified).
- Registration precedent — `mcp_tools/_registry.py:19-22`: data-driven tables
  + one-line `register(mcp)` per module. Prompts should follow the same spirit:
  a data table of (name, description, template) + a registration loop.
- FastMCP prompt API (official SDK, `mcp>=1.27,<2.0`): functions decorated with
  `@mcp.prompt()` or registered imperatively; a prompt function returns a
  string or a list of messages. VERIFY the exact API in the installed SDK
  version before writing code (Step 1) — do not guess from memory.
- Profile gating constraint: prompt recipes reference tools from MULTIPLE
  profiles (analyze needs core+analysis; migrate needs migration). Prompts are
  cheap in context (only name+description are listed), so register ALL prompts
  unconditionally in `build_server`, and have each prompt's text state which
  `--profile` the tools require — e.g. "requires the `analysis` profile
  (`--profile analysis`)". Do NOT attempt profile-gated prompts.
- Settled scope decisions the prompt text must respect (plans/README.md):
  plaintext passwords never appear as tool arguments (credentials resolve
  server-side — the analyze/develop prompts should mention
  `ms-access-mcp credentials set` for protected DBs); write operations require
  `confirm=True`; no remote transports.

## Commands you will need

| Purpose     | Command                                       | Expected on success |
|-------------|-----------------------------------------------|---------------------|
| Lint        | `uv run ruff check .`                         | exit 0              |
| Typecheck   | `uv run pyright src/ms_access_mcp/`           | 0 errors            |
| Unit tests  | `uv run pytest tests/unit/ -q`                | all pass            |
| Budget      | `uv run python scripts/tool_budget.py`        | PASS (tool counts unchanged) |
| Targeted    | `uv run pytest tests/unit/test_mcp_prompts.py -q` | all pass        |

## Scope

**In scope**:
- `src/ms_access_mcp/mcp_tools/prompts.py` (create) — prompt definitions +
  `register_prompts(mcp)`
- `src/ms_access_mcp/server.py` — call `register_prompts(mcp)` in
  `build_server` (unconditional, before/after tool registration)
- `tests/unit/test_mcp_prompts.py` (create)
- `README.md` — short "Workflow prompts" section

**Out of scope**:
- MCP resources (schema-as-resource is a separate grounded finding; its own
  spike plan — do not mix)
- Any tool registration changes, profile composition (`--profile core,analysis`
  is a separate finding), new tools
- `cli.py`

## Git workflow

- Branch: `plan/046-workflow-prompts`
- Conventional commits; one per step. Do NOT push unless instructed.

## Steps

### Step 1: Verify the installed FastMCP prompt API

Read the installed SDK (`uv run python -c "import mcp.server.fastmcp, inspect;
print(inspect.signature(mcp.server.fastmcp.FastMCP.add_prompt))"` and the
module source) and record in the step report: how prompts are registered
imperatively, what the prompt function may return (str vs messages), and how
`list_prompts`/`get_prompt` expose them. All code below depends on this.

**Verify**: the step report contains the verified signature(s) and a minimal
working registration snippet executed against the installed version.

### Step 2: Define the three prompts (RED→GREEN)

Create `src/ms_access_mcp/mcp_tools/prompts.py` with a data table + registrar
matching the `_registry.py` spirit. Content requirements per prompt (the text
is the product — invest here):

- `analyze_database(path: str)` — step recipe: connect (mention server-side
  credential resolution + the `credentials set` CLI for protected DBs) →
  `get_tables` → `get_table_schema` per relevant table → `get_relationships` →
  `get_indexes` → `get_database_statistics` → (`analysis` profile:
  `get_er_diagram`, `analyze_query`). End with: summarize findings,
  normalization/index opportunities, and ASK before any write.
- `develop_new_database(path: str)` — `create_access_database` → design tables
  → `create_table`/`execute_sql_script` (ddl profile, `confirm=True` gates) →
  `create_index`/`create_relationship` → optional `export_data`-based seed
  check → `maintenance_backup` before destructive changes. State the readonly
  default and how writes are enabled.
- `migrate_database(path: str, target: str)` — `extract_schema` → review the
  extracted model → `upload_schema` → `transfer_data` → poll
  `get_migration_status` → verify counts. Mention `connection_ref` profiles for
  target credentials (never passwords inline) and the `migrate` extra for
  postgres/mysql drivers.

Each prompt returns a user-message string with the db path interpolated and
explicit tool names in backticks. Keep each under ~120 lines of text.

Failing test first (`tests/unit/test_mcp_prompts.py`): build the server, list
prompts, assert the three names exist; get each prompt and assert the returned
text mentions the expected tool names and the interpolated path.

**Verify**: `uv run pytest tests/unit/test_mcp_prompts.py -q` → pass;
`uv run ruff check .` → exit 0.

### Step 3: Wire into `build_server` + docs

Call `register_prompts(mcp)` in `build_server` for every profile (prompts are
profile-independent). Add a short README "Workflow prompts" section listing the
three prompts and their required profiles.

**Verify**: `uv run pytest tests/unit/ -q` → all pass;
`uv run python scripts/tool_budget.py` → PASS (tool counts unchanged — prompts
are not tools); `uv run pyright src/ms_access_mcp/` → 0 errors.

## Test plan

- `tests/unit/test_mcp_prompts.py`: names listed; per-prompt content assertions
  (tool names present, path interpolated, credential guidance present);
  registration works for `core` and `full` profiles identically.
- Structural pattern: `tests/unit/test_mcp_registry.py` (plan 033) for
  build-and-inspect style.

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0 (≥ baseline; no deletions)
- [ ] The three prompts are listed by `build_server("core")` and `build_server("full")`
- [ ] Prompt texts reference only tools that exist (cross-checked in test)
- [ ] `uv run python scripts/tool_budget.py` PASS; ruff clean; pyright 0 errors
- [ ] README documents the prompts
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- The installed FastMCP version's prompt API differs materially from Step 1's
  assumption (e.g. no imperative registration) — adapt via the decorator form
  INSIDE `prompts.py` only; if neither form fits the `build_server` dynamic
  pattern, stop and report.
- A prompt recipe would need to reference a tool that does not exist (recipe
  drift) — stop; fix the recipe, do not invent tools.
- Registering prompts unconditionally breaks `tool_budget.py` profile-composition
  gates (Gate 2/3 count more than tools) — stop and report the gate semantics.
- The existing unit suite is red before you start — stop.

## Maintenance notes

- Prompt text ages with the tool surface: when wave-2 typed tools (045
  follow-up) or new profiles land, re-read the three recipes. The content tests
  will catch name drift.
- Reviewers: prompt text is LLM-facing documentation — check it for accuracy
  against the facade semantics (especially confirm/readonly guidance), not just
  style.
- Natural follow-ups (separate plans, do not expand this one): schema-as-MCP-
  resource spike; composable profiles (`--profile core,analysis`); a
  `compare_schemas` spike composing extract+render on two databases.
