@echo off
REM Ollama 服务启动脚本 (Windows)
REM 使用 Qwen2.5-7B 模型

echo ========================================
echo BidMind AI Lite - Ollama 服务启动
echo ========================================
echo.
echo 注意: 首次运行需要先安装 Ollama
echo 下载地址: https://ollama.com/download
echo ========================================

REM 检查 Ollama 是否已安装
where ollama >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [错误] Ollama 未安装或不在 PATH 中
    echo 请从 https://ollama.com/download 下载并安装 Ollama
    pause
    exit /b 1
)

echo.
echo [步骤1] 检查 Ollama 服务状态...

REM 尝试拉取模型（如果服务未运行会自动启动）
echo.
echo [步骤2] 拉取 Qwen2.5-7B 模型（首次需要下载约4GB）...
ollama pull qwen2.5:7b

if %ERRORLEVEL% neq 0 (
    echo [错误] 模型拉取失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo Ollama 服务已就绪
echo API 地址: http://localhost:11434/v1
echo 模型名称: qwen2.5:7b
echo ========================================
echo.
echo 服务将保持运行，按 Ctrl+C 停止
echo.

REM 保持窗口开启，Ollama服务在后台运行
echo 可以开始使用 BidMind AI Lite 了
pause
