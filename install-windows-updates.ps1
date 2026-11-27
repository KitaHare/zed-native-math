# SPDX-License-Identifier: GPL-3.0-or-later
[CmdletBinding()]
param(
    [ValidateSet('install', 'status', 'check', 'retry', 'disable', 'rollback', 'uninstall', 'cleanup')]
    [string]$Action = 'install',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$taskName = 'Zed Native Math Stable Update'
$worker = Join-Path $PSScriptRoot 'windows_update.py'
$statePath = Join-Path $PSScriptRoot '.work\windows-updates\state.json'
$binDir = Join-Path $PSScriptRoot 'dist\bin'
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Zed.lnk'
$startMenuLink = Join-Path ([Environment]::GetFolderPath('Programs')) 'Zed Native Math.lnk'
$allowedDescriptions = @(
    "Zed Native Math stable source updater: $PSScriptRoot",
    "Zed Native Math release updater: $PSScriptRoot"
)

function Broadcast-EnvironmentChange {
    try {
        Add-Type -Namespace Win32 -Name NativeMethods -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true, CharSet = System.Runtime.InteropServices.CharSet.Auto)]
public static extern System.IntPtr SendMessageTimeout(
    System.IntPtr hWnd, uint Msg, System.UIntPtr wParam, string lParam,
    uint fuFlags, uint uTimeout, out System.UIntPtr lpdwResult);
'@ -ErrorAction SilentlyContinue
        $result = [UIntPtr]::Zero
        [Win32.NativeMethods]::SendMessageTimeout([IntPtr]0xffff, 0x001A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$result) | Out-Null
    }
    catch {}
}

