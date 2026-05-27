# From-zero E2E: public then private (isolated test DBs).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "=== Generate test env files ===" -ForegroundColor Cyan
py scripts/generate_test_env.py

function Run-Profile($name, $dotenv) {
    Write-Host "`n=== E2E: $name ($dotenv) ===" -ForegroundColor Cyan
    $dbKey = if ($name -eq "public") { "state_test_public.db" } else { "state_test_private.db" }
    foreach ($f in @($dbKey, "$dbKey-wal", "$dbKey-shm")) {
        if (Test-Path $f) { Remove-Item $f -Force }
    }
    $env:DOTENV_FILE = $dotenv
    $env:PYTHONUNBUFFERED = "1"
    $log = "test_log_$name.txt"
    py main.py 2>&1 | Tee-Object -FilePath $log
    if ($LASTEXITCODE -ne 0) { throw "main.py exited $LASTEXITCODE for $name" }
    py scripts/verify_test_queue.py $name
    if ($LASTEXITCODE -ne 0) { throw "verify failed for $name" }
}

Run-Profile "public" ".env.test.public"
Run-Profile "private" ".env.test.private"

Write-Host "`n=== ALL E2E TESTS PASSED ===" -ForegroundColor Green
