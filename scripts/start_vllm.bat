@echo off
REM vLLM 服务启动脚本
REM 注意: vLLM 不支持 Windows，请使用 WSL2 或 Linux

echo ========================================
echo BidMind AI Lite - vLLM 服务启动
echo ========================================
echo.
echo [警告] vLLM 不支持 Windows 原生环境
echo.
echo 请选择以下方案之一:
echo.
echo 方案A: 使用 Ollama (推荐，Windows原生支持)
echo   运行: scripts\start_ollama.bat
echo.
echo 方案B: 使用 WSL2 运行 vLLM
echo   1. 在 WSL2 Ubuntu 中安装: pip install vllm
echo   2. 启动服务:
echo      python -m vllm.entrypoints.openai.api_server ^
echo        --model /mnt/h/BidMind\ AI\ Lite/models/qwen2.5-7b ^
echo        --served-model-name qwen2.5-7b ^
echo        --host 0.0.0.0 --port 8001
echo.
echo ========================================
pause
