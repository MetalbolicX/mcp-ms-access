# Plan 045: Typed MCP tool schemas — wave 1 (core profile)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat dde0998..HEAD -- src/ms_access_mcp/mcp_tools/ src/ms_access_mcp/application/database.py scripts/tool_budget.py tests/unit/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: plans/042-facade-adapter-contract.md (facade signatures must
  be settled before the tool layer mirrors them)
- **Category**: direction / dx
- **Planned at**: commit `dde0998`, 2026-09-16

## Why this matters

This is the single biggest LLM-UX defect in the server — and it contradicts a
recorded product decision. `plans/README.md` line 15 states: "MCP token budget:
`core` profile ≤ 30 **typed** tools". What shipped is the opposite: ~134 of
~140 tools take `(path: str, params: dict[str, Any] | None, confirm: bool)`, so
the wire schema says `params: object` and the LLM client must GUESS dict keys
(`sql`? `table`? `row`? `top`?) from one-line descriptions that don't enumerate
them ("Get schema for a table"). Every tool call carries a fail-and-retry tax,
and the ≤30 budget's "typed" justification is unmet. FastMCP generates JSON
Schema from function signatures automatically — the fix is mechanical per tool,
and the facade methods are already typed to mirror. Wave 1 covers the 21 core
tools; ddl/analysis/maintenance/migration are explicitly a follow-up wave.

## Current state

`src/ms_access_mcp/mcp_tools/core.py` — the generic wrapper shape (verbatim,
:81-94, via the dead `_make_tool` factory at :70-130):

```python
def wrapper(
    path: str,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
) -> CallToolResult:
    # Create a fresh db instance for each call
    db = DatabaseUseCases(readonly=not write_op)

    # Connect if path provided
    if path:
        result = db.open_database(path)
        ...
```

Note: `_make_tool` has ZERO callers — the 21 wrappers are hand-written but
repeat the same open-and-check boilerplate (`db.open_database(path)` appears
~20×). This plan replaces both the dead factory and the boilerplate with typed
functions plus one shared open-helper.

The registry mechanism (KEEP — data-driven registration is a settled decision,
plans/README.md 2026-09-14: decorators were rejected because profile-gated
dynamic loading depends on the registry):

```python
# mcp_tools/_registry.py:19-22
def register_tools(mcp: FastMCP, tools: ToolRegistry) -> None:
    for name, (fn, description) in tools.items():
        mcp.add_tool(fn, name=name, description=description)
```

The typed exemplar already in the repo — `mcp_tools/versioning.py:19-23`:

```python
def versioning_export_object(kind: str, name: str, out_dir: str) -> CallToolResult:
    """Export a single object (form, report, macro, query, module) to a text file."""
    uc = _new_versioning(readonly=True)
    result = uc.export_object(kind, name, out_dir)
    return to_mcp_result(_result_to_dict(result))
```

Facade signatures to mirror (from `application/database.py`, verified):
`update_data(table_name: str, data: dict[str, Any], where: str | None = None,
connection_name="default", confirm=False)`, `create_table(table_name,
columns: dict[str, str], ...)`, `create_index(table_name, index_name,
columns: list[str], unique=False, ...)`, etc. — read each facade signature at
execution time; they are the source of truth (and plan 042 fixes the drifted
ones first).

Core tool inventory (`core.py:587-609`, 21 tools): connect_access,
disconnect_access, list_connections, get_tables, get_table_schema, get_indexes,
get_relationships, get_queries, create_query, set_query_sql, delete_query,
save_query, query_data, insert_data, update_data, delete_data, execute_raw_sql,
export_data, get_database_statistics, get_system_tables, get_linked_tables.

Schema introspection precedent: `scripts/tool_budget.py:12-23` already reaches
`server._tool_manager._tools` to count tools — the new schema test uses the
same access pattern to read each tool's `inputSchema`.

## Commands you will need

| Purpose     | Command                                     | Expected on success |
|-------------|---------------------------------------------|---------------------|
| Lint        | `uv run ruff check .`                       | exit 0              |
| Typecheck   | `uv run pyright src/ms_access_mcp/mcp_tools/` | 0 errors          |
| Unit tests  | `uv run pytest tests/unit/ -q`              | all pass            |
| Budget      | `uv run python scripts/tool_budget.py`      | PASS (21 core tools, unchanged names) |
| Targeted    | `uv run pytest tests/unit/test_mcp_core.py tests/unit/test_tool_schemas.py -q` | all pass |

## Scope

**In scope**:
- `src/ms_access_mcp/mcp_tools/core.py` — typed wrappers for the 21 core tools;
  delete the dead `_make_tool` factory; one shared `_open_or_err` helper for
  the open-and-check boilerplate
- `tests/unit/test_tool_schemas.py` (create) — inputSchema assertions
- `tests/unit/test_mcp_core.py` (extend) — per-tool behavior through the new
  signatures
- `README.md` — one paragraph noting the typed-params wire change (client-visible)

**Out of scope**:
- `mcp_tools/ddl.py`, `analysis.py`, `maintenance.py`, `migration.py`,
  `automation.py` — wave 2 (follow-up plan; record in plans/README.md backlog)
- Tool NAMES and result payload shapes — the wire contract changes only in
  REQUEST schema (params dict → typed args); responses stay byte-compatible
- The connection-lifecycle redesign (per-call `DatabaseUseCases`, disconnect
  semantics) — separate high-impact finding, its own plan; here you only keep
  the existing per-call construction working behind typed signatures
- `server.py`, `profiles.py`, `_registry.py` — the registry pattern stays

