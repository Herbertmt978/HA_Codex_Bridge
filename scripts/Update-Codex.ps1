[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CodexPath,

    [string]$LogPath = "C:\CodexHA\logs\codex-update.log",

    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-UpdateLog {
    param([string]$Message)

    $timestamp = [DateTimeOffset]::Now.ToString("yyyy-MM-ddTHH:mm:sszzz")
    Add-Content -LiteralPath $LogPath -Value "[$timestamp] $Message" -Encoding UTF8
}

function Get-CodexVersion {
    $output = & $CodexPath --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Codex version check failed with exit code $LASTEXITCODE."
    }

    $version = (($output | Out-String).Trim() -split "`r?`n")[0]
    if (-not $version) {
        throw "Codex version check returned no version."
    }
    if ($version.Length -gt 200) {
        $version = $version.Substring(0, 200)
    }
    return ($version -replace "[^\x20-\x7E]", "?")
}

function Get-FileDigest {
    param([Parameter(Mandatory = $true)][string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Get-ManagedReleaseJunction {
    $codexFullPath = [IO.Path]::GetFullPath($CodexPath)
    $visibleBinPath = Split-Path -Parent $codexFullPath
    $visibleBin = Get-Item -LiteralPath $visibleBinPath -Force -ErrorAction SilentlyContinue
    if ($null -eq $visibleBin -or $visibleBin.LinkType -ne "Junction") {
        return $null
    }

    $visibleTarget = [string]@($visibleBin.Target)[0]
    if ([string]::IsNullOrWhiteSpace($visibleTarget) -or (Split-Path -Leaf $visibleTarget) -ne "bin") {
        return $null
    }
    $currentPath = Split-Path -Parent $visibleTarget
    $current = Get-Item -LiteralPath $currentPath -Force -ErrorAction SilentlyContinue
    if ($null -eq $current -or $current.LinkType -ne "Junction") {
        return $null
    }
    $currentTarget = [string]@($current.Target)[0]
    if ([string]::IsNullOrWhiteSpace($currentTarget)) {
        return $null
    }
    return [PSCustomObject]@{
        Path = [IO.Path]::GetFullPath($currentPath)
        Target = [IO.Path]::GetFullPath($currentTarget)
    }
}

function Set-ManagedReleaseJunction {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Target
    )

    $current = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($current.LinkType -ne "Junction") {
        throw "Managed Codex current path is no longer a junction."
    }
    $currentTarget = [IO.Path]::GetFullPath([string]@($current.Target)[0])
    $targetFullPath = [IO.Path]::GetFullPath($Target)
    if ($currentTarget -ieq $targetFullPath) {
        return
    }

    [IO.Directory]::Delete($Path)
    try {
        New-Item -ItemType Junction -Path $Path -Target $targetFullPath | Out-Null
    }
    catch {
        if (-not (Test-Path -LiteralPath $Path)) {
            New-Item -ItemType Junction -Path $Path -Target $currentTarget -ErrorAction SilentlyContinue | Out-Null
        }
        throw
    }

    $restored = Get-Item -LiteralPath $Path -Force
    $restoredTarget = [IO.Path]::GetFullPath([string]@($restored.Target)[0])
    if ($restored.LinkType -ne "Junction" -or $restoredTarget -ine $targetFullPath) {
        throw "Managed Codex release junction could not be verified after rollback."
    }
}

function New-CodexBackup {
    $codexFullPath = [IO.Path]::GetFullPath($CodexPath)
    $codexDirectory = Split-Path -Parent $codexFullPath
    $candidatePaths = @(
        $codexFullPath,
        (Join-Path $codexDirectory "codex.exe"),
        (Join-Path $codexDirectory "codex-real.exe")
    )
    $backupDirectory = Join-Path `
        ([IO.Path]::GetTempPath()) `
        ("codex-bridge-update-{0}" -f [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $backupDirectory | Out-Null

    try {
        $managedRelease = Get-ManagedReleaseJunction
        $seenPaths = @{}
        $targets = @()
        $index = 0
        foreach ($candidatePath in $candidatePaths) {
            $fullPath = [IO.Path]::GetFullPath($candidatePath)
            if ($seenPaths.ContainsKey($fullPath)) {
                continue
            }
            $seenPaths[$fullPath] = $true

            $exists = Test-Path -LiteralPath $fullPath -PathType Leaf
            $backupPath = $null
            $originalHash = $null
            if ($exists) {
                $backupName = "{0:D2}-{1}" -f $index, (Split-Path -Leaf $fullPath)
                $backupPath = Join-Path $backupDirectory $backupName
                Copy-Item -LiteralPath $fullPath -Destination $backupPath
                $originalHash = Get-FileDigest -Path $backupPath
            }

            $targets += [PSCustomObject]@{
                Path = $fullPath
                Existed = $exists
                BackupPath = $backupPath
                OriginalHash = $originalHash
            }
            $index += 1
        }

        return [PSCustomObject]@{
            Directory = $backupDirectory
            Targets = @($targets)
            ManagedCurrentPath = if ($null -ne $managedRelease) { $managedRelease.Path } else { $null }
            ManagedCurrentTarget = if ($null -ne $managedRelease) { $managedRelease.Target } else { $null }
        }
    }
    catch {
        Remove-Item -LiteralPath $backupDirectory -Recurse -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Restore-CodexBackup {
    param([Parameter(Mandatory = $true)]$Backup)

    if ($Backup.ManagedCurrentPath -and $Backup.ManagedCurrentTarget) {
        Set-ManagedReleaseJunction `
            -Path $Backup.ManagedCurrentPath `
            -Target $Backup.ManagedCurrentTarget
        return
    }

    foreach ($target in $Backup.Targets) {
        $currentExists = Test-Path -LiteralPath $target.Path -PathType Leaf
        if ($target.Existed) {
            if ($currentExists) {
                $currentHash = Get-FileDigest -Path $target.Path
                if ($currentHash -eq $target.OriginalHash) {
                    continue
                }
            }

            Copy-Item -LiteralPath $target.BackupPath -Destination $target.Path -Force
            $restoredHash = Get-FileDigest -Path $target.Path
            if ($restoredHash -ne $target.OriginalHash) {
                throw "A Codex executable could not be verified after rollback."
            }
        }
        elseif ($currentExists) {
            Remove-Item -LiteralPath $target.Path -Force
        }
    }
}

function Remove-CodexBackup {
    param([Parameter(Mandatory = $true)]$Backup)

    Remove-Item -LiteralPath $Backup.Directory -Recurse -Force
}

function Get-BundledModelCatalog {
    $output = & $CodexPath debug models --bundled 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Bundled model catalog smoke test failed with exit code $LASTEXITCODE."
    }

    $text = ($output | Out-String).Trim()
    $catalog = $null
    if ($text) {
        try {
            $catalog = $text | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            foreach ($line in ($text -split "`r?`n")) {
                $candidate = $line.Trim()
                if (-not ($candidate.StartsWith("{") -and $candidate.EndsWith("}"))) {
                    continue
                }
                try {
                    $catalog = $candidate | ConvertFrom-Json -ErrorAction Stop
                    break
                }
                catch {
                    $catalog = $null
                }
            }
        }
    }

    if (
        $null -eq $catalog -or
        -not ($catalog.PSObject.Properties.Name -contains "models") -or
        @($catalog.models).Count -eq 0
    ) {
        throw "Bundled model catalog smoke test failed validation."
    }

    $firstModel = @($catalog.models)[0]
    if (
        $null -eq $firstModel -or
        -not ($firstModel.PSObject.Properties.Name -contains "slug") -or
        [string]::IsNullOrWhiteSpace([string]$firstModel.slug)
    ) {
        throw "Bundled model catalog smoke test returned an invalid model entry."
    }

    return $catalog
}

$logDirectory = Split-Path -Parent $LogPath
if ($logDirectory) {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
}
if (Test-Path -LiteralPath $LogPath) {
    $logFile = Get-Item -LiteralPath $LogPath
    if ($logFile.Length -gt 5MB) {
        Move-Item -LiteralPath $LogPath -Destination "$LogPath.previous" -Force
    }
}

$backup = $null
try {
    if (-not (Test-Path -LiteralPath $CodexPath -PathType Leaf)) {
        throw "Codex executable was not found at the configured path."
    }

    $before = Get-CodexVersion
    Write-UpdateLog "Installed version: $before"

    if ($CheckOnly) {
        Write-UpdateLog "Check-only mode completed."
        exit 0
    }

    $backup = New-CodexBackup
    $backedUpCount = @($backup.Targets | Where-Object { $_.Existed }).Count
    Write-UpdateLog "Backed up $backedUpCount Codex executable(s)."

    Write-UpdateLog "Starting Codex self-update."
    $codexInstallDirectory = Split-Path -Parent ([IO.Path]::GetFullPath($CodexPath))
    $priorInstallDirectory = [Environment]::GetEnvironmentVariable(
        "CODEX_INSTALL_DIR",
        [EnvironmentVariableTarget]::Process
    )
    $priorProcessPath = [Environment]::GetEnvironmentVariable(
        "PATH",
        [EnvironmentVariableTarget]::Process
    )
    try {
        # Keep the official installer on the exact path used by the bridge,
        # including supported custom/junction-based standalone installs.
        [Environment]::SetEnvironmentVariable(
            "CODEX_INSTALL_DIR",
            $codexInstallDirectory,
            [EnvironmentVariableTarget]::Process
        )
        $windowsSystemDirectory = Join-Path $env:SystemRoot "System32"
        [Environment]::SetEnvironmentVariable(
            "PATH",
            "$windowsSystemDirectory;$priorProcessPath",
            [EnvironmentVariableTarget]::Process
        )
        $null = & $CodexPath update 2>&1
        $updateExitCode = $LASTEXITCODE
    }
    finally {
        [Environment]::SetEnvironmentVariable(
            "CODEX_INSTALL_DIR",
            $priorInstallDirectory,
            [EnvironmentVariableTarget]::Process
        )
        [Environment]::SetEnvironmentVariable(
            "PATH",
            $priorProcessPath,
            [EnvironmentVariableTarget]::Process
        )
    }
    if ($updateExitCode -ne 0) {
        throw "Codex update failed with exit code $updateExitCode."
    }
    Write-UpdateLog "Codex self-update command completed."

    $after = Get-CodexVersion
    $catalog = Get-BundledModelCatalog
    Write-UpdateLog "Bundled model catalog smoke test passed with $(@($catalog.models).Count) model(s)."
    Write-UpdateLog "Updated version: $after"

    Remove-CodexBackup -Backup $backup
    $backup = $null
    exit 0
}
catch {
    $failureMessage = $_.Exception.Message
    $rollbackFailure = $null
    if ($null -ne $backup) {
        try {
            Restore-CodexBackup -Backup $backup
            Write-UpdateLog "Rollback completed."
            Remove-CodexBackup -Backup $backup
            $backup = $null
        }
        catch {
            $rollbackFailure = $_.Exception.Message
        }
    }

    Write-UpdateLog "ERROR: $failureMessage"
    if ($rollbackFailure) {
        Write-UpdateLog "ROLLBACK ERROR: $rollbackFailure"
        Write-UpdateLog "Rollback backup retained for manual recovery."
    }
    exit 1
}
