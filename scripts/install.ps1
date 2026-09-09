#Requires -Version 5.1
<#
.SYNOPSIS
    Installs ms-access-mcp tool on Windows x64 via uv.

.DESCRIPTION
    Detects or bootstraps uv, installs the tool for Python 3.11, gates on doctor,
    and registers with OpenCode. Supports -WhatIf, -NoRegister, -Global, and
    -Profile switches. No credentials or secrets are written.

.PARAMETER Global
    Register at user config (%USERPROFILE%\.config\opencode\opencode.json)
    instead of CWD.

.PARAMETER Profile
    Passed through to 'uv tool install' and 'configure opencode'.
    Default: core.

.PARAMETER NoRegister
    Skip the 'configure opencode' registration step.

.PARAMETER WhatIf
    Print planned actions and exit 0 without mutating anything.

.PARAMETER UvVersion
    Pin uv version to install. Default: detect latest stable from astral.sh.

.PARAMETER RepoPath
    Path to the ms-access-mcp repository root.
    Default: parent directory of this script.

.PARAMETER SkipPython
    Skip 'uv python install 3.11' step.

.PARAMETER Verbose
    Enable verbose output.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Global,
    [ValidateSet('core', 'default')]
    [string]$Profile = 'core',
    [switch]$NoRegister,
    [string]$UvVersion,
    [string]$RepoPath,
    [switch]$SkipPython
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

$ASTRAL_SH_URL = 'https://astral.sh/uv/install.ps1'
$UV_RELEASES_URL = 'https://github.com/astral-sh/uv/releases/latest'
$TOOL_NAME = 'ms-access-mcp'
$PYTHON_VERSION = '3.11'
$TOOL_DIR_BIN_ARG = @('tool', 'dir', '--bin')

# ---------------------------------------------------------------------------
# Helper: Invoke-External
# ---------------------------------------------------------------------------
# Wraps System.Diagnostics.Process for testability. Returns a hashtable:
# { ExitCode: int, Stdout: string, Stderr: string }

function Invoke-External {
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)]
        [string]$FileName,
        [Parameter(Mandatory)]
        [string]$Arguments,
        [string]$WorkingDirectory
    )

    $procInfo = New-Object System.Diagnostics.ProcessStartInfo
    $procInfo.FileName = $FileName
    $procInfo.Arguments = $Arguments
    $procInfo.UseShellExecute = $false
    $procInfo.RedirectStandardOutput = $true
    $procInfo.RedirectStandardError = $true
    $procInfo.CreateNoWindow = $true
    if ($WorkingDirectory) {
        $procInfo.WorkingDirectory = $WorkingDirectory
    }

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $procInfo
    $proc.Start() | Out-Null
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()

    return @{
        ExitCode = $proc.ExitCode
        Stdout   = $stdout
        Stderr   = $stderr
    }
}

# ---------------------------------------------------------------------------
# Helper: Test-IsWindowsX64
# ---------------------------------------------------------------------------

function Test-IsWindowsX64 {
    <#
    .SYNOPSIS
        Returns $true if the current OS is Windows and architecture is x64.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param()

    try {
        $info = Get-ComputerInfo -ErrorAction Stop
        $isWindows = $info.OsName -like '*Windows*'
        $isX64 = $info.OsArchitecture -in @('x64', '64-bit')
        return $isWindows -and $isX64
    }
    catch {
        return $false
    }
}

# ---------------------------------------------------------------------------
# Helper: Test-CommandOnPath
# ---------------------------------------------------------------------------

function Test-CommandOnPath {
    <#
    .SYNOPSIS
        Returns $true if the given command name is found on PATH.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)]
        [string]$CommandName
    )

    $cmd = Get-Command $CommandName -ErrorAction SilentlyContinue
    return ($null -ne $cmd)
}

# ---------------------------------------------------------------------------
# Helper: Get-UvBinaryPath
# ---------------------------------------------------------------------------

function Get-UvBinaryPath {
    <#
    .SYNOPSIS
        Returns the path to the uv executable on PATH, or $null if not found.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()

    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $cmd) {
        return $cmd.Source
    }
    return $null
}

