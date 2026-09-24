# Plan 073: Windows credential-file hardening — owner-only DACL via a shared atomic-write helper

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat a7f2edb..HEAD -- src/ms_access_mcp/credentials.py`
> On any change, compare "Current state" excerpts against live code.

## Status

- **Priority**: P1 (security)
- **Effort**: M
- **Risk**: MED (ACL changes can break multi-account service scenarios — see STOP)
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `a7f2edb`, 2026-09-24
- **Owns the deferred 2026-09-16 finding** "Windows DACL hardening for
  credentials files" (plans/README.md audit-deferred list).

## Why this matters

The credential store holds plaintext DB passwords (`{"credentials": {path:
password}}`) and full external-DB profile connection strings (which may embed
`PWD=`). On Windows the code applies NO permission hardening at all — the
`os.chmod` calls are gated behind `if os.name != "nt":`
(credentials.py:258,313,448,493), and chmod on Windows only toggles the
read-only attribute anyway. The comment at :256 claims "Set POSIX permissions
to 0o600 (owner read/write only)" — false assurance on the platform this
product targets (Windows x64). Any local principal/process can read the store
while the code implies owner-only. Fix: apply an explicit owner-only DACL
(owner + SYSTEM + Administrators, inheritance disabled) after each write,
via pywin32 (`win32security`), which is already a core dependency.

## Current state

- `src/ms_access_mcp/credentials.py` — four write sites share the same
  duplicated atomic-write block. `write_entry` (:209, chmod at :249-258):
  ```python
  try:
      with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
          json.dump(data, fh, indent=2, ensure_ascii=False)
          fh.flush()
          os.fsync(fh.fileno())
      os.replace(tmp_path_str, str(target))

      # Set POSIX permissions to 0o600 (owner read/write only)
      if os.name != "nt":
          os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)

  except BaseException:
      # Clean up the temp file on failure
      try:
          os.unlink(tmp_path_str)
      except OSError:
          pass
      raise
  ```
  The same block (modulo variable names) repeats in `remove_entry`
  (:306-319), `write_profile` (:441-454), `remove_profile` (:486-496).
- Store locations: global (`~`-based) and LOCAL —
  `Path(db_path).resolve().parent / ".ms-access-mcp.json"` (:98) — colocated
  with the database (plan 076 owns the colocation POLICY; this plan owns
  file hardening).
- No `win32security`/`icacls` usage anywhere in src/ (verified by grep).
- pywin32 is a core dependency (`sys_platform == 'win32'` marker) —
  `win32security` imports are available; guard imports the way
  `backends/access/__init__.py:90` guards `win32com.client` (function-local
  import + `type: ignore[import-not-found]` if pyright needs it).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Lint | `uv run ruff check .` | exit 0 |
| Types | `uv run pyright` | ≤ baseline |
| Unit tests | `uv run pytest tests/unit/ -q` | all pass |
| ACL inspection (manual) | `icacls <tmpfile>` | owner/SYSTEM/Administrators only, no inheritance |
| Tool budget | `uv run python scripts/tool_budget.py` | PASS |

## Scope

**In scope**:
- `src/ms_access_mcp/credentials.py` (shared `_atomic_write_store` helper + Windows DACL + comment fix)
- `tests/unit/test_credentials_windows_acl.py` (create, Windows-only)
- `tests/unit/test_credentials.py` (extend only if the helper extraction needs it)

**Out of scope**:
- DPAPI encryption at rest (design follow-up — see Maintenance notes).
- Colocation policy (plan 076).
- `doctor.py` ACL reporting (optional Step 6 — only if trivial).
- POSIX branch semantics (unchanged).

## Git workflow

- Branch: `plan/073-windows-credential-acl`
- RED → GREEN per step. Do NOT push.

## Steps

### Step 1: RED — write_entry yields an owner-only DACL on Windows

Create `tests/unit/test_credentials_windows_acl.py`:

```python
"""Windows credential stores must be owner-only (DACL, inheritance off)."""

import os
import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")

def _acl_entries(path):
    import win32security

    sd = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
    )
    return sd.GetSecurityDescriptorDacl()
