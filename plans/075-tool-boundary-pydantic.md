# Plan 075: Validate migration + linked-table tool params with pydantic models (no more raw-dict trust at the boundary)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/mcp_tools/migration.py src/ms_access_mcp/mcp_tools/ddl.py src/ms_access_mcp/models/`
> On any change, compare "Current state" excerpts against live code.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW-MED (stricter types may reject previously-tolerated junk — intended)
- **Depends on**: plan 070 (migration.py application layer settled first)
- **Category**: security | dx
- **Planned at**: commit `a7f2edb`, 2026-09-24

## Why this matters

Core tools validate via typed pydantic parameters (plan 045), but the tools
that accept EXTERNAL connection identifiers still pull raw dicts:
`mcp_tools/migration.py` (`connection_string = p.get("connection_string", "")`)
and `mcp_tools/ddl.py` linked-table handlers (`p.get("connect_string", "")`).
Non-string or malformed values pass unvalidated into the application layer
and surface as raw type-confusion errors deep in `contains_credential` /
`get_connector` — exactly on the tools handling remote connection strings.
The schema-validation contract core tools enforce is absent where it matters
most. (Downstream mitigations exist — the hard-reject of embedded credential
tokens — this is boundary hardening, not an open injection.)

## Current state

- `src/ms_access_mcp/mcp_tools/migration.py` — `upload_schema` and
  `transfer_data` handlers (def around :87-140):
  ```python
  uc = _new_migration()
  p = params or {}
  target_type = p.get("target_type", "")
  connection_string = p.get("connection_string", "")
  connection_ref = p.get("connection_ref", "")
  schema_json = p.get("schema_json", {})

  if not target_type:
      dict_result = {"success": False, "error": "target_type is required"}
      return to_mcp_result(dict_result)
  if not connection_string and not connection_ref:
      dict_result = {"success": False, "error": "connection_string is required"}
      return to_mcp_result(dict_result)
  ```
  (Same pattern in `extract_schema` before :74 and `get_migration_status`.)
- `src/ms_access_mcp/mcp_tools/ddl.py:120-195` — four linked-table handlers:
  ```python
  p = params or {}
  name = p.get("name", "")
  source_table = p.get("source_table", "")
  connect_string = p.get("connect_string", "")
  connect_ref = p.get("connect_ref", "")
  connection_name = p.get("connection_name", "default")
  uc = _new_db(readonly=False)
  result = uc.create_linked_table(...)
  ```
  (`create_linked_table` ~:124, `refresh_linked_table` ~:149,
  `unlink_table` ~:174, `upsert_linked_table` ~:186.)
- `src/ms_access_mcp/models/migration.py` — pydantic v2 models exist
  (`ExtractedSchema`:30, `TableResult`:52, `MigrationJob`:78,
  `TableTransferConfig`:106) but NONE model the tool PARAMS; no
  connect_string model exists anywhere (searched — only
  `models/__init__.py:103` `LinkedTableInfo.connect_string`, an OUTPUT
  dataclass).
- Error convention at this boundary: `{"success": False, "error": "..."}`
  through `to_mcp_result` — validation errors must use the same envelope.
- Test exemplars: `tests/unit/test_mcp_migration.py` (plan 027 — param
  validation tests exist for the manual checks), `tests/unit/test_mcp_ddl.py`
  (plan 040 — connect_ref passthrough tests).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | ≤ baseline |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS (schemas may grow — budget counts TOOLS, not schema size; verify no tool-count change) |

## Scope

**In scope**:
- `src/ms_access_mcp/models/migration.py` (new param models)
- `src/ms_access_mcp/models/__init__.py` (linked-table param models — or a new `models/linked_tables.py`; match how models/ is organized)
- `src/ms_access_mcp/mcp_tools/migration.py` (parse via models)
- `src/ms_access_mcp/mcp_tools/ddl.py` (parse via models)
- `tests/unit/test_mcp_migration.py`, `tests/unit/test_mcp_ddl.py` (extend)

**Out of scope**:
- The use-case layer's own validation (hard-reject of credential literals —
  settled behavior, stays).
- `automation.py` property-bag tools (deferred typing-strategy decision).
- Changing tool NAMES, docstrings' credential guidance, or profile wiring.

## Git workflow