function Update-UserPath {
    param([switch]$Remove)
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @()
    if ($current) {
        $entries = @($current -split ';' | Where-Object { $_ -and $_.Trim() -ne '' })
    }
    $normalizedBin = $binDir.TrimEnd('\').ToLowerInvariant()
    $filtered = @($entries | Where-Object {
        $norm = $_.Trim().TrimEnd('\').ToLowerInvariant()
        if ($norm -eq $normalizedBin) { return $false }
        if (-not $Remove -and $norm -like '*\appdata\local\programs\zed\bin') { return $false }
        return $true
    })
    if (-not $Remove) {
        $filtered += $binDir
    }
    $updated = ($filtered -join ';')
    if ($updated -ne ($entries -join ';')) {
        [Environment]::SetEnvironmentVariable('Path', $updated, 'User')
        Broadcast-EnvironmentChange
    }
}

function Register-ShellIntegration {
    param([string]$PythonW, [string]$ActiveDir)
    $icon = (Join-Path $ActiveDir 'Zed.exe') + ',0'
    $launchFileCmd = "`"$PythonW`" `"$worker`" launch `"%1`""
    $launchBgCmd = "`"$PythonW`" `"$worker`" launch `"%V`""

    $contextMenus = @(
        @{ Key = 'HKCU:\Software\Classes\*\shell\ZedNativeMath'; Command = $launchFileCmd },
        @{ Key = 'HKCU:\Software\Classes\Directory\shell\ZedNativeMath'; Command = $launchFileCmd },
        @{ Key = 'HKCU:\Software\Classes\Directory\Background\shell\ZedNativeMath'; Command = $launchBgCmd }
    )
    foreach ($entry in $contextMenus) {
        New-Item -Path ($entry.Key + '\command') -Force | Out-Null
        Set-ItemProperty -LiteralPath $entry.Key -Name '(Default)' -Value '通过 Z&ed 打开'
        Set-ItemProperty -LiteralPath $entry.Key -Name 'Icon' -Value $icon
        Set-ItemProperty -LiteralPath ($entry.Key + '\command') -Name '(Default)' -Value $entry.Command
    }

    # "Open with" application entry (HKCU\Software\Classes\Applications\Zed.exe)
    $appKey = 'HKCU:\Software\Classes\Applications\Zed.exe'
    New-Item -Path ($appKey + '\DefaultIcon') -Force | Out-Null
    New-Item -Path ($appKey + '\shell\open\command') -Force | Out-Null
    Set-ItemProperty -LiteralPath $appKey -Name 'FriendlyAppName' -Value 'Zed Native Math'
    Set-ItemProperty -LiteralPath ($appKey + '\DefaultIcon') -Name '(Default)' -Value $icon
    Set-ItemProperty -LiteralPath ($appKey + '\shell\open') -Name 'FriendlyAppName' -Value 'Zed Native Math'
    Set-ItemProperty -LiteralPath ($appKey + '\shell\open\command') -Name '(Default)' -Value $launchFileCmd

    # zed:// URL protocol handler (OAuth callbacks and deep links)
    $protoKey = 'HKCU:\Software\Classes\zed'
    New-Item -Path ($protoKey + '\DefaultIcon') -Force | Out-Null
    New-Item -Path ($protoKey + '\shell\open\command') -Force | Out-Null
    Set-ItemProperty -LiteralPath $protoKey -Name '(Default)' -Value 'URL:zed Protocol'
    Set-ItemProperty -LiteralPath $protoKey -Name 'URL Protocol' -Value ''
    Set-ItemProperty -LiteralPath ($protoKey + '\DefaultIcon') -Name '(Default)' -Value $icon
    Set-ItemProperty -LiteralPath ($protoKey + '\shell\open\command') -Name '(Default)' -Value $launchFileCmd
}

function Unregister-ShellIntegration {
    foreach ($key in @(
        'HKCU:\Software\Classes\*\shell\ZedNativeMath',
        'HKCU:\Software\Classes\Directory\shell\ZedNativeMath',
        'HKCU:\Software\Classes\Directory\Background\shell\ZedNativeMath'
    )) {
        if (Test-Path -LiteralPath $key) {
            Remove-Item -LiteralPath $key -Recurse -Force
        }
    }
    foreach ($key in @('HKCU:\Software\Classes\Applications\Zed.exe', 'HKCU:\Software\Classes\zed')) {
        $cmdKey = $key + '\shell\open\command'
        if (Test-Path -LiteralPath $cmdKey) {
            $cmd = (Get-ItemProperty -LiteralPath $cmdKey -ErrorAction SilentlyContinue).'(default)'
            if ($cmd -like "*$worker*") {
                Remove-Item -LiteralPath $key -Recurse -Force
            }
        }
    }
    if (Test-Path -LiteralPath $startMenuLink) {
        Remove-Item -LiteralPath $startMenuLink -Force
    }
}

try {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
    $pythonw = Join-Path (Split-Path $python -Parent) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) { throw 'pythonw.exe is required for the background updater.' }
    if ($Action -in @('status', 'check', 'retry', 'rollback', 'cleanup')) {
        $workerAction = @{ status = 'status'; check = 'sync'; retry = 'sync'; rollback = 'rollback'; cleanup = 'cleanup' }[$Action]
        $arguments = @($worker, $workerAction)
        if ($Action -eq 'retry') { $arguments += '--force' }
        if ($Action -eq 'cleanup' -and $DryRun) { $arguments += '--dry-run' }
        & $python @arguments
        exit $LASTEXITCODE
    }

    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Description -notin $allowedDescriptions -or
        $existing.Actions.Arguments -notlike "*`"$worker`" sync*")) {
        throw 'A different task already uses this name; it was not changed.'
    }
    if ($Action -in @('disable', 'uninstall')) {
        & $python $worker disable
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        if ($existing) {
            if ($Action -eq 'disable') { $existing | Disable-ScheduledTask | Out-Null }
            else { $existing | Unregister-ScheduledTask -Confirm:$false }
        }
        if ($Action -eq 'uninstall') {
            Update-UserPath -Remove
            Unregister-ShellIntegration
            if (Test-Path -LiteralPath $desktopLink) {
                $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
                $shell = New-Object -ComObject WScript.Shell
                $link = $shell.CreateShortcut($desktopLink)
                if ($link.TargetPath -eq $pythonw -and $link.Arguments -eq "`"$worker`" launch") {
                    $link.TargetPath = Join-Path $state.current.directory 'Zed.exe'
                    $link.Arguments = ''
                    $link.WorkingDirectory = $state.current.directory
                    $link.Save()
                }
            }
        }
        Write-Output "Windows updater: $Action complete. Applications and recovery state retained."
        exit 0
    }

    & $python $worker initialize
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $taskAction = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$worker`" sync" -WorkingDirectory $PSScriptRoot
    $periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 6)
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger @($periodic, $logon) `
        -Principal $principal -Settings $settings `
        -Description "Zed Native Math release updater: $PSScriptRoot" -Force | Out-Null

    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $backupDirectory = Join-Path $PSScriptRoot '.work\desktop-shortcut-backup'
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    $backup = Join-Path $backupDirectory 'Zed-before-auto-update.lnk'
    if ((Test-Path -LiteralPath $desktopLink) -and -not (Test-Path -LiteralPath $backup)) {
        Copy-Item -LiteralPath $desktopLink -Destination $backup
    }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($shortcutPath in @($desktopLink, $startMenuLink)) {
        $link = $shell.CreateShortcut($shortcutPath)
        $link.TargetPath = $pythonw
        $link.Arguments = "`"$worker`" launch"
        $link.WorkingDirectory = $PSScriptRoot
        $link.IconLocation = (Join-Path $state.current.directory 'Zed.exe') + ',0'
        $link.Description = 'Zed Native Math - downloads prebuilt releases'
        $link.Save()
    }

    Update-UserPath
    Register-ShellIntegration -PythonW $pythonw -ActiveDir $state.current.directory

    Start-ScheduledTask -TaskName $taskName
    Write-Output 'Installed: GitHub Release check every 6 hours, desktop/Start Menu shortcuts, Explorer menu, zed:// protocol, and zed CLI in user PATH.'
    Write-Output "Status: .\install-windows-updates.ps1 status"
    Write-Output "Cleanup old local build caches (optional): .\install-windows-updates.ps1 cleanup -DryRun"
}
catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
