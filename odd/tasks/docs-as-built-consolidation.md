# ODD Feature: As-built documentation consolidation

## Goal

Consolidate the current, built state of mcp-ms-access into an engineering
documentation layer modeled on the mcp-ms-excel agreements (docs 01-03),
without changing source behavior. User ruling: work one task at a time,
stop after each task for review.

## Tasks

- [x] T1 docs/01-architecture.md — as-built architecture derived from code
- [ ] T2 docs/02-contracts.md — command/error/limits contract catalogue
- [ ] T3 docs/03-safety-and-configuration.md — consolidated safety model, deduplicated against README
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
  claims listed in-doc (6 items). Commit pending user authorization.
