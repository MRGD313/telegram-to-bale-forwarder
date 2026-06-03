$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env.public") -and (Test-Path ".env.public.example")) {
    Copy-Item ".env.public.example" ".env.public"
    Write-Host "Created .env.public from .env.public.example - fill credentials, then re-run."
    exit 0
}

# Prefer .env.public; optional local override .env.mofidot
if (Test-Path ".env.public") {
    $env:DOTENV_FILE = ".env.public"
} elseif (Test-Path ".env.mofidot") {
    $env:DOTENV_FILE = ".env.mofidot"
} else {
    $env:DOTENV_FILE = ".env"
}

$env:PYTHONUNBUFFERED = "1"
Write-Host "Using DOTENV_FILE=$($env:DOTENV_FILE)"
py main.py
