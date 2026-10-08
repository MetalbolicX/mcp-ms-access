# ODD Feature: CLI mcp --profile help drift (discrepancy 1)

## Goal

Resolve recorded discrepancy 1: the `mcp --profile` help text in
cli.py lists 5 of 8 profiles (missing ddl, analysis, migration) while
validation accepts all 8 (profiles.py:14, server.py:149). Fix by
deriving the valid-values sentence from PROFILES — single source of
truth so the drift cannot recur.

## Tasks

- [x] T1 RED: parser-surface test asserting the "Valid values:" segment
  of the mcp --profile help equals set(PROFILES)
- [x] T2 GREEN: cli.py imports PROFILES and builds the help dynamically
- [x] T3 Verification: focused pytest + ruff + full gate qualify.py
- [x] T4 Docs sync (docs/01 claim 6, docs/02 claim 5 -> resolved) +
  ledger + work-unit commit

## Constraints

- Deliberately NO `choices=` on the argument: adding it would change
  the observable exit code for invalid profiles (argparse exit 2
  instead of server-side exit 1). Zero behavior change beyond help
  text; server keeps owning validation.
- Stale parenthetical "(automation, maintenance, versioning)" replaced
  by the accurate "additional profiles are opt-in".
- Inline direct (small, mechanical, parent context); stacked on
  fix/db-op-error-typing (0af7ffa).

## Evidence

- Defect: cli.py _build_mcp_parser hand-written help "Valid values:
  core, automation, maintenance, versioning, full" (5) vs PROFILES
  frozenset of 8 (profiles.py:14).
- Test home: tests/unit/test_cli_surface.py (structural parser
  invariants; existing pattern extracts subparsers from
  parser._actions dest == "command").

## Evidence (delivery)

- T1 inline (small mechanical): TestMcpProfileHelpContract appended to
  test_cli_surface.py; RED observed (1 failed: help listed {core,
  automation, maintenance, versioning, full} vs PROFILES of 8).
- T2 inline: cli.py imports PROFILES (:33); _build_mcp_parser builds
  valid_profiles = ", ".join(sorted(PROFILES)) (:1471); stale
  parenthetical replaced. Rendered help verified listing all 8.
- T3 via gentle-ai-verify: qualify.py exit 0 (ruff OK, pyright 0 errors,
  unit 1667 passed / 3 skipped incl. the new contract, budget PASS);
  full unit suite separate run exit 0. No unexpected mutations.
- T4: docs/01 claim 6 and docs/02 claim 5 marked resolved with new refs.

## Status: CLOSED (all 4 tasks done; merge/push remain user decisions)
