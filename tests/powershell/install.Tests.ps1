#Requires -Version 5.1
$ErrorActionPreference = 'Continue'
$RepoRoot = 'D:\code\python\mcp-ms-access'
$InstallerScript = Join-Path $RepoRoot 'scripts\install.ps1'

function New-TestDirectory {
    $tmp = [System.IO.Path]::GetTempPath()
    $name = 'mcp_install_test_' + [System.Guid]::NewGuid().ToString('N')
    $path = Join-Path $tmp $name
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    return $path
}

Describe 'install.ps1 helper functions' {

    BeforeAll {
        if (Test-Path $InstallerScript) {
            . $InstallerScript
        }
    }

    # ---------------------------------------------------------------------------
    # Test-IsWindowsX64
    # ---------------------------------------------------------------------------

    It 'Test-IsWindowsX64: returns true on Windows x64' {
        Mock Get-ComputerInfo {
            [PSCustomObject]@{ OsArchitecture = 'x64'; OsName = 'Microsoft Windows 10 Pro' }
        }
        $result = Test-IsWindowsX64
        $result | Should Be $true
    }

    It 'Test-IsWindowsX64: returns false on x86' {
        Mock Get-ComputerInfo {
            [PSCustomObject]@{ OsArchitecture = 'x86'; OsName = 'Microsoft Windows 10 Pro' }
        }
        $result = Test-IsWindowsX64
        $result | Should Be $false
    }

    It 'Test-IsWindowsX64: returns false on non-Windows' {
        Mock Get-ComputerInfo {
            [PSCustomObject]@{ OsArchitecture = 'x64'; OsName = 'Linux' }
        }
        $result = Test-IsWindowsX64
        $result | Should Be $false
    }

    # ---------------------------------------------------------------------------
    # Resolve-MsAccessMcpExe
    # ---------------------------------------------------------------------------

    It 'Resolve-MsAccessMcpExe: returns exe path joined to bin dir' {
        $bin = 'C:\tools\uv-tools\bin'
        $result = Resolve-MsAccessMcpExe -UvBinPath $bin
        $result | Should Be (Join-Path $bin 'ms-access-mcp.exe')
    }

    It 'Resolve-MsAccessMcpExe: result has .exe extension' {
        $bin = 'C:\tools\uv-tools\bin'
        $result = Resolve-MsAccessMcpExe -UvBinPath $bin
        $result.EndsWith('.exe') | Should Be $true
    }

    # ---------------------------------------------------------------------------
    # Test-CommandOnPath
    # ---------------------------------------------------------------------------

    It 'Test-CommandOnPath: returns true for existing command' {
        $result = Test-CommandOnPath -CommandName 'powershell'
        $result | Should Be $true
    }

    It 'Test-CommandOnPath: returns false for nonexistent command' {
        $result = Test-CommandOnPath -CommandName 'nonexistent_command_xyz123abc'
        $result | Should Be $false
    }

    # ---------------------------------------------------------------------------
    # Invoke-Install: WhatIf (uses ShouldProcess so no actual execution)
    # ---------------------------------------------------------------------------

    It 'Invoke-Install WhatIf: creates no opencode.json' {
        $testDir = New-TestDirectory
        $repoPath = $testDir
        New-Item -ItemType File -Path (Join-Path $repoPath 'pyproject.toml') -Force | Out-Null

        try {
            # Mock the gate checks so we can test WhatIf behavior
            Mock Test-IsWindowsX64 { $true }
            Mock Test-CommandOnPath { $true } -ParameterFilter { $CommandName -eq 'uv' }
            Mock Test-Path { $true } -ParameterFilter { $Path -like '*\ms-access-mcp.exe' }

            Mock Invoke-External {
                if ($Arguments -match 'tool dir --bin') {
                    return @{ ExitCode = 0; Stdout = $testDir; Stderr = '' }
                }
                return @{ ExitCode = 0; Stdout = ''; Stderr = '' }
            }

            Invoke-Install -RepoPath $repoPath -NoRegister -WhatIf

            $opencodeJsonPath = Join-Path $testDir 'opencode.json'
            $opencodeJsonPath | Should Not Exist
        }
        finally {
            Remove-Item -Recurse -Force $testDir -ErrorAction SilentlyContinue
        }
    }

    It 'Invoke-Install WhatIf: does not throw' {
        $testDir = New-TestDirectory
        $repoPath = $testDir
        New-Item -ItemType File -Path (Join-Path $repoPath 'pyproject.toml') -Force | Out-Null

        try {
            Mock Test-IsWindowsX64 { $true }
            Mock Test-CommandOnPath { $true } -ParameterFilter { $CommandName -eq 'uv' }
            Mock Test-Path { $true } -ParameterFilter { $Path -like '*\ms-access-mcp.exe' }
            Mock Invoke-External {
                if ($Arguments -match 'tool dir --bin') {
                    return @{ ExitCode = 0; Stdout = $testDir; Stderr = '' }
                }
                return @{ ExitCode = 0; Stdout = ''; Stderr = '' }
            }

            { Invoke-Install -RepoPath $repoPath -NoRegister -WhatIf } | Should Not Throw
        }
        finally {
            Remove-Item -Recurse -Force $testDir -ErrorAction SilentlyContinue
        }
    }

    # ---------------------------------------------------------------------------
    # Invoke-Install: doctor failure
    # ---------------------------------------------------------------------------

    It 'Invoke-Install: doctor failure throws before registration' {
        $testDir = New-TestDirectory
        $repoPath = $testDir
        New-Item -ItemType File -Path (Join-Path $repoPath 'pyproject.toml') -Force | Out-Null

        try {
            Mock Test-IsWindowsX64 { $true }
            Mock Test-CommandOnPath { $true } -ParameterFilter { $CommandName -eq 'uv' }
            Mock Test-Path { $true } -ParameterFilter { $Path -like '*\ms-access-mcp.exe' }
            Mock Invoke-External {
                if ($Arguments -match 'tool dir --bin') {
                    return @{ ExitCode = 0; Stdout = $testDir; Stderr = '' }
                }
                if ($Arguments -match 'doctor') {
                    # Doctor fails
                    return @{ ExitCode = 1; Stdout = '{"status":"fail"}'; Stderr = '' }
                }
                return @{ ExitCode = 0; Stdout = ''; Stderr = '' }
            }

            { Invoke-Install -RepoPath $repoPath -NoRegister } | Should Throw
        }
        finally {
            Remove-Item -Recurse -Force $testDir -ErrorAction SilentlyContinue
        }
    }

    # ---------------------------------------------------------------------------
    # Invoke-Install: -NoRegister skips configure
    # ---------------------------------------------------------------------------

    It 'Invoke-Install -NoRegister: configure not called' {
        $testDir = New-TestDirectory
        $repoPath = $testDir
        New-Item -ItemType File -Path (Join-Path $repoPath 'pyproject.toml') -Force | Out-Null

        $configureCalled = $false

        try {
            Mock Test-IsWindowsX64 { $true }
            Mock Test-CommandOnPath { $true } -ParameterFilter { $CommandName -eq 'uv' }
            Mock Test-Path { $true } -ParameterFilter { $Path -like '*\ms-access-mcp.exe' }
            Mock Invoke-External {
                if ($Arguments -match 'tool dir --bin') {
                    return @{ ExitCode = 0; Stdout = $testDir; Stderr = '' }
                }
                if ($Arguments -match 'configure') {
                    $script:configureCalled = $true
                }
                return @{ ExitCode = 0; Stdout = ''; Stderr = '' }
            }

            Invoke-Install -RepoPath $repoPath -NoRegister

            $configureCalled | Should Be $false
        }
        finally {
            Remove-Item -Recurse -Force $testDir -ErrorAction SilentlyContinue
        }
    }

    # ---------------------------------------------------------------------------
    # Invoke-Install: -Global propagated to configure
    # ---------------------------------------------------------------------------

    It 'Invoke-Install -Global: configure called with --global' {
        $testDir = New-TestDirectory
        $repoPath = $testDir
        New-Item -ItemType File -Path (Join-Path $repoPath 'pyproject.toml') -Force | Out-Null

        $script:configureArgs = $null

        try {
            Mock Test-IsWindowsX64 { $true }
            Mock Test-CommandOnPath { $true } -ParameterFilter { $CommandName -eq 'uv' }
            Mock Test-Path { $true } -ParameterFilter { $Path -like '*\ms-access-mcp.exe' }
            Mock Invoke-External {
                if ($Arguments -match 'tool dir --bin') {
                    return @{ ExitCode = 0; Stdout = $testDir; Stderr = '' }
                }
                if ($Arguments -match 'configure') {
                    $script:configureArgs = $Arguments
                }
                return @{ ExitCode = 0; Stdout = ''; Stderr = '' }
            }

            Invoke-Install -RepoPath $repoPath -Global

            $script:configureArgs | Should Not BeNullOrEmpty
            $script:configureArgs | Should Match '--global'
        }
        finally {
            Remove-Item -Recurse -Force $testDir -ErrorAction SilentlyContinue
        }
    }

    # ---------------------------------------------------------------------------
    # Invoke-Install: -Profile passed to uv tool install
    # ---------------------------------------------------------------------------

    It 'Invoke-Install -Profile core: passed to uv tool install' {
        $testDir = New-TestDirectory
        $repoPath = $testDir
        New-Item -ItemType File -Path (Join-Path $repoPath 'pyproject.toml') -Force | Out-Null

        $script:toolInstallArgs = $null

        try {
            Mock Test-IsWindowsX64 { $true }
            Mock Test-CommandOnPath { $true } -ParameterFilter { $CommandName -eq 'uv' }
            Mock Test-Path { $true } -ParameterFilter { $Path -like '*\ms-access-mcp.exe' }
            Mock Invoke-External {
                if ($Arguments -match 'tool dir --bin') {
                    return @{ ExitCode = 0; Stdout = $testDir; Stderr = '' }
                }
                if ($Arguments -match 'tool install') {
                    $script:toolInstallArgs = $Arguments
                }
                return @{ ExitCode = 0; Stdout = ''; Stderr = '' }
            }

            Invoke-Install -RepoPath $repoPath -Profile core

            $script:toolInstallArgs | Should Not BeNullOrEmpty
            $script:toolInstallArgs | Should Match '--profile'
            $script:toolInstallArgs | Should Match 'core'
        }
        finally {
            Remove-Item -Recurse -Force $testDir -ErrorAction SilentlyContinue
        }
    }

    # ---------------------------------------------------------------------------
    # S9: No secrets
    # ---------------------------------------------------------------------------

    It 'S9: install.ps1 contains no credential patterns' {
        $content = Get-Content $InstallerScript -Raw -ErrorAction SilentlyContinue
        if ([string]::IsNullOrWhiteSpace($content)) {
            Set-ItResult -Skipped -Because 'install.ps1 is empty'
            return
        }

        $hasAzure = $content -match '\$env:AZURE'
        $hasOpenAI = $content -match '\$env:OPENAI'
        $hasAnthropic = $content -match '\$env:ANTHROPIC'
        $hasGithubToken = $content -match '\$env:GITHUB_TOKEN'
        $hasOpenAIKey = $content -match '\$env:OPENAI_API_KEY'

        $hasAzure | Should Be $false
        $hasOpenAI | Should Be $false
        $hasAnthropic | Should Be $false
        $hasGithubToken | Should Be $false
        $hasOpenAIKey | Should Be $false
    }
}
