@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0.."

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_mcp_servers.ps1"
if errorlevel 1 (
    echo.
    echo MCP 服务启动失败，已取消启动 xAgent。
    pause
    popd
    exit /b 1
)

python -m xAgent %*
popd
endlocal
