# Proposal: Installer and OpenCode Registration

## Intent

Deliver Windows installation and safe OpenCode registration for `ms-access-mcp mcp --profile core`. Registration defaults to the project to avoid global MCP schema cost.

## Scope

### In Scope
- Build `scripts/install.ps1` to detect/bootstrap uv, install Python 3.11 and the tool, gate on doctor, and support registration controls and `-WhatIf`.
- Implement pure target, merge, conflict, removal, atomic-write, validation, and rollback operations.
- Expose `configure opencode [--global] [--remove] [--force]` using the executable resolved from `uv tool dir --bin`.
- Test installer, CLI, preservation, and recovery behavior.

### Out of Scope
- Any mutation of `opencode.jsonc`.
- Global-by-default behavior, credentials, other clients, PyInstaller, or non-Windows installation.

## Capabilities

### New Capabilities
- `installer`: Detect-before-bootstrap uv setup, tool installation, doctor gate, and registration controls.
- `opencode-config`: Safe `mcp.ms-access` merge/removal, JSONC isolation, transactions, validation, and rollback.
- `cli-configure-subcommand`: Local-default registration, global opt-in, removal, and forced replacement.

### Modified Capabilities
None.

## Approach

Keep deterministic operations in `opencode_config.py` and argparse, subprocess, and PowerShell boundaries thin. Resolve and register the absolute uv-installed executable. Mutate only plain `opencode.json`, preserving unrelated keys. Create a timestamped sibling backup, fsync a sibling temporary file, and atomically replace. Run `opencode debug config`; on failure, restore the backup or remove a newly created file. Warn when validation is unavailable.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `scripts/install.ps1` | New | Installation and registration |
| `src/ms_access_mcp/opencode_config.py` | Modified | Configuration transaction core |
| `src/ms_access_mcp/cli.py` | Modified | Configure command wiring |
| `tests/unit/test_opencode_config.py` | New | Safety scenarios |
| `tests/unit/test_cli.py` | Modified | CLI coverage |
| `plans/README.md` | Modified | Plan status |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| OpenCode semantics change | Medium | Validate writes; rollback failures |
| Partial replacement | Low | Backup first; fsync and atomic replace |
| uv path moves | Medium | Re-register with `--force` |
| Doctor fails | Medium | Abort before registration |

## Rollback Plan

Restore the timestamped backup after failed validation, or delete a new target when no prior file existed. `configure opencode --remove` removes only `mcp.ms-access`; code reversion leaves unrelated configuration intact.

## Dependencies

- Plan 004 entry point; Windows x64; Python 3.11+; uv; OpenCode CLI for validation.

## Success Criteria

- [ ] Clean installation yields a working absolute-path executable without activation.
- [ ] Registration defaults locally; `--global` selects user configuration.
- [ ] Unrelated keys survive; JSONC bytes and mtime never change.
- [ ] Conflicts abort unless forced; removal affects only `mcp.ms-access`.
- [ ] Doctor or validation failure leaves no partial registration.
- [ ] Installer, CLI, transaction, and rollback tests pass.
