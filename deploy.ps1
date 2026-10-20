<#
.SYNOPSIS
    BidMind AI Lite Cloud Deployment Script
.DESCRIPTION
    Maintainer-oriented Alibaba Cloud ECS reference deployment. This is not a
    provider-neutral installer; read docs/production-deployment.md and review
    every path, host, service, and synchronization policy before use.

    Auto build frontend, clean backend, upload to Alibaba Cloud ECS.
    Every successful deploy path also exports, uploads, and remotely imports the
    local LLM Center registry snapshot (llm_* config tables) so cloud UI matches
    local, except -AllPreserveCloud which leaves cloud llm_* tables unchanged.
    Does not run auto_publish (that would overwrite imported DB versions).
.PARAMETER ServerIP
    ECS public IP (required)
.PARAMETER Frontend
    Deploy frontend (build + upload dist)
.PARAMETER Backend
    Deploy backend (clean + upload app + scripts + requirements.txt)
.PARAMETER App
    Deploy frontend + backend together (high-frequency path, excludes OCR)
.PARAMETER Infra
    Deploy infrastructure files (docker-compose.prod.yml + nginx)
.PARAMETER OCR
    Deploy OCR sidecar files
.PARAMETER AISettings
    Deploy AI model settings (storage/ai_settings.json) to server
.PARAMETER AllNoAISettings
    Deploy everything except AI model settings, preserving server-side AI config
.PARAMETER AllPreserveCloud
    Same as -AllNoAISettings, and also skip LLM Center export/upload/import
.PARAMETER All
    Deploy everything (AI settings are included only if local file exists)
.PARAMETER RemoteUser
    SSH login user (default: ecs-user, the account Aliyun creates by default)
.PARAMETER KeyPath
    Path to SSH private key (default: ~\.ssh\bidmind-prod-key.pem)
.EXAMPLE
    .\deploy.ps1 -ServerIP "203.0.113.10" -Frontend
    .\deploy.ps1 -ServerIP "203.0.113.10" -Backend
    .\deploy.ps1 -ServerIP "203.0.113.10" -App
    .\deploy.ps1 -ServerIP "203.0.113.10" -Infra
    .\deploy.ps1 -ServerIP "203.0.113.10" -OCR
    .\deploy.ps1 -ServerIP "203.0.113.10" -AISettings
    .\deploy.ps1 -ServerIP "203.0.113.10" -AllNoAISettings
    .\deploy.ps1 -ServerIP "203.0.113.10" -AllPreserveCloud
    .\deploy.ps1 -ServerIP "203.0.113.10" -All
    # Custom user and key
    .\deploy.ps1 -ServerIP "203.0.113.10" -RemoteUser "ops" -KeyPath "C:\keys\my.pem" -All
#>

param(
    [Parameter(Mandatory = $true, HelpMessage = "ECS public IP address")]
    [string]$ServerIP,

    [string]$RemoteUser = "ecs-user",

    [string]$KeyPath = "$env:USERPROFILE\.ssh\bidmind-prod-key.pem",

    [switch]$Frontend,
    [switch]$Backend,
    [switch]$App,
    # Keep the old -Config flag as an alias so existing commands do not break.
    [Alias("Config")]
    [switch]$Infra,
    [switch]$OCR,
    [switch]$AISettings,
    [switch]$AllNoAISettings,
    [switch]$AllPreserveCloud,
    [switch]$All
)

# ==================== Script Settings ====================
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RemotePath = "/opt/bidmind"
$RemoteTarget = "${RemoteUser}@${ServerIP}"

# Check SSH key (must exist, no password fallback in current security model)
$hasSSHKey = Test-Path $KeyPath

# Common SSH/SCP args used by every remote call.
# PowerShell splatting: pass with @SshArgs to expand the array.
$SshArgs = @(
    "-i", $KeyPath,
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10",
    "-o", "BatchMode=yes"
)

# ==================== Utility Functions ====================
function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host ">>> $Message" -ForegroundColor Cyan
}

function Write-OK {
    param([string]$Message)
    Write-Host "    [OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "    [WARN] $Message" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    Write-Host "    [FAIL] $Message" -ForegroundColor Red
}

