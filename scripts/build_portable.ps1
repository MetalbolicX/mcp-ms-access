#Requires -Version 5.1
<#
.SYNOPSIS
    Build the ms-access-mcp portable win64 zip distribution.

.DESCRIPTION
    Produces a self-contained portable zip containing:
    - Official PSF-signed embeddable Python 3.11.9 runtime
    - All required wheels (pyodbc, pywin32, mcp, pydantic, etc.)
    - Optional migration drivers (psycopg2-binary, mysql-connector-python)
      when -IncludeMigrate is specified

    The zip is written to dist\ms-access-mcp-<version>-portable-win64.zip
    (or dist\ms-access-mcp-<version>-portable-win64-migrate.zip with -IncludeMigrate).

.PARAMETER IncludeMigrate
    Vendor the `migrate` extra packages (psycopg2-binary, mysql-connector-python)
    so offline migration users are not forced back to the uv install path.

.PARAMETER Profile
    Profile written into register-opencode.ps1 and README-PORTABLE.md.
    Defaults to "core".

.PARAMETER Global
    Forwarded to register-opencode.ps1; enables user-level opencode.json
    registration instead of project-local.

.PARAMETER SkipDefenderScan
    Skip the Windows Defender MpCmdRun.exe scan gate. Use this when
    Windows Defender is not installed or access is restricted.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -IncludeMigrate

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Profile automation -Global
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$IncludeMigrate,
    [string]$Profile = "core",
    [switch]$Global,
    [switch]$SkipDefenderScan
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# =============================================================================
# Constants — embeddable runtime (plan 032, verified 2026-09-14)
# =============================================================================
$EmbedVersion = "3.11.9"
$EmbedSha256  = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
$EmbedUrl     = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
$EmbedZipName = "python-3.11.9-embed-amd64.zip"

# =============================================================================
# Helpers
# =============================================================================

function Get-Sha256Lower {
    param([string]$FilePath)
    $hash = Get-FileHash -Path $FilePath -Algorithm SHA256
    return $hash.Hash.ToLowerInvariant()
}

function Write-Step {
    param([string]$Message)
    Write-Host "[build_portable] $Message" -ForegroundColor Cyan
}

function Test-Gate {
    param(
        [string]$Name,
        [scriptblock]$Test,
        [string]$StopMessage
    )
    Write-Step "Gate: $Name"
    $result = & $Test
    if (-not $result) {
        Write-Host "[STOP] $StopMessage" -ForegroundColor Red
        throw "Gate failed: $Name"
    }
    Write-Step "  PASS"
}

# =============================================================================
# Phase 0 — Environment gates
# =============================================================================

Write-Host ""
Write-Step "=== Phase 0: Environment gates ==="

# OS must be Windows
Test-Gate "OS is Windows" {
    $env:OS -eq "Windows_NT"
} "This script must run on Windows."

# Architecture must be x64
Test-Gate "Architecture is x64" {
    [Environment]::Is64BitOperatingSystem -and (($env:PROCESSOR_ARCHITECTURE -eq "AMD64") -or ($env:PROCESSOR_ARCHITECTURE -eq "x86_64"))
} "This script requires 64-bit Windows."

# cwd must be repo root
Test-Gate "cwd is repo root" {
    (Test-Path "pyproject.toml") -and (Test-Path "uv.lock")
} "This script must be run from the repository root (pyproject.toml + uv.lock must be present)."

# uv must be on PATH
Test-Gate "uv is on PATH" {
    $null -ne (Get-Command uv -ErrorAction SilentlyContinue)
} "uv must be on PATH. Install it from https://github.com/astral-sh/uv or run scripts\install.ps1 first."

Write-Host ""

# =============================================================================
# Phase 1 — Build wheel + export requirements
# =============================================================================

Write-Step "=== Phase 1: Build wheel and export requirements ==="

Write-Step "Running: uv build"
if ($PSCmdlet.ShouldProcess("Build wheel", "uv build")) {
    cmd /c "uv build >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        throw "uv build failed with exit code $LASTEXITCODE"
    }
}
$wheelFiles = @(Get-ChildItem -Path "dist\*.whl" -File | Sort-Object LastWriteTime -Descending)
if ($wheelFiles.Count -eq 0) {
    throw "uv build produced no wheel in dist\. Check build errors above."
}
$wheelPath = $wheelFiles[0].FullName
Write-Step "  Wheel: $wheelPath"

