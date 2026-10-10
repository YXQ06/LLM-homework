@echo off
REM 启动 AnythingLLM MCP Server
REM 请先在系统环境变量中设置 ANYTHINGLLM_API_KEY
set ANYTHINGLLM_BASE_URL=http://localhost:3001
python server.py