function Test-SSHConnection {
    Write-Step "Verifying SSH connection..."
    Write-Host "    Target: $RemoteTarget" -ForegroundColor Gray
    Write-Host "    Key:    $KeyPath" -ForegroundColor Gray
    Write-Host ""

    ssh @SshArgs $RemoteTarget "echo CONNECTION_OK"

    if ($LASTEXITCODE -eq 0) {
        Write-OK "SSH connection verified"
        return $true
    }
    else {
        Write-Fail "SSH connection failed. Please check:"
        Write-Host "    1. Is IP correct? $ServerIP" -ForegroundColor Gray
        Write-Host "    2. Does key exist? $KeyPath" -ForegroundColor Gray
        Write-Host "    3. Is the key authorized for $RemoteUser on the server?" -ForegroundColor Gray
        Write-Host "       (Aliyun ECS auto-imports the key chosen at instance creation)" -ForegroundColor Gray
        Write-Host "    4. Is port 22 open in security group + allowed for your IP?" -ForegroundColor Gray
        return $false
    }
}

# ==================== Frontend Deploy ====================
function Deploy-Frontend {
    Write-Step "Deploying frontend..."

    Write-Step "Building frontend production..."
    Push-Location "$ProjectRoot\frontend"

    if (-not (Test-Path "node_modules")) {
        Write-Warn "node_modules not found, installing..."
        npm install
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "npm install failed"
            Pop-Location
            return $false
        }
    }

    # 保留 npm/tsc 原始输出；失败时再打摘要，避免终端只剩 [FAIL]
    npm run build
    $frontendBuildExit = $LASTEXITCODE
    if ($frontendBuildExit -ne 0) {
        Write-Fail "Frontend build failed (exit=$frontendBuildExit; see tsc/vite errors above)"
        Pop-Location
        return $false
    }
    Pop-Location
    Write-OK "Frontend build done"

    $distPath = "$ProjectRoot\frontend\dist"
    if (-not (Test-Path $distPath)) {
        Write-Fail "dist not found: $distPath"
        return $false
    }

    $fileCount = (Get-ChildItem -Recurse $distPath -File).Count
    Write-OK "dist contains $fileCount files"

    Write-Step "Uploading frontend dist..."
    ssh @SshArgs $RemoteTarget "rm -rf ${RemotePath}/frontend/dist && mkdir -p ${RemotePath}/frontend"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Prepare remote frontend directory failed"
        return $false
    }
    scp @SshArgs -r "${distPath}" "${RemoteTarget}:${RemotePath}/frontend/"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Frontend upload failed"
        return $false
    }
    Write-OK "Frontend upload done"
    return $true
}

