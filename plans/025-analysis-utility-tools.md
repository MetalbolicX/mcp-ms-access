# Plan 025: Analysis & utility tool gaps

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat c764e12..HEAD -- src/ms_access_mcp/ tests/ plans/README.md`
> Plans 022 and 024 land BEFORE this one — their changes (new `ddl` and
> `migration` profiles) are EXPECTED drift. If anything else in scope changed,
> compare excerpts against live code; on mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/022-ddl-profile.md (the `ddl` profile must exist — `execute_sql_script` lands there), plans/024-migration-profile.md (sequence — both touch `profiles.py`/`server.py`)
- **Category**: feature
- **Planned at**: commit `c764e12`, 2026-09-11

## Execution mode

`@medium` + strict TDD: RED → GREEN → REFACTOR per tool. Read-only analysis
tools get pure-function unit tests; the write tools (`save_query`,
`execute_sql_script`, `maintenance_discard_dev_copy`) get confirm-gate tests
first. This plan adds no user-facing workflow surface, so no SDD cycle.

## Why this matters

Six reference tools remain unmatched after plans 022–024: `get_er_diagram`,
`analyze_query`, `generate_sql`, `save_query`, `discard_dev_copy`, and
`execute_sql_script`. Each is small, each has the capability already present
(or trivially composable) in this repo, and each closes a named parity gap.
Deliberately NOT included (standing exclusions): `launch_access`/`close_access`
(managed lifecycle), active-connection state (stateless design),
`store_credential`/`clear_credentials` (plaintext defect), `recover_access`
(CLI-only by design).

## Current state

### Oracle behavioral specs (read-only oracle: `D:\code\python\ms-access-mcp-server`)

**`get_er_diagram`** — `mcp/schema.py:140-186`, verbatim:

```python
@mcp.tool()
def get_er_diagram(connection_name: str = "default") -> dict:
    """Get the database schema as nodes and edges for ER diagram visualization. ..."""
    adapter = _ensure_connected(connection_name)
    if adapter is None:
        return {"success": False, "error": "Not connected to database"}
    tables = adapter.get_tables()
    relationships = adapter.get_relationships()

    nodes = []
    for table in tables:
        nodes.append({
            "id": table.name,
            "type": "table",
            "data": {
                "label": table.name,
                "columns": [f.model_dump() for f in table.fields],
                "record_count": table.record_count,
            },
        })

    edges = []
    for rel in relationships:
        edges.append({
            "id": rel.name,
            "source": rel.foreign_table,
            "target": rel.table,
            "label": rel.name,
            "animated": False,
        })

    return {
        "success": True,
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }
```

NEW-SERVER ADAPTATION: compose from the EXISTING use cases `get_tables()` and
`get_relationships()` results (their dict shapes), not from adapter model
objects — read `application/database.py` for the exact result dicts and build
nodes/edges from them. The output contract is the oracle's keys: `nodes`,
`edges`, `node_count`, `edge_count`.

**`analyze_query`** — `mcp/analysis.py:13-50`, verbatim (thin tool; logic in
`services/query_analyzer.py`):

```python
@mcp.tool()
def analyze_query(
    sql: str,
    params: list | None = None,
    connection_name: str = "default",
    dry_run: bool = False,
    sample_size: int = 0,
) -> dict:
    """Analyze query performance and return complexity, timing, and schema analysis. ..."""
    adapter = _ensure_connected(connection_name)
    if adapter is None:
        return {"success": False, "error": "Not connected to database"}

    try:
        from ..services.query_analyzer import QueryAnalyzerService

        result = QueryAnalyzerService.analyze(
            sql=sql,
            params=params,
            adapter=adapter,
            dry_run=dry_run,
            sample_size=sample_size,
        )
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}
```

PORT RULE: read oracle `src/ms_access_mcp/services/query_analyzer.py` and port
it to `src/ms_access_mcp/application/analysis.py`, adapted to this repo's
adapter seam (`execute_query` returns `{"success", "rows", "count", "columns"
[, "error"]}` — see `backends/odbc.py:78-92`). STOP if the oracle service
exceeds ~400 lines or drags in other oracle services — then reduce scope to
structural analysis + optional execution timing and record the reduction in
the plan row.

**`save_query`** — `mcp/vba.py:284-331`, verbatim (composition only):

```python
@mcp.tool()
def save_query(query_name: str, sql: str, overwrite: bool = False, connection_name: str = "default") -> dict:
    """Save or update a saved query definition in the Access database. ..."""
    ...
    if overwrite:
        existing_queries = adapter.get_queries()
        query_exists = any(q.name == query_name for q in existing_queries)
        if query_exists:
            result = adapter.set_query_sql(query_name, sql)
            if result.get("success"):
                return {"success": True, "query": query_name, "action": "updated"}
            ...
        else:
            result = adapter.create_query(query_name, sql)
            ...
    else:
        existing_queries = adapter.get_queries()
        query_exists = any(q.name == query_name for q in existing_queries)
        if query_exists:
            return {"success": False, "error": f"Query '{query_name}' already exists. Use overwrite=True to update."}
        result = adapter.create_query(query_name, sql)
        ...
