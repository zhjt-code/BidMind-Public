# Start BidMind API and ARQ worker groups with the repository venv.
#
# This script standardizes production startup commands only. It does not
# implement restart loops or kill existing processes. Use Windows Service,
# NSSM, systemd, Docker Compose, or another supervisor for auto-restart.

param(
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8000,
    [switch]$SkipApi,
    [switch]$SkipProcessWorker,
    [switch]$SkipLlmWorker,
    [switch]$SkipMaintenanceWorker
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$backendDir = Join-Path $repoRoot "backend"
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
$workerScript = Join-Path $backendDir "scripts\start_arq_worker.ps1"

if (-not (Test-Path $pythonExe)) {
    throw "Python interpreter not found: $pythonExe"
}

if (-not (Test-Path $workerScript)) {
    throw "Worker startup script not found: $workerScript"
}

function Start-BidMindProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    Write-Host "Starting $Name..."
    Start-Process `
        -FilePath "powershell.exe" `
        -WorkingDirectory $backendDir `
        -ArgumentList @(
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            $Command
        )
}

if (-not $SkipApi) {
    $apiCommand = "& '$pythonExe' -m uvicorn app.main:app --host $BindHost --port $Port"
    Start-BidMindProcess -Name "BidMind API" -Command $apiCommand
}

if (-not $SkipProcessWorker) {
    $processCommand = "& '$workerScript' app.worker.ProcessWorkerSettings"
    Start-BidMindProcess -Name "Process Worker" -Command $processCommand
}

if (-not $SkipLlmWorker) {
    $llmCommand = "& '$workerScript' app.worker.LlmWorkerSettings"
    Start-BidMindProcess -Name "LLM Worker" -Command $llmCommand
}

if (-not $SkipMaintenanceWorker) {
    $maintenanceCommand = "& '$workerScript' app.worker.MaintenanceWorkerSettings"
    Start-BidMindProcess -Name "Maintenance Worker" -Command $maintenanceCommand
}

Write-Host "Startup commands submitted. Use a production supervisor for restart policy."
