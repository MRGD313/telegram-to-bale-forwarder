# Run before first git push. Fails if secrets or runtime files would be committed.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$forbidden = @(
    ".env", ".env.private", ".env.public", ".env.mofidot",
    ".env.test.public", ".env.test.private",
    "session.session", "state.db", "state_private.db"
)
$bad = @()
foreach ($f in $forbidden) {
    if (Test-Path $f) {
        $st = git status --porcelain $f 2>$null
        if ($st -match '^\?\?|^A |^M ') { $bad += $f }
    }
}

Write-Host "=== Secret pattern scan (tracked files only) ===" -ForegroundColor Cyan
$hits = git grep -n -E "API_HASH=[a-f0-9]{16,}|BALE_BOT_TOKEN=[0-9]+:" -- ':!*.example' ':!docs/*' 2>$null
if ($hits) {
    Write-Host $hits -ForegroundColor Red
    $bad += "pattern-match"
} else {
    Write-Host "No obvious hardcoded tokens in tracked files." -ForegroundColor Green
}

if ($bad.Count) {
    Write-Host "`nFAIL: Do not commit:" -ForegroundColor Red
    $bad | ForEach-Object { Write-Host "  $_" }
    exit 1
}
Write-Host "`nPASS: Safe to push (still review 'git add' manually)." -ForegroundColor Green