# ==================== Backend Deploy ====================
function Deploy-Backend {
    Write-Step "Deploying backend..."

    $TempDir = "$env:TEMP\bidmind-deploy-$(Get-Date -Format 'yyyyMMddHHmmss')"
    New-Item -ItemType Directory -Path "$TempDir\backend" -Force | Out-Null

    Write-Step "Cleaning backend code..."

    Write-Host "    Copying app/ ..."
    Copy-Item -Recurse "$ProjectRoot\backend\app" "$TempDir\backend\app"

    Write-Host "    Copying scripts/ (production only)..."
    # 先显式建目录, 再用通配符把内容拷进去, 避免 PowerShell Copy-Item -Recurse
    # 在 dest 父目录已存在时的"目录嵌套或静默失败"陷阱 (尤其当 __pycache__
    # 子目录被 IDE/Python 进程锁住时, Copy-Item 会静默漏拷却不抛错)。
    # 复制完成后必须做 Test-Path + 计数双重校验, 否则失败会被一路带到 scp 才暴露。
    New-Item -ItemType Directory -Path "$TempDir\backend\scripts" -Force | Out-Null
    Copy-Item -Recurse -Force "$ProjectRoot\backend\scripts\*" "$TempDir\backend\scripts\" -ErrorAction SilentlyContinue

    if (-not (Test-Path "$TempDir\backend\scripts")) {
        Write-Fail "scripts/ staging directory creation failed: $TempDir\backend\scripts"
        Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
        return $false
    }

    Get-ChildItem -Path "$TempDir\backend\scripts" -Recurse -File -Include "*.ps1" |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path "$TempDir\backend\scripts" -Recurse -Directory -Filter "__pycache__" |
        ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }

    $scriptCount = (Get-ChildItem -Path "$TempDir\backend\scripts" -Recurse -File -Filter "*.py" -ErrorAction SilentlyContinue).Count
    Write-Host "    Included production Python scripts: $scriptCount"

    if ($scriptCount -lt 1) {
        Write-Fail "scripts/ copy produced 0 .py files. Source: $ProjectRoot\backend\scripts"
        Write-Host "    Possible causes:" -ForegroundColor Gray
        Write-Host "      - Source directory has no .py files (verify with: ls backend\scripts\*.py)" -ForegroundColor Gray
        Write-Host "      - __pycache__ being held open by IDE / venv (close editors and retry)" -ForegroundColor Gray
        Write-Host "      - Antivirus quarantine on temp directory" -ForegroundColor Gray
        Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
        return $false
    }

    # migrations_legacy/ is no longer deployed -- all schema changes go through Alembic.

    Write-Host "    Copying migrations_alembic/ ..."
    if (Test-Path "$ProjectRoot\backend\migrations_alembic") {
        Copy-Item -Recurse "$ProjectRoot\backend\migrations_alembic" "$TempDir\backend\migrations_alembic"
    }

    if (Test-Path "$ProjectRoot\backend\alembic.ini") {
        Write-Host "    Copying alembic.ini ..."
        Copy-Item "$ProjectRoot\backend\alembic.ini" "$TempDir\backend\"
    }

    Write-Host "    Copying requirements.txt ..."
    Copy-Item "$ProjectRoot\backend\requirements.txt" "$TempDir\backend\"

    if (Test-Path "$ProjectRoot\backend\Dockerfile") {
        Write-Host "    Copying Dockerfile ..."
        Copy-Item "$ProjectRoot\backend\Dockerfile" "$TempDir\backend\"
    }

    if (Test-Path "$ProjectRoot\backend\.dockerignore") {
        Write-Host "    Copying .dockerignore ..."
        Copy-Item "$ProjectRoot\backend\.dockerignore" "$TempDir\backend\"
    }

    Write-Host "    Removing __pycache__ ..."
    Get-ChildItem -Path "$TempDir" -Recurse -Directory -Filter "__pycache__" |
        ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }

    Get-ChildItem -Path "$TempDir" -Recurse -Include "*.pyc", "*.pyo" |
        Remove-Item -Force -ErrorAction SilentlyContinue

    $fileCount = (Get-ChildItem -Recurse "$TempDir\backend" -File).Count
    $sizeKB = [math]::Round((Get-ChildItem -Recurse "$TempDir\backend" -File | Measure-Object -Property Length -Sum).Sum / 1KB)
    Write-OK "Clean done: $fileCount files, ~${sizeKB}KB"

    Write-Step "Included: app/ scripts/ migrations_alembic/ alembic.ini requirements.txt Dockerfile .dockerignore"
    Write-Step "Excluded files:"
    Write-Host "    - .env / .env.example (secrets)" -ForegroundColor Gray
    Write-Host "    - tests/ (test code)" -ForegroundColor Gray
    Write-Host "    - README.md (docs)" -ForegroundColor Gray
    Write-Host "    - __pycache__/ (Python cache)" -ForegroundColor Gray
    Write-Host "    - storage/ (local data)" -ForegroundColor Gray
    Write-Host "    - scripts/*.ps1 (local/dev helper scripts)" -ForegroundColor Gray

    Write-Step "Uploading backend code to staging..."
    $remoteStage = "/tmp/bidmind-backend-upload-$(Get-Date -Format 'yyyyMMddHHmmss')"
    ssh @SshArgs $RemoteTarget "rm -rf ${remoteStage} && mkdir -p ${remoteStage}"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Prepare remote backend staging directory failed"
        Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
        return $false
    }

    $uploadFailed = $false
    $appPath = (Resolve-Path "${TempDir}\backend\app").Path -replace '\\', '/'
    $scriptsPath = (Resolve-Path "${TempDir}\backend\scripts").Path -replace '\\', '/'
    $migrationsPath = (Resolve-Path "${TempDir}\backend\migrations_alembic").Path -replace '\\', '/'
    $alembicPath = (Resolve-Path "${TempDir}\backend\alembic.ini").Path -replace '\\', '/'
    $requirementsPath = (Resolve-Path "${TempDir}\backend\requirements.txt").Path -replace '\\', '/'
    $dockerfilePath = (Resolve-Path "${TempDir}\backend\Dockerfile").Path -replace '\\', '/'
    $dockerignorePath = (Resolve-Path "${TempDir}\backend\.dockerignore").Path -replace '\\', '/'

    scp @SshArgs -r $appPath "${RemoteTarget}:${remoteStage}/"
    if ($LASTEXITCODE -ne 0) { Write-Fail "Upload app/ to staging failed"; $uploadFailed = $true }

    scp @SshArgs -r $scriptsPath "${RemoteTarget}:${remoteStage}/"
    if ($LASTEXITCODE -ne 0) { Write-Fail "Upload scripts/ to staging failed"; $uploadFailed = $true }

    scp @SshArgs -r $migrationsPath "${RemoteTarget}:${remoteStage}/"
    if ($LASTEXITCODE -ne 0) { Write-Fail "Upload migrations_alembic/ to staging failed"; $uploadFailed = $true }

    scp @SshArgs $alembicPath $requirementsPath $dockerfilePath $dockerignorePath "${RemoteTarget}:${remoteStage}/"
    if ($LASTEXITCODE -ne 0) { Write-Fail "Upload backend root files to staging failed"; $uploadFailed = $true }

    if ($uploadFailed) {
        ssh @SshArgs $RemoteTarget "rm -rf ${remoteStage}" | Out-Null
        Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
        Write-Fail "Backend upload had errors"
        return $false
    }

    Write-Step "Activating backend staging files..."
    ssh @SshArgs $RemoteTarget "mkdir -p ${RemotePath}/backend && rm -rf ${RemotePath}/backend/app ${RemotePath}/backend/scripts ${RemotePath}/backend/migrations_alembic ${RemotePath}/backend/alembic.ini ${RemotePath}/backend/requirements.txt ${RemotePath}/backend/Dockerfile ${RemotePath}/backend/.dockerignore && mv ${remoteStage}/app ${RemotePath}/backend/app && mv ${remoteStage}/scripts ${RemotePath}/backend/scripts && mv ${remoteStage}/migrations_alembic ${RemotePath}/backend/migrations_alembic && mv ${remoteStage}/alembic.ini ${RemotePath}/backend/alembic.ini && mv ${remoteStage}/requirements.txt ${RemotePath}/backend/requirements.txt && mv ${remoteStage}/Dockerfile ${RemotePath}/backend/Dockerfile && mv ${remoteStage}/.dockerignore ${RemotePath}/backend/.dockerignore && rm -rf ${remoteStage}"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Activate backend staging files failed"
        Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
        return $false
    }

    Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue

    Write-OK "Backend upload done"
    return $true
}

