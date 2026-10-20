@echo off
chcp 65001 >nul
setlocal

:: ============================================================
:: BidMind AI Lite - 服务状态检查
:: ============================================================

echo.
echo ============================================================
echo   BidMind AI Lite - 服务状态
echo ============================================================
echo.

:: PostgreSQL
echo [PostgreSQL]
docker ps --filter "name=bidmind-postgres" --format "  状态: {{.Status}}" 2>nul
if %errorlevel% neq 0 (
    echo   状态: 未运行
)
docker exec bidmind-postgres pg_isready -U bidmind >nul 2>&1
if %errorlevel% equ 0 (
    echo   连接: 正常 (localhost:5432)
) else (
    echo   连接: 不可用
)
echo.

:: Ollama
echo [Ollama LLM]
curl -s -o nul -w "" http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% equ 0 (
    echo   状态: 运行中
    echo   地址: http://localhost:11434
    for /f "delims=" %%i in ('curl -s http://localhost:11434/api/tags 2^>nul ^| findstr /r "name"') do (
        echo   模型: %%i
    )
) else (
    echo   状态: 未运行
)
echo.

:: Backend
echo [FastAPI 后端]
curl -s http://localhost:8000/health >nul 2>&1
if %errorlevel% equ 0 (
    echo   状态: 运行中
    echo   地址: http://localhost:8000
    echo   文档: http://localhost:8000/docs
) else (
    echo   状态: 未运行
)
echo.

:: Frontend
echo [React 前端]
netstat -ano | findstr ":3000 " | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo   状态: 运行中
    echo   地址: http://localhost:3000
) else (
    echo   状态: 未运行
)
echo.

echo ============================================================
echo.
pause
endlocal
