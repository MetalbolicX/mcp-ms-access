# cli-configure-subcommand Specification

## Purpose

`configure opencode` registers, force-replaces, or removes the `ms-access` MCP entry using the absolute installed executable path.

## Requirements

### Requirement: Local-Default Targeting
The command MUST target `./opencode.json` (CWD) by default; `%USERPROFILE%\.config\opencode\opencode.json` only with `--global`.

#### Scenario: S5 — global opt-in
- WHEN `configure opencode` runs without flags
- THEN `./opencode.json` in CWD is the target
- WHEN run with `--global`
- THEN the user-level config is the target

### Requirement: Absolute Command Registration
`command[0]` MUST equal the resolved absolute path of the uv-installed `ms-access-mcp.exe`, never a bare name. Entry shape: `{ "type": "local", "command": [<abs path>, "mcp", "--profile", "core"], "enabled": true, "timeout": 30000 }`.

#### Scenario: S8 — absolute command
- GIVEN the tool is installed via uv
- WHEN registration completes
- THEN `command[0]` is the absolute path to `ms-access-mcp.exe`

### Requirement: Removal and Force Flags
The command MUST support `--remove` (config-core removal) and `--force` (replace conflicting entry after backup); without `--force`, conflicts MUST abort.

#### Scenario: removal via CLI
- GIVEN a target config containing `mcp.ms-access`
- WHEN `configure opencode --remove` runs
- THEN only that entry is deleted
