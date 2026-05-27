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
Write-Host "Using DOTENV_FILE=.env.private"
py main.py