```

Test: call `write_entry` (or the lower-level store writer the module
exposes — inspect its API first) against a tmp_path store; then assert via
the DACL: every ACE grants access ONLY to the file owner, SYSTEM, and
Administrators; and the descriptor is protected (inheritance removed —
`GetSecurityDescriptorControl` includes `SE_DACL_PROTECTED`).

**Verify**: FAILS (default inherited ACL is broad; not protected).

### Step 2: GREEN — `_harden_nt` + wire into the (new) shared writer

1. Extract the four duplicated atomic-write blocks into one private helper
   in credentials.py:
   ```python
   def _atomic_write_store(target: Path, payload: dict) -> None:
       """Atomically write the JSON store, hardened per-platform."""
       # fdopen/json.dump/flush/fsync/os.replace — verbatim from the four sites
       if os.name != "nt":
           os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
       else:
           _harden_nt(target)
   ```
2. Implement `_harden_nt`:
   ```python
   def _harden_nt(target: Path) -> None:
       """Owner-only DACL (owner+SYSTEM+Administrators, inheritance off)."""
       import win32security  # type: ignore[import-not-found]

       sd = win32security.GetNamedSecurityInfo(
           str(target), win32security.SE_FILE_OBJECT, win32security.OWNER_SECURITY_INFORMATION
       )
       owner = sd.GetSecurityDescriptorOwner()
       dacl = win32security.ACL()
       for account in (owner, win32security.ConvertStringSidToSid("S-1-5-18"),      # SYSTEM
                                  win32security.ConvertStringSidToSid("S-1-5-32-544")):  # Administrators
           dacl.AddAccessAllowedAce(win32security.ACL_REVISION, 2032127, account)   # FILE_ALL_ACCESS
       win32security.SetNamedSecurityInfo(
           str(target), win32security.SE_FILE_OBJECT,
           win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
           dacl=dacl,
       )
   ```
   (SIDs via well-known identifiers avoid localization issues with account
   names; FILE_ALL_ACCESS = 2032127. If the pywin32 API shape differs at
   execution time, adapt — the CONTRACT is the test's: owner/SYSTEM/Admin
   only + protected; match how dispatcher.py handles pywin32 API variance.)
3. All four call sites (`write_entry`, `remove_entry`, `write_profile`,
   `remove_profile`) route through `_atomic_write_store` (their `except
   BaseException` cleanup of the tmp file moves into the helper).
4. Fix the false comment: replace "Set POSIX permissions to 0o600 (owner
   read/write only)" with the truthful two-platform description.

**Verify**: Step-1 test passes; `uv run pytest tests/unit/test_credentials.py tests/unit/test_cli_credentials.py -q` green (behavior identical apart from ACL).

### Step 3: RED → GREEN — the other three writers get it for free (pin it)

Add one parametrized test driving `remove_entry`, `write_profile`,
`remove_profile` through the same ACL assertion (they now share the helper —
the test PINS that wiring; if you wrote Step 2 completely, these arrive
GREEN: acceptable — commit them together with a message noting the pins are
construction-green by design, or write them RED-first by landing Step 1's
helper-wiring per-writer; either is honest if stated).

**Verify**: all ACL tests pass.

### Step 4: Optional — surface weak ACLs in doctor (drop if non-trivial)

If `doctor.py` has an existing credentials check with an obvious extension
point (inspect first), add a WARN when the store's DACL is broader than
owner-only. ONE small RED→GREEN cycle max; if it grows, SKIP and note in the
README row.

### Step 5: Full gates + README note

**Verify**: ruff / pyright / unit / tool_budget green. Add (or extend) a
short README security-section line: on Windows the store is protected by an
owner-only DACL; previously-stored files written before this plan keep their
old ACL — recommend re-running `credentials set` once to re-harden (rotation
advice per plan 072's note stays operational guidance).

## Test plan

- New Windows-only ACL tests (4 writers × DACL contract).
- Existing credentials/CLI-credentials suites green (atomic-write semantics
  preserved: temp-file + fsync + replace).
- Manual: `icacls` on a freshly written store shows the three principals and
  "No inheritance".

## Done criteria

- [ ] Single `_atomic_write_store` helper (four duplicates gone)
- [ ] Windows: owner/SYSTEM/Administrators-only protected DACL after every write
- [ ] POSIX chmod branch unchanged; false 0o600 comment corrected
- [ ] `rg -n "os.chmod" src/ms_access_mcp/credentials.py` → only inside the helper
- [ ] All gates green; `plans/README.md` row updated (deferral closed)

## STOP conditions

- pywin32 ACL API shape fights the helper beyond the contract test (adapt
  within the contract; report if it needs a design decision).
- Evidence of a legitimate multi-account usage pattern (e.g. tests or docs
  showing a service account other than the writer must READ the store) —
  report before shipping the owner-only default.
- Existing tests fail because they assert the OLD duplicated write blocks
  verbatim — refresh deliberately per plan-035 lesson.

## Maintenance notes

- DPAPI at rest (`win32crypt.CryptProtectData`) is the stronger follow-up;
  needs a migration path for existing stores — separate plan, operator
  decision.
- Plan 076 (colocation) builds on this: a hardened file next to the DB is
  still a file that TRAVELS with the DB directory.
- If a future store format lands (v3), the helper is the single choke point.
