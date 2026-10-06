# Plan 089: Credential staging order and error-scrub hardening

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` (section "Plans 077–093").
>
> **Drift check (run first)**:
> `git diff --stat e4a4ce0..HEAD -- src/ms_access_mcp/credentials.py src/ms_access_mcp/errors.py src/ms_access_mcp/application/database.py tests/unit/test_credentials.py tests/unit/test_error_scrubbing.py tests/unit/test_credentials_windows_acl.py`
> On any mismatch with "Current state", treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: MED
- **Depends on**: plans/077-*.md (green baseline)
- **Category**: security
- **Planned at**: commit `e4a4ce0`, 2026-10-04

## Why this matters

Three hardening gaps, all confirmed statically and with benign in-memory
probes during the audit:

1. The credential store's atomic write replaces the target file BEFORE
   applying Windows ACL hardening — a hardening failure leaves the new
   store installed with inherited (unhardened) permissions while the CLI
   reports a write failure, and the old store is already destroyed.
2. `errors.strip_password` only matches credential tokens anchored at
   string start or immediately after `;` with no whitespace — the
   credential DETECTOR (`credentials.contains_credential`) is stricter
   than the SCRUBBER, so `"; PWD=…"` and `"PWD = …"` survive scrubbing on
   boundaries guarded only by `strip_password`.
3. `open_database`'s connect-failure path prints a raw `traceback`
   (unscrubbed exception internals) and builds its message from `repr(e)`
   plus an exact-substring password replace — `repr` escaping (`\`, `"`)
   can defeat the exact match.

Findings are defensive maintenance; no secret values appear in this plan.

## Current state

- `src/ms_access_mcp/credentials.py` — `_atomic_write_store`
  (~lines 79–104): mkstemp in the target's directory (~line 81) →
  write → `Path(tmp).replace(target)` (~line 91) → chmod / `_harden_nt`
  (~lines 93–99). The except handler unlinks only the tmp name (already
  gone post-replace). Audit-verified ordering; NO tests currently cover
  `_harden_nt` failure or ACL ordering (`grep _atomic_write_store|_harden_nt tests/` → 0 hits).
- Windows behavior note (documented OS semantics, not executed in the
  audit): a same-volume rename carries the SOURCE security descriptor —
  hardening the staging file BEFORE replace closes the window without
  pre-deleting the target.
- `src/ms_access_mcp/errors.py` — `strip_password` (~lines 10–24):
  patterns anchored `^|;` (no whitespace tolerance). Probed with benign
  placeholders: `"; PWD=…"` (space after `;`) and `"PWD = …"` (spaces
  around `=`) survive. Defense-in-depth elsewhere is intact
  (`application/database.py:1071-1073` exact replace; migration text wipe).
- `src/ms_access_mcp/application/database.py` — `open_database` connect
  failure (~lines 196–205): `traceback.print_exc()` then
  `message.replace(password, "***")` over `f"connect failed: {e!r}"`.
- `src/ms_access_mcp/credentials.py` — `contains_credential`
  (~lines 109–115) uses `\bPWD=` style detection ANYWHERE in the string —
  the asymmetry is the finding.
- Tests: `tests/unit/test_credentials.py`, `test_error_scrubbing.py`
  (plan 072 census — must stay green), `test_credentials_windows_acl.py`.
- Conventions: scrub helpers in `errors.py`; credential literals are
  hard-rejected at tool boundaries (plan 040) — unchanged here.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Focused tests | `uv run pytest tests/unit/test_credentials.py tests/unit/test_credentials_windows_acl.py tests/unit/test_error_scrubbing.py tests/unit/test_database.py -q` | all pass |
| Unit suite | `uv run pytest tests/unit/ -q` | all pass |
| Fast gate | `uv run python scripts/qualify.py` | exit 0 |

## Scope

**In scope**:
- `src/ms_access_mcp/credentials.py` (`_atomic_write_store` ordering)
- `src/ms_access_mcp/errors.py` (`strip_password` patterns)
- `src/ms_access_mcp/application/database.py` (open_database failure path only)
- `tests/unit/test_credentials.py`, `tests/unit/test_credentials_windows_acl.py`, `tests/unit/test_error_scrubbing.py`

**Out of scope**:
- The documented SQL-text credential-smell limitation (README security
  notes — settled).
- Any change to tool-boundary literal rejection (plan 040 behavior).
- Migration error wiping (already stricter than `strip_password`).
- Bearer-token formats beyond the existing PWD/password token family.

