# BidMind AI Lite - Iteration 1-2 Production Verification Script (PowerShell)
# Usage:
#   Local (no Docker):  .\verify_iter_1_2.ps1
#   Local Docker:       .\verify_iter_1_2.ps1 -ServerUrl http://localhost:8000 -Container bidmind-backend
#   Remote:             .\verify_iter_1_2.ps1 -ServerUrl http://server.example.invalid:8000 -Container bidmind-backend

param(
    [string]$ServerUrl = "http://localhost:8000",
    [string]$Container = "",
    [string]$BackendDir = "backend"
)

$pass = 0
$fail = 0
$warn = 0

function Test-Check {
    param([string]$Label, [scriptblock]$Block)
    Write-Host ""
    Write-Host "=== $Label ===" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    try {
        & $Block
        Write-Host "[PASS] $Label" -ForegroundColor Green
        $script:pass++
    }
    catch {
        Write-Host "[FAIL] $Label -- $_" -ForegroundColor Red
        $script:fail++
    }
}

function Test-Warn {
    param([string]$Label, [string]$Msg)
    Write-Host ""
    Write-Host "=== $Label ===" -ForegroundColor Cyan
    Write-Host "[WARN] $Msg" -ForegroundColor Yellow
    $script:warn++
}

# ---- 1/7 /health ----
Test-Check "[1/7] /health (DB + service)" {
    $resp = Invoke-RestMethod -Uri "$ServerUrl/health" -ErrorAction Stop
    Write-Host "  status=$($resp.status), database=$($resp.database)"
    if ($resp.status -ne "healthy") { throw "status=$($resp.status)" }
}

# ---- 2/7 /health/live ----
Test-Check "[2/7] /health/live (liveness)" {
    $resp = Invoke-RestMethod -Uri "$ServerUrl/health/live" -ErrorAction Stop
    Write-Host "  status=$($resp.status)"
    if ($resp.status -ne "alive") { throw "status=$($resp.status)" }
}

# ---- 3/7 /health/ready ----
Test-Check "[3/7] /health/ready (readiness)" {
    $resp = Invoke-RestMethod -Uri "$ServerUrl/health/ready" -ErrorAction Stop
    Write-Host "  status=$($resp.status)"
    if ($resp.status -ne "ready") { throw "status=$($resp.status)" }
}

# ---- 4/7 /metrics ----
$metricsRaw = $null
try {
    $metricsRaw = (Invoke-WebRequest -Uri "$ServerUrl/metrics" -UseBasicParsing -ErrorAction Stop).Content
}
catch {}

if ($metricsRaw) {
    Test-Check "[4/7] /metrics (Prometheus)" {
        $lines = ($metricsRaw -split "`n").Count
        Write-Host "  /metrics lines: $lines"

        $coreMetrics = @(
            "guard_invocation_total",
            "gate_verdict_total",
            "failure_state_distribution_total",
            "cove_outcome_total",
            "judge_outcome_total",
            "writer_gate_reject_total",
            "llm_calls_total",
            "llm_tokens_total"
        )
        $hits = 0
        foreach ($m in $coreMetrics) {
            if ($metricsRaw -match "(?m)^$m") { $hits++ }
        }
        Write-Host "  core metrics matched: $hits / $($coreMetrics.Count)"
        if ($lines -lt 20) { throw "too few lines: $lines" }
        if ($hits -lt 1) { throw "zero core metrics found" }
        if ($hits -lt 5) { Write-Host "  (some counters not yet registered - normal if no traffic yet)" -ForegroundColor Yellow }
    }
}
else {
    Test-Warn "[4/7] /metrics (Prometheus)" "METRICS_ENABLED is False or /metrics not exposed. Set METRICS_ENABLED=True in .env for production."
}

# ---- 5/7 alembic current ----
Test-Check "[5/7] alembic current (migration head)" {
    if ($Container) {
        $resultLines = docker exec $Container alembic current 2>&1
    }
    else {
        Push-Location $BackendDir
        $resultLines = alembic current 2>&1
        Pop-Location
    }
    $resultText = ($resultLines | Out-String)
    $resultText -split "`n" | ForEach-Object { Write-Host "  $_" }
    if ($resultText -notmatch "head") { throw "not at head" }
}

# ---- 6/7 B5 Counters ----
if ($metricsRaw) {
    Test-Check "[6/7] B5 Counters (CoVe/Judge/WriterGate)" {
        $b5Metrics = @(
            "cove_outcome_total",
            "judge_outcome_total",
            "writer_gate_reject_total",
            "material_source_trace_outcome_total",
            "judge_inconclusive_total"
        )
        Write-Host "  B5 metric values:"
        $found = $false
        foreach ($m in $b5Metrics) {
            $matched = ($metricsRaw -split "`n") | Where-Object { $_ -match "^$m" } | Select-Object -First 3
            foreach ($line in $matched) { Write-Host "    $line"; $found = $true }
        }
        if (-not $found) { Write-Host "    (none registered yet - normal if no traffic)" -ForegroundColor Yellow }
        Write-Host "  (all zero with real traffic = check framework wiring)"
    }
}
else {
    Test-Warn "[6/7] B5 Counters (CoVe/Judge/WriterGate)" "Skipped: /metrics not available."
}

# ---- 7/7 env check ----
Test-Check "[7/7] env safety (no placeholders)" {
    $absBackend = (Resolve-Path $BackendDir -ErrorAction Stop).Path
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) "_bidmind_env_check.py"
    @'
import sys, os
sys.path.insert(0, os.environ.get("PYPATH", "."))
from app.core.config import settings
errors = []
if settings.ADMIN_PASSWORD == "<SET_ADMIN_PASSWORD>":
    errors.append("ADMIN_PASSWORD is placeholder")
jwt = getattr(settings, "JWT_SECRET_KEY", "") or ""
if "change-in-production" in jwt:
    errors.append("JWT_SECRET_KEY is placeholder")
if not getattr(settings, "METRICS_ENABLED", False):
    errors.append("METRICS_ENABLED is False (set True for production)")
if settings.LOG_LEVEL.upper() == "DEBUG":
    errors.append("LOG_LEVEL is DEBUG (should be INFO)")
if errors:
    for e in errors:
        print(f"  [!] {e}")
    raise SystemExit(1)
print("  All env checks passed")
'@ | Set-Content -Path $tmp -Encoding UTF8
    try {
        if ($Container) {
            docker cp $tmp "${Container}:/tmp/_env_check.py"
            docker exec $Container python /tmp/_env_check.py
        }
        else {
            $env:PYPATH = $absBackend
            Push-Location $absBackend
            python $tmp
            Pop-Location
            $env:PYPATH = $null
        }
        if ($LASTEXITCODE -ne 0) { throw "env check failed" }
    }
    finally {
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

# ---- Summary ----
Write-Host ""
Write-Host "==========================================" -ForegroundColor Yellow
$total = $pass + $fail + $warn
Write-Host "  Result: $pass passed / $fail failed / $warn warnings (total $total)" -ForegroundColor Yellow
Write-Host "==========================================" -ForegroundColor Yellow

if ($fail -gt 0) {
    Write-Host "  Failures found. See docs/production_iter_verification.md" -ForegroundColor Red
    exit 1
}
elseif ($warn -gt 0) {
    Write-Host "  Passed with warnings. Address before production." -ForegroundColor Yellow
    exit 0
}
else {
    Write-Host "  All passed. Ready for iteration 3-4 live testing." -ForegroundColor Green
    exit 0
}
