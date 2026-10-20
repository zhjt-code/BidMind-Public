# Start ARQ worker with correct PYTHONPATH and venv python.
#
# Why this script exists:
#   - Running "arq app.worker.WorkerSettings" directly often fails because
#     the backend directory is not automatically added to sys.path, which
#     leads to ModuleNotFoundError: No module named 'app.worker'.
#   - This script locates repo root + backend dir, exports PYTHONPATH,
#     and invokes the venv python explicitly via "python -m arq".

param(
    [Parameter(Position = 0)]
    [string]$WorkerSettings = "app.worker.WorkerSettings",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArqArgs
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Split-Path -Parent $scriptDir
$repoRoot = Split-Path -Parent $backendDir
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Python interpreter not found: $pythonExe"
}

# app.worker uses backend as import root, so PYTHONPATH must include it
# before arq tries to import the WorkerSettings module.
if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $env:PYTHONPATH = $backendDir
} else {
    $env:PYTHONPATH = "$backendDir;$env:PYTHONPATH"
}

Push-Location $backendDir
try {
    & $pythonExe -m arq $WorkerSettings @ArqArgs
} finally {
    Pop-Location
}
