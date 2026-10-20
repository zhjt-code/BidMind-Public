# 清理并重新安装所有依赖
# 用法: .\scripts\reinstall_deps.ps1

Write-Host "=== 开始清理并重新安装 Python 依赖 ===" -ForegroundColor Cyan

# 1. 卸载可能冲突的包
Write-Host "`n[1/4] 卸载现有的 LangChain 相关包..." -ForegroundColor Yellow
pip uninstall -y langchain langchain-core langchain-community langchain-text-splitters langchain-openai

# 2. 卸载其他可能冲突的包
Write-Host "`n[2/4] 卸载其他可能冲突的包..." -ForegroundColor Yellow
pip uninstall -y pydantic python-dotenv openai

# 3. 清理 pip 缓存
Write-Host "`n[3/4] 清理 pip 缓存..." -ForegroundColor Yellow
pip cache purge

# 4. 重新安装所有依赖
Write-Host "`n[4/4] 重新安装所有依赖..." -ForegroundColor Yellow
pip install -r requirements.txt

Write-Host "`n=== 安装完成！===" -ForegroundColor Green
Write-Host "运行以下命令验证安装:" -ForegroundColor Cyan
Write-Host "  pip list | Select-String 'langchain|pydantic|openai'" -ForegroundColor White