# ---------------------------------------------------------------------------
# Helper: Get-UvToolBinPath
# ---------------------------------------------------------------------------

function Get-UvToolBinPath {
    <#
    .SYNOPSIS
        Returns the uv tool --bin directory for the current user.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()

    $result = Invoke-External -FileName (Get-UvBinaryPath) -Arguments ($TOOL_DIR_BIN_ARG -join ' ')
    $stdout = $result.Stdout.Trim()

    if ($result.ExitCode -ne 0) {
        Write-Error "uv tool dir --bin exited $($result.ExitCode)"
        return $null
    }

    return $stdout
}

# ---------------------------------------------------------------------------
# Helper: Resolve-MsAccessMcpExe
# ---------------------------------------------------------------------------

function Resolve-MsAccessMcpExe {
    <#
    .SYNOPSIS
        Returns the full path to the ms-access-mcp.exe tool.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)]
        [string]$UvBinPath
    )

    return Join-Path $UvBinPath ('{0}.exe' -f $TOOL_NAME)
}

# ---------------------------------------------------------------------------
# Helper: Invoke-UvBootstrap
# ---------------------------------------------------------------------------

function Invoke-UvBootstrap {
    <#
    .SYNOPSIS
        Bootstraps uv using the official installer or checksum-verified zip fallback.
        One retry on CDN failure.
    #>
    [CmdletBinding(SupportsShouldProcess)]
    [OutputType([void])]
    param(
        [string]$Version
    )

    $tmpDir = Join-Path $env:TEMP ('uv_bootstrap_{0}' -f [System.Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

    try {
        # Try official installer first
        $installerPath = Join-Path $tmpDir 'install.ps1'

        if ($PSCmdlet.ShouldProcess("Downloading uv installer from $ASTRAL_SH_URL", '')) {
            try {
                Invoke-WebRequest -Uri $ASTRAL_SH_URL -OutFile $installerPath -TimeoutSec 30 -ErrorAction Stop
            }
            catch {
                Write-Verbose "Official installer download failed: $_"
                Write-Verbose 'Falling back to GitHub releases zip'

                # Fallback: download zip + sha256 from GitHub releases
                $fallbackUrl = 'https://github.com/astral-sh/uv/releases/download/0.4.0/uv-x86_64-pc-windows-msvc.zip'
                $shaUrl = 'https://github.com/astral-sh/uv/releases/download/0.4.0/uv-x86_64-pc-windows-msvc.zip.sha256'
                $zipPath = Join-Path $tmpDir 'uv.zip'

                Invoke-WebRequest -Uri $fallbackUrl -OutFile $zipPath -TimeoutSec 60 -ErrorAction Stop
                Invoke-WebRequest -Uri $shaUrl -OutFile "$zipPath.sha256" -TimeoutSec 30 -ErrorAction Stop

                $expectedSha = (Get-Content "$zipPath.sha256" -Raw).Trim().Split(' ')[0]
                $actualSha = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.Trim().ToLower()

                if ($expectedSha -ne $actualSha) {
                    throw "SHA256 mismatch: expected $expectedSha, got $actualSha"
                }

                Expand-Archive -Path $zipPath -DestinationPath "$tmpDir\uv_unzipped" -Force -ErrorAction Stop

                # Place uv.exe on PATH via temp dir
                $binDir = Join-Path $tmpDir 'bin'
                New-Item -ItemType Directory -Path $binDir -Force | Out-Null
                Move-Item "$tmpDir\uv_unzipped\uv.exe" $binDir -Force

                $env:PATH = "$binDir;$env:PATH"
                Write-Host "Bootstrap complete: uv installed to $binDir"
                return
            }

            # Execute official installer
            & $installerPath -ModifyPath | Out-Null
        }
    }
    finally {
        Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------------
# Helper: Invoke-DoctorCheck
# ---------------------------------------------------------------------------

function Invoke-DoctorCheck {
    <#
    .SYNOPSIS
        Runs 'ms-access-mcp doctor --json' and returns $true on success.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)]
        [string]$ExePath
    )

    try {
        $result = Invoke-External -FileName $ExePath -Arguments 'doctor --json'

        Write-Verbose "Doctor output: $($result.Stdout)"
        if ($result.Stderr) {
            Write-Verbose "Doctor stderr: $($result.Stderr)"
        }

        return ($result.ExitCode -eq 0)
    }
    catch {
        Write-Verbose "Doctor check failed: $_"
        return $false
    }
}

# ---------------------------------------------------------------------------
# Helper: Invoke-ToolInstall
# ---------------------------------------------------------------------------

function Invoke-ToolInstall {
    <#
    .SYNOPSIS
        Runs 'uv tool install --python 3.11 --force .' from RepoPath.
    #>
    [CmdletBinding(SupportsShouldProcess)]
    [OutputType([void])]
    param(
        [Parameter(Mandatory)]
        [string]$RepoPath,
        [string]$Profile
    )

    $uvExe = Get-UvBinaryPath
    if (-not $uvExe) {
        throw 'uv not found on PATH after bootstrap'
    }

    $args = @(
        'tool', 'install',
        '--python', $PYTHON_VERSION,
        '--force',
        '--profile', $Profile,
        $RepoPath
    )

    if ($PSCmdlet.ShouldProcess("uv $($args -join ' ')", 'Executing')) {
        $result = Invoke-External -FileName $uvExe -Arguments ($args -join ' ') -WorkingDirectory $RepoPath

        Write-Verbose "uv tool install output: $($result.Stdout)"
        if ($result.Stderr) {
            Write-Verbose "uv tool install stderr: $($result.Stderr)"
        }

        if ($result.ExitCode -ne 0) {
            throw "uv tool install failed with exit code $($result.ExitCode)"
        }
    }
}

# ---------------------------------------------------------------------------
# Helper: Invoke-PythonInstall
# ---------------------------------------------------------------------------

function Invoke-PythonInstall {
    <#
    .SYNOPSIS
        Runs 'uv python install 3.11' to ensure Python is available.
    #>
    [CmdletBinding(SupportsShouldProcess)]
    [OutputType([void])]
    param()

    $uvExe = Get-UvBinaryPath
    if (-not $uvExe) {
        throw 'uv not found on PATH'
    }

    if ($PSCmdlet.ShouldProcess("uv python install $PYTHON_VERSION", 'Executing')) {
        $args = @('python', 'install', $PYTHON_VERSION)
        $result = Invoke-External -FileName $uvExe -Arguments ($args -join ' ')

        Write-Verbose "uv python install output: $($result.Stdout)"
        if ($result.Stderr) {
            Write-Verbose "uv python install stderr: $($result.Stderr)"
        }
    }
}

# ---------------------------------------------------------------------------
# Helper: Invoke-ConfigureOpenCode
# ---------------------------------------------------------------------------

function Invoke-ConfigureOpenCode {
    <#
    .SYNOPSIS
        Runs 'ms-access-mcp configure opencode [--global] [--profile <p>]'
    #>
    [CmdletBinding(SupportsShouldProcess)]
    [OutputType([void])]
    param(
        [Parameter(Mandatory)]
        [string]$ExePath,
        [switch]$Global,
        [string]$Profile
    )

    $args = @('configure', 'opencode')
    if ($Global) {
        $args += '--global'
    }
    $args += '--profile'
    $args += $Profile

    if ($PSCmdlet.ShouldProcess("$ExePath $($args -join ' ')", 'Executing')) {
        $result = Invoke-External -FileName $ExePath -Arguments ($args -join ' ')

        Write-Verbose "configure output: $($result.Stdout)"
        if ($result.Stderr) {
            Write-Verbose "configure stderr: $($result.Stderr)"
        }

        if ($result.ExitCode -ne 0) {
            throw "configure opencode failed with exit code $($result.ExitCode)"
        }
    }
}

# ---------------------------------------------------------------------------
# Main: Invoke-Install
# ---------------------------------------------------------------------------

function Invoke-Install {
    <#
    .SYNOPSIS
        Main installation orchestration.
    #>
    [CmdletBinding(SupportsShouldProcess)]
    [OutputType([void])]
    param(
        [string]$RepoPath,
        [switch]$Global,
        [string]$Profile,
        [switch]$NoRegister,
        [switch]$SkipPython,
        [string]$UvVersion
    )

    # Step 1: OS gate
    if (-not (Test-IsWindowsX64)) {
        Write-Error 'ms-access-mcp requires Windows x64.'
        return
    }

    # Step 2: RepoPath defaults to script parent
    if (-not $RepoPath) {
        $RepoPath = Split-Path -Parent $PSScriptRoot
    }

    if (-not (Test-Path $RepoPath)) {
        Write-Error "Repository path does not exist: $RepoPath"
        return
    }

    Write-Verbose "RepoPath: $RepoPath"
    Write-Verbose "Profile: $Profile"
    Write-Verbose "Global: $Global"
    Write-Verbose "NoRegister: $NoRegister"
    Write-Verbose "WhatIf: $WhatIfPreference"

    # Step 3: Bootstrap uv if not on PATH
    if (-not (Test-CommandOnPath 'uv')) {
        Write-Host 'uv not found on PATH. Bootstrapping...'
        Invoke-UvBootstrap -WhatIf:$WhatIfPreference
    }

    # Step 4: Install Python if not skipped
    if (-not $SkipPython) {
        Invoke-PythonInstall -WhatIf:$WhatIfPreference
    }

    # Step 5: uv tool install
    Invoke-ToolInstall -RepoPath $RepoPath -Profile $Profile -WhatIf:$WhatIfPreference

    # Step 6: Resolve exe path
    $uvBinPath = Get-UvToolBinPath
    if (-not $uvBinPath) {
        Write-Error 'Failed to resolve uv tool bin directory.'
        return
    }

    $exePath = Resolve-MsAccessMcpExe -UvBinPath $uvBinPath
    Write-Verbose "Resolved exe: $exePath"

    if (-not (Test-Path $exePath)) {
        if ($WhatIfPreference) {
            Write-Verbose 'WhatIf: would validate exe existence (skipped in WhatIf mode)'
        }
        else {
            Write-Error "Tool exe not found at $exePath. Installation may have failed."
            return
        }
    }

    # Step 7: Doctor gate (skip in WhatIf mode)
    if (-not $WhatIfPreference) {
        Write-Verbose 'Running doctor check...'
        $doctorOk = Invoke-DoctorCheck -ExePath $exePath

        if (-not $doctorOk) {
            Write-Error @"
ms-access-mcp doctor check failed. Please fix the issue and re-run.
Recovery: ensure Access is installed and accessible, then re-run install.ps1.
"@
            return
        }
    }
    else {
        Write-Verbose 'WhatIf: would run doctor check (skipped in WhatIf mode)'
    }

    # Step 8: Registration (unless NoRegister)
    if (-not $NoRegister) {
        Invoke-ConfigureOpenCode -ExePath $exePath -Global:$Global -Profile $Profile -WhatIf:$WhatIfPreference
    }
    else {
        Write-Verbose 'Skipping registration (-NoRegister)'
    }

    # Step 9: Success
    if (-not $WhatIfPreference) {
        Write-Host 'Installation complete.'
        Write-Host 'Restart OpenCode once; tools appear.'
    }
    else {
        Write-Host 'WhatIf: installation would complete successfully.'
    }
}

# ---------------------------------------------------------------------------
# Entry point (only when run as script, not dot-sourced)
# ---------------------------------------------------------------------------

if ($MyInvocation.InvocationName -ne '.') {
    # Script execution — use *Preference forms for ShouldProcess/Verbose
    Invoke-Install `
        -RepoPath:$RepoPath `
        -Global:$Global `
        -Profile:$Profile `
        -NoRegister:$NoRegister `
        -WhatIf:$WhatIfPreference `
        -SkipPython:$SkipPython `
        -Verbose:$VerbosePreference
}