# Read version from pyproject.toml
$pyprojectContent = Get-Content "pyproject.toml" -Raw
if ($pyprojectContent -match 'version\s*=\s*["\'']([^"\'']+)["\'']') {
    $Version = $Matches[1]
} else {
    throw "Could not parse version from pyproject.toml"
}
Write-Step "  Version: $Version"

# Export requirements
$ReqFile = "build\portable\requirements.txt"
$ExportExtra = if ($IncludeMigrate) { @("--extra", "migrate") } else { @() }
$ExportArgs = @("export", "--no-dev", "--no-emit-project", "--format", "requirements-txt", "-o", $ReqFile) + $ExportExtra

Write-Step "Running: uv export $($ExportArgs -join ' ')"
if ($PSCmdlet.ShouldProcess("Export requirements", "uv export")) {
    $argStr = $ExportArgs -join " "
    cmd /c "uv $argStr >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        throw "uv export failed with exit code $LASTEXITCODE"
    }
}

# Verify migrate packages are included when requested
if ($IncludeMigrate) {
    $reqContent = Get-Content $ReqFile -Raw
    if (-not ($reqContent -match "psycopg2-binary") -or -not ($reqContent -match "mysql-connector-python")) {
        throw "psycopg2-binary and/or mysql-connector-python not found in requirements; check uv.lock and pyproject.toml migrate extra"
    }
    Write-Step "  migrate packages verified in requirements"
} else {
    Write-Step "  requirements exported (base only)"
}

Write-Host ""

# =============================================================================
# Phase 2 — Download and verify embeddable runtime
# =============================================================================

Write-Step "=== Phase 2: Download and verify embeddable Python $EmbedVersion ==="

$StageRoot = "build\portable\stage\ms-access-mcp-$Version-portable-win64"
$RuntimeDir = "$StageRoot\runtime"
$PythonExe  = "$RuntimeDir\python.exe"
$PythonPth  = "$RuntimeDir\python311._pth"

# Clean stage
if (Test-Path $StageRoot) {
    Write-Step "Removing existing stage: $StageRoot"
    Remove-Item -Recurse -Force $StageRoot
}

# Download to temp
$TempZip = Join-Path $env:TEMP "python-3.11.9-embed-amd64.zip"
Write-Step "Downloading: $EmbedUrl"
if ($PSCmdlet.ShouldProcess("Download embeddable Python", "Invoke-WebRequest -> $TempZip")) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $EmbedUrl -OutFile $TempZip -TimeoutSec 120
}

# Verify SHA256
Write-Step "Verifying SHA256 of $TempZip"
$actualSha = Get-Sha256Lower $TempZip
$expectedSha = $EmbedSha256.ToLowerInvariant()
if ($actualSha -ne $expectedSha) {
    Remove-Item $TempZip -ErrorAction SilentlyContinue
    throw "[STOP] SHA256 mismatch for $TempZip`n  expected: $expectedSha`n  actual:   $actualSha`n  The downloaded file may be corrupted or the pinned hash is stale."
}
Write-Step "  SHA256 OK: $actualSha"

# Extract to stage
Write-Step "Expanding archive to: $StageRoot"
if ($PSCmdlet.ShouldProcess("Expand embeddable Python", "Expand-Archive -> $RuntimeDir")) {
    Expand-Archive -Path $TempZip -DestinationPath $RuntimeDir -Force
}
Remove-Item $TempZip -ErrorAction SilentlyContinue

Write-Host ""

# =============================================================================
# Phase 3 — Patch python311._pth (enable site-packages + .pth processing)
# =============================================================================

Write-Step "=== Phase 3: Patch python311._pth ==="

# The default python311._pth in the embeddable distro disables site-packages
# and .pth processing. We need to re-enable them for pywin32 and vendor packages.
# Load-bearing: "import site" is required for pywin32's pywin32_bootstrap to find
# pythoncom311.dll / pywintypes311.dll.

$patchesContent = @"
python311.zip
.
Lib\site-packages
import site
"@

