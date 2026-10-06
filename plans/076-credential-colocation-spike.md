# Plan 076: Spike — decide the credential-store colocation policy (local `.ms-access-mcp.json` next to the DB)

> **Executor instructions**: This is an INVESTIGATION plan. The deliverable
> is a written decision + (only if the decision is Option A-guard) ONE small
> warning implementation with tests. Do not build a migration or relocate
> anything by default. Update the status row in `plans/README.md` when done.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/credentials.py src/ms_access_mcp/application/session_backup.py src/ms_access_mcp/doctor.py`
> Note: plan 073 adds `_atomic_write_store`/`_harden_nt` to credentials.py —
> run AFTER 073 and reconcile its excerpts.

## Status

- **Priority**: P3 (investigate; becomes P2 if decision is "relocate")
- **Effort**: S (spike) + S (optional guard)
- **Risk**: LOW (default: documentation + at most a warning)
- **Depends on**: plan 073 (ACL hardening context — a hardened file that
  still TRAVELS with the DB directory is the remaining question)
- **Category**: security | direction
- **Planned at**: commit `a7f2edb`, 2026-09-24

## Why this matters

The LOCAL credential store path is
`Path(db_path).resolve().parent / ".ms-access-mcp.json"` (credentials.py:98)
— colocated with the database file. The DB's directory is precisely what
users back up, zip, share, and sync (OneDrive/SharePoint/network shares): a
plaintext-credentials file placed there travels WITH the data. The
global-store convention (`~`-based) already honors the "credentials stay on
the machine" principle; the local file violates it by design. Whether to
warn, guard, or relocate is a PRODUCT decision with a migration cost — this
spike produces the evidence and the decision, not the upheaval.

## Current state

- `src/ms_access_mcp/credentials.py:94-98`:
  ```python
  Returns:
      ``<db_dir>/.ms-access-mcp.json``.

  """
  return Path(db_path).resolve().parent / ".ms-access-mcp.json"
  ```
  Lookup order: local file shadows global (README documents this; CLI
  `credentials set --local` writes it).
- Store contents: `{"credentials": {db_path: password}}` + a `profiles`
  section (v2) holding full external-DB connection strings (may embed
  `PWD=`).
- After plan 073: files are owner-only-DACL on Windows — hardening at rest,
  but a copied/zipped directory still carries the readable-on-restore file
  (ACLs frequently don't survive zip round-trips).
- Adjacent machinery to inventory (does anything else copy the DB's
  directory?): `application/session_backup.py` (snapshots the DB FILE into
  `_AUTO_SESSION_DIR` — verify it never copies siblings),
  `application/db_export.py` (writes outputs elsewhere),
  `scripts/build_portable.ps1` (ships code, not user data),
  `doctor.py` (reports on stores).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | ≤ baseline |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |

## Scope

**In scope**:
- `plans/` decision note (below) — the PRIMARY deliverable, also mirrored as
  a short section in the README security area if the operator approves
- OPTIONALLY (decision-dependent, ≤1 file + tests):
  `src/ms_access_mcp/credentials.py` sync-root warning hook, surfaced via
  `doctor.py`

**Out of scope**:
- Relocating existing local stores (a follow-up plan ONLY if the spike
  decides "relocate" — with a migration path).
- Changing lookup order or the CLI surface.
- Any encryption work (DPAPI — separate deferred plan).

## Git workflow

- Branch: `plan/076-credential-colocation-spike`
- Investigate → write the decision into this plan file's Outcome section +
  README row; implementation (if any) RED→GREEN. Do NOT push.

## Steps

### Step 1: Inventory the travel surfaces (read-only)

For each, record file:line evidence of what it copies/writes and whether a
colocated `.ms-access-mcp.json` would travel:

1. `session_backup.create_session_backup` — copies DB file only, or
   directory? (`rg -n "shutil|copy" src/ms_access_mcp/application/session_backup.py`)
2. Export paths (db_export.py) — same question.
3. `doctor.py` — does it enumerate/report local stores? (Warning hook point.)
4. README/docs — what do we PROMISE users about where credentials live?

### Step 2: Survey the friction evidence (read-only)

- README setup/credentials sections: do docs encourage `--local`?
- Tests: `rg -n "ms-access-mcp.json" tests/` — how load-bearing is the
  local-store path in the contract suite?
- Issue/plan history: plans 039/040 rationale for local stores (grep
  plans/039*, plans/040* for "local") — what problem did colocation solve
  (multi-DB per machine? portability?).

### Step 3: Write the decision

Append an `## Outcome` section to THIS plan file choosing one, with the
evidence table:

- **Option A — Keep colocation, add guards**: sync-root warning
  (OneDrive/Dropbox path detection via known env vars/registry — keep it
  heuristic and WARN-only), doctor check "local store present in a synced
  directory", README risk note. Implementation: ONE RED→GREEN cycle for the
  warning (unit-testable with a fake sync-root path), ≤50 lines.
- **Option B — Default global, local opt-in**: flip the default, keep
  `--local` as explicit opt-in with a warning at write time. Requires a
  follow-up migration plan (existing local stores) — DO NOT implement here;
  file the follow-up as a proposed plan stub in the Outcome.
- **Option C — Document-only**: evidence shows low real-world exposure
  (e.g. docs never recommend --local + doctor will flag after 073); then a
  README paragraph + this note suffice.

Default recommendation if evidence is ambiguous: **Option A** (guards now,
relocation decision deferred with data).

### Step 4 (only if Option A): RED→GREEN the sync-root warning

RED: unit test — a local store path under a fake configured sync root
(inject the root via the detection function's input; do not depend on the
machine actually having OneDrive) produces a warning in the doctor/store
report. GREEN: implement the heuristic detector + wire into doctor's
credentials section.

**Verify**: gates green (ruff/pyright/unit/tool_budget).

## Test plan

- Spike: no tests required beyond the inventory evidence.
- Option A only: 2 unit tests (detected → warn; not-detected → silent).

## Done criteria

- [x] Inventory table with file:line evidence appended to this plan
- [x] `## Outcome` section names the chosen option + rationale
- [x] If A: warning implemented with tests; if B: follow-up stub recorded in
      plans/README; if C: README paragraph drafted
- [x] `plans/README.md` row updated (status DONE with the decision one-liner)

## STOP conditions

- Evidence contradicts the premise (e.g. local stores are already
  deprecated) — report instead of deciding.
- The operator is needed for a product call between A and B — surface the
  table and ask via the README row status `BLOCKED (operator decision)`.
- Any implementation creeping past the ≤50-line guard budget.

## Maintenance notes

- The strongest long-term answer remains DPAPI-at-rest + global default;
  this spike's decision should note that sequencing explicitly.
- If Option B ever lands, plan 073's `_atomic_write_store` is the single
  write choke point the migration will use — noted for that future plan.

## Outcome

**Chosen: Option A — keep colocation, add a heuristic warning.** The local
credential path remains an explicit feature, documented and tested; changing
its default would require migration behavior and is not justified by this
inventory. The warning is best-effort, WARN-only, and doctor checks the local
store beside its current working directory; it does not claim to find stores
for unrelated database paths. The guard is under 50 added source lines.

| Travel/friction surface | Evidence | Finding |
|---|---|---|
| Automatic session backup | `src/ms_access_mcp/application/session_backup.py:87-104` | `shutil.copy2(db_path, backup_path)` copies only the DB file; destination is the temp autosession directory (`:26`, `:38-42`). Sibling credential file does not travel. |
| Data export | `src/ms_access_mcp/application/db_export.py:97-124, 165-178` | Writes CSV/JSON to caller-provided `output_path`; XLSX delegates to adapter (`:134-154`). No directory/sibling copy. |
| Doctor hook | `src/ms_access_mcp/doctor.py:293-306` | Existing `run_checks` has platform/dependency/driver checks, no credential reporting. Added optional credentials warning check for a local store in cwd. |
| User-facing promise and friction | `README.md:18-45, 58-64` | Global is the documented default (`:19-20`); `--local` is explicitly documented, local shadows global, security notes describe at-rest permissions but not sync/backup exposure. |
| Contract surface | `tests/unit/test_credentials.py:38-40`; `tests/unit/test_cli_credentials.py:236` | Tests assert the colocated file path and explicit `--local` write behavior; local placement is a supported contract, not deprecated. |
| Original rationale | `plans/039-server-side-db-credentials.md:302, 315-329` | 039 established explicit local set/remove and documented path/lookup semantics; no deprecation or replacement appears in the current contract. |
| Named profiles | `plans/040-named-credential-profiles.md:50-51, 68-69, 73-90`; `README.md:102` | Profiles extend global credentials storage, rather than redefining local DB-password storage. |

The local store is owner-restricted at rest (`credentials.py:52-77, 79-103`),
but ACLs do not prevent the file from traveling in a synced/copied folder.
`credentials.py:142-152` confirms the current resolved parent-directory
placement; its current implementation differs from the plan's stale excerpt
only in surrounding hardening changes (073 is present). No claim is made that
the heuristic catches every sync client or configured sync path.

**Long-term sequencing:** DPAPI-at-rest remains the strongest Windows answer;
global-by-default is a separate later decision, after the protection and
migration story are ready. Do not relocate stores in this spike. If relocation
is selected later, create a migration plan using `_atomic_write_store` as the
single write choke point.

**README paragraph for a follow-up doc change:** Local credential files are
stored beside the database and can travel with folders that are backed up,
shared, or synchronized. Prefer the global store unless local placement is
intentional; `doctor` warns when its current working directory's local store
falls beneath a recognized OneDrive or Dropbox root. Detection is heuristic
and is not a guarantee that a store is outside synchronized storage.
