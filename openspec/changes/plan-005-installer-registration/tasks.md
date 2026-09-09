# Tasks: Installer and OpenCode Registration

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~600-700 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Transaction core `src/ms_access_mcp/opencode_config.py`: merge/conflict/backup/atomic-write/rollback | PR 1 | `uv run pytest tests/unit/test_opencode_config.py -q` | Tempdir; validator mocked | Delete Unit 1 files |
| 2 | `configure opencode [--global] [--remove] [--force]` in `src/ms_access_mcp/cli.py` | PR 2 | `uv run pytest tests/unit/test_cli.py -q` | argv tests; core mocked | Revert Unit 2 files |
| 3 | Gates + uv bootstrap + controls in `scripts/install.ps1` | PR 3 | `pwsh -NoProfile -File scripts/install.ps1 -NoRegister -WhatIf` | Fresh `UV_TOOL_BIN_DIR` tempdir; `-NoRegister` skips registration | Delete `scripts/install.ps1`; restore `pyproject.toml` if touched |

## Phase 1: opencode_config core

- [x] 1.1 RED S1: absent `opencode.json` -> created with only `$schema` + `mcp.ms-access`
- [x] 1.2 RED S2: existing `mcp.engram` + `permission` keys survive merge
- [x] 1.3 RED S3: differing `mcp.ms-access.command` aborts, no writes; force replaces after backup
- [x] 1.4 RED S4: sibling `opencode.jsonc` bytes and mtime unchanged
- [x] 1.5 RED S6: failed validation restores `opencode.json.bak-<ts>` or deletes new file
- [x] 1.6 RED threat `opencode debug config`: injected failing validator -> rollback restores original bytes
- [x] 1.7 RED S9: written config has no secrets, only path + flags
- [x] 1.8 GREEN: implement `src/ms_access_mcp/opencode_config.py` per design contract
- [x] 1.9 REFACTOR: pure functions; subprocess confined to validate()

## Phase 2: cli configure subcommand

- [ ] 2.1 RED S5: no flag targets CWD `opencode.json`; `--global` targets user config
- [ ] 2.2 RED S7: `--remove` deletes only `mcp.ms-access`; missing key is no-op exit 0
- [ ] 2.3 RED S8: entry matches contract; `command[0]` absolute, never bare name
- [ ] 2.4 GREEN: add `configure opencode` subparser + handler in `src/ms_access_mcp/cli.py`; exit codes 0/1
- [ ] 2.5 REFACTOR: handler thin; logic in core

## Phase 3: installer.ps1

- [ ] 3.1 RED threat WhatIf: `-WhatIf` mutates zero files
- [ ] 3.2 RED threat `uv tool install`: failed install aborts; `opencode.json` unchanged
- [ ] 3.3 RED threat doctor: `ms-access-mcp doctor --json` failure aborts before registration
- [ ] 3.4 GREEN: create `scripts/install.ps1` — detect-then-bootstrap uv (checksum zip fallback), `uv tool install --python 3.11 --force .`, doctor gate, validation delegated to CLI, `-Global`/`-Remove`/`-Force`/`-NoRegister`/`-WhatIf`
- [ ] 3.5 Verify: `pwsh -NoProfile -File scripts/install.ps1 -NoRegister -WhatIf` exits 0

## Phase 4: end-to-end drill (orchestrator)

- [ ] 4.1 Fresh `UV_TOOL_BIN_DIR` tempdir install; exe answers tools/list via `scripts/stdio_smoke.py` (read-only)
- [ ] 4.2 Register, validate, remove on throwaway project dir; unrelated keys byte-preserved
- [ ] 4.3 Rollback drill: corrupt after backup, re-run, original restored
- [ ] 4.4 Update `plans/README.md` row 005

Apply-phase agent: @medium (CAP:5, strict TDD).
