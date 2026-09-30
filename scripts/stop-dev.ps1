param(
    [switch]$StopInfrastructure
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$RuntimeRoot = Join-Path $BackendRoot "data\dev-processes"

foreach ($name in @("backend", "frontend")) {
    $pidPath = Join-Path $RuntimeRoot "$name.pid"
    if (-not (Test-Path -LiteralPath $pidPath)) {
        continue
    }

    $processId = [int](Get-Content -LiteralPath $pidPath -Raw)
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        Stop-Process -Id $processId
        Write-Host "Stopped $name process $processId."
    }
    Remove-Item -LiteralPath $pidPath -Force
}

if ($StopInfrastructure) {
    docker compose --project-directory $BackendRoot `
        -f (Join-Path $BackendRoot "docker-compose.yml") stop redis
    if ($LASTEXITCODE -ne 0) { throw "Failed to stop optional Redis infrastructure." }
}
