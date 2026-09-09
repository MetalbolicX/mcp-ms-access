## Exploration: plan-005-installer-registration

### Current State

The package is named `ms-access-mcp` and exposes the console script
`ms-access-mcp = "ms_access_mcp.cli:main"` from `pyproject.toml`. The existing
CLI uses one top-level `argparse` parser with subcommands; `mcp` is already
registered and forwards to the stdio server. Its `--profile` argument defaults
to `None`, then `resolve_profile()` applies `ACCESS_MCP_PROFILE` or `core`, so
the registration command should write:

```json
{
  "type": "local",
  "command": ["C:\\absolute\\path\\ms-access-mcp.exe", "mcp", "--profile", "core"],
  "enabled": true,
  "timeout": 30000
}
```

`uv tool dir --bin` currently returns `C:\Users\MetalbolicX\.local\bin` on
this machine. Therefore `uv tool install --python 3.11 --force .` should make
the registered executable the absolute path from that directory joined with
`ms-access-mcp.exe`; the implementation must not register the bare command.
The plan's snapshot says uv was absent, but uv is now present as 0.12.11, so
the installer must retain its detect-before-bootstrap behavior.

`opencode debug config` was run locally. It exited 0 and emitted one parseable
JSON document (634 output lines), with top-level `$schema`, `mcp`, `permission`,
and other configuration keys. The parsed merged `mcp` object contained the
existing `context7` remote entry and `engram` local entry. `opencode mcp add
--help` exposes `--url`, `--env`, and `--header`, but no local command option;
local registration therefore cannot be a noninteractive command-flag flow and
must be implemented by writing the config file.

The global OpenCode directory currently has both `opencode.json` and
`opencode.jsonc`. The JSONC file owns the existing `context7` and `engram`
entries, including a command array. There is no JSONC round-trip dependency in
the project, and standard JSON parsing cannot preserve comments or formatting.
The invariant must therefore be: mutate only the plain target `opencode.json`,
add or remove only `mcp.ms-access`, and never rewrite, parse-and-reserialize,
rename, or otherwise touch `opencode.jsonc`.

The repository already contains `src/ms_access_mcp/opencode_config.py`, but it
is only a one-line placeholder. Unit tests live under `tests/unit/` and use
pytest with ordinary `test_*.py` modules, so the planned
`tests/unit/test_opencode_config.py` matches the existing structure.

For uv bootstrap, the current Astral installer documents the unpinned
PowerShell command `powershell -ExecutionPolicy ByPass -c "irm
https://astral.sh/uv/install.ps1 | iex"` and the pinned form
`https://astral.sh/uv/<version>/install.ps1`. The release fallback follows
`https://github.com/astral-sh/uv/releases/download/<version>/uv-x86_64-pc-windows-msvc.zip`
with a matching `.zip.sha256` sidecar. The installer should verify that sidecar
before extracting the fallback archive and should use the versioned installer
URL when `-UvVersion` is supplied.

### Affected Areas

- `pyproject.toml` — defines the `ms-access-mcp` executable that uv installs.
- `src/ms_access_mcp/cli.py` — existing argparse wiring needs a `configure opencode` branch without disturbing database or stdio dispatch.
- `src/ms_access_mcp/server.py` — confirms the registered command reaches stdio MCP and resolves the default profile through `resolve_profile()`.
- `src/ms_access_mcp/profiles.py` — establishes `core` as the fallback profile.
- `src/ms_access_mcp/opencode_config.py` — placeholder for pure config loading, merge/remove, validation, backup, atomic replacement, and rollback functions.
- `scripts/install.ps1` — new Windows x64 installer: pinned/detected uv bootstrap, Python 3.11, uv tool installation, doctor gate, registration, and `-WhatIf`/failure handling.
- `tests/unit/test_opencode_config.py` — new tempfile-based tests for merge, conflict, JSONC untouched, absolute command, atomic backup/rollback, and removal behavior.
- `tests/unit/test_cli.py` or `tests/unit/test_cli_basic.py` — likely location for `configure opencode` parser/wiring coverage, following the existing subprocess and patched-argv patterns.
- `C:\Users\<user>\.config\opencode\opencode.json` — global opt-in target; the adjacent `.jsonc` file is explicitly out of scope for mutation.

### Approaches

1. **Pure Python config core with CLI boundary** — keep JSON loading, deterministic merge/remove, conflict comparison, target selection, backup, atomic write, and rollback in `opencode_config.py`; let `cli.py` translate argparse options and invoke `opencode debug config` as an external validation boundary.
   - Pros: directly matches the repository's functional/declarative convention; tempfile tests can cover filesystem semantics without OpenCode or Access; local/global behavior is explicit and auditable.
   - Cons: validation is an external process and must be mocked or skipped with a warning when OpenCode is absent; rollback must distinguish write failures from validation failures.
   - Effort: Medium

2. **Delegate registration to `opencode mcp add`** — invoke the OpenCode command and attempt to automate its local-server prompts.
   - Pros: would follow the CLI's own registration UX if it supported a noninteractive command argument.
   - Cons: the installed help shows no local command option; prompt automation is brittle, interactive-only, and would not provide reliable control over backups, conflict detection, JSONC avoidance, or rollback.
   - Effort: High

### Recommendation

Use Approach 1. Register the exact absolute executable path returned by
`uv tool dir --bin`, append `mcp --profile core`, and default to the project
working directory's `opencode.json`; require `--global` for the user config
path. Treat `opencode.json` as a JSON object and preserve unrelated keys while
adding only `mcp.ms-access`. If an existing entry differs, abort unless
`--force` is present.

Make writes transactional: create a timestamped sibling backup before changing
an existing target, write serialized JSON to a temporary sibling file, flush
and fsync it, then replace the target with `os.replace` (or the Windows
equivalent). Run `opencode debug config` against the resulting configuration;
nonzero exit means validation failure, so restore the backup and report
recovery instructions. A fresh target should be removed again on validation
failure when no prior backup exists. The same validation/rollback gate should
be used by the installer after doctor succeeds, while `-NoRegister` bypasses
registration and `-WhatIf` performs no mutation.

Never include `opencode.jsonc` in the target discovery or rewrite path. Tests
should assert its bytes and mtime remain unchanged, and should exercise both
the fresh-project and global-like directory cases with mocked validation.

### Risks

- OpenCode's merged configuration can change schema or precedence; the installer must treat a nonzero `opencode debug config` exit as a hard registration failure and preserve the previous file.
- A backup made after a write or a non-atomic direct write would defeat rollback; backup creation must precede replacement and temporary files must be siblings of the target.
- `opencode.jsonc` may contain comments or ownership that is not represented in the merged JSON; writing it would lose user data, so its untouched invariant is mandatory.
- The absolute uv bin path can move between installations or users; `configure --force` is the intended recovery, and registration should resolve the path at invocation time.
- The pinned installer script and GitHub zip/sidecar can become unavailable or differ by architecture; the x64 check and one fallback retry must fail before changing project or OpenCode state.
- `doctor --json` may fail on machines without Access/COM/ODBC; installation must abort before registration rather than leave a partially configured server.

### Ready for Proposal

Yes — tell the orchestrator to propose the pure-function config core plus a
thin argparse/PowerShell boundary, with project-local registration as the
default, explicit global opt-in, post-write `opencode debug config` validation,
timestamped backup/atomic replacement/rollback, and an absolute uv-installed
executable. The proposal must make the JSONC-untouched rule and noninteractive
`opencode mcp add` limitation explicit.