- Branch: `plan/075-tool-boundary-pydantic`
- RED → GREEN per step. Do NOT push.

## Steps

### Step 1: Define the param models

In `models/migration.py` (pydantic v2, following existing model style):

```python
class UploadSchemaParams(BaseModel):
    target_type: str = ""
    connection_string: str = ""
    connection_ref: str = ""
    schema_json: dict[str, Any] = Field(default_factory=dict)


class TransferDataParams(BaseModel):
    target_type: str = ""
    connection_string: str = ""
    connection_ref: str = ""
    schema_json: dict[str, Any] = Field(default_factory=dict)
    transfer_mode: str = "auto"
    verification_mode: str = "count-only"
    table_overrides: dict[str, Any] = Field(default_factory=dict)
    # (match EVERY key the handler actually reads — enumerate with a read of
    #  the handler before writing the model)
```

And linked-table models (in models/ following the same convention):

```python
class CreateLinkedTableParams(BaseModel):
    name: str
    source_table: str
    connect_string: str = ""
    connect_ref: str = ""
    connection_name: str = "default"


class RefreshLinkedTableParams(BaseModel):
    name: str
    connect_string: str | None = None
    connect_ref: str = ""
    connection_name: str = "default"


class UnlinkTableParams(BaseModel):
    name: str
    connection_name: str = "default"
```

(`upsert` reuses the Create shape.) Field presence semantics: keep current
defaults EXACTLY (e.g. refresh's `connect_string` may be absent → None).

**Verify**: `uv run pyright` clean on models; models import without side
effects.

### Step 2: RED — type-confusion params get typed errors

Extend `tests/unit/test_mcp_migration.py` and `tests/unit/test_mcp_ddl.py`
with parametrized bad-input cases per handler, e.g.:

- `upload_schema(params={"target_type": 123, "connection_string": "x"})` →
  `{"success": False, "error": <contains "target_type">}` — a validation
  message, NOT a deep use-case traceback string.
- `transfer_data(params={"connection_string": ["evil"]})` → typed error.
- `create_linked_table(params={"name": None, ...})` → typed error.
- Positive control: a fully valid params dict behaves exactly as before
  (existing tests already pin this — keep them green unmodified).

**Verify**: new tests FAIL (raw pass-through today: `123` reaches the use
case and errors deep, or worse, works by luck).

### Step 3: GREEN — parse at the boundary

In each handler, replace the `p.get(...)` block with:

```python
try:
    parsed = UploadSchemaParams.model_validate(params or {})
except ValidationError as e:
    return to_mcp_result({"success": False, "error": f"Invalid params: {e}"})
```

then use `parsed.target_type`, etc. Preserve the existing
required-string manual checks (empty-string semantics are business rules,
not schema rules — pydantic validates TYPES, the handler keeps its
"connection_string is required" logic).

**Verify**: Step-2 tests pass; existing mcp_migration/mcp_ddl suites green
(refresh any test that deliberately passed weird-but-tolerated types —
plan-035 lesson, separate commit with rationale).

### Step 4: Full gates

**Verify**: ruff / pyright / unit / tool_budget green (tool COUNT unchanged).

## Test plan

- New: ≥6 negative param cases + positive controls per handler family.
- Existing boundary suites (plans 027/040) green.

## Done criteria

- [ ] Param models exist for upload/transfer + 4 linked-table handlers
- [ ] No `p.get("connection_string"|"connect_string", ...)` raw pulls remain
      in those handlers (`rg -n 'p.get\("connect' src/ms_access_mcp/mcp_tools/` → 0)
- [ ] Typed validation errors use the `{"success": False, "error": ...}` envelope
- [ ] All gates green; `plans/README.md` row updated

## STOP conditions

- A handler reads more params than the model captured (enumerate FIRST; a
  missed key silently changes behavior — if discovered late, STOP and
  report).
- The tool schema generated by FastMCP changes in a way that breaks
  `scripts/tool_budget.py` or `test_cli_mcp_equivalence.py`.
- Plan 070's migration.py shape drifted the handler contract.

## Maintenance notes

- These models are the seed for plan 046's workflow prompts (prompt recipes
  can reference the validated param shapes).
- `automation.py` typing remains the deferred strategy decision (2026-09-14
  list) — this plan does not precede it.
