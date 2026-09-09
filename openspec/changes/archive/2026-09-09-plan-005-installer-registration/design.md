# Design: Installer and OpenCode Registration

## Technical Approach

Two deliverables: (1) `opencode_config.py` — pure-function transaction core (target resolution, merge, conflict detection, backup, atomic write, validation, rollback); (2) `scripts/install.ps1` — thin orchestration: detect-then-bootstrap uv, `uv tool install --python 3.11 --force .`, doctor gate, delegate registration to `ms-access-mcp configure opencode`. `cli.py` adds a `configure opencode` subparser wired to the core. Maps to specs: installer (bootstrap, doctor gate, WhatIf, no secrets), opencode-config (S1–S7), cli-configure-subcommand (S5/S8, `--remove`/`--force`).

## Architecture Decisions

| # | Decision | Choice | Alternatives | Rationale |
|---|----------|--------|--------------|-----------|
| 1 | Config core | Pure functions in `opencode_config.py`; CLI/PS thin | Monolithic PowerShell JSON mutation | Testable via tempfiles, no subprocess mocking |
| 2 | Atomic write | Temp sibling + `fsync` + `os.replace` | In-place rewrite | Atomic on NTFS; crash never leaves partial file |
| 3 | Backup | Timestamped sibling `opencode.json.bak-<ts>` | In-memory rollback only | Survives crash; never overwrites prior backups |
| 4 | Validation gate | `opencode debug config` post-write; warn when OpenCode absent | JSON parse only | Only OpenCode knows merge semantics; absent CLI must not block |
| 5 | Conflict detection | Deep dict equality on `mcp.ms-access` | Compare `command[0]` only | Catches timeout/enabled/type drift |
| 6 | `--remove` semantics | Delete only `mcp.ms-access` key | Regenerate minimal file | S7: unrelated keys survive |
| 7 | Installer flow | Detect uv before bootstrap; astral.sh failure → one retry from GitHub zip with `.sha256` check | Always bootstrap; single URL | Respects existing installs; CDN outage ≠ failure; checksum stops corrupt zips |
| 8 | Scope resolution | Default `./opencode.json`; `--global` → `%USERPROFILE%\.config\opencode\opencode.json` | Global default | Proposal: avoid global MCP schema cost |

## Data Flow

Register:

```
install.ps1 / CLI ─> abs exe = (uv tool dir --bin)/ms-access-mcp.exe
      v
resolve_target(--global?) ─> opencode.json path
      v
load JSON (or {$schema}) ─conflict & !--force─> abort, zero writes
      v
backup .bak-<ts> ─> tmp sibling + fsync ─> os.replace
      v
opencode debug config ─fail─> restore backup (or delete new file)
      v  ok / unavailable(warn)
done
```

Remove:

```
configure opencode --remove ─> resolve_target ─> load
      ├─ key missing ─> Ok (no-op, no write)
      v
backup ─> del mcp["ms-access"] ─> tmp + os.replace ─> validate ─fail─> restore
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `scripts/install.ps1` | Create | uv bootstrap, tool install, doctor gate, registration, `-WhatIf` |
| `src/ms_access_mcp/opencode_config.py` | Modify | Replace stub with transaction core |
| `src/ms_access_mcp/cli.py` | Modify | Add `configure opencode` subparser + handler |
| `tests/unit/test_opencode_config.py` | Create | S1–S7 on temp dirs |
| `tests/unit/test_cli.py` | Modify | Flag wiring, scope, exit codes |
| `plans/README.md` | Modify | Plan 005 status |

## Interfaces / Contracts

```python
def resolve_target(global_scope: bool, cwd: Path) -> Path: ...
def build_entry(exe: Path) -> dict: ...  # {"type":"local","command":[str(exe),"mcp","--profile","core"],"enabled":True,"timeout":30000}
def register(target: Path, exe: Path, *, force: bool = False) -> Ok | Err: ...
def remove(target: Path) -> Ok | Err: ...
def validate(target_dir: Path) -> str: ...  # "ok" | "failed" | "unavailable"
def detect_conflict(existing: dict, desired: dict) -> bool: ...
```

`Ok`/`Err` reuse the `application.database` result pattern. Exit codes: 0 success/no-op, 1 conflict/validation failure.

```powershell
param(
    [switch]$Global,
    [switch]$Remove,
    [switch]$Force,
    [switch]$SkipRegistration,
    [switch]$WhatIf
)
```

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit | merge preservation, fresh file, conflict abort/force, removal, JSONC isolation (bytes+mtime), rollback, backup naming | `tempfile.TemporaryDirectory`; subprocesses monkeypatched |
| Integration | real `opencode debug config` on throwaway temp config | skipif opencode missing |
| Integration | full `install.ps1` drill | `UV_TOOL_BIN_DIR` → fresh temp dir; assert exe + registration |
| E2E | real machine, user-present Access project, launch via OpenCode | manual, out of CI |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|----------|---------------|-----------------|-------------------|
| Documentation-like paths (`install.ps1` via powershell) | Applicable | Switch-only params; no positional path execution | `-WhatIf` asserts zero files created/modified/deleted |
| Git repository selection (CWD authority; `--global`) | Applicable | CWD is authority; `--global` → USERPROFILE | No-flag run writes CWD file; `--global` writes user file |
| Commit state | N/A — never commits | — | none |
| Push state | N/A — never pushes | — | none |
| PR commands | N/A — never opens PRs | — | none |
| `uv tool install` subprocess | Applicable | Non-zero exit → abort; config untouched yet | Failed install → abort, `opencode.json` unchanged |
| `opencode debug config` subprocess | Applicable | Failure → restore backup or delete new file | Failing validator (injected) → rollback; bytes restored |
| `ms-access-mcp doctor --json` subprocess | Applicable | Failure → abort before registration | Doctor failure leaves no registration write |
| `opencode mcp add` discovery | Verified non-viable | v1.18.30 lacks local-command flag → manual merge | Entry shape equals S8 contract |
| Secrets in written config | Applicable | Only path/flags written | No tokens/credentials in output (S9) |

## Migration / Rollout

No migration required. Additive: new script, new subcommand, stub replaced. Existing configs untouched unless configure is invoked.

## Open Questions

None.
