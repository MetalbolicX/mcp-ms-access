# Plan 055: First-class legacy .mdb (Jet 3 / Jet 4) support

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MEDIUM (new backend paths; format conversion lives out-of-proc)
- **Depends on**: none (self-contained)
- **Category**: feature
- **Planned at**: 2026-09-19

## Why this matters

The MCP server is ACE-only today. Every code path that touches a `.mdb`
file (Access 95–2003) either fails cryptically or is unreachable:

- `doctor.py` hard-requires x64 — ACE OLEDB and the x64 ODBC driver are
  fine, but Jet 4.0 is 32-bit-only.
- `backends/odbc.py:45` hard-codes `'Microsoft Access Driver (*.mdb, *.accdb)'`
  — the same driver handles both, but Jet 3 (Access 95/97) cannot be opened
  by ACE at all.
- `backends/ado.py:18` uses `'Microsoft.ACE.OLEDB.12.0'` — ACE cannot open
  Jet 3 files; the error is an opaque COM exception, not a clear诊断.
- `backends/dispatcher.py:26-28` hardcodes `DEFAULT_DB_VERSION=128`
  (`dbVersion120` / `.accdb`) on creation.
- `application/maintenance.py:545-566` limits crash-recovery to `.accdb`
  only — `.mdb` files are invisible to the safety net.
- No `.mdb` fixture exists in the test suite.

Adding a format sniffer and a Jet-3 bridge unlocks the full Access file
family without breaking any ACE/`.accdb` path.

## Scope

### Phase 1 — Jet 4 / .mdb Access 2000–2003 first-class support

**In scope**:

- Format sniffing: read the first 32 bytes of any `.mdb` / `.accdb` file;
  classify as `JET3`, `JET4`, `ACE`, or `UNKNOWN` before any backend
  construction. Extension alone is not trusted — ACE refuses Jet 3 with a
  cryptic COM exception if you point it at the wrong file.
- Create `.mdb` via DAO using `dbVersion40` (= 64) so new files are
  Jet 4 / Access 2000–2003 format (compatible with the ODBC driver on both
  x64 and x86).
- Extend crash-recovery in `application/maintenance.py` to handle `.mdb`
  files alongside `.accdb`.
- `doctor.py` format matrix: enumerate `pyodbc.drivers()` and probe the
  registry for both ACE and Jet 4.0 entries under
  `HKLM\SOFTWARE\Microsoft\Office\<version>\Access Connectivity Engine`.
- Test-time `.mdb` fixtures: at least one DAO-created Jet 4 `.mdb` in
  `tests/fixtures/` committed to the repo so CI can exercise the path
  without a running Access installation.
- Clear Jet 3 errors: all error messages that mention Jet/ACE internals
  gain a human-readable "Access format could not be determined" fallback
  before propagating the raw COM/ODBC exception.

**Out of scope for Phase 1**:

- Jet 3 / Access 95–97 support (see Phase 2).
- `.mdw` workgroup security files.
- 32-bit Office machines (Jet 4.0 is 32-bit only; ACE works fine).

### Phase 2 — Jet 3.x / Access 95–97 bridge

**Problem**: ACE OLEDB and the ACE ODBC driver cannot open Jet 3.x files
(Access 95/97). Jet 4.0 itself is 32-bit-only.

**Solution**: out-of-process conversion via
`C:\Windows\SysWOW64\cscript.exe` running a JRO `JetEngine.CompactDatabase`
script:

```
Source: Provider=Microsoft.Jet.OLEDB.4.0;Data Source=<input.mdb>
Dest:  Jet OLEDB:Engine Type=5;Provider=Microsoft.Jet.OLEDB.4.0;Data Source=<output.mdb>
```

`Engine Type=5` produces a Jet 4 sibling copy that ACE can open. The
original Jet 3 file is preserved. This is a one-way bridge (Jet 3 → Jet 4).

**`doctor.py` WOW6432Node probe**: on 64-bit Windows, the 32-bit Jet 4.0
registry keys live under
`HKLM\SOFTWARE\WOW6432Node\Microsoft\Office\<version>\Access Connectivity Engine`.
The driver probe must check both the native and WOW6432Node paths.

**Out of scope for Phase 2**:

- Access 1.x / 2.x support.
- `.mdw` workgroup security (linking, password-changing, etc.).
- 32-bit Office machines (no ACE available in-process; external conversion
  script still works but requires 32-bit Jet).

## Commands you will need

| Purpose             | Command                                              | Expected on success      |
|---------------------|------------------------------------------------------|--------------------------|
| Lint                | `uv run ruff check src/ms_access_mcp/backends/format_detector.py` | exit 0                   |
| Typecheck           | `uv run pyright src/ms_access_mcp/backends/format_detector.py`   | 0 errors                 |
| Unit tests          | `uv run pytest tests/unit/test_format_detector.py -q`            | all pass                 |

## Steps — Phase 1

### Step 1: Create `src/ms_access_mcp/backends/format_detector.py`

Sniff the first 32 bytes:

- Bytes 0–3 = `00 01 00 00` → standard Jet/ACE family.
- Magic ASCII string at offset 4:
  - `'Standard Jet DB'` → Jet family.
  - `'Standard ACE DB'` → ACE family.
