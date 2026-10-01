param(
    [string]$Python = "D:\anaconda3\envs\concord\python.exe",
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [switch]$Live
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

Push-Location $BackendRoot
try {
    if ($Live) {
        Invoke-RestMethod "$BaseUrl/health" | Out-Null
        $ArtifactRoot = Join-Path $BackendRoot "data\m8b-demo-live"
        & $Python -m evaluation.m4_end_to_end `
            --base-url $BaseUrl `
            --episode-id specialist_recovery_after_tool_failures__self_correction `
            --concurrency 1 `
            --output-dir $ArtifactRoot `
            --run-id m8b-live-demo
        if ($LASTEXITCODE -ne 0) { throw "Live demonstration failed." }
        $Artifact = Join-Path $ArtifactRoot "raw_runs.jsonl"
        $RuntimeDatabase = Join-Path $BackendRoot "data\concord_runtime.sqlite3"
        & $Python -m evaluation.m8_productization.showcase `
            --artifact $Artifact `
            --runtime-db $RuntimeDatabase `
            --require-pass
    }
    else {
        $Artifact = Join-Path $BackendRoot "evaluation\m8_productization\results\m8_b\final\raw_runs.jsonl"
        & $Python -m evaluation.m8_productization.showcase `
            --artifact $Artifact `
            --require-pass
    }
    if ($LASTEXITCODE -ne 0) { throw "Showcase validation failed." }
}
finally {
    Pop-Location
}