# ==================== Infra Deploy ====================
function Deploy-Infra {
    Write-Step "Deploying infrastructure files..."
    $uploadFailed = $false

    if (Test-Path "${ProjectRoot}\docker-compose.prod.yml") {
        Write-Host "    Uploading docker-compose.prod.yml ..."
        scp @SshArgs "${ProjectRoot}\docker-compose.prod.yml" "${RemoteTarget}:${RemotePath}/"
        if ($LASTEXITCODE -ne 0) { Write-Fail "Upload docker-compose.prod.yml failed"; $uploadFailed = $true }
        else { Write-OK "docker-compose.prod.yml" }
    }
    else {
        Write-Warn "docker-compose.prod.yml not found"
    }

    if (Test-Path "${ProjectRoot}\nginx") {
        Write-Host "    Uploading nginx/ ..."
        ssh @SshArgs $RemoteTarget "mkdir -p ${RemotePath}/nginx"
        if ($LASTEXITCODE -ne 0) { Write-Fail "Prepare remote nginx directory failed"; $uploadFailed = $true }
        scp @SshArgs "${ProjectRoot}\nginx\nginx.conf" "${RemoteTarget}:${RemotePath}/nginx/"
        if ($LASTEXITCODE -ne 0) { Write-Fail "Upload nginx.conf failed"; $uploadFailed = $true }
        scp @SshArgs "${ProjectRoot}\nginx\Dockerfile" "${RemoteTarget}:${RemotePath}/nginx/"
        if ($LASTEXITCODE -ne 0) { Write-Fail "Upload nginx Dockerfile failed"; $uploadFailed = $true }
        if (-not $uploadFailed) { Write-OK "nginx config files" }
    }
    else {
        Write-Warn "nginx/ directory not found"
    }

    if ($uploadFailed) {
        Write-Fail "Infrastructure upload had errors"
        return $false
    }

    Write-OK "Infrastructure deploy done"
    return $true
}