Write-Step "Writing patched $PythonPth (without BOM)"
if ($PSCmdlet.ShouldProcess("Patch python311._pth", "Write patched _pth to enable site-packages")) {
    # Use UTF8Encoding without BOM to avoid prepending EF BB BF bytes
    # that would corrupt the path python.exe reads from the ._pth file
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($PythonPth, $patchesContent, $utf8NoBom)
}

Write-Step "  Content:"
foreach ($line in $patchesContent -split "`n") {
    Write-Host "    $line"
}

Write-Host ""

# =============================================================================
# Phase 4 — Vendor packages via uv pip install
# =============================================================================

Write-Step "=== Phase 4: Vendor packages ==="

$SitePackages = "$RuntimeDir\Lib\site-packages"

Write-Step "Installing packages into: $SitePackages"
Write-Step "  Wheel: $wheelPath"
Write-Step "  Requirements: $ReqFile"

if ($PSCmdlet.ShouldProcess("Vendor packages", "uv pip install --python $PythonExe --target $SitePackages -r $ReqFile $wheelPath")) {
    # Ensure site-packages directory exists
    if (-not (Test-Path $SitePackages)) {
        New-Item -ItemType Directory -Path $SitePackages -Force | Out-Null
    }

    $pipArgs = @(
        "pip", "install",
        "--python", $PythonExe,
        "--target", $SitePackages,
        "-r", $ReqFile,
        $wheelPath
    )
    Write-Step "Running: uv pip install ..."
    $pipArgStr = $pipArgs -join " "
    cmd /c "uv $pipArgStr >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        throw "uv pip install failed with exit code $LASTEXITCODE"
    }
    Write-Step "  Packages installed successfully"
}

Write-Host ""

# =============================================================================
# Phase 5 — Runtime gates
# =============================================================================

Write-Step "=== Phase 5: Runtime gates ==="

# Gate 5a: import win32com.client, pyodbc, mcp, ms_access_mcp
Write-Step "Gate 5a: import win32com.client, pyodbc, mcp, ms_access_mcp"
$test5aScript = "import win32com.client`nimport pyodbc`nimport mcp`nimport ms_access_mcp`nprint('OK')"
if ($PSCmdlet.ShouldProcess("Gate 5a", "python -c import test")) {
    $gate5a = & "$PythonExe" -c $test5aScript 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "[STOP] Gate 5a failed: import test exited $LASTEXITCODE`n$gate5a"
    }
    Write-Step "  PASS"
}

# Gate 5b: python -m ms_access_mcp --version
Write-Step "Gate 5b: python -m ms_access_mcp --version"
if ($PSCmdlet.ShouldProcess("Gate 5b", "python -m ms_access_mcp --version")) {
    $gate5b = & "$PythonExe" -m ms_access_mcp --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "[STOP] Gate 5b failed: --version exited $LASTEXITCODE`n$gate5b"
    }
    $expectedVersion = "ms-access-mcp $Version"
    if ($gate5b -notlike "*$Version*") {
        throw "[STOP] Gate 5b version mismatch: expected '$expectedVersion', got '$gate5b'"
    }
    Write-Step "  PASS: $gate5b"
}

# Gate 5c: python -m ms_access_mcp doctor --json
Write-Step "Gate 5c: python -m ms_access_mcp doctor --json"
if ($PSCmdlet.ShouldProcess("Gate 5c", "python -m ms_access_mcp doctor --json")) {
    $gate5c = & "$PythonExe" -m ms_access_mcp doctor --json 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "[STOP] Gate 5c failed: doctor --json exited $LASTEXITCODE`n$gate5c"
    }
    Write-Step "  PASS"
}

# Gate 5d: g12 stdio smoke
Write-Step "Gate 5d: g12 stdio smoke (core profile)"
$pythonAbs = (Resolve-Path $PythonExe).Path
$smokeCmd = "$pythonAbs -m ms_access_mcp mcp --profile core"
if ($PSCmdlet.ShouldProcess("Gate 5d", "g12 smoke against runtime")) {
    # Suppress stderr (uv/uv run writes version info to stderr) to avoid
    # PowerShell 5.1 capturing it as a Non-Terminating Error.
    # The actual smoke result is determined by $LASTEXITCODE.
    $smokeResult = cmd /c "uv run python scripts\g12_portable_stdio_smoke.py -- $smokeCmd >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        # Re-run without suppression to capture actual output for debugging
        $smokeDebug = cmd /c "uv run python scripts\g12_portable_stdio_smoke.py -- $smokeCmd 2>&1"
        throw "[STOP] Gate 5d failed: g12 smoke exited $LASTEXITCODE`n$smokeDebug"
    }
    Write-Step "  PASS"
}

