[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [string]$CodexPath = "$env:LOCALAPPDATA\Programs\OpenAI\Codex\bin\codex.exe",

    [string]$UpdaterPath = (Join-Path $PSScriptRoot "Update-Codex.ps1"),

    [string]$LogPath = "C:\CodexHA\logs\codex-update.log",

    [string]$TaskName = "CodexBridgeAutoUpdate",

    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$DailyAt = "03:15",

    [switch]$RunNow,

    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Uninstall) {
    Write-Output "Scheduled task: $TaskName"
    if ($PSCmdlet.ShouldProcess($TaskName, "Unregister Codex auto-update task")) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    exit 0
}

foreach ($requiredPath in ($CodexPath, $UpdaterPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required file does not exist: $requiredPath"
    }
}

$runAt = [DateTime]::ParseExact(
    $DailyAt,
    "HH:mm",
    [Globalization.CultureInfo]::InvariantCulture
)
$quotedUpdater = '"{0}"' -f $UpdaterPath
$quotedCodex = '"{0}"' -f $CodexPath
$quotedLog = '"{0}"' -f $LogPath
$actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File $quotedUpdater -CodexPath $quotedCodex -LogPath $quotedLog"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArguments
$trigger = New-ScheduledTaskTrigger -Daily -At $runAt
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 15)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType S4U `
    -RunLevel Limited

Write-Output "Scheduled task: $TaskName"
Write-Output "Daily update time: $DailyAt"
Write-Output "Codex executable: $CodexPath"
Write-Output "Updater log: $LogPath"

if ($PSCmdlet.ShouldProcess($TaskName, "Register daily Codex auto-update task")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Updates the Codex CLI used by Home Assistant Codex Bridge." `
        -Force | Out-Null

    if ($RunNow) {
        Start-ScheduledTask -TaskName $TaskName
    }
}
