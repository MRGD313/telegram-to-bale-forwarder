$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $env:FORCE_NEW_FORWARDER) {
    $existing = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match "main\.py" -and $_.ProcessId -ne $PID
    }
    if ($existing) {
        Write-Host "Another forwarder instance is already running. Set FORCE_NEW_FORWARDER=1 to bypass."
        exit 1
    }
}

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
Write-Host "Using DOTENV_FILE=.env.private"
py main.py