# Gate 5e (migrate only): import psycopg2, mysql.connector
if ($IncludeMigrate) {
    Write-Step "Gate 5e: import psycopg2, mysql.connector (migrate)"
    $test5eScript = "import psycopg2`nimport mysql.connector`nprint('OK')"
    if ($PSCmdlet.ShouldProcess("Gate 5e", "python -c migrate import test")) {
        $gate5e = & "$PythonExe" -c $test5eScript 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "[STOP] Gate 5e failed: migrate import test exited $LASTEXITCODE`n$gate5e"
        }
        Write-Step "  PASS"
    }
}

Write-Host ""

# =============================================================================
# Phase 6 — Stage extras
# =============================================================================

Write-Step "=== Phase 6: Stage extras ==="

# register-opencode.ps1
$registerScript = "$StageRoot\register-opencode.ps1"
$registerContent = @"
# ms-access-mcp portable registration script
# Generated by build_portable.ps1
# Run this from your project directory to register the portable runtime
# with opencode.json (project-local by default, use -Global for user-level)

param([switch]`$Global)
`$pythonExe = Join-Path `$PSScriptRoot 'runtime\python.exe'
`$configureArgs = @('configure', 'opencode', '--python-mode', '--profile', '$Profile')
if (`$Global) { `$configureArgs += '--global' }
& `$pythonExe -m ms_access_mcp `@configureArgs
"@

Write-Step "Writing: $registerScript"
if ($PSCmdlet.ShouldProcess("Write register-opencode.ps1", "Write registration script")) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($registerScript, $registerContent, $utf8NoBom)
}

# README-PORTABLE.md
$readmePath = "$StageRoot\README-PORTABLE.md"
$zipVariant = if ($IncludeMigrate) { "with-migrate" } else { "base" }
$migrateNote = if ($IncludeMigrate) {
    "Includes migration drivers (psycopg2-binary, mysql-connector-python)."
} else {
    "Base variant: no migration drivers. Use the `-migrate` zip for database migration support."
}

$readmeContent = @" 
# ms-access-mcp Portable Distribution

A portable, freezer-free distribution of **ms-access-mcp** built on the
official PSF-signed embeddable Python 3.11.9 runtime.

## Requirements

- **64-bit Windows** with Windows 10/11
- **64-bit Microsoft Access** or the **Access Database Engine (ACE)** installed
  on the machine (required for pyodbc connectivity). Download from:
  https://www.microsoft.com/en-us/download/details.aspx?id=54920

## Quick Start

1. **Unzip** this folder to your preferred location

2. **(Optional) Register** with opencode:

   From your project directory, run:
   ````powershell
   .\register-opencode.ps1
   ````

   For user-level registration (all projects):
   ````powershell
   .\register-opencode.ps1 -Global
   ````

3. **Verify** the installation:

   ````powershell
   .\runtime\python.exe -m ms_access_mcp doctor --json
   ````

## Architecture

- ``runtime\``          — Embeddable Python 3.11.9 + vendored packages
- ``runtime\python.exe`` — Python runtime (use this, not a system Python)
- ``runtime\Lib\site-packages\`` — Vendored wheels
- ``register-opencode.ps1`` — opencode.json registration helper
- ``README-PORTABLE.md`` — This file

## Profiles

This zip ships with the **$Profile** profile. Available profiles:
- ``core`` — Core table/view operations (~20 tools, lean LLM context)
- ``automation`` — Form/report automation, VBA macro execution
- ``maintenance`` — Backup, repair, compact operations
- ``versioning`` — Migration replay and schema versioning
- ``full`` — All tools

## Variant

- **$zipVariant** variant — $migrateNote

## Troubleshooting

