<#
.SYNOPSIS
Start the installed Codex UI with the locally installed 0.162.1 core.

.DESCRIPTION
Temporary workaround for the modelProviderMismatch regression verified with
0.166.0-alpha.6. CODEX_CLI_PATH is set only in the child process environment.
The script does not close an existing Codex instance or change user settings.
Close Codex first, then run this script. A normal subsequent launch restores
the application's bundled core.

.PARAMETER Check
Verify the paths and core version without launching Codex.
#>
[CmdletBinding()]
param(
    [string]$CodexAppPath,
    [string]$CodexCliPath,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'

if (-not $CodexCliPath) {
    $CodexCliPath = Join-Path $env:APPDATA 'npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe'
}
if (-not (Test-Path -LiteralPath $CodexCliPath -PathType Leaf)) {
    throw 'The local compatibility core was not found. Supply -CodexCliPath; this script does not download or replace Codex.'
}
$CodexCliPath = (Resolve-Path -LiteralPath $CodexCliPath).Path
$coreVersion = (& $CodexCliPath --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $coreVersion -ne 'codex-cli 0.162.1') {
    throw "This workaround was verified with codex-cli 0.162.1. Found: $coreVersion"
}

# Both executable names have shipped as the Codex desktop application.
$desktopProcesses = @(Get-Process -Name ChatGPT, Codex -ErrorAction SilentlyContinue)
if (-not $CodexAppPath) {
    $CodexAppPath = $desktopProcesses | ForEach-Object {
        $candidate = $_.Path
        if ($candidate -and $candidate -match '[\\/]OpenAI\.Codex_[^\\/]+[\\/]app[\\/][^\\/]+\.exe$') {
            $candidate
        }
    } | Select-Object -First 1
}
if (-not $CodexAppPath) {
    $codexPackage = Get-AppxPackage -Name OpenAI.Codex -ErrorAction SilentlyContinue |
        Sort-Object Version -Descending | Select-Object -First 1
    if ($codexPackage) {
        foreach ($executable in @('ChatGPT.exe', 'Codex.exe')) {
            $candidate = Join-Path $codexPackage.InstallLocation "app\$executable"
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $CodexAppPath = $candidate
                break
            }
        }
    }
}
if (-not $CodexAppPath -or -not (Test-Path -LiteralPath $CodexAppPath -PathType Leaf)) {
    throw 'The Codex desktop executable was not found. Supply -CodexAppPath with its installed path.'
}
$CodexAppPath = (Resolve-Path -LiteralPath $CodexAppPath).Path
$runningDesktop = @($desktopProcesses | Where-Object { $_.Path -eq $CodexAppPath })

if ($Check) {
    [pscustomobject]@{
        AppPath = $CodexAppPath
        CorePath = $CodexCliPath
        CoreVersion = $coreVersion
        DesktopRunning = ($runningDesktop.Count -gt 0)
        CanLaunch = ($runningDesktop.Count -eq 0)
    }
    return
}
if ($runningDesktop.Count -gt 0) {
    throw 'Close Codex after its active tasks finish, then run this script again. An existing instance would keep its current core.'
}

$launchInfo = [System.Diagnostics.ProcessStartInfo]::new()
$launchInfo.FileName = $CodexAppPath
$launchInfo.WorkingDirectory = Split-Path -Parent $CodexAppPath
$launchInfo.UseShellExecute = $false
$launchInfo.EnvironmentVariables['CODEX_CLI_PATH'] = $CodexCliPath
$launched = [System.Diagnostics.Process]::Start($launchInfo)
if ($null -eq $launched) {
    throw 'Codex did not start.'
}
Write-Output "Started Codex with $coreVersion for this launch only (PID $($launched.Id))."
