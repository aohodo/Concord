param(
    [string]$Python = "D:\anaconda3\envs\concord\python.exe",
    [switch]$SkipFrontend,
    [switch]$SkipSecurity
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

Push-Location $ProjectRoot
try {
    & $Python -m pytest backend/tests -q
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }

    & $Python -m pytest evaluation/v2_interaction/tests -q
    if ($LASTEXITCODE -ne 0) { throw "Interaction evaluation tests failed." }

    & $Python -m ruff check backend evaluation/v2_interaction
    if ($LASTEXITCODE -ne 0) { throw "Python lint failed." }

    & $Python -m pip_audit -r backend/requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Python dependency audit failed." }

    if (-not $SkipSecurity) {
        & $Python backend/evaluation/m8_productization/public_safety.py --root $ProjectRoot
        if ($LASTEXITCODE -ne 0) { throw "Public safety scan failed." }
    }

    if (-not $SkipFrontend) {
        Push-Location (Join-Path $ProjectRoot "frontend")
        try {
            npm ci
            if ($LASTEXITCODE -ne 0) { throw "Frontend dependency install failed." }
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }

            npm audit --audit-level=high
            if ($LASTEXITCODE -ne 0) { throw "Frontend dependency audit failed." }
        }
        finally {
            Pop-Location
        }
    }

    docker compose -f backend/docker-compose.yml config -q
    if ($LASTEXITCODE -ne 0) { throw "Backend Compose validation failed." }

    docker compose -f frontend/docker-compose.yml config -q
    if ($LASTEXITCODE -ne 0) { throw "Frontend Compose validation failed." }
}
finally {
    Pop-Location
}
