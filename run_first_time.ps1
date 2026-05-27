$ErrorActionPreference = "Stop"

Write-Host "Installing dependencies..."
py -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host ".env created from .env.example"
        Write-Host "Fill .env values, then re-run this script."
        exit 0
    }
    else {
        Write-Error ".env.example not found."
    }
}

Write-Host "Starting forwarder..."
py main.py