## Git workflow

- Branch: `plan/089-credential-scrub-hardening` from `main`.
- Conventional commits: `fix(security): harden credential store before replace; widen scrub boundaries`.

## Steps

### Step 1: RED — staging-order test

In `tests/unit/test_credentials_windows_acl.py` (nt-focused) and/or
`tests/unit/test_credentials.py` (posix chmod path): monkeypatch
`_harden_nt` (and the chmod equivalent) to (a) RECORD call order and
(b) raise on demand. Assert:
1. happy path: harden is called on the STAGING file BEFORE `os.replace`
   (order recorded: harden(tmp) precedes replace);
2. harden-failure path: the ORIGINAL target file is untouched (byte-equal),
   the tmp file is unlinked, and the error propagates.

**Verify**: both RED (today harden runs after replace).

### Step 2: GREEN — reorder staging

In `_atomic_write_store`: move chmod/`_harden_nt` BEFORE the replace; on
hardening failure, unlink the staging file and re-raise (target never
touched). Keep the except handler's unlink idempotent for the
pre-replace failure window.

**Verify**: Step-1 tests GREEN; existing credential tests still pass.

### Step 3: RED→GREEN — widen `strip_password`

- RED in `tests/unit/test_error_scrubbing.py` (add to the plan-072 census):
  benign placeholder cases — `"; PWD=secret;X=1"` (space after `;`),
  `"PWD = secret"` (spaces around `=`), and a mid-string `"Server=x; PWD=secret"`
  all scrub to forms without the placeholder value. Use obvious placeholder
  strings (e.g. `scrubme`), never anything resembling a real credential.
- GREEN in `errors.py`: allow optional whitespace around the separator and
  the `=` (e.g. `(?:^|;)\s*` prefix and `\s*=\s*`), and permit matches
  after whitespace following `;`. Keep the existing replacement marker
  semantics (`***`-style) unchanged.

**Verify**: new cases GREEN; the ENTIRE existing plan-072 census test file passes (no regression in what used to be stripped).

### Step 4: RED→GREEN — open_database failure path

- RED: test that a connect failure whose exception text embeds a
  connect-string-with-password produces an `Err` message with the secret
  scrubbed EVEN when `repr` escaping is present (use a placeholder
  password containing a backslash and a quote to defeat exact matching),
  and that NOTHING unscrubbed is printed to stderr (capsys).
- GREEN in `application/database.py` `open_database`: build the message
  from `str(e)` (not `repr`), run `errors.strip_password` FIRST, keep the
  exact-value replace as the second layer, and replace
  `traceback.print_exc()` with a DEBUG-level `logging` call on the
  scrubbed text (module logger; no raw traceback to stderr).

**Verify**: new tests GREEN; existing `test_database.py` connect-failure tests still pass.

### Step 5: Full gates

**Verify**: `uv run pytest tests/unit/ -q` → all pass; `uv run pyright` → 0; `uv run ruff check .` → 0.

## Test plan

- Staging order (happy + harden-failure + original-untouched).
- Scrub census additions (3 benign-placeholder cases; census file fully green).
- Open_database: repr-escaped placeholder scrubbed; no unscrubbed stderr.
- Windows ACL unit semantics follow the existing
  `test_credentials_windows_acl.py` monkeypatch style (no real registry/ACL
  mutation).

## Done criteria

- [x] Harden-before-replace ordering unit-pinned; harden failure leaves the original store intact (test)
- [x] Whitespace-tolerant scrub cases pass; plan-072 census fully green
- [x] `open_database` failure: no raw `traceback.print_exc` (`grep -n "print_exc" src/ms_access_mcp/application/database.py` empty), repr-proof scrub test passes
- [x] Unit suite + qualify green; `plans/README.md` row updated

## STOP conditions

- If on real Windows the rename-descriptor assumption proves wrong in
  operator verification (staging ACL not preserved), stop and report —
  the fallback (harden after replace PLUS immediate re-harden of target
  on failure) is a product decision requiring evidence.
- If widening `strip_password` breaks any census case that DELIBERATELY
  documents a non-match, stop and present the conflict.
- If `str(e)` (vs `repr`) loses diagnostic content the doctor relies on,
  stop and report the consumer.

## Maintenance notes

- The scrubber should never be weaker than `contains_credential`; if the
  detector gains a token family, the scrubber inherits it in the same
  commit (add a paired test convention).
- Real-DACL behavior on end-user machines is an operator verification
  follow-up; record its result in the plan row.