# ==================== OCR Deploy ====================
function Deploy-OCR {
    Write-Step "Deploying OCR sidecar files..."
    $uploadFailed = $false

    if (Test-Path "${ProjectRoot}\ocr") {
        Write-Host "    Uploading ocr/ ..."
        ssh @SshArgs $RemoteTarget "rm -rf ${RemotePath}/ocr && mkdir -p ${RemotePath}/ocr"
        if ($LASTEXITCODE -ne 0) { Write-Fail "Prepare remote ocr directory failed"; $uploadFailed = $true }
        scp @SshArgs "${ProjectRoot}\ocr\Dockerfile" "${RemoteTarget}:${RemotePath}/ocr/"
        if ($LASTEXITCODE -ne 0) { Write-Fail "Upload OCR Dockerfile failed"; $uploadFailed = $true }
        scp @SshArgs "${ProjectRoot}\ocr\main.py" "${RemoteTarget}:${RemotePath}/ocr/"
        if ($LASTEXITCODE -ne 0) { Write-Fail "Upload OCR main.py failed"; $uploadFailed = $true }
        scp @SshArgs "${ProjectRoot}\ocr\requirements.txt" "${RemoteTarget}:${RemotePath}/ocr/"
        if ($LASTEXITCODE -ne 0) { Write-Fail "Upload OCR requirements.txt failed"; $uploadFailed = $true }
        foreach ($sidecarFile in @("structure_engine.py", "structure_blocks.py", "cpu_quota.py")) {
            scp @SshArgs "${ProjectRoot}\ocr\$sidecarFile" "${RemoteTarget}:${RemotePath}/ocr/"
            if ($LASTEXITCODE -ne 0) { Write-Fail "Upload OCR $sidecarFile failed"; $uploadFailed = $true }
        }
        if (-not $uploadFailed) { Write-OK "ocr sidecar files" }
    }
    else {
        Write-Warn "ocr/ directory not found"
    }

    if ($uploadFailed) {
        Write-Fail "OCR upload had errors"
        return $false
    }

    Write-OK "OCR deploy done"
    return $true
}

# ==================== AI Settings Deploy ====================
function Deploy-AISettings {
    Write-Step "Deploying AI settings..."

    $settingsFile = "$ProjectRoot\backend\storage\ai_settings.json"
    if (-not (Test-Path $settingsFile)) {
        Write-Warn "ai_settings.json not found at: $settingsFile"
        if ($AISettings -and -not $All) {
            Write-Warn "Configure AI models in the frontend Settings page first"
            return $false
        }

        # Allow first-time full deploy to proceed before AI settings are exported from UI.
        Write-Warn "Skipping AI settings deploy during -All"
        return $true
    }

    $sizeKB = [math]::Round((Get-Item $settingsFile).Length / 1KB, 1)
    Write-Host "    Source: $settingsFile (~${sizeKB}KB)" -ForegroundColor Gray

    ssh @SshArgs $RemoteTarget "mkdir -p ${RemotePath}/storage"

    # Backup existing server config before overwrite
    ssh @SshArgs $RemoteTarget "if [ -f ${RemotePath}/storage/ai_settings.json ]; then cp ${RemotePath}/storage/ai_settings.json ${RemotePath}/storage/ai_settings.json.bak.`$(date +%%Y%%m%%d%%H%%M%%S); echo BACKUP_CREATED; else echo NO_EXISTING; fi"

    scp @SshArgs "$settingsFile" "${RemoteTarget}:${RemotePath}/storage/"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "AI settings upload failed"
        return $false
    }

    Write-OK "AI settings deployed to ${RemotePath}/storage/ai_settings.json"
    return $true
}