```

NEW-SERVER ADAPTATION: this is pure composition of the EXISTING core use cases
`get_queries`, `create_query`, `set_query_sql` (already MCP tools). Implement
as a new tool function in `mcp_tools/core.py` that calls the three use cases
on ONE `DatabaseUseCases(readonly=False)` instance, `confirm`-gated. Core goes
20 → 21 tools, within the ≤30 budget (plans/README product decision).

**`discard_dev_copy`** — `mcp/dev_copy.py:411-436`, verbatim:

```python
@destructive_guard(action="discard_dev_copy")
@mcp.tool()
def discard_dev_copy(production_path: str | None = None, connection_name: str = "default", confirm: bool = False, dry_run: bool = False) -> dict:
    """Discard the active dev copy and reconnect to production.

    Deletes the dev copy file, removes the manifest, and reconnects to
    the production database. Your production changes are lost.
    ..."""
```

NEW-SERVER ADAPTATION: the stateless design has no "reconnect" — discard =
delete the dev-copy file + remove its manifest. READ
`src/ms_access_mcp/application/maintenance.py` dev-copy implementation
(`create_dev_copy`/`deploy_dev_copy`/`dev_copy_status`) first and mirror its
manifest conventions exactly. New use case `discard_dev_copy(confirm)`;
destructive → `readonly=False` + confirm gate. MCP tool name:
`maintenance_discard_dev_copy` (maintenance-profile prefixing convention),
added to `mcp_tools/maintenance.py` (6 → 7 tools).

**`execute_sql_script`** — oracle `mcp/persistence.py:371` (file-based).
This repo's ADO backend already has the engine:
`src/ms_access_mcp/backends/ado.py:84-112`, verbatim:

```python
    def execute_script(self, sql_script: str) -> dict:
        """Execute a SQL script containing multiple statements.

        Parses the script into individual statements and executes
        each one in order. Returns a summary of results.
        """
        if not self._connected or self._conn is None:
            return {"success": False, "error": "Not connected"}

        statements = _parse_script_lines(sql_script)
        results: list[dict] = []
        errors: list[str] = []

        for stmt in statements:
            if not stmt.strip():
                continue
            try:
                self._conn.Execute(stmt)
                results.append({"statement": stmt[:60], "success": True})
            except Exception as e:
                errors.append(str(e))
                results.append({"statement": stmt[:60], "success": False, "error": str(e)})

        return {
            "success": len(errors) == 0,
            "statements": len(statements),
            "results": results,
            "errors": errors,
        }