- For Jet family, version byte at offset `0x14`:
  - `0x00` → `JET3` (Access 95/97).
  - `0x01` → `JET4` (Access 2000–2003).
- Any other header (bad magic, encrypted, scrambled) → `UNKNOWN`.
- Files < 32 bytes or nonexistent → `UNKNOWN` (do not raise).

Public API:

```python
from enum import Enum
from pathlib import Path

class AccessFormat(Enum):
    JET3 = "jet3"
    JET4 = "jet4"
    ACE = "ace"
    UNKNOWN = "unknown"

def detect_access_format(path: Path) -> AccessFormat:
    ...
```

### Step 2: Create `tests/unit/test_format_detector.py`

Pure unit tests using `tmp_path` fixtures. One builder helper:

```python
def header(magic: bytes, version_byte: int) -> bytes:
    """Build a 32-byte Jet/ACE header blob.

    magic: ASCII string for offset 4 (e.g. b'Standard Jet DB').
    version_byte: int written at offset 0x14 (0 for Jet3, 1 for Jet4).
    """
```

Test cases:

| Header bytes              | Expected enum |
|---------------------------|---------------|
| `00 01 00 00` + Jet magic + `0x00` at 0x14 | `JET3`        |
| `00 01 00 00` + Jet magic + `0x01` at 0x14 | `JET4`        |
| `00 01 00 00` + ACE magic + any at 0x14     | `ACE`         |
| Garbage bytes (no magic match)              | `UNKNOWN`     |
| Truncated file (< 32 bytes)                  | `UNKNOWN`     |
| Empty file                                    | `UNKNOWN`     |
| Nonexistent path                             | `UNKNOWN`     |

### Step 3: Verify Phase 1

```powershell
uv run pytest tests/unit/test_format_detector.py -q
uv run ruff check src/ms_access_mcp/backends/format_detector.py
```

## Steps — Phase 2

### Step 4: JRO conversion helper

Create `src/ms_access_mcp/backends/_jet3_converter.py` (private module,
not part of the public adapter API):

- Detect `JET3` via `detect_access_format`.
- Build a JRO `JetEngine` cscript script tempfile.
- Execute via `C:\Windows\SysWOW64\cscript.exe //NoLogo //E:JScript <script>`.
- Target path: sibling file with `.mdb` extension + `.jet4` intermediate
  suffix (e.g. `Northwind.mdb.jet4`) renamed to `Northwind.mdb` on success.
- Clean up the intermediate on failure.
- Return `True` on success, `False` on any error (log detail; do not raise
  in the helper — callers decide).

### Step 5: Integrate into dispatcher

`backends/dispatcher.py`: before constructing any adapter, call
`detect_access_format`. If `JET3` is detected and the requested backend is
`auto` or `odbc`, raise a clear `Jet3ConversionNeeded` error with the
conversion helper's path so callers can opt-in to conversion.

### Step 6: `doctor.py` WOW64 probe

In the driver-enumeration section, check
`HKLM\SOFTWARE\WOW6432Node\Microsoft\Office\<version>\Access Connectivity Engine`
entries in addition to the native path. Report Jet 4.0 availability in the
format matrix table.

## Test plan

- `test_format_detector.py` covers the sniffer directly (Step 2).
- `test_dispatcher.py` or equivalent: add a case that passes a `JET3`
  header file and asserts `Jet3ConversionNeeded` is raised.
- Integration test for the JRO converter (Step 4) uses a real but small
  Jet 3 fixture if available; skip if JRO is not registered on the machine.
- No new tests for `doctor.py` changes — manual verification via
  `uv run python -m ms_access_mcp.doctor` is sufficient.

## Done criteria

- [ ] `tests/unit/test_format_detector.py` — all pass
- [ ] `ruff check src/ms_access_mcp/backends/format_detector.py` — exit 0
- [ ] `pyright src/ms_access_mcp/backends/format_detector.py` — 0 errors
- [ ] `plans/055-legacy-mdb-support.md` exists and follows plan style
- [ ] Phase 2 conversion helper exists at
  `src/ms_access_mcp/backends/_jet3_converter.py`
- [ ] `backends/dispatcher.py` raises `Jet3ConversionNeeded` for `JET3`
  inputs on `auto`/`odbc` backends
- [ ] `doctor.py` probes WOW6432Node for Jet 4.0 driver entries

## STOP conditions

Stop and report back (do not improvise) if:

- The sniffer misclassifies any known `.accdb` file as non-`ACE`
  (regression — ACE headers are well-documented; verify against a real
  `.accdb` fixture first).
- JRO `cscript.exe` produces a nonzero exit code on a valid Jet 3 input
  (environment issue — do not silently skip; surface the error).
- Any new adapter path raises an exception that the existing test suite
  does not cover — add the case before proceeding.

## Maintenance notes

- Phase 1 is self-contained and safe to land independently of Phase 2.
- Phase 2 depends on `pywin32` being importable for the cscript approach;
  if win32com is unavailable the helper should gracefully degrade (log a
  warning and return `False`).
- Jet 4.0 itself is 32-bit only — on 64-bit Python the `dbVersion40`
  creation path via DAO is the only reliable way to produce a new `.mdb`.