## Git workflow

- Branch: `plan/045-typed-core-tools`
- Conventional commits; one per cohort step. Do NOT push unless instructed.

## Steps

### Step 1: Schema golden test first (RED)

Create `tests/unit/test_tool_schemas.py`: build `build_server("core")`, walk
the registered tools (precedent: `scripts/tool_budget.py:12-23`), and assert
for EVERY core tool that `inputSchema["properties"]` contains concrete typed
properties — i.e. no tool's entire parameter surface is a single opaque
`params` object. Write the assertion so it fails on today's code and passes
only when real per-param schemas exist. Also assert the 21 tool NAMES are
unchanged (pin the set).

**Verify**: `uv run pytest tests/unit/test_tool_schemas.py -q` → FAILS on the
current code (quote the failure in the commit message).

### Step 2: Extract the shared open-helper

Replace the ~20× repeated open-and-check block with one helper in `core.py`:

```python
def _open_or_err(db: DatabaseUseCases, path: str) -> CallToolResult | None:
    """Open path on db; return an error CallToolResult on failure, else None."""
```

Migrate `connect_access` first as the exemplar; behavior must stay identical
(existing tests pin it).

**Verify**: `uv run pytest tests/unit/test_mcp_core.py -q` → all pass.

### Step 3: Type the introspection cohort (read tools)

Convert, mirroring facade signatures exactly: `get_tables`, `get_table_schema`,
`get_indexes`, `get_relationships`, `get_queries`, `get_database_statistics`,
`get_system_tables`, `get_linked_tables`, `list_connections`,
`disconnect_access`. Target shape:

```python
def get_table_schema(path: str, table: str) -> CallToolResult:
    """Get schema for a table.

    Args:
        path: Absolute path to the .accdb/.mdb file.
        table: Table name (case-insensitive, as stored in the database).
    """
```

Every parameter gets a docstring line — FastMCP surfaces these in the schema
description. Remove `params`/`confirm` from read tools entirely.

**Verify**: schema test now passes for this cohort; `test_mcp_core.py` updated
to call the typed signatures (no `params=` dicts); suite green.

### Step 4: Type the query + data-write cohort

Convert `query_data`, `execute_raw_sql`, `create_query`, `set_query_sql`,
`delete_query`, `save_query`, `insert_data`, `update_data`, `delete_data`,
`export_data`. Write tools keep an explicit `confirm: bool = False` parameter.
`execute_raw_sql` keeps its existing write-policy pre-check semantics (today in
`_make_tool` :108-121 — port that logic into the typed wrapper; it is
load-bearing).

**Verify**: schema test passes for all 21; confirm-gating tests still pass;
`uv run python scripts/tool_budget.py` → PASS.

### Step 5: Delete the dead factory + docs

Delete `_make_tool` (zero callers after Steps 3–4) and update the README tool
section to state that core tools take typed parameters (one paragraph; note for
client developers that `params` dicts are gone from core).

**Verify**: `rg -n "_make_tool" src/ tests/` → no matches; full suite green;
`uv run pyright src/ms_access_mcp/mcp_tools/` 0 errors.

## Test plan

- `tests/unit/test_tool_schemas.py` (new): per-tool concrete-properties
  assertion + tool-name set pin. This is the drift guard — any future generic
  wrapper fails it.
- `tests/unit/test_mcp_core.py` (extend): for each converted tool, one call
  with typed args asserting the facade received the exact kwargs (pattern
  already used by the `connect_ref` passthrough tests from plan 040).
- Confirm/write-policy behavior: readonly + missing confirm → error payload;
  unchanged from today.

## Done criteria

- [ ] `uv run pytest tests/unit/ -q` exits 0 (≥ baseline; no deletions)
- [ ] `uv run pytest tests/unit/test_tool_schemas.py -q` — all 21 core tools have concrete typed schemas
- [ ] `uv run python scripts/tool_budget.py` PASS (21 core tools, same names)
- [ ] `rg -n "params: dict" src/ms_access_mcp/mcp_tools/core.py` → no matches
- [ ] `rg -n "_make_tool" src/` → no matches
- [ ] `uv run ruff check .` exit 0; `uv run pyright src/ms_access_mcp/mcp_tools/` 0 errors
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated + wave-2 backlog entry added

## STOP conditions

- FastMCP does NOT generate per-parameter schemas from typed signatures the way
  this plan assumes (inspect one converted tool's `inputSchema` in Step 3
  before converting the rest) — stop and report the actual generated schema.
- A facade method's true signature differs from the excerpts (plan 042 changed
  it, or drift) — mirror the LIVE signature, and if it is untyped/dict-shaped
  itself, stop: fix the facade first (that is plan 042's job).
- Keeping `execute_raw_sql`'s write-policy semantics requires behavior the
  generic wrapper had but a typed signature cannot express — stop and report.
- The existing unit suite is red before you start — stop.

## Maintenance notes

- The wire contract change is REQUEST-side only; client configs (OpenCode
  registration etc.) are unaffected. Note it in the release notes as
  client-visible.
- Wave 2 follow-up (record as backlog): `ddl.py` (17 tools), `analysis.py`
  (3), `maintenance.py` (7), `migration.py` (4) — same pattern; `automation.py`
  (~66 tools) needs a typing strategy decision first (per-control property bags
  may legitimately stay dict-typed).
- Reviewers: scrutinize docstrings — they ARE the LLM-facing documentation now;
  every param line should state format/constraints (e.g. "WHERE clause WITHOUT
  the WHERE keyword" if that is the facade's convention — check the facade
  docstring and copy its language).
