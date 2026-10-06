# Plan 103: Rename the shadowing schema_json fields (wire-compatible alias)

> **Executor instructions**: as plan 094. Update the status row in
> `plans/README.md` (section "Plans 099–104") when done.
>
> **Drift check (run first)**:
> `git diff --stat 2fbe1de..HEAD -- src/ms_access_mcp/models/migration.py`
> On mismatch with "Current state", STOP.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plan 075 (tool-boundary pydantic) history
- **Category**: DX / warnings hygiene
- **Planned at**: commit `2fbe1de`, 2026-10-10

## Why this matters

LIVE-CONFIRMED (evidence pass): importing the migration models emits

```
models/migration.py:44: UserWarning: Field name "schema_json" in
  "UploadSchemaParams" shadows an attribute in parent "BaseModel"
models/migration.py:60: UserWarning: Field name "schema_json" in
  "TransferDataParams" shadows an attribute in parent "BaseModel"
```

Deferred DX item since the 2026-09-14 audit; still firing on every
import that doesn't suppress warnings. The shadow risks confusing any
code that ever touches `BaseModel.schema_json` semantics.

## Current state (HEAD `2fbe1de`)

- `models/migration.py`: `UploadSchemaParams.schema_json` (`:44`
  area, field at `:57`) and `TransferDataParams.schema_json` (`:60`
  docstring, field `:67`).
- The field name is the MCP wire contract (`schema_json` in tool
  input schemas) — the rename must be Python-side only.

## Steps

1. **RED** — a unit test that imports the module under
   `warnings.simplefilter("error", UserWarning)` (scoped context) and
   asserts no UserWarning mentioning "shadows" fires. Fails today.
2. **GREEN** — rename the Python attributes to `schema_payload` with
   `pydantic.Field(alias="schema_json")` and
   `model_config = ConfigDict(populate_by_name=True)` on both models:
   - wire name unchanged (tool schemas keep `schema_json`);
   - internal callers passing `schema_json=` by keyword keep working
     via the alias; grep `schema_json=` construction sites and
     `.schema_json` attribute reads first and migrate them to the new
     attribute name in the same commit (house: no dual-name access
     left behind).
3. **Pins** — existing `test_tool_schemas`/model tests must stay green
   untouched (they are the wire-compat proof). If a golden embeds the
   Python attribute name (not the alias), refresh it deliberately in a
   separate commit with the prior assertion in the message.
4. Verify import is warning-free under `-W always` (evidence command
   recorded in the row).

## Verify

```
PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -W always -c "import ms_access_mcp.models.migration" 2>&1 | grep -i shadow | wc -l   # 0
PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest tests/unit/test_models_migration.py tests/unit/test_tool_schemas.py -q -p no:cacheprovider
```
Plus standard full gates.

## STOP conditions

- A consumer requires the Python attribute to be literally
  `schema_json` (e.g. `getattr` by string beyond the two models) —
  surface it; do not half-migrate.

## Out of scope

- Any other model fields; any wire-name change.
