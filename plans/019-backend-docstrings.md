# Plan 019: Document every public surface in backends/** (re-enable ruff D)

> **Executor instructions**: Follow this plan step by step. This is a
> doc-only plan: you write docstrings and fix docstring formatting, nothing
> else. Zero behavior change — if a docstring would require inventing
> semantics you cannot verify from the code, STOP for that site (see the
> escape hatch in step 3) and flag it. Commit in batches, one commit per
> file. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat ab9a373..HEAD -- pyproject.toml src/ms_access_mcp/backends/`
> Plan 018 must be DONE first (it lands the select/per-file-ignores
> mechanism this plan builds on). If any in-scope file changed since this
> plan was written, compare the "Current state" excerpts against the live
> code before proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: LOW
- **Depends on**: plans/018-ruff-mechanism-deliberate-suppressions.md
- **Category**: tech-debt (docs)
- **Planned at**: commit `ab9a373`, 2026-09-11

## Execution mode

**Direct edit (doc-only remediation).** Docstrings and config only; the
existing test suite + ruff + pyright are the gates. Not TDD (no behavior
change by intent), not SDD.

## Why this matters

The D (pydocstyle) family is currently unselected repo-wide, and
`backends/**` — the most complex layer of the codebase (COM lifecycle,
DAO/ADO/ODBC adapters, dispatcher thread ownership) — carries 105
undocumented or malformed public surfaces. This is exactly where onboarding
cost is highest: public adapter methods whose contracts (best-effort
returns, bool semantics, thread affinity) are not obvious from signatures.
After this plan, the D family is enforced on `backends/**`, so new public
methods cannot land undocumented, and every other directory's exemption is
explicit and justified instead of implicit.

## Current state

**Mechanism (established by plan 018 — read it first)**: ruff rule families
are enabled globally via `[tool.ruff.lint].select`
(`pyproject.toml:38-47`, currently `E,F,W,I,N,C4,UP`); `per-file-ignores`
entries only silence families already in `select` — ruff has no per-file
`select`. After plan 018, every per-file-ignores code is live and carries a
justification comment.

**Verified census at `ab9a373`** (re-measure in step 1): D violations in
`backends/**` = 105 total:

| Code | Count | Meaning |
|------|-------|---------|
| D102 | 82 | undocumented public method |
| D107 | 10 | undocumented public `__init__` |
| D413 | 9 | missing blank line after last docstring section — auto-fixable |
| D105 | 2 | undocumented magic method |
| D103 | 1 | undocumented public function |
| D401 | 1 | first line not in imperative mood |

Per-file distribution of the 96 hand-written sites (D102/107/105/103/401):

| File | Sites |
|------|-------|
| `odbc.py` | 38 |
| `dao.py` | 16 |
| `versioning.py` | 15 |
| `schema_inspector.py` | 12 |
| `dispatcher.py` | 5 |
| `db_operations.py` | 3 |
| `access.py` | 3 |
| `ado.py` | 2 |
| `__init__.py` | 2 |

D violations OUTSIDE `backends/` (verified at `ab9a373`): 334 total —
`cli.py` 148, `application/**` ~158, `models.py` 21, `errors.py` 3,
`mcp_tools/core.py` 3, `profiles.py` 2, `server.py` 1, `capabilities.py` 1,
`cli_format.py` 1. These stay exempt via per-file-ignores in this plan.

**Existing docstring style to match** — exemplar
`src/ms_access_mcp/backends/access.py:1987-1990`:

```python
def connect_hidden(db_path: str) -> AccessAdapter:
    """Convenience: create and connect an AccessAdapter in hidden mode.

    Returns the connected adapter. Caller must call disconnect() when done.
```

One-line imperative summary; optional detail paragraphs for non-obvious
contracts. Match the conventions already present in the file you are
editing — do not impose a new convention (no Args:/Returns: sections
unless the file already uses them).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| D census (backends) | `uv run ruff check src/ms_access_mcp/backends/ --isolated --statistics --select D` | statistics table (exit 1 is fine — violations exist) |
| D census (rest) | `uv run ruff check src/ms_access_mcp/ --isolated --statistics --select D --exclude src/ms_access_mcp/backends` | statistics table (exit 1 is fine) |
| Full unit | `uv run pytest tests/unit/ -q` | all pass |
| Types | `uv run pyright` | 0 errors |

(The `--isolated` commands print cosmetic D203/D211 and D212/D213
incompatibility warnings — ignore them.)

## Scope

**In scope** (the only files you should modify):
- `pyproject.toml` (`select` + per-file-ignores entries with justification
  comments)
- `src/ms_access_mcp/backends/**` — docstrings only

**Out of scope** (do NOT touch, even though they look related):
- The 334 D violations outside `backends/` (`cli.py`, `application/**`,
  `models.py`, etc.) — they get justified per-file-ignores entries in step
  1 and are deferred to a future plan.
- Any code change: no renames, no reformatting of code, no type
  annotations, no raise-mechanics fixes (plan 020 owns EM/TRY003).
- `tests/**` and `src/ms_access_mcp/doctor.py` — already exempt.

## Git workflow

- Branch: `plan/019-backend-docstrings`
- One commit per file batch; conventional commits, e.g.
  `docs(backends): document public surfaces in odbc.py`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Enable D with justified exemptions, then unmask backends

1. Re-measure both D censuses (commands table) and record the numbers in
   your step report. If the backends D total drifted from 105 by more than
   ±10, treat as STOP (a landed plan changed the surface — re-plan).
2. Add `"D"` to `[tool.ruff.lint].select` in `pyproject.toml`.
3. Run `uv run ruff check .`. For EVERY path outside `backends/` that now
   reports violations, add a per-file-ignores entry `"<path>" = ["D"]` with
   a justification comment, e.g.:
   `# D: backends/** documented in plan 019; this path deferred to a future doc plan.`
   Expected paths (from the verified snapshot): `"tests/**"` and
   `"src/ms_access_mcp/doctor.py"` already carry `"D"`; add entries for
   `src/ms_access_mcp/cli.py`, `src/ms_access_mcp/application/**`,
   `src/ms_access_mcp/mcp_tools/**`, `src/ms_access_mcp/models.py`,
   `src/ms_access_mcp/errors.py`, `src/ms_access_mcp/profiles.py`,
   `src/ms_access_mcp/server.py`, `src/ms_access_mcp/capabilities.py`,
   `src/ms_access_mcp/cli_format.py`. Use the ruff output, not this list —
   the list is the expectation, ruff is the truth.
4. Remove `"D"` from the `src/ms_access_mcp/backends/**` per-file-ignores
   entry.

**Verify**: `uv run ruff check .` → exit 1, reporting ~105 violations ALL
under `src/ms_access_mcp/backends/` (zero elsewhere). If any violation
appears outside `backends/`, a per-file-ignores entry is missing — fix the
config, not the code.

### Step 2: Auto-fix D413

Run `uv run ruff check src/ms_access_mcp/backends/ --select D413 --fix`.
Expected: 9 fixed. Review the diff — it must contain only blank-line
insertions inside docstrings.

**Verify**: `uv run ruff check src/ms_access_mcp/backends/ --select D` →
~96 violations; `uv run pytest tests/unit/ -q` → all pass. Commit.

### Step 3: Hand-write the remaining docstrings, one file per commit

Work the files in ascending site count (momentum + smallest diffs first):
`__init__.py`, `ado.py`, `access.py`, `db_operations.py`, `dispatcher.py`,
`schema_inspector.py`, `versioning.py`, `dao.py`, `odbc.py`. For each file:

1. Run `uv run ruff check <file> --select D` to list its sites.
2. Write each docstring matching the existing style of that file (see the
   `connect_hidden` exemplar in "Current state"). Describe the OBSERVABLE
   contract: what the method returns (including best-effort `bool`
   semantics where plans 010/016/017 established them), what it swallows,
   thread/dispatch constraints. Derive every claim from the code in front
   of you — never from this paragraph.
3. Fix the D401 site by rewording the first line to imperative mood
   (e.g. "Return ..." instead of "Returns ...") — do not reword anything
   else in that docstring.
4. **Escape hatch for unknowable semantics**: if a public method's contract
   cannot be determined from the code and its callers, do NOT guess. Leave
   it undocumented with
   `# noqa: D102 - TODO: semantics unclear, flagged by plan 019` on the
   `def` line, and list the site in your step report. More than 3 such
   sites in the whole plan → STOP (the plan's premise — that these are
   documentable from code — is broken).
5. D105 (magic methods): a one-line imperative summary suffices
   (e.g. `"""Return the adapter's name for logging."""` for `__repr__`).

**Verify** (after each file):
`uv run ruff check <file> --select D` → exit 0;
`uv run pytest tests/unit/ -q` → all pass. Commit per file.

### Step 4: Final gate

**Verify**: `uv run ruff check .` → exit 0;
`uv run pytest tests/unit/ -q` → all pass (unchanged test count);
`uv run pyright` → 0 errors;
`git diff --stat` → only `pyproject.toml` and `src/ms_access_mcp/backends/**`;
`git diff` review → docstrings, blank lines, and noqa markers ONLY (no code
lines changed).

## Test plan

- No new tests: existing suite + ruff + pyright are the gates.
- Verification per file: ruff D-clean for that file + full unit suite green.

## Done criteria

- [ ] `"D"` is in `[tool.ruff.lint].select`; `backends/**` no longer
      appears in any D-related per-file-ignores entry
- [ ] Every path exempt from D carries a per-file-ignores entry with an
      inline justification comment
- [ ] `uv run ruff check .` exits 0
- [ ] `uv run pytest tests/unit/ -q` exits 0 with unchanged test count
- [ ] `uv run pyright` → 0 errors
- [ ] `git diff` shows zero code changes (docstrings, blank-line insertions,
      and at most 3 justified `noqa: D102` markers only)
- [ ] Every flagged semantics-unclear site (if any) is listed in the final
      report
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- Plan 018 is not DONE, or its select/per-file-ignores mechanism doesn't
  match "Current state".
- The re-measured backends D census differs from 105 by more than ±10.
- More than 3 sites require the semantics-unclear escape hatch.
- A docstring you need to write would require changing code to be true
  (e.g. the docstring contract and the implementation disagree — report
  the disagreement; do not "fix" the code).
- Step 1's ruff run shows D violations outside `backends/` after the
  exemption entries — config error, not a code-fix opportunity.

## Maintenance notes

- The per-file-ignores entries added in step 1 are the deferred-docstring
  backlog: `cli.py` (148) and `application/**` (~158) are the big items.
  A future doc plan should pick them up directory by directory and shrink
  the exemption list.
- Reviewers should scrutinize: docstring claims about best-effort/swallow
  behavior against the implementation; any `noqa: D102` marker without the
  TODO justification is a reject.
- If plan 020 (raise mechanics) lands concurrently, sequence AFTER it or
  rebase: both touch `pyproject.toml` and `backends/**` files.