# ==================== LLM Center Registry (default on any deploy) ====================
function Deploy-LlmCenterRegistry {
    # Export local LLM Center config tables and upload snapshot.
    # Runs by default after any successful deploy target selection.
    Write-Step "Deploying LLM Center registry snapshot (default)..."

    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Write-Fail "Local venv python not found: $venvPython"
        Write-Host "    Create/activate project .venv before deploy." -ForegroundColor Gray
        return $false
    }

    $outFile = Join-Path $ProjectRoot "backend\storage\llm_center_registry.json.gz"
    $outDir = Split-Path $outFile -Parent
    if (-not (Test-Path $outDir)) {
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    }

    Write-Host "    Exporting local llm_* tables via llm_center_registry_sync.py ..." -ForegroundColor Gray
    & $venvPython (Join-Path $ProjectRoot "backend\scripts\llm_center_registry_sync.py") export --output $outFile
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "LLM Center registry export failed"
        return $false
    }
    if (-not (Test-Path $outFile)) {
        Write-Fail "Export succeeded but snapshot missing: $outFile"
        return $false
    }

    $sizeKB = [math]::Round((Get-Item $outFile).Length / 1KB, 1)
    Write-Host "    Snapshot: $outFile (~${sizeKB}KB)" -ForegroundColor Gray

    ssh @SshArgs $RemoteTarget "mkdir -p ${RemotePath}/storage"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Prepare remote storage directory failed"
        return $false
    }

    scp @SshArgs $outFile "${RemoteTarget}:${RemotePath}/storage/llm_center_registry.json.gz"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "LLM Center registry upload failed"
        return $false
    }

    Write-OK "LLM Center registry uploaded to ${RemotePath}/storage/llm_center_registry.json.gz"
    return $true
}

function Import-LlmCenterRegistry {
    # 职责: 把已上传的本地 llm_* 快照写入云端库并重启消费进程。
    # 不做: 不重建镜像、不发布代码注册源草稿、不覆盖 ai_settings。
    Write-Step "Importing LLM Center registry into cloud database..."

    $scriptLocal = Join-Path $ProjectRoot "backend\scripts\llm_center_registry_sync.py"
    if (-not (Test-Path $scriptLocal)) {
        Write-Fail "Local sync script missing: $scriptLocal"
        return $false
    }

    ssh @SshArgs $RemoteTarget "mkdir -p ${RemotePath}/backend/scripts"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Prepare remote scripts directory failed"
        return $false
    }

    scp @SshArgs $scriptLocal "${RemoteTarget}:${RemotePath}/backend/scripts/llm_center_registry_sync.py"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Upload llm_center_registry_sync.py failed"
        return $false
    }

    Write-Host "    docker cp script + import --yes + restart workers ..." -ForegroundColor Gray
    ssh @SshArgs $RemoteTarget "set -e; cd ${RemotePath}; cid=`$(docker compose -f docker-compose.prod.yml ps -q backend); if [ -z `"`$cid`" ]; then echo BACKEND_CONTAINER_NOT_RUNNING; exit 2; fi; docker cp ${RemotePath}/backend/scripts/llm_center_registry_sync.py `$cid:/app/scripts/llm_center_registry_sync.py; docker compose -f docker-compose.prod.yml exec -T backend python scripts/llm_center_registry_sync.py import --input /app/storage/llm_center_registry.json.gz --yes; docker compose -f docker-compose.prod.yml restart backend worker-process worker-llm worker-maintenance"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "LLM Center registry import failed"
        Write-Host "    backend 容器必须已在运行。若刚做首次 up，先在服务器 build/up，再重新执行 deploy.ps1。" -ForegroundColor Gray
        Write-Host "    不要用代码注册源发布来代替表导入。" -ForegroundColor Gray
        return $false
    }

    Write-OK "LLM Center registry imported; backend/workers restarted"
    return $true
}

# ==================== Main ====================
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  BidMind AI Lite - Cloud Deploy Tool" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Server: $ServerIP"
Write-Host "  Project: $ProjectRoot"
Write-Host "==========================================" -ForegroundColor Cyan

if (-not $hasSSHKey) {
    Write-Host ""
    Write-Host "  [FAIL] SSH private key not found at: $KeyPath" -ForegroundColor Red
    Write-Host "  This script no longer supports password login (security baseline)." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Fix it with one of:" -ForegroundColor Gray
    Write-Host "    1. Place your Aliyun-issued .pem at $KeyPath" -ForegroundColor Gray
    Write-Host "    2. Pass an explicit path:" -ForegroundColor Gray
    Write-Host "       .\deploy.ps1 -ServerIP $ServerIP -KeyPath C:\path\to\key.pem -All" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  On Windows, ensure the .pem permissions are tight:" -ForegroundColor Gray
    Write-Host "    icacls `"$KeyPath`" /inheritance:r /grant:r `"`$(`$env:USERNAME):R`"" -ForegroundColor Gray
    exit 1
}

