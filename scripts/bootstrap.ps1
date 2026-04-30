# PowerShell equivalent of bootstrap.sh for Windows.
#
# Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
#
# Mirrors bootstrap.sh: copies .env if missing, creates .venv, installs the
# Python deps, builds the React bundle when npm is available, runs the test
# suite. Each step prints "==> ..." headers and aborts on the first failure.

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

function Invoke-Step {
    param([string]$Description, [scriptblock]$Action)
    Write-Host "==> $Description"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Bootstrap failed while running: $Description"
        Write-Host "Fix the error above and rerun .\scripts\bootstrap.ps1"
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host 'Created .env from .env.example'
}

$venvPython = Join-Path '.venv' 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Invoke-Step 'python -m venv .venv' { python -m venv .venv }
}

Invoke-Step 'install Python deps' {
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install --no-cache-dir -e .
    & $venvPython -m pip install --no-cache-dir -e .\webserver
}

if (Get-Command npm -ErrorAction SilentlyContinue) {
    Invoke-Step 'npm install (webserver/ui)' {
        npm --prefix .\webserver\ui install --no-audit --no-fund
    }
    Invoke-Step 'npm run build (webserver/ui)' {
        npm --prefix .\webserver\ui run build
    }
} else {
    Write-Host '==> skipping webserver/ui build (npm not found; using committed bundle)'
}

Invoke-Step 'run tests' {
    & $venvPython -m unittest discover -s tests
}

Write-Host @'

Bootstrap complete.

Next manual steps:
1. Fill the required secrets in .env
2. Run `python -m kato.configure_project` to validate the configuration
3. Run `python -m kato.main` (or `make run` if you have GNU Make)
'@
