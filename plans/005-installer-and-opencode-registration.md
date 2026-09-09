# Plan 005: Installer and OpenCode registration (SDD)

> **Executor instructions**: This plan runs as an **SDD cycle**:
> explore → propose → spec (scenarios) → design → tasks → apply → verify.
> Apply-phase tasks are dispatched to `@medium` subagents (CAP:5, strict TDD).
> The orchestrator re-verifies config-mutation gates in its own shell.
> Update `plans/README.md` when done.
>
> **Drift check (run first)**: new repo has plans 001–004 DONE (a working
> `ms-access-mcp mcp` stdio command is the thing being registered).

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/004-mcp-core-profile.md
- **Category**: dx
- **Planned at**: oracle commit `c8beaab`, 2026-09-08

## Why this matters

The user's historical pain is installation and startup: manual venv
activation, manual server launch, HTTP+bearer config. The fix is one
`install.ps1` that bootstraps pinned uv, installs the tool into an isolated
uv-managed environment (no activation ever), runs doctor, and registers
OpenCode **project-locally by default** (global only with an explicit flag,
because every registered MCP server costs tool-schema tokens in every
session). Config mutation must be safe: backup, atomic write, validation,
rollback.

## Current state (verified on this machine, 2026-09-08)

- OpenCode global config dir `C:\Users\<user>\.config\opencode\` contains BOTH `opencode.json` AND `opencode.jsonc`; they deep-merge per key, and the `.jsonc` currently owns `mcp.engram` (absolute-path command array). **Consequence**: the merge helper must write plain `opencode.json` and only ADD the new unique key `mcp.ms-access`; it must NEVER rewrite `.jsonc` (comment preservation is unsolvable safely).
- Local MCP entry shape (verified from the live schema and the working Engram entry):
```jsonc
{ "mcp": { "ms-access": {
    "type": "local",
    "command": ["C:\\abs\\path\\ms-access-mcp.exe", "mcp", "--profile", "core"],
    "enabled": true, "timeout": 30000 } } }
```
- `opencode` CLI v1.18.29: `opencode mcp add` is interactive-only for local servers (no command flag) — noninteractive registration MUST go through the config file. Validation: `opencode debug config`.
- uv facts: NOT currently installed. Installer: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`; versioned URLs `https://astral.sh/uv/<version>/install.ps1` pin exactly; GitHub release zips (`uv-x86_64-pc-windows-msvc.zip`) carry `.sha256` sidecars. `uv tool install --python 3.11 --force .` produces `%USERPROFILE%\.local\bin\ms-access-mcp.exe` (`uv tool dir --bin`). `uv tool install` has no `--extras` flag — extras go inline in the package spec; plan 001 made pywin32 a core platform dep so plain `.` suffices.
- Product entry point: `ms-access-mcp mcp [--profile core]` (plan 004).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Config validate | `opencode debug config` | exit 0, JSON prints |
| Tool exe | `uv tool dir --bin` | path containing `ms-access-mcp.exe` |
| Doctor | `ms-access-mcp doctor --json` | exit 0 |
| Configure unit tests | `uv run pytest tests/unit/test_opencode_config.py -q` | all pass |

## Scope

**In scope**: `scripts/install.ps1`, `src/ms_access_mcp/opencode_config.py`, `src/ms_access_mcp/cli.py` (`configure` subcommand), `tests/**`, SDD artifacts.

**Out of scope**: global registration as default behavior, JSONC editing, other harnesses (Claude/Cursor — backlog), PyInstaller.

## Git workflow

Branch `plan/005-installer`. SDD artifacts stored per repo convention (docs/sdd/ or Engram with `capture_prompt: false` — decide in propose phase). Conventional commits. No push.

## SDD cycle skeleton (fill each phase before moving on)

### Explore (done — facts inlined above; add nothing speculative)

### Propose

Intent: one-command install → zero-activation exe → safe local-default OpenCode registration. In scope: steps 1–5 of the Apply section. Out of scope: global-by-default, JSONC writes, credentials of any kind.

### Spec (minimum scenario set — write these as tests in apply)

