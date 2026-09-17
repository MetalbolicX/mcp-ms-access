# Plan 050: Repo hygiene — delete stray root files, clear the last pyright error

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 655f712..HEAD -- src/ms_access_mcp/application/migration.py .gitignore`
> If either file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW (one attribute-access change in a read path; two file
  deletions of untracked strays; one .gitignore line)
- **Depends on**: none (independent of 048/049; lands in any order)
- **Category**: tech-debt
- **Planned at**: commit `655f712` (plan-047 branch tip), 2026-09-16

## Why this matters

Two kinds of rot accumulate while bigger plans run: (1) stray files at the
repo root that confuse every fresh clone and CI (`nul`, a Windows reserved
device name, and a `pnpm-workspace.yaml` left over from the pre-Python
era), and (2) the last known pyright error in the tree
(`migration.py:563`), which forces every plan since 040 to carve out an
"acceptable pre-existing error" exception in its gate. Clearing both makes
`pyright src/ms_access_mcp/ → 0 errors` a zero-exception gate again and
shrinks the repo's surprise surface. Both items are already on the
2026-09-16 audit backlog; this plan is the cheap sweep.

## Current state

- Repo root (`Get-ChildItem -Force` evidence, 2026-09-16): `nul` (0 bytes)
  and `pnpm-workspace.yaml` (31 bytes), both **untracked**
  (`git status --short` → `?? pnpm-workspace.yaml`; `nul` is invisible to
  ordinary git plumbing on Windows — hence the delete command below).
- The audit backlog (plans/README.md, 2026-09-16 deep audit) already lists:
  "delete stray `pnpm-workspace.yaml` + root `nul` file — S hygiene".
- `src/ms_access_mcp/application/migration.py:562-563` — the typed-seam
  leftover from plan 037:
  ```python
  query_result = adapter.execute_query(query)  # type: ignore[attr-defined]
  rows = query_result.get("rows", []) if query_result else []
  ```
  `adapter.execute_query` returns `QueryResult` (typed dataclass,
  `src/ms_access_mcp/models/__init__.py:90-96`):
  ```python
  @dataclass
  class QueryResult:
      success: bool
      rows: list[dict]
      count: int
      columns: list[str]
      error: str | None = None
  ```
  So `.get("rows", [])` is a dict-ism on a dataclass — pyright flags
  `Attribute "get" is unknown (reportAttributeAccessIssue)`. The
  `row.get(c)` on line 571 is fine (`rows` is `list[dict]`).
- Every plan since 040 carries the exception "1 pre-existing error in
  migration.py:563 (acceptable)" — clearing it retires that carve-out.
- `.gitignore` currently has no `nul` entry (repo root listing evidence).

## Commands you will need

| Purpose     | Command                                          | Expected on success |
|-------------|--------------------------------------------------|---------------------|
| Lint        | `uv run ruff check .`                            | exit 0              |
| Typecheck   | `uv run pyright src/ms_access_mcp/`              | 0 errors            |
| Unit tests  | `uv run pytest tests/unit/ -q`                   | all pass            |
| Migration U | `uv run pytest tests/unit/test_use_cases_migration.py -q` (locate exact file with `rg -ln "transfer_data" tests/unit/` first) | all pass |

## Scope

**In scope**:

- `src/ms_access_mcp/application/migration.py` — line 563 only
- `.gitignore` — one `nul` line
- Deletion of untracked `nul` and `pnpm-workspace.yaml` (no git content)

**Out of scope** (do NOT touch):

- The `# type: ignore[attr-defined]` on :562 — it guards the adapter-union
  attribute access, not the `.get`; leave unless pyright proves it stale
  (if removing it yields 0 errors, you may remove it in the same commit).
- Any other `.get(` sites in migration.py — sweep only :563.
- Credential-file DACL hardening, `pip-audit`, and the other audit backlog
  items — each is its own decision, not hygiene.

## Git workflow

- Branch: `plan/050-repo-hygiene-strays-pyright`.
- One or two commits: `fix(migration): typed access on QueryResult.rows`,
  `chore: delete stray root files, ignore nul`.
- Do NOT push unless instructed.

## Steps

### Step 1: Fix the pyright error

`migration.py:563`:

```python
rows = query_result.rows if query_result else []
```

Behavioral note: `.get("rows", [])` defaulted to `[]` when the key was
absent; the dataclass always has `rows` (possibly empty), so the attribute
form is equivalent for every real return value. Do not change lines 564-576.

**Verify**:
- `uv run pyright src/ms_access_mcp/` → **0 errors, 0 warnings** (the
  project-wide exception is now retired)
- `uv run pytest tests/unit/ -q` → all pass (locate and run the migration
  transfer tests specifically as extra assurance)

### Step 2: Delete the strays

`nul` is a Windows reserved device name — plain `Remove-Item nul` may
resolve to the device instead of the file. Use the literal NT path:

```powershell
Remove-Item -LiteralPath "\\?\D:\code\python\mcp-ms-access\nul" -Force
Remove-Item -LiteralPath "pnpm-workspace.yaml" -Force
```

**Verify**: `Get-ChildItem -Force -File | Where-Object { $_.Name -in 'nul','pnpm-workspace.yaml' }`
→ empty output.

### Step 3: Guard against `nul` recurrence

Append one line to `.gitignore`:

```
nul
```

(Accidental `> nul` redirects or tooling create this file on Windows; the
entry keeps it out of status noise forever.)

**Verify**: `git status --short` shows only your intended changes; `git check-ignore -v nul`
prints the matching rule if the file is recreated (optional spot-check via
`New-Item -ItemType File nul` then delete again — skip if uncomfortable,
the rule is trivially correct).

## Test plan

- No new tests — the change is an attribute-access equivalence on a typed
  boundary, pinned by pyright itself (that is the gate) and the existing
  migration unit tests.
- If any migration test breaks, the equivalence assumption above is wrong —
  see STOP conditions.

## Done criteria

- [ ] `uv run pyright src/ms_access_mcp/` → 0 errors, 0 warnings
- [ ] `uv run ruff check .` → exit 0
- [ ] `uv run pytest tests/unit/ -q` → all pass
- [ ] `Get-ChildItem -Force` shows neither `nul` nor `pnpm-workspace.yaml`
- [ ] `.gitignore` contains the `nul` entry
- [ ] `git status` clean of unintended changes; `plans/README.md` row 050
      updated (also update plans 040/042 rows' parenthetical only if they
      cite the migration.py:563 exception — remove the stale exception text)

## STOP conditions

Stop and report back (do not improvise) if:

- Any migration unit test fails after the :563 change (the dataclass
  default-equivalence assumption is false — that is a finding, not a
  hygiene fix).
- `pyright` reports NEW errors elsewhere after the change (the ignore
  comment on :562 was masking more than expected).
- The `\\?\` delete fails or removes something unexpected — verify the
  literal path before retrying; never widen the wildcard.

## Maintenance notes

- Plans 041-047's DONE rows that mention "1 pre-existing pyright error
  (migration.py:563, acceptable)" become historical statements — leave
  them; only rows that state it as an ACTIVE exception need the parenthetical
  trimmed.
- If `nul` reappears after this plan, find the tool redirecting to it
  instead of re-deleting silently.