if (-not ($Frontend -or $Backend -or $App -or $Infra -or $OCR -or $AISettings -or $AllNoAISettings -or $AllPreserveCloud -or $All)) {
    Write-Host ""
    Write-Host "Please specify what to deploy:" -ForegroundColor Yellow
    Write-Host "  -Frontend    Deploy frontend (build + upload)" -ForegroundColor White
    Write-Host "  -Backend     Deploy backend (clean + upload)" -ForegroundColor White
    Write-Host "  -App         Deploy frontend + backend (common path, excludes OCR)" -ForegroundColor White
    Write-Host "  -Infra       Deploy docker-compose + nginx files" -ForegroundColor White
    Write-Host "  -OCR         Deploy OCR sidecar files" -ForegroundColor White
    Write-Host "  -AISettings  Deploy AI model settings (ai_settings.json)" -ForegroundColor White
    Write-Host "  -AllNoAISettings  Deploy everything except AI settings (still syncs LLM Center)" -ForegroundColor White
    Write-Host "  -AllPreserveCloud  Deploy everything except AI settings and LLM Center" -ForegroundColor White
    Write-Host "  -All         Deploy everything (AI settings if local file exists)" -ForegroundColor White
    Write-Host ""
    Write-Host "Examples:" -ForegroundColor Gray
    Write-Host "  .\deploy.ps1 -ServerIP '203.0.113.10' -Frontend" -ForegroundColor Gray
    Write-Host "  .\deploy.ps1 -ServerIP '203.0.113.10' -Backend" -ForegroundColor Gray
    Write-Host "  .\deploy.ps1 -ServerIP '203.0.113.10' -App" -ForegroundColor Gray
    Write-Host "  .\deploy.ps1 -ServerIP '203.0.113.10' -Infra" -ForegroundColor Gray
    Write-Host "  .\deploy.ps1 -ServerIP '203.0.113.10' -OCR" -ForegroundColor Gray
    Write-Host "  .\deploy.ps1 -ServerIP '203.0.113.10' -AISettings" -ForegroundColor Gray
    Write-Host "  .\deploy.ps1 -ServerIP '203.0.113.10' -AllNoAISettings" -ForegroundColor Gray
    Write-Host "  .\deploy.ps1 -ServerIP '203.0.113.10' -AllPreserveCloud" -ForegroundColor Gray
    Write-Host "  .\deploy.ps1 -ServerIP '203.0.113.10' -All" -ForegroundColor Gray
    exit 0
}

if ($AllNoAISettings -and ($All -or $AISettings -or $AllPreserveCloud)) {
    Write-Host ""
    Write-Fail "-AllNoAISettings cannot be combined with -All, -AISettings, or -AllPreserveCloud"
    Write-Host "    Use -AllNoAISettings to preserve server-side AI config while syncing LLM Center." -ForegroundColor Gray
    Write-Host "    Use -AllPreserveCloud to also leave cloud LLM Center unchanged." -ForegroundColor Gray
    Write-Host "    Use -All or -AISettings only when you intentionally overwrite server AI config." -ForegroundColor Gray
    exit 1
}

if ($AllPreserveCloud -and ($All -or $AISettings)) {
    Write-Host ""
    Write-Fail "-AllPreserveCloud cannot be combined with -All or -AISettings"
    Write-Host "    Use -AllPreserveCloud to preserve server-side AI settings and LLM Center." -ForegroundColor Gray
    Write-Host "    Use -All or -AISettings only when you intentionally overwrite server AI config." -ForegroundColor Gray
    exit 1
}

# SSH test
if (-not (Test-SSHConnection)) {
    exit 1
}

# Execute deploy
$success = $true
$deployed = @()

if ($Frontend -or $App -or $AllNoAISettings -or $AllPreserveCloud -or $All) {
    if (Deploy-Frontend) { $deployed += "Frontend" }
    else { $success = $false }
}

if ($Backend -or $App -or $AllNoAISettings -or $AllPreserveCloud -or $All) {
    if (Deploy-Backend) { $deployed += "Backend" }
    else { $success = $false }
}

if ($Infra -or $AllNoAISettings -or $AllPreserveCloud -or $All) {
    if (Deploy-Infra) { $deployed += "Infra" }
    else { $success = $false }
}

if ($OCR -or $AllNoAISettings -or $AllPreserveCloud -or $All) {
    if (Deploy-OCR) { $deployed += "OCR" }
    else { $success = $false }
}

