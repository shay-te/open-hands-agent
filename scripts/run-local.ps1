# PowerShell equivalent of run-local.sh for Windows.
#
# Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File .\scripts\run-local.ps1
#
# Mirrors run-local.sh: loads .env, then runs kato. The planning webserver
# is embedded as a daemon thread in the same Python process so it shares
# the live ClaudeSessionManager; set KATO_WEBSERVER_DISABLED=true in .env
# to skip it.

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

if (-not (Test-Path '.env')) {
    Write-Host '.env is missing. Run .\scripts\bootstrap.ps1 first.'
    exit 1
}

$venvPython = Join-Path '.venv' 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host '.venv is missing. Run .\scripts\bootstrap.ps1 first.'
    exit 1
}

# Source .env into the current process. Skips comments and blank lines.
foreach ($line in Get-Content '.env') {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    $eqIndex = $trimmed.IndexOf('=')
    if ($eqIndex -lt 1) { continue }
    $key = $trimmed.Substring(0, $eqIndex).Trim()
    $value = $trimmed.Substring($eqIndex + 1).Trim()
    # Strip wrapping single or double quotes.
    if ($value.Length -ge 2) {
        $first = $value[0]
        $last = $value[$value.Length - 1]
        if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
            $value = $value.Substring(1, $value.Length - 2)
        }
    }
    [System.Environment]::SetEnvironmentVariable($key, $value, 'Process')
}

& $venvPython -m kato.main
exit $LASTEXITCODE