1. **Fresh project registration**: directory with no `opencode.json` → file created containing ONLY `"$schema"` + `mcp.ms-access`; unrelated keys absent-none-lost.
2. **Preserve unrelated config**: existing `opencode.json` with `mcp.engram` + `permission` block → after merge, both intact byte-for-byte (modulo JSON formatting), `mcp.ms-access` added.
3. **Conflict detection**: existing `mcp.ms-access` with a DIFFERENT command → abort with clear message, no write (unless `--force` overwrites after backup).
4. **JSONC never touched**: directory/global containing `opencode.jsonc` → merge targets plain `.json` only; `.jsonc` mtime unchanged (test asserts).
5. **Global opt-in**: `configure opencode --global` writes `%USERPROFILE%\.config\opencode\opencode.json`; default (no flag) writes `./opencode.json` in CWD.
6. **Rollback**: validation failure after write (simulated by corrupting a copy in tests) → original file restored from timestamped backup; backup files land next to target as `opencode.json.bak-<ts>`.
7. **Remove**: `configure opencode --remove` deletes only the `mcp.ms-access` key, preserving everything else; works both scopes.
8. **Absolute command**: written command[0] equals resolved `uv tool dir --bin\ms-access-mcp.exe` (never bare `ms-access-mcp`).
9. **No secrets**: installer/configure never write credentials; env vars only for non-secret options.

### Design

- `opencode_config.py`: pure functions — `load(path)`, `merge(entry, config)`, `write_atomic(path, text, backup=True)`, `register(scope, exe_path, profile, force)`, `remove(scope)`, `validate_via_cli(cwd)` (shells `opencode debug config`, treats nonzero as failure → rollback).
- `install.ps1` parameters: `-Global`, `-Profile core`, `-NoRegister`, `-UvVersion <pin>`. Flow: verify x64 → bootstrap uv if absent (versioned installer URL, checksum-verified zip as fallback) → `uv python install 3.11` → `uv tool install --python 3.11 --force .` (run from the repo the script ships in) → `uv tool dir --bin` → `ms-access-mcp doctor --json` (abort on failure) → registration (default: local CWD with warning if CWD == repo root; `-Global` for global; `-NoRegister` skips) → print restart-once notice.
- Failure posture: any failed step leaves prior state intact and prints recovery instructions.

### Tasks / Apply

1. `opencode_config.py` merge/atomic/backup/rollback core — strict TDD on scenarios 1–4, 6, 8 (pure filesystem, temp dirs).
2. CLI `configure opencode [--global] [--remove] [--force]` — scenarios 5, 7; wiring to doctor-validated exe path.
3. `scripts/install.ps1` — flow above; dry-run flag `-WhatIf` honored.
4. Validation hook: post-write `opencode debug config` (skipped gracefully with a warning if `opencode` absent from PATH — scenario tests mock it).

### Verify (gates — orchestrator runs in-shell)

- Simulated clean install: `UV_TOOL_BIN_DIR` pointed at a fresh temp dir; full `install.ps1 -NoRegister` run; exe spawns `mcp` and answers `tools/list` (reuse plan-004 `stdio_smoke.py` against the installed exe).
- Registration on a THROWAWAY project dir (never the real global config during tests): merge → validate → remove; unrelated keys preserved (byte-diff of unrelated subtrees).
- Rollback drill: manually corrupt config after backup; re-run; original restored.
- Real-machine final check (one time, user-present): default local registration in an actual Access working project; restart OpenCode; tools appear.

## Done criteria

- [ ] All spec scenarios have automated tests, green
- [ ] Simulated clean install passes end-to-end with zero activation steps
- [ ] Rollback drill restores original config
- [ ] `.jsonc` untouched in all tests
- [ ] Installer aborts cleanly when doctor fails (simulated)
- [ ] `plans/README.md` row 005 updated

## STOP conditions

- OpenCode config semantics change (schema/merge behavior differs from the verified facts).
- `opencode debug config` cannot be used noninteractively as a validator.
- uv bootstrap fails on this machine after one retry with the checksum-verified zip fallback.

## Maintenance notes

- The absolute-exe-path entry breaks only if uv's bin dir moves — `configure --force` re-registration is the recovery.
- Backlog: `configure claude`/`configure cursor` follow the same pure-function core.
