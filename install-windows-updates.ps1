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
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Zed.lnk'
$allowedDescriptions = @(
    "Zed Native Math stable source updater: $PSScriptRoot",
    "Zed Native Math release updater: $PSScriptRoot"
)

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
        if ($Action -eq 'uninstall' -and (Test-Path -LiteralPath $desktopLink)) {
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
    $link = $shell.CreateShortcut($desktopLink)
    $link.TargetPath = $pythonw
    $link.Arguments = "`"$worker`" launch"
    $link.WorkingDirectory = $PSScriptRoot
    $link.IconLocation = (Join-Path $state.current.directory 'Zed.exe') + ',0'
    $link.Description = 'Zed Native Math - downloads prebuilt releases'
    $link.Save()
    Start-ScheduledTask -TaskName $taskName
    Write-Output 'Installed: GitHub Release check every 6 hours, at logon, and on desktop launch.'
    Write-Output "Status: .\install-windows-updates.ps1 status"
    Write-Output "Cleanup old local build caches (optional): .\install-windows-updates.ps1 cleanup -DryRun"
}
catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
