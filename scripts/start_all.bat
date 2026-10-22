@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: ============================================================
:: BidMind AI Lite - 全服务启动脚本
:: ============================================================

set "PROJECT_ROOT=%~dp0.."
set "BACKEND_DIR=%PROJECT_ROOT%\backend"
set "FRONTEND_DIR=%PROJECT_ROOT%\frontend"
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"

echo.
echo ============================================================
echo   BidMind AI Lite - E2E 测试环境启动
echo ============================================================
echo.

:: ------------------------------------------------------------
:: 检查前置条件
:: ------------------------------------------------------------
echo [1/6] 检查前置条件...

docker --version >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] Docker 未安装或未启动
    pause
    exit /b 1
)
echo       Docker: OK

if not exist "%VENV_PYTHON%" (
    echo [ERROR] Python 虚拟环境未创建
    pause
    exit /b 1
)
echo       Python venv: OK

call npm --version >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] Node.js/npm 未安装
    pause
    exit /b 1
)
echo       Node.js: OK

set "OLLAMA_AVAILABLE=0"
where ollama >nul 2>&1
if !errorlevel! equ 0 (
    set "OLLAMA_AVAILABLE=1"
    echo       Ollama: OK
) else (
    echo       Ollama: 未安装 [跳过]
)

echo.

:: ------------------------------------------------------------
:: 启动 PostgreSQL
:: ------------------------------------------------------------
echo [2/6] 启动 PostgreSQL 数据库...

cd /d "%PROJECT_ROOT%"

docker ps --filter "name=bidmind-postgres" --format "{{.Names}}" 2>nul | findstr /i "bidmind-postgres" >nul 2>&1
if !errorlevel! equ 0 (
    echo       已在运行中 - 跳过
    goto :pg_ready
)

docker ps -a --filter "name=bidmind-postgres" --format "{{.Names}}" 2>nul | findstr /i "bidmind-postgres" >nul 2>&1
if !errorlevel! equ 0 (
    echo       启动已有容器...
    docker start bidmind-postgres >nul 2>&1
    goto :pg_wait
)

echo       创建并启动新容器...
docker-compose up -d postgres >nul 2>&1

:pg_wait
echo       等待数据库就绪...
timeout /t 3 /nobreak >nul

:pg_ready
docker exec bidmind-postgres pg_isready -U bidmind >nul 2>&1
if !errorlevel! equ 0 (
    echo       PostgreSQL: 端口 5432 - 就绪
) else (
    echo [ERROR] PostgreSQL 启动失败
    pause
    exit /b 1
)

echo.

:: ------------------------------------------------------------
:: 初始化数据库表
:: ------------------------------------------------------------
echo [3/6] 检查数据库表...

"%VENV_PYTHON%" "%PROJECT_ROOT%\scripts\init_tables.py" >nul 2>&1
echo       数据库表: 已就绪

echo.

:: ------------------------------------------------------------
:: 启动 Ollama
:: ------------------------------------------------------------
echo [4/6] 启动 Ollama LLM 服务...

if "!OLLAMA_AVAILABLE!"=="0" (
    echo       未安装 - 跳过
    goto :ollama_done
)

curl -s -o nul http://localhost:11434/api/tags >nul 2>&1
if !errorlevel! equ 0 (
    echo       已在运行中 - 跳过
    goto :ollama_done
)

echo       启动 Ollama 服务...
start "" /min ollama serve
timeout /t 3 /nobreak >nul
echo       Ollama: 端口 11434 - 启动中

:ollama_done
echo.

:: ------------------------------------------------------------
:: 启动后端服务
:: ------------------------------------------------------------
echo [5/6] 启动 FastAPI 后端...

netstat -ano 2>nul | findstr ":8000 " | findstr "LISTENING" >nul 2>&1
if !errorlevel! equ 0 (
    echo       已在运行中 - 跳过
    goto :backend_done
)

echo       启动后端服务...

:: 创建临时启动脚本解决路径空格问题
set "BACKEND_STARTER=%TEMP%\bidmind_backend.bat"
echo @echo off > "%BACKEND_STARTER%"
echo cd /d "%BACKEND_DIR%" >> "%BACKEND_STARTER%"
echo "%VENV_PYTHON%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >> "%BACKEND_STARTER%"

start "BidMind Backend" /min cmd /c "%BACKEND_STARTER%"
timeout /t 5 /nobreak >nul
echo       FastAPI: 端口 8000 - 启动中

:backend_done
echo.

:: ------------------------------------------------------------
:: 启动前端服务
:: ------------------------------------------------------------
echo [6/6] 启动 React 前端...

if not exist "%FRONTEND_DIR%\node_modules" (
    echo       安装前端依赖...
    cd /d "%FRONTEND_DIR%"
    call npm install >nul 2>&1
)

netstat -ano 2>nul | findstr ":3000 " | findstr "LISTENING" >nul 2>&1
if !errorlevel! equ 0 (
    echo       已在运行中 - 跳过
    goto :frontend_done
)

echo       启动前端服务...

:: 创建临时启动脚本
set "FRONTEND_STARTER=%TEMP%\bidmind_frontend.bat"
echo @echo off > "%FRONTEND_STARTER%"
echo cd /d "%FRONTEND_DIR%" >> "%FRONTEND_STARTER%"
echo call npm run dev >> "%FRONTEND_STARTER%"

start "BidMind Frontend" /min cmd /c "%FRONTEND_STARTER%"
timeout /t 5 /nobreak >nul
echo       React: 端口 3000 - 启动中

:frontend_done
echo.

:: ------------------------------------------------------------
:: 等待服务就绪
:: ------------------------------------------------------------
echo [检查] 等待服务就绪...

set "RETRY=0"
:wait_backend
if !RETRY! geq 10 goto :wait_done
curl -s -o nul http://localhost:8000/health >nul 2>&1
if !errorlevel! equ 0 (
    echo       后端: 就绪
    goto :wait_done
)
set /a RETRY+=1
timeout /t 1 /nobreak >nul
goto :wait_backend

:wait_done
echo.

:: ------------------------------------------------------------
:: 完成
:: ------------------------------------------------------------
echo ============================================================
echo   所有服务启动完成
echo ============================================================
echo.
echo   服务地址:
echo   ---------------------------------------------------------
echo   PostgreSQL:  localhost:5432 (user: bidmind, db: bidmind)
if "!OLLAMA_AVAILABLE!"=="1" echo   Ollama API:  http://localhost:11434
echo   后端 API:    http://localhost:8000
echo   后端文档:    http://localhost:8000/docs
echo   前端界面:    http://localhost:3000
echo   ---------------------------------------------------------
echo.
echo   测试数据位于: trial data\
echo   - 招标.pdf
echo   - BJ投标1.pdf
echo   - CK投标2.pdf
echo.
echo   按任意键打开前端界面...
pause >nul

start http://localhost:3000

endlocal
