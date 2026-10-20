@echo off
chcp 65001 >nul
setlocal

:: ============================================================
:: BidMind AI Lite - 全服务停止脚本
:: ============================================================

echo.
echo ============================================================
echo   BidMind AI Lite - 停止所有服务
echo ============================================================
echo.

:: 停止前端 (Vite dev server)
echo [1/4] 停止前端服务...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)
echo       已停止

:: 停止后端 (Uvicorn)
echo [2/4] 停止后端服务...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)
echo       已停止

:: 停止 Ollama
echo [3/4] 停止 Ollama 服务...
taskkill /IM ollama.exe /F >nul 2>&1
taskkill /IM ollama_llama_server.exe /F >nul 2>&1
echo       已停止

:: 停止 PostgreSQL (可选)
echo [4/4] PostgreSQL 容器...
set /p STOP_DB="是否停止数据库容器? (y/N): "
if /i "%STOP_DB%"=="y" (
    docker stop bidmind-postgres >nul 2>&1
    echo       已停止
) else (
    echo       保持运行
)

echo.
echo ============================================================
echo   所有服务已停止
echo ============================================================
echo.

pause
endlocal
