@echo off
chcp 65001 >nul
echo.
echo 🐾 宠物医院 MCP Server 启动脚本
echo ==================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到 Python，请先安装 Python 3.10+
    echo    下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 检查并安装依赖
echo 📦 检查依赖...
pip show mcp >nul 2>&1
if errorlevel 1 (
    echo    正在安装 MCP SDK...
    pip install "mcp[cli]>=2.0.0"
    if errorlevel 1 (
        echo ❌ 安装 MCP SDK 失败
        pause
        exit /b 1
    )
)

pip show httpx >nul 2>&1
if errorlevel 1 (
    echo    正在安装 httpx...
    pip install httpx>=0.27.0
    if errorlevel 1 (
        echo ❌ 安装 httpx 失败
        pause
        exit /b 1
    )
)

echo ✅ 依赖检查完成
echo.

REM 检查宠物医院 API
echo 🔍 检查宠物医院 REST API...
curl -s http://127.0.0.1:8080/health >nul 2>&1
if errorlevel 1 (
    echo ⚠️  未检测到宠物医院 REST API
    echo    请确保 pethospital.exe 已启动在 http://127.0.0.1:8080
    echo.
    set /p CONTINUE="是否继续启动 MCP Server? (y/N): "
    if /i not "%CONTINUE%"=="y" (
        exit /b 1
    )
) else (
    echo ✅ 宠物医院 API 正在运行
)

echo.
echo 🚀 启动 MCP Server...
echo    监听地址: http://localhost:8000/mcp
echo    按 Ctrl+C 停止服务
echo.

python server.py
