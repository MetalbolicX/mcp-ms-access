# opencode-config Specification

## Purpose

Transactional management of the `mcp.ms-access` entry in plain `opencode.json`, without touching unrelated keys or `opencode.jsonc`.

## Requirements

### Requirement: Fresh Registration
The system MUST create `opencode.json` when absent, containing only `$schema` and `mcp.ms-access`.

#### Scenario: S1 — fresh registration
- GIVEN no `opencode.json` exists
- WHEN registration runs
- THEN the file contains only `$schema` and `mcp.ms-access`

### Requirement: Merge Preservation
The system MUST preserve all pre-existing keys when adding `mcp.ms-access`.

#### Scenario: S2 — preserve unrelated config
- GIVEN `opencode.json` contains `mcp.engram` and `permission`
- WHEN `mcp.ms-access` is merged in
- THEN both remain intact (modulo formatting)

### Requirement: Conflict Detection
The system MUST abort with a clear message and write nothing when `mcp.ms-access` exists with a different command, unless forced.

#### Scenario: S3 — conflicting entry
- GIVEN `mcp.ms-access.command` differs from the resolved command
- WHEN registration runs without force
- THEN it aborts and the file is unchanged
- WHEN run with force
- THEN the entry is replaced after backup

### Requirement: JSONC Isolation
The system MUST NOT read or mutate `opencode.jsonc`; only plain `.json` is targeted.

#### Scenario: S4 — jsonc untouched
- GIVEN an `opencode.jsonc` exists in the target scope
- WHEN registration runs
- THEN its bytes and mtime are unchanged

### Requirement: Rollback
The system MUST take a timestamped sibling backup before overwrite, validate after write, restore the backup (or delete a newly created file) on failure.

#### Scenario: S6 — rollback
- GIVEN backup `opencode.json.bak-<ts>` was taken
- WHEN post-write validation fails
- THEN the original is restored; no partial registration remains

### Requirement: Removal
The system MUST remove only `mcp.ms-access`, preserving everything else.

#### Scenario: S7 — remove entry
- GIVEN `opencode.json` contains `mcp.ms-access` plus unrelated keys
- WHEN removal runs
- THEN only `mcp.ms-access` is deleted
