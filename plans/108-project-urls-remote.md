# Plan 108: [project.urls] + origin remote

> **Executor instructions**: Trivial; execute directly. When done,
> update the status row in `plans/README.md` (section "Plans 105–108").
>
> **Drift check (run first)**: `git remote -v` empty;
> `grep -n "project.urls" pyproject.toml` empty.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: packaging metadata
- **Planned at**: commit `5593a4b`, 2026-10-10

## Why this matters

Operator decision resolved this cycle: the package declares
`https://github.com/MetalbolicX/mcp-ms-access` and configures it as
`origin`. Until the GitHub repo is created and pushed (operator, out
of scope), the URL is declarative metadata and the remote is inert.

## Steps

1. `pyproject.toml`: add
   ```
   [project.urls]
   Homepage = "https://github.com/MetalbolicX/mcp-ms-access"
   Repository = "https://github.com/MetalbolicX/mcp-ms-access"
   ```
2. `git remote add origin https://github.com/MetalbolicX/mcp-ms-access.git`
3. Sanity: `.venv/Scripts/python.exe -B -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(d['project']['urls'])"`

## Verify

- tomllib parses the urls table; `git remote -v` shows origin.
- Budget gate unaffected (metadata-only).

## Out of scope

- Creating the GitHub repo, pushing, release tagging beyond what
  exists.
