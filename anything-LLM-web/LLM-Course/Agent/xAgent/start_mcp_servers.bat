@echo off
chcp 65001 >nul
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_mcp_servers.ps1"
echo.
if errorlevel 1 (
    echo MCP 服务未能全部启动，请查看 "%~dp0logs" 下的日志。
)
pause
endlocal
