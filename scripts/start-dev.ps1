param(
    [string]$Python = "D:\anaconda3\envs\concord\python.exe",
    [switch]$WithInfrastructure,
    [switch]$BackendOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$RuntimeRoot = Join-Path $BackendRoot "data\dev-processes"
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}
if ($WithInfrastructure) {
    docker compose --project-directory $BackendRoot -f (Join-Path $BackendRoot "docker-compose.yml") up -d redis
    if ($LASTEXITCODE -ne 0) { throw "Failed to start optional infrastructure." }
}

$Backend = Start-Process -FilePath $Python `
    -ArgumentList "main.py" `
    -WorkingDirectory $BackendRoot `
    -RedirectStandardOutput (Join-Path $RuntimeRoot "backend.stdout.log") `
    -RedirectStandardError (Join-Path $RuntimeRoot "backend.stderr.log") `
    -WindowStyle Hidden `
    -PassThru
$Backend.Id | Set-Content -LiteralPath (Join-Path $RuntimeRoot "backend.pid")

if (-not $BackendOnly) {
    $FrontendRoot = Join-Path $ProjectRoot "frontend"
    $Frontend = Start-Process -FilePath "npm.cmd" `
        -ArgumentList "run", "dev" `
        -WorkingDirectory $FrontendRoot `
        -RedirectStandardOutput (Join-Path $RuntimeRoot "frontend.stdout.log") `
        -RedirectStandardError (Join-Path $RuntimeRoot "frontend.stderr.log") `
        -WindowStyle Hidden `
        -PassThru
    $Frontend.Id | Set-Content -LiteralPath (Join-Path $RuntimeRoot "frontend.pid")
}

Write-Host "Concord started. API: http://127.0.0.1:8000  Console: http://127.0.0.1:5173"
Write-Host "Logs: $RuntimeRoot"
