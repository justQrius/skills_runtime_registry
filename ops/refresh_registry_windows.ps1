[CmdletBinding()]
param(
    [string]$RegistryUrl = 'http://127.0.0.1:8125/mcp',
    [string]$AdminKeyFile = (Join-Path $env:USERPROFILE '.skill-registry\admin_key'),
    [string]$VercelLinkDirectory = (Join-Path $env:LOCALAPPDATA 'skill-registry\vercel'),
    [string]$IdsFile,
    [string]$ClientPath,
    [string]$VercelPath,
    [string]$PythonPath,
    [string]$LogFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IdsFile) { $IdsFile = Join-Path $PSScriptRoot 'refresh-ids.txt' }
if (-not $ClientPath) { $ClientPath = Join-Path $PSScriptRoot 'refresh_client.py' }
if (-not $LogFile) { $LogFile = Join-Path $env:LOCALAPPDATA 'skill-registry\refresh.log' }
$logDirectory = Split-Path $LogFile -Parent
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$failureFile = Join-Path $logDirectory 'refresh-last-failure.txt'
$successFile = Join-Path $logDirectory 'refresh-last-success.txt'
Start-Transcript -LiteralPath $LogFile -Append | Out-Null
trap {
    $message = "{0:o} {1}" -f (Get-Date), $_.Exception.Message
    [System.IO.File]::WriteAllText($failureFile, $message)
    $host.UI.WriteErrorLine($message)
    Stop-Transcript | Out-Null
    exit 1
}

if (-not (Test-Path -LiteralPath (Join-Path $VercelLinkDirectory '.vercel\project.json'))) {
    throw "Vercel link missing at $VercelLinkDirectory; run install_windows_refresh_task.ps1 first"
}
if (-not (Test-Path -LiteralPath $AdminKeyFile)) {
    throw "Registry admin key file missing: $AdminKeyFile"
}

$vercelExecutable = $VercelPath
if (-not $vercelExecutable) {
    $vercel = Get-Command vercel.cmd -ErrorAction SilentlyContinue
    if (-not $vercel) {
        $fallback = Join-Path $env:APPDATA 'npm\vercel.cmd'
        if (Test-Path -LiteralPath $fallback) {
            $vercelExecutable = $fallback
        } else {
            throw 'vercel.cmd is required for just-in-time OIDC credentials'
        }
    } else {
        $vercelExecutable = $vercel.Source
    }
}
$pythonExecutable = $PythonPath
if (-not $pythonExecutable) {
    $pythonExecutable = (Get-Command python.exe -ErrorAction Stop).Source
}
$ids = @(Get-Content -LiteralPath $IdsFile | ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith('#') })
if ($ids.Count -eq 0) {
    throw "No skill IDs configured in $IdsFile"
}

$arguments = @(
    'env', 'run',
    '--cwd', $VercelLinkDirectory,
    '--environment', 'development',
    '--',
    $pythonExecutable,
    $ClientPath,
    '--url', $RegistryUrl,
    '--admin-key-file', $AdminKeyFile
) + $ids

& $vercelExecutable @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Registry refresh failed with exit code $LASTEXITCODE"
}
[System.IO.File]::WriteAllText($successFile, (Get-Date).ToString('o'))
if (Test-Path -LiteralPath $failureFile) { [System.IO.File]::Delete($failureFile) }
Stop-Transcript | Out-Null
