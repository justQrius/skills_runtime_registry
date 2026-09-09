[CmdletBinding()]
param(
    [string]$Project,
    [string]$Scope,
    [string]$TaskName = 'Skill Registry Catalog Refresh',
    [string]$VercelLinkDirectory = (Join-Path $env:LOCALAPPDATA 'skill-registry\vercel'),
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'skill-registry\automation'),
    [string]$DailyAt = '03:00'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$vercel = Get-Command vercel.cmd -ErrorAction SilentlyContinue
if (-not $vercel) {
    $fallback = Join-Path $env:APPDATA 'npm\vercel.cmd'
    if (Test-Path -LiteralPath $fallback) {
        $vercelPath = $fallback
    } else {
        throw 'vercel.cmd is required to install the refresh task'
    }
} else {
    $vercelPath = $vercel.Source
}

New-Item -ItemType Directory -Path $VercelLinkDirectory -Force | Out-Null
$linkArgs = @('link', '--cwd', $VercelLinkDirectory, '--yes', '--no-color')
if ($Project) { $linkArgs += @('--project', $Project) }
if ($Scope) { $linkArgs += @('--scope', $Scope) }
& $vercelPath @linkArgs
if ($LASTEXITCODE -ne 0) { throw "Vercel project link failed with exit code $LASTEXITCODE" }

# The runtime command fetches a fresh token. Do not retain the link-time token.
$localEnv = Join-Path $VercelLinkDirectory '.env.local'
if (Test-Path -LiteralPath $localEnv) {
    [System.IO.File]::Delete($localEnv)
}

New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
$runner = Join-Path $InstallDirectory 'refresh_registry_windows.ps1'
$ids = Join-Path $InstallDirectory 'refresh-ids.txt'
$client = Join-Path $InstallDirectory 'refresh_client.py'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'refresh_registry_windows.ps1') -Destination $runner -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'refresh-ids.txt') -Destination $ids -Force
Copy-Item -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) 'python\skill_registry\refresh_client.py') -Destination $client -Force
$pwsh = (Get-Command powershell.exe -ErrorAction Stop).Source
$python = (Get-Command python.exe -ErrorAction Stop).Source
$actionArgs = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`" -VercelLinkDirectory `"$VercelLinkDirectory`" -VercelPath `"$vercelPath`" -PythonPath `"$python`" -IdsFile `"$ids`" -ClientPath `"$client`""
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Refresh curated Skill Registry IDs using a just-in-time Vercel OIDC token.' `
    -Force | Out-Null

& $runner -VercelLinkDirectory $VercelLinkDirectory -VercelPath $vercelPath `
    -PythonPath $python -IdsFile $ids -ClientPath $client

Get-ScheduledTask -TaskName $TaskName
