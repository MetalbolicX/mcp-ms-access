# ODD Feature: As-built documentation consolidation

## Goal

Consolidate the current, built state of mcp-ms-access into an engineering
documentation layer modeled on the mcp-ms-excel agreements (docs 01-03),
without changing source behavior. User ruling: work one task at a time,
stop after each task for review.

## Tasks

- [x] T1 docs/01-architecture.md — as-built architecture derived from code
- [x] T2 docs/02-contracts.md — command/error/limits contract catalogue
- [x] T3 docs/03-safety-and-configuration.md — consolidated safety model, deduplicated against README
- [ ] T4 Cross-references README <-> docs + consistency readback
- [ ] T5 Final verification + work-unit commit closure

## Constraints

- Documentation only; no source changes.
- As-built: derived from code/plans/Engram memory, never new design.
- Keep plans ledger, OpenSpec lifecycle, and operational README as-is.
- One task at a time per user ruling; report and stop after each.
- Work-unit commit per task on a feature branch (branch first from main);
  push/PR remain user decisions.

## Evidence

- Gap analysis: Engram observation #9608 (2026-10-08).
- Excel reference model: D:/code/python/mcp-ms-excel docs/01-05.
- Access built state: 94 plan files, plans 001-110 delivered, clean main.
- T1: gentle-ai-explore returned path:line map (2026-10-08); parent wrote
  docs/01-architecture.md (169 lines, 9 sections); spot-check verified
  cli.py:1944, server.py:82, errors.py:34/54, test_architecture_boundaries.py:9,
  pyproject.toml:21; corrected 3 drifted refs to dispatcher.py:35,
  profiles.py:14, _session_registry.py:27. Passive docs: no runnable tests
  applicable; structural readback + spot-checks used instead. Unverified
  claims listed in-doc (6 items). Work-unit commit: 42f29bd on branch
  docs/as-built-consolidation (2 files, 207 insertions), user-authorized.
- T2: gentle-ai-explore returned contract catalogue with path:line evidence;
  parent wrote docs/02-contracts.md (161 lines, 9 sections): Ok/Err envelope
  + MCP/CLI rendering + exit codes 0/1/2, AccessMcpError hierarchy with
  scrubbing guarantees and SQL-text hole, 4 authorization gates, limits
  (core<=30 hard gate tool_budget.py:55, 120s DISPATCH_TIMEOUT dispatcher.py:43,
  no default row caps), 22 CLI families, MCP param conventions, credentials
  resolution order + hard-reject, 5 unverified claims. Spot-checks verified
  safety.py:76/81/85, credentials.py:244/401/415, core.py:253/270,
  errors.py:34/54/90, tool_budget core<=30. Passive docs: structural
  verification only. Work-unit commit recorded in git history on
  docs/as-built-consolidation (T2 commit; hash backfilled below).
- T3: residual evidence gathered inline (env-var inventory grep, crash-recovery
  spec read, backend selector read, doctor check map, README section map);
  parent wrote docs/03-safety-and-configuration.md (162 lines, 10 sections):
  5-env-var complete table, safety invariants + honest boundaries, backend
  capability model (COM-only caps), credentials storage security, crash
  resilience model (5-rung ladder + auto-session invariants + g13 matrix),
  7 doctor checks, distribution surface, dedupe map README<->docs, 4
  unverified claims. Spot-checks verified safety.py:15/62, profiles.py:84/130,
  credentials.py:13, automation.py:68/76, backends __init__ 80/121,
  opencode_config.py:173/307, database.py:182/187. Passive docs: structural
  verification only. Work-unit commit on docs/as-built-consolidation.
