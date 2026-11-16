# SPDX-License-Identifier: GPL-3.0-or-later
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('verify', 'apply', 'test', 'build', 'package')]
    [string]$Action,
    [string]$Source,
    [string]$Destination,
    [string]$Manifest,
    [switch]$Release
)

$ErrorActionPreference = 'Stop'

try {
    if (-not $Source) { $Source = Join-Path $PSScriptRoot '.work\zed-upstream' }
    if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
        throw 'Run this entry point on Windows.'
    }
    if ($Action -in @('test', 'build')) {
        if ($env:VSCMD_ARG_TGT_ARCH -ne 'x64') {
            $vswhere = Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Microsoft Visual Studio\Installer\vswhere.exe'
            if (-not (Test-Path -LiteralPath $vswhere)) {
                throw 'Install Visual Studio C++ Build Tools and a Windows SDK first.'
            }
            $installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
            if ($LASTEXITCODE -ne 0 -or -not $installation) {
                throw 'No Visual Studio installation with the x64 C++ tools was found.'
            }
            Import-Module (Join-Path $installation 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll')
            Enter-VsDevShell -VsInstallPath $installation -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
        }
        $compiler = Get-Command cl.exe -ErrorAction Stop
        $spectre = [IO.Path]::GetFullPath((Join-Path (Split-Path $compiler.Source -Parent) '..\..\..\lib\spectre\x64'))
        if (-not (Test-Path -LiteralPath $spectre)) {
            throw 'Missing MSVC x64 Spectre-mitigated libraries. Add the matching MSVC Spectre component in Visual Studio Installer, then rerun this command.'
        }
        if ($Action -eq 'build') {
            $sdkHeader = Join-Path $env:WindowsSdkDir "Include\$env:WindowsSDKVersion\shared\sdkddkver.h"
            if (-not (Test-Path -LiteralPath $sdkHeader) -or
                -not (Select-String -LiteralPath $sdkHeader -Pattern '^#define\s+NTDDI_WIN11_GE\b' -Quiet)) {
                throw 'The full Zed build requires Windows SDK 10.0.26100 or newer (NTDDI_WIN11_GE). Install it alongside your current SDK, then start a fresh developer shell and rerun build. Markdown tests can still run with the older SDK.'
            }
            $toolVersion = $env:VCToolsVersion -replace '\\$', ''
            if (-not $toolVersion -or [version]$toolVersion -lt [version]'14.44.0') {
                throw 'The prebuilt WebRTC library requires MSVC 14.44 or newer for the full Zed link. Install newer x64/x86 Build Tools and matching Spectre libraries, then start a fresh developer shell. Markdown tests can still use the older tools.'
            }
        }
    }

    $localCargo = Join-Path $PSScriptRoot '.work\cargo'
    if (Test-Path -LiteralPath (Join-Path $localCargo 'bin\rustup.exe')) {
        $env:CARGO_HOME = $localCargo
        $env:RUSTUP_HOME = Join-Path $PSScriptRoot '.work\rustup'
        $env:Path = (Join-Path $localCargo 'bin') + ';' + $env:Path
    }
    $localNasm = Join-Path $PSScriptRoot '.work\tools\nasm-3.02-win64\nasm-3.02'
    if (Test-Path -LiteralPath (Join-Path $localNasm 'nasm.exe')) {
        $env:Path = $localNasm + ';' + $env:Path
    }
    if ($Action -in @('test', 'build') -and -not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        throw 'Rust is missing. Install rustup, then the toolchain pinned in manifest.json.'
    }

    $arguments = @((Join-Path $PSScriptRoot 'build.py'), $Action, '--source', $Source)
    if ($Destination) { $arguments += @('--destination', $Destination) }
    if ($Manifest) { $arguments += @('--manifest', $Manifest) }
    if ($Release) { $arguments += '--release' }
    $python = Get-Command python -ErrorAction Stop
    # Cargo reports progress on stderr; Windows PowerShell must not treat it as a terminating error.
    $ErrorActionPreference = 'Continue'
    & $python @arguments
    exit $LASTEXITCODE
}
catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