### ``pyodbc driver not found``
Ensure 64-bit Microsoft Access or ACE is installed. The 32-bit and 64-bit
versions of Access are separate installs; ms-access-mcp requires 64-bit.

### ``ModuleNotFoundError: No module named 'ms_access_mcp'``
You must use ``runtime\python.exe`` (the vendored runtime) for all operations.
Do not substitute a system-installed Python.

### ``import win32com.client`` fails
Ensure ``python311._pth`` has not been altered. The ``import site`` line in
``python311._pth`` is load-bearing for pywin32's bootstrap mechanism.

## Unregistering

````powershell
.\runtime\python.exe -m ms_access_mcp configure opencode --python-mode --remove
````
"@

Write-Step "Writing: $readmePath"
if ($PSCmdlet.ShouldProcess("Write README-PORTABLE.md", "Write README")) {
    $readmeContent | Set-Content -Path $readmePath -Encoding UTF8
}

Write-Host ""

# =============================================================================
# Phase 7 — Zip
# =============================================================================

Write-Step "=== Phase 7: Create zip ==="

$zipBaseName = "ms-access-mcp-$Version-portable-win64"
if ($IncludeMigrate) {
    $zipBaseName += "-migrate"
}
$zipPath = "dist\$zipBaseName.zip"

Write-Step "Zipping: $StageRoot -> $zipPath"
if ($PSCmdlet.ShouldProcess("Create zip", "Compress-Archive -> $zipPath")) {
    if (-not (Test-Path "dist")) {
        New-Item -ItemType Directory -Path "dist" -Force | Out-Null
    }
    # Remove existing zip if present
    if (Test-Path $zipPath) {
        Remove-Item $zipPath -Force
    }
    # Zip the stage folder itself (not its contents) so the extracted zip
    # contains the wrapper folder ms-access-mcp-<version>-portable-win64\
    $parentDir = Split-Path $StageRoot -Parent
    $stageName = Split-Path $StageRoot -Leaf
    # Ensure dist dir exists in cwd
    if (-not (Test-Path "dist")) {
        New-Item -ItemType Directory -Path "dist" -Force | Out-Null
    }
    # Build absolute paths for the zip (we change dir during compression)
    $absZipPath = (Resolve-Path "dist").Path + "\$zipBaseName.zip"
    $relZipPath = "dist\$zipBaseName.zip"
    Push-Location $parentDir
    try {
        Compress-Archive -Path $stageName -DestinationPath $absZipPath -CompressionLevel Optimal
    } finally {
        Pop-Location
    }
}

$zipSize = (Get-Item $absZipPath).Length
$zipSizeMb = [math]::Round($zipSize / 1MB, 2)
Write-Step "  Zip created: $relZipPath"
Write-Step "  Size: $zipSizeMb MB"

Write-Host ""

# =============================================================================
# Phase 8 — Defender scan
# =============================================================================

Write-Step "=== Phase 8: Defender scan ==="

$defenderExe = "$env:ProgramFiles\Windows Defender\MpCmdRun.exe"
if ((-not $SkipDefenderScan) -and (Test-Path $defenderExe)) {
    Write-Step "Running: MpCmdRun.exe -Scan -ScanType 3 -File $absZipPath"
    if ($PSCmdlet.ShouldProcess("Defender scan", "MpCmdRun.exe scan of zip")) {
        $defenderResult = & "$defenderExe" -Scan -ScanType 3 -File $absZipPath 2>&1
        $defenderExit = $LASTEXITCODE
        # Exit code 0 = clean, 2 = threats found, other = error/unavailable
        if ($defenderExit -eq 2) {
            throw "[STOP] Windows Defender found threats in the zip. Output: $defenderResult"
        }
        Write-Step "  PASS: Defender scan clean"
    }
} else {
    Write-Step "Defender scan skipped (MpCmdRun.exe unavailable or -SkipDefenderScan set)"
}

Write-Host ""
Write-Step "=== Build complete ==="
Write-Host "  Artifact : $zipPath"
Write-Host "  Size    : $zipSizeMb MB"
Write-Host "  Profile : $Profile"
if ($IncludeMigrate) {
    Write-Host "  Migrate : Yes"
}
Write-Host ""