```

(`_parse_script_lines` at `ado.py:19-37` strips `--` comments, splits on `;`.)

PORT RULE: promote `_parse_script_lines` into a shared module
`src/ms_access_mcp/backends/script.py` (both backends import it); add
`execute_script` to the DAO backend using `db.Execute(stmt, DAO_DB_FAIL_ON_ERROR)`
per statement (pattern at `dao.py:479-490`) returning the same summary shape;
ODBC rejects with `BackendCapabilityMismatchError` (pattern at
`odbc.py:393-396`). Application use case `execute_sql_script(sql, confirm)` in
`application/database.py` following the plan-022 write pattern. MCP tool
`execute_sql_script` in the **`ddl` profile** (plan 022 must be DONE),
`params: {"sql": "..."}` (text, NOT a file path — no path-guard debate),
confirm-gated.

### `generate_sql`

Oracle `mcp/schema.py:110-137` writes a Jet SQL DDL script via
`adapter.generate_sql(output_path)` with path-guard validation. Backends here
already implement it: `backends/dao.py:220`, `backends/odbc.py:385` (via
`backends/schema_inspector.py:43`). READ those signatures first; add a thin
use case in `application/database.py` (`generate_sql(output_path)` — path
guard FIRST, `database.py:806` pattern) and a read-only MCP tool.
NOTE: `versioning_export_schema_ddl` overlaps conceptually; keep both (oracle
parity) — check `application/versioning.py` and reuse its logic if it already
produces the same artifact.

### Profile wiring

Add `"analysis"` profile (module `ms_access_mcp.mcp_tools.analysis`,
tools: `get_er_diagram`, `analyze_query`, `generate_sql`) to
`profiles.py:14-43` exactly as plan 022 added `ddl`; update `server.py:121-127`
help text. All three are read-only (`readonly=True`, no confirm).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Unit suite | `uv run pytest tests/unit -q` | all pass, 0 failed |
| Full suite | `uv run pytest -q` | 0 failed (2 skipped normal) |
| Integration | `uv run pytest -q -m "com_integration or odbc_integration"` | 0 failed |
| Lint | `uv run ruff check .` | `All checks passed!` |
| Typecheck | `uv run pyright` | `0 errors, 0 warnings, 0 informations` |
| Kill stray Access | `taskkill /F /IM MSACCESS.EXE` | exit 0 or "not found" — either fine |

## Scope

**In scope** (the only files you should modify):

- `src/ms_access_mcp/mcp_tools/analysis.py` (create — 3 read-only tools)
- `src/ms_access_mcp/application/analysis.py` (create — ported query analyzer)
- `src/ms_access_mcp/application/database.py` (add `get_er_diagram`,
  `generate_sql`, `execute_sql_script` use cases)
- `src/ms_access_mcp/application/maintenance.py` (add `discard_dev_copy`)
- `src/ms_access_mcp/mcp_tools/core.py` (add `save_query` ONLY)
- `src/ms_access_mcp/mcp_tools/maintenance.py` (add `maintenance_discard_dev_copy`)
- `src/ms_access_mcp/mcp_tools/ddl.py` (add `execute_sql_script` — plan 022's module)
- `src/ms_access_mcp/backends/script.py` (create — shared script parser)
- `src/ms_access_mcp/backends/ado.py`, `backends/dao.py`, `backends/odbc.py`
  (`execute_script` only — ado refactored to shared parser, dao added, odbc rejects)
- `src/ms_access_mcp/profiles.py`, `src/ms_access_mcp/server.py` (analysis profile)
- `tests/unit/test_mcp_analysis.py` (create), `tests/unit/test_database.py`,
  `tests/unit/test_maintenance.py`, `tests/unit/test_mcp_core.py`,
  `tests/unit/test_mcp_maintenance.py`, `tests/unit/test_mcp_ddl.py` (extend)
- `plans/README.md` (status row)

**Out of scope**:

- `pyproject.toml` / `uv.lock` (no dependency changes; preserve the deliberate
  uncommitted `uv.lock` modification; `.opencode/` never committed)
- CLI additions for these tools (separate decision; MCP-only here)
- The excluded-by-design tools listed in "Why this matters"
- Any change to existing tool names, signatures, or result shapes

## Git workflow

- Branch: `plan/025-analysis-utility-tools`
- Conventional commits, English, no AI attribution; one commit per step is fine.
- Do NOT push. Do NOT commit `uv.lock` or `.opencode/`.

## Steps

### Step 1: Pre-flight

Drift check (022/024 changes expected); `git status --porcelain`; confirm
plans 022 and 024 rows are DONE in `plans/README.md`; branch.

**Verify**: on branch `plan/025-analysis-utility-tools`; `ddl.py` and
`migration` profile exist.

### Step 2: `get_er_diagram` (RED → GREEN)

RED: `tests/unit/test_database.py` — fake adapter returning known
tables/relationships; assert nodes/edges/counts contract. GREEN: use case
composing `get_tables` + `get_relationships`; tool in new
`mcp_tools/analysis.py` (registry + `register`, versioning.py:62-93 pattern);
profile wiring.

**Verify**: new test passes; `load_profile("analysis")` returns the module.

### Step 3: `generate_sql` (RED → GREEN)

RED: fake adapter with `generate_sql`; path-guard-first test (disallowed out
path → Err, adapter never called). GREEN: thin use case + tool in
`analysis.py`. Check `application/versioning.py` first for reusable logic.

**Verify**: tests pass; ruff clean.

### Step 4: `analyze_query` (RED → GREEN)

RED: fake-adapter tests for dry_run structure, execution-timing path, error
propagation. GREEN: port oracle `services/query_analyzer.py` →
`application/analysis.py` per the PORT RULE above (STOP conditions apply);
tool with params `sql`, `dry_run`, `sample_size` (`params` dict plumbing).

**Verify**: tests pass; tool registered in the analysis profile.

### Step 5: `save_query` in core (RED → GREEN)

RED: `tests/unit/test_mcp_core.py` — extend `EXPECTED_TOOLS` (20 → 21) +
composition tests with fakes: exists+overwrite → "updated"; exists+no
overwrite → Err naming `overwrite=True`; missing → "created"; confirm gate.
GREEN: tool function composing the three use cases on one `readonly=False`
instance.

**Verify**: `uv run pytest tests/unit/test_mcp_core.py -q` green; core tool
count 21.

### Step 6: `maintenance_discard_dev_copy` (RED → GREEN)

RED: `tests/unit/test_maintenance.py` — fake manifest state; discard removes
dev-copy file + manifest; confirm gate; missing-manifest → clean Err.
GREEN: use case mirroring the existing manifest conventions + tool in
`maintenance.py` (registry 6 → 7).

**Verify**: maintenance unit tests green.

### Step 7: `execute_sql_script` (RED → GREEN)

RED: parser unit tests (comments stripped, `;` split, empty statements
skipped); DAO `execute_script` fake-dispatcher test (per-statement Execute,
summary shape identical to ado's); ODBC rejection test; use-case confirm gate;
ddl-tool registration test (16 → 17).
GREEN: `backends/script.py` + ado refactor + dao method + odbc rejection +
use case + tool.

**Verify**: `uv run pytest tests/unit -q` green; `backends/ado.py` diff shows
ONLY the parser extraction (no behavior change — its existing tests pin it).

### Step 8: Gates + index

Full gate table, all green (quote each); COM runs bracketed by `taskkill`;
update `plans/README.md` row 025 → DONE.

**Verify**: `git status` shows only in-scope files.

## Test plan

- Unit per step above; every write tool has a confirm-gate test; every
  read-only tool has a result-shape test.
- Integration: none required beyond existing suites (all capabilities ride
  already-qualified backend paths). If `execute_sql_script` integration is
  cheap on a temp fixture copy, add ONE round-trip test
  (`tests/integration/test_execute_script.py`, com_integration marker) —
  optional but preferred.
- Verification: full gate table green.

## Done criteria

ALL must hold:

- [ ] `analysis` profile exists with exactly `get_er_diagram`,
  `analyze_query`, `generate_sql`; included in `"full"`
- [ ] Core = 21 tools incl. `save_query` (≤30 budget intact)
- [ ] Maintenance = 7 tools incl. `maintenance_discard_dev_copy`
- [ ] ddl profile = 17 tools incl. `execute_sql_script`
- [ ] `backends/script.py` shared parser; ado/dao/odbc `execute_script`
  behavior: execute / execute / clean rejection
- [ ] No changes to pre-existing tool names/signatures/result shapes
- [ ] `uv run pytest -q` 0 failed; `ruff` clean; `pyright` 0/0/0
- [ ] `plans/README.md` row 025 updated; `uv.lock`/`.opencode/` untouched

## STOP conditions

Stop and report back (do not improvise) if:

- Plan 022 or 024 is not DONE (dependency missing).
- Oracle `services/query_analyzer.py` exceeds ~400 lines or imports other
  oracle services (then apply the reduced scope from Step 4 and record it).
- The dev-copy manifest conventions in `application/maintenance.py` don't
  match what this plan assumes (read them first; on mismatch, report).
- `backends/dao.py:220` / `odbc.py:385` / `schema_inspector.py:43` don't
  exist or have incompatible signatures.
- Any pre-existing test needs editing to stay green (other than the planned
  EXPECTED_TOOLS/registry-count extensions).
- A gate fails twice after one reasonable fix attempt.

## Maintenance notes

- After this plan, the reference-parity gap list shrinks to the deliberate
  exclusions only; the comparison table in `plans/README.md` (or a future
  parity doc) should be regenerated.
- `analyze_query` on the ODBC backend can only do structural + timing
  analysis (no showplan); the tool description must say so.
- Reviewer focus: the `save_query` composition ordering (check-exists →
  create/update on ONE instance), script-parser edge cases (`;` inside string
  literals is NOT handled — same limitation as the oracle; document it in the
  tool description), and that `discard_dev_copy` can never touch the
  production path.
- If a future plan adds CLI commands for these tools, reuse the use cases
  unchanged — that is the layering test this plan's seams must pass.