if ($AISettings -or $All) {
    if (Deploy-AISettings) { $deployed += "AISettings" }
    else { $success = $false }
}

# Default: export/upload then remotely import LLM Center registry on every successful path.
# Exception: -AllPreserveCloud leaves cloud llm_* tables unchanged.
if ($success -and $deployed.Count -gt 0) {
    if ($AllPreserveCloud) {
        Write-Step "Skipping LLM Center registry sync (-AllPreserveCloud)"
        Write-OK "Cloud llm_* tables left unchanged"
    }
    else {
        if (Deploy-LlmCenterRegistry) {
            $deployed += "LlmCenterRegistry"
            if (Import-LlmCenterRegistry) { $deployed += "LlmCenterImport" }
            else { $success = $false }
        }
        else { $success = $false }
    }
}

# ==================== Result ====================
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan

if ($success) {
    Write-Host "  Deploy done! Deployed: $($deployed -join ', ')" -ForegroundColor Green
}
else {
    Write-Host "  Deploy had errors, check logs above" -ForegroundColor Red
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next: SSH to server and restart services:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  ssh -i `"$KeyPath`" $RemoteUser@$ServerIP" -ForegroundColor White
Write-Host "  cd /opt/bidmind" -ForegroundColor White

if ($deployed -contains "Backend" -or $deployed -contains "Infra" -or $deployed -contains "OCR") {
    Write-Host ""
    Write-Host "  # Rebuild containers when backend/infra/OCR files changed:" -ForegroundColor Gray
    Write-Host '  docker compose -f docker-compose.prod.yml build' -ForegroundColor White
    Write-Host ""
    Write-Host "  # Or rebuild specific services:" -ForegroundColor Gray
    if ($deployed -contains "Backend") {
        Write-Host '  docker compose -f docker-compose.prod.yml build backend worker-process worker-llm worker-maintenance  # backend code/deps changed' -ForegroundColor DarkGray
    }
    if ($deployed -contains "Infra") {
        Write-Host '  docker compose -f docker-compose.prod.yml build nginx    # when nginx config changed' -ForegroundColor DarkGray
    }
    if ($deployed -contains "OCR") {
        Write-Host '  docker compose -f docker-compose.prod.yml build ocr-1 ocr-2 ocr-3 ocr-4 ocr-5  # when OCR image/code changed' -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "  # Restart all services:" -ForegroundColor Gray
    Write-Host '  docker compose -f docker-compose.prod.yml --env-file .env up -d' -ForegroundColor White
    Write-Host ""
    if ($deployed -contains "LlmCenterImport") {
        Write-Host "  # LLM Center tables were already imported from the local snapshot." -ForegroundColor Gray
        Write-Host "  # After rebuild/up, do not publish code-registry drafts; that overwrites imported DB versions." -ForegroundColor Gray
    }
    elseif ($AllPreserveCloud) {
        Write-Host "  # LLM Center and ai_settings.json were left unchanged on the server." -ForegroundColor Gray
        Write-Host "  # Do not publish code-registry drafts; leave cloud LLM Center as-is." -ForegroundColor Gray
    }
}
elseif ($deployed -contains "AISettings") {
    Write-Host ""
    Write-Host "  # AI settings updated, restart backend + worker services to reload:" -ForegroundColor Gray
    Write-Host '  docker compose -f docker-compose.prod.yml restart backend worker-process worker-llm worker-maintenance' -ForegroundColor White
}
elseif ($deployed -contains "Frontend") {
    Write-Host ""
    Write-Host "  # Frontend is static, just restart nginx:" -ForegroundColor Gray
    Write-Host '  docker compose -f docker-compose.prod.yml restart nginx' -ForegroundColor White
}

if ($deployed -contains "LlmCenterImport") {
    Write-Host ""
    Write-Host "  # LLM Center /admin/llm-center already matches local (snapshot imported)." -ForegroundColor Gray
}

Write-Host ""
Write-Host "  # Check status:" -ForegroundColor Gray
Write-Host '  docker compose -f docker-compose.prod.yml ps' -ForegroundColor White
Write-Host ""
Write-Host "  # View logs:" -ForegroundColor Gray
Write-Host '  docker compose -f docker-compose.prod.yml logs -f backend' -ForegroundColor White
Write-Host ""



