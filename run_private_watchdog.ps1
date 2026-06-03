$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env.private")) {
    if (Test-Path ".env.private.example") {
        Copy-Item ".env.private.example" ".env.private"
        Write-Host "Created .env.private from .env.private.example - fill API_ID, tokens, mappings, then re-run."
        exit 0
    }
    Write-Error "Create .env.private from .env.private.example"
}

$env:DOTENV_FILE = ".env.private"
$env:PYTHONUNBUFFERED = "1"

# Restart delay in seconds after unexpected exit.
$restartDelaySec = 8
# 0 = unlimited restarts.
$maxRestarts = 0
$restarts = 0

Write-Host "Using DOTENV_FILE=$($env:DOTENV_FILE)"
Write-Host "Watchdog: delay=${restartDelaySec}s max_restarts=$maxRestarts (0=unlimited)"

while ($true) {
    Write-Host "Starting forwarder: py main.py"
    & py main.py
    $code = $LASTEXITCODE

    if ($code -eq 0) {
        Write-Host "Forwarder exited normally (code 0). Watchdog stops."
        break
    }

    $restarts++
    Write-Host "Forwarder exited with code $code."
    if ($maxRestarts -gt 0 -and $restarts -ge $maxRestarts) {
        Write-Host "Reached max restarts ($maxRestarts). Watchdog stops."
        break
    }

    Write-Host "Restarting in $restartDelaySec seconds..."
    Start-Sleep -Seconds $restartDelaySec
}
