@echo off
REM PostgreSQL 数据库启动脚本 (Docker)

echo ========================================
echo BidMind AI Lite - PostgreSQL 启动
echo ========================================
echo.

REM 检查 Docker 是否运行
docker info >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [错误] Docker 未运行
    echo 请先启动 Docker Desktop
    pause
    exit /b 1
)

echo [步骤1] 启动 PostgreSQL + PGVector 容器...
cd /d "%~dp0.."
docker-compose up -d postgres

if %ERRORLEVEL% neq 0 (
    echo [错误] 容器启动失败
    pause
    exit /b 1
)

echo.
echo [步骤2] 等待数据库就绪...
timeout /t 5 /nobreak >nul

REM 检查数据库健康状态
:check_health
docker-compose exec -T postgres pg_isready -U bidmind -d bidmind_lite >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo 等待数据库启动...
    timeout /t 2 /nobreak >nul
    goto check_health
)

echo.
echo ========================================
echo PostgreSQL 已就绪
echo ========================================
echo 连接信息:
echo   主机: localhost
echo   端口: 5432
echo   用户: bidmind
echo   密码: bidmind
echo   数据库: bidmind_lite
echo ========================================
echo.
echo 停止数据库: docker-compose down
echo 查看日志: docker-compose logs -f postgres
echo.
pause
