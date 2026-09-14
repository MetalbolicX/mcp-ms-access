# Plan 032: Portable win64 zip distribution — embeddable Python runtime, optional migration drivers

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 4aba764..HEAD -- src/ms_access_mcp/cli.py src/ms_access_mcp/opencode_config.py src/ms_access_mcp/__main__.py scripts/ pyproject.toml`
> Expected: empty output. If any in-scope file changed since this plan was
> written, compare the "Current state" excerpts against the live code before
> proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/005-installer-and-opencode-registration.md (registration machinery reused), plans/024-migration-profile.md (the `migrate` extra this plan vendors)
- **Category**: dx
- **Planned at**: commit `4aba764`, 2026-09-14

## Why this matters

Users who cannot run the uv bootstrap (`scripts/install.ps1`) — policy-locked
or offline machines — have no install path today; the only planned fallback was
a "Portable PyInstaller onedir" backlog line. The operator rejected the
PyInstaller class of freezers: Windows Defender's ML heuristics routinely
false-positive packed bootloaders, and pywin32/win32com have classic
hidden-import and gen_py failure modes under freezing. This plan ships a
freezer-free portable zip built on the **official PSF-signed `python.exe`**
(Windows embeddable distribution) plus standard prebuilt wheels — plain,
well-known binaries that Defender does not flag. The end-user flow is:
unzip → run one register command → done. A `-IncludeMigrate` build switch
vendors the optional migration drivers (psycopg2, mysql-connector) so offline
migration users are not forced back to the uv path.

## Current state

Facts the executor needs (verified 2026-09-14 against `main @ 4aba764`):

- `src/ms_access_mcp/__main__.py` — **does not exist**. `python -m ms_access_mcp`
  fails today; only the console script (`ms-access-mcp` entry point) and
  `python -m ms_access_mcp.server` work.
- `src/ms_access_mcp/cli.py` — argparse CLI; `def main() -> int` at
  `cli.py:974`; subparsers at `cli.py:978`; `mcp` subcommand at
  `cli.py:1159-1172` (`--profile`, default from `ACCESS_MCP_PROFILE`).
  `configure opencode` resolves its command via `_resolve_exe_path()` at
  `cli.py:814-844`: `--exe-path` override (`:823-827`, flag registered at
  `:995`) → `shutil.which("ms-access-mcp")` (`:830-832`) → uv-tool fallback
  `%USERPROFILE%\.local\bin\ms-access-mcp.exe` (`:835-842`).
- `src/ms_access_mcp/opencode_config.py` — `entry_from()` builds the MCP entry
  with `command=(str(exe_path), "mcp", "--profile", profile)` at
  `opencode_config.py:147-154`. There is **no way to express
  `[python.exe, "-m", "ms_access_mcp", "mcp", ...]`** today.
- `pyproject.toml` — `[project.scripts] ms-access-mcp = "ms_access_mcp.cli:main"`;
  core deps `mcp>=1.27,<2.0`, `pydantic>=2.0`, `pyodbc>=5.0`,
  `pywin32>=306; sys_platform == 'win32'`; extras
  `migrate = ["psycopg2-binary>=2.9.0", "mysql-connector-python>=8.0.0"]`,
  `xlsx = ["openpyxl>=3.1.0"]` (deliberately unused — plan 023 is IISAM-only),
  `dev`; `requires-python = ">=3.11"`; hatchling backend; `.python-version` =
  `3.11`; `uv.lock` present at repo root.
- `scripts/g11_installed_stdio_smoke.py` — reusable stdio smoke template:
  discovers an installed exe (`:114-134`), spawns
  `[exe_path, "mcp", "--profile", "core"]` (`:172-179`), performs the MCP
  initialize → tools/list → get_tables → EOF handshake, and "runs from any cwd
  — does not depend on the source tree" (`:11`). Its argv is exe-only, so it
  cannot smoke `[python.exe, "-m", ...]` as-is.
- `scripts/install.ps1:278-296` — the repo's proven download pattern: versioned
  URL → temp zip → SHA256 verification → `Expand-Archive`. Imitate its style
  (`SupportsShouldProcess`, `-WhatIf`, gate-on-failure with recovery text).
- Repo conventions (from `plans/README.md`): conventional commits (English, no
  AI attribution); branch per plan `plan/NNN-<slug>`; never push unless
  instructed; strict TDD = RED (quoted failing output) → GREEN → REFACTOR;
  COM hygiene — never mutate canonical fixtures.

## Commands you will need

| Purpose          | Command                                                        | Expected on success            |
|------------------|----------------------------------------------------------------|--------------------------------|
| Sync env         | `uv sync --extra dev`                                          | exit 0                         |
| Unit tests       | `uv run pytest tests/unit -q`                                  | all pass (1040+ currently)     |
| Lint             | `uv run ruff check .`                                          | exit 0                         |
| Typecheck        | `uv run pyright`                                               | 0 errors                       |
| Build wheel      | `uv build`                                                     | `dist/*.whl` + `dist/*.tar.gz` |
| Export locked deps | `uv export --no-dev --no-emit-project --format requirements-txt` | pinned requirements on stdout  |
| Portable smoke   | `uv run python scripts/g12_portable_stdio_smoke.py -- <exe> [args...]` | handshake OK, exit 0   |

## Scope

**In scope** (the only files you should modify):

- `src/ms_access_mcp/__main__.py` (create)
- `src/ms_access_mcp/opencode_config.py` (module-mode command support)
- `src/ms_access_mcp/cli.py` (`--python-mode` flag on `configure opencode`)
- `scripts/build_portable.ps1` (create)
- `scripts/g12_portable_stdio_smoke.py` (create)
- `tests/unit/test_main_module.py` (create)
- `tests/unit/test_configure_python_mode.py` (create)
- `plans/README.md` (status row + backlog line — orchestrator may own this)

**Out of scope** (do NOT touch, even though they look related):

- `pyproject.toml`, `uv.lock` — the `migrate`/`xlsx` extras already exist;
  `uv export --extra migrate` consumes them. No lock regeneration.
- `scripts/install.ps1` — the uv install path stays exactly as-is.
- `src/ms_access_mcp/safety.py`, `docs/`, `openspec/` — untouched.
- The `xlsx` extra is NOT vendored into the zip (IISAM-only decision, plan 023).
- No code signing, no winget/Scoop/Chocolatey publishing, no CI workflow, no
  auto-update — deferred backlog, separate decision.

## Git workflow

- Branch: `plan/032-portable-zip` from `main @ 4aba764`.
- Conventional commits, English, no AI attribution (match `git log` style,
  e.g. `feat(portable): ...`, `test(portable): ...`, `build(portable): ...`).
- Do NOT push or open a PR unless the operator instructs it.

## Steps

### Step 1: `__main__.py` entry (strict TDD)

RED — create `tests/unit/test_main_module.py`:

```python
"""Pin `python -m ms_access_mcp` dispatch (portable-runtime entry)."""

from __future__ import annotations

import subprocess
import sys


def test_module_main_version_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ms_access_mcp", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
```

Run `uv run pytest tests/unit/test_main_module.py -q` → quote the failure
(`No module named ms_access_mcp.__main__`).

GREEN — create `src/ms_access_mcp/__main__.py`:

```python
"""Module entry point: ``python -m ms_access_mcp`` (portable runtime)."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
```

**Verify**: `uv run pytest tests/unit/test_main_module.py -q` → 1 passed.

### Step 2: `--python-mode` registration (strict TDD)

RED — create `tests/unit/test_configure_python_mode.py`. First run
`Get-ChildItem tests\unit\test_*opencode*`; if it returns matches, model the
new file's fixtures/mocking after those files, otherwise after
`tests/unit/test_server_build.py`. Pin:

- `entry_from(exe, "core", module_mode=True)` produces command argv
  `[str(exe), "-m", "ms_access_mcp", "mcp", "--profile", "core"]`.
- `entry_from(exe, "core")` (default) is byte-identical to today's output —
  `[str(exe), "mcp", "--profile", "core"]`. Zero drift for uv installs.
- `configure opencode --python-mode` uses `sys.executable` as the exe path
  (monkeypatch `sys.executable`; assert the resolved command starts with it).

Run → quote the failures. Expected failure signatures:
`TypeError: entry_from() got an unexpected keyword argument 'module_mode'`
and argparse `error: unrecognized arguments: --python-mode`.

GREEN:

1. `opencode_config.py` — extend `entry_from` (near `:147-154`) with keyword
   `module_mode: bool = False`; when true, insert `"-m", "ms_access_mcp"`
   between the exe path and `"mcp"`.
2. `cli.py` — register `--python-mode` on the `configure opencode` parser next
   to `--exe-path` (`:995`); in the configure handler, when set, use
   `Path(sys.executable)` as the exe path and pass `module_mode=True` to
   `entry_from`. Mutually exclusive with `--exe-path` (`add_mutually_exclusive_group`).

**Verify**: `uv run pytest tests/unit/test_configure_python_mode.py tests/unit -q`
→ all pass; `uv run ms-access-mcp configure opencode --help` lists
`--python-mode`; `uv run ruff check src/ tests/` exit 0; `uv run pyright` 0 errors.

### Step 3: `g12_portable_stdio_smoke.py` (parameterized stdio smoke)

Create `scripts/g12_portable_stdio_smoke.py` by copying
`scripts/g11_installed_stdio_smoke.py` **in full** — including its fixture-DB
copy scaffolding and `ACCESS_MCP_ALLOWED_DIRS` env setup (g11 `:114-134`,
`:248-251`) and its `>= 20 tools` assertion — and replacing only the exe
discovery with an argument parser:

```python
parser = argparse.ArgumentParser()
parser.add_argument("command", nargs=argparse.REMAINDER,
                    help="full command list after `--`, e.g. -- runtime\\python.exe -m ms_access_mcp mcp --profile core")
```

Strip a leading `--` from `command`, require non-empty, spawn it, and reuse
g11's handshake checks verbatim (initialize → initialized → tools/list →
get_tables against the fixture copy → EOF; same pass/fail reporting).

**Verify**: `uv run python scripts/g12_portable_stdio_smoke.py -- uv run python -u -m ms_access_mcp mcp --profile core`
→ handshake OK, exit 0 (requires Step 1's `__main__.py`; smokes against the
source tree as a sanity check).

### Step 4: `scripts/build_portable.ps1` (the build)

Create the script imitating `install.ps1` structure (`[CmdletBinding(SupportsShouldProcess)]`,
`-WhatIf` support, gate-on-failure with recovery text). Parameters:

```powershell
param(
  [switch]$IncludeMigrate,   # vendor psycopg2 + mysql-connector (the `migrate` extra)
  [string]$Profile = "core", # profile written into register-opencode.ps1 and README-PORTABLE.md
  [switch]$Global,           # forwarded to register-opencode.ps1 (--global registration)
  [switch]$SkipDefenderScan
)
```

Behavior, in order — abort with non-zero exit on any gate failure:

1. **Gates**: OS is Windows x64; cwd is repo root (`pyproject.toml` + `uv.lock`
   present); `uv` on PATH (`uv --version`).
2. **Artifacts**: `uv build` (capture newest `dist\*.whl`);
   `uv export --no-dev --no-emit-project --format requirements-txt
   [--extra migrate when -IncludeMigrate] -o build\portable\requirements.txt`.
3. **Embeddable runtime**: constants `$EmbedVersion = "3.11.9"` and
   `$EmbedSha256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"`
   at script top (SHA256 computed from the actual artifact 2026-09-14; MD5
   cross-checked against the python.org release page,
   `6d9aa08531d48fcc261ba667e2df17c4`). Download
   `https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip`
   to temp, `Get-FileHash -Algorithm SHA256` must equal `$EmbedSha256`
   (case-insensitive; mismatch = STOP), then `Expand-Archive` to
   `build\portable\stage\ms-access-mcp-<version>-portable-win64\runtime\`
   (`<version>` read from `pyproject.toml`).
4. **Patch `runtime\python311._pth`** to exactly:
   ```
   python311.zip
   .
   Lib\site-packages
   import site
   ```
   The `import site` line is load-bearing: it enables `.pth` processing, which
   is what pywin32's `pywin32_bootstrap` uses to resolve
   `pythoncom311.dll`/`pywintypes311.dll`. Do NOT run `pywin32_postinstall`
   (it registers shell extensions machine-wide — unwanted for a portable zip;
   `win32com.client` automation works without it).
5. **Vendor packages**:
   `uv pip install --python runtime\python.exe --target runtime\Lib\site-packages -r build\portable\requirements.txt <wheel-from-step-2>`.
6. **Runtime gates** (all must pass before zipping):
   - `& runtime\python.exe -c "import win32com.client, pyodbc, mcp, ms_access_mcp"` → exit 0
   - `& runtime\python.exe -m ms_access_mcp --version` → stdout equals
     `ms-access-mcp <version>` where `<version>` is read from `pyproject.toml`
     (note: the string is hardcoded at `cli.py:976`; if pyproject and cli.py
     have drifted, this gate catches it — fix `cli.py:976`, not the gate)
   - `& runtime\python.exe -m ms_access_mcp doctor --json` → exit 0 (same
     exit-code gate `install.ps1:52` uses)
   - `uv run python scripts\g12_portable_stdio_smoke.py -- <abs>\runtime\python.exe -m ms_access_mcp mcp --profile core` → exit 0
     (smoke is always on `core` — smallest tool surface)
   - When `-IncludeMigrate`: `& runtime\python.exe -c "import psycopg2, mysql.connector"` → exit 0
7. **Stage extras**: write `register-opencode.ps1` into the stage root —
   one line: `& "$PSScriptRoot\runtime\python.exe" -m ms_access_mcp configure opencode --python-mode --profile <Profile>`
   where `<Profile>` is the `$Profile` parameter; when `$Global` is set,
   append `--global`. Write `README-PORTABLE.md`: 3 steps (unzip →
   optional `register-opencode.ps1` → requires 64-bit MS Access/ACE on the
   machine), the `doctor` command, and the zip variant (with/without migrate).
8. **Zip**: `Compress-Archive` stage folder →
   `dist\ms-access-mcp-<version>-portable-win64.zip` (append `-migrate` before
   `.zip` when `-IncludeMigrate`). Report final size.
9. **AV gate** (unless `-SkipDefenderScan`): if
   `$env:ProgramFiles\Windows Defender\MpCmdRun.exe` exists, run
   `& "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" -Scan -ScanType 3 -File <zip>`
   → exit code 0 expected (2 = threats found = STOP). If the exe is absent,
   print "Defender scan skipped (MpCmdRun.exe unavailable)" and continue.

**Verify** (orchestrator runs both variants):
`pwsh scripts\build_portable.ps1` → `dist\ms-access-mcp-*-portable-win64.zip`
exists, all step-6 gates green.
`pwsh scripts\build_portable.ps1 -IncludeMigrate` → `-migrate.zip` exists and
the psycopg2/mysql import gate passed.

### Step 5: Fresh-unzip end-to-end (manual gate)

Run ALL sub-steps from a dedicated empty scratch dir — create
`%TEMP%\mcp-e2e-client\` and `Set-Location` into it first. Registration is
project-local by default (writes `.\opencode.json` in the **current**
directory, `cli.py:811`), so the scratch dir isolates the entry and makes
removal unambiguous.

1. Unzip the base zip to `%TEMP%\mcp-portable-e2e\` (delete it first if present).
2. `& %TEMP%\mcp-portable-e2e\ms-access-mcp-*-portable-win64\register-opencode.ps1`
   → exit 0; inspect `%TEMP%\mcp-e2e-client\opencode.json`: the
   `mcp.ms-access` command argv must be
   `[<abs>\runtime\python.exe, "-m", "ms_access_mcp", "mcp", "--profile", "core"]`.
3. `opencode debug config` → exit 0.
4. Run g12 against the registered argv → exit 0.
5. From the SAME scratch dir, remove the entry
   (`& <abs>\runtime\python.exe -m ms_access_mcp configure opencode --python-mode --remove`),
   confirm `opencode.json` no longer contains `ms-access`, then delete both
   temp dirs.

**Verify**: all five sub-steps pass, in a shell that has NOT run `uv sync`
(proves the zip is self-contained).

## Test plan

- `tests/unit/test_main_module.py` — subprocess `--version` happy path (Step 1).
- `tests/unit/test_configure_python_mode.py` — module-mode argv shape, default
  zero-drift, `sys.executable` resolution (Step 2).
- No production logic beyond the above; the PS1 build is verified by its own
  gates (Steps 4–5), matching how `install.ps1` is qualified.
- **Verify**: `uv run pytest tests/unit -q` → all pass, including the new tests;
  `uv run ruff check .` exit 0; `uv run pyright` 0 errors.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `uv run pytest tests/unit -q` exits 0; `test_main_module.py` and
      `test_configure_python_mode.py` exist and pass
- [ ] `uv run ruff check .` exits 0; `uv run pyright` reports 0 errors
- [ ] `uv run python -m ms_access_mcp --version` exits 0
- [ ] `uv run ms-access-mcp configure opencode --help` shows `--python-mode`
- [ ] `scripts/build_portable.ps1` produces both zip variants with all step-6
      gates green (orchestrator re-runs both in-shell — do not trust a report)
- [ ] Step 5 fresh-unzip e2e passes (registered command is the runtime
      `python.exe` + `-m` argv)
- [ ] `git status` shows no files outside the in-scope list modified
- [ ] `plans/README.md` row 032 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The code at `cli.py:814-844`/`:974-995` or `opencode_config.py:147-154`
  doesn't match the "Current state" description (drift since `4aba764`).
- The embeddable zip cannot be downloaded, or its SHA256 mismatches the
  pinned constant `009d6bf7…fd3b`.
- `import win32com.client` fails inside the embeddable runtime after the
  `._pth` patch, twice, after confirming `import site` is present.
- `uv export --extra migrate` does not include `psycopg2-binary` and
  `mysql-connector-python` in the exported requirements.
- A step's verification fails twice after a reasonable fix attempt.
- The fix appears to require touching an out-of-scope file
  (`pyproject.toml`/`uv.lock`/`install.ps1`).

## Maintenance notes

- The embeddable pin is terminal for the 3.11 series: per PEP 664, 3.11.9 is
  the LAST 3.11 release with binary artifacts (3.11.10+ are source-only; a
  `3.11.16-embed-amd64.zip` does not exist — verified 404). Bumping the
  runtime means jumping Python series (3.12/3.13 embeddable + matching
  wheels) — its own plan; do not attempt it inside this one.
- The zip intentionally contains no unsigned packed executables — that is the
  AV-safety property; do not add launchers/shim exes later without re-opening
  the Defender question. The registered command must stay the PSF-signed
  `python.exe`.
- If `configure opencode` later gains transport/profile options, extend
  `module_mode` argv in the same place (`opencode_config.entry_from`).
- 64-bit MS Access / ACE remains a machine requirement; the zip cannot and
  must not bundle it — `doctor --json` is the operator-facing check.
- Deferred (not this plan): winget/Scoop publishing of the zip, CI build
  workflow, code signing, auto-update, `xlsx` extra in zip.
