#!/usr/bin/env bash
# AnythingLLM MCP Server 启动脚本（Linux / macOS）
# Windows 用户请使用 start.bat
set -e
cd "$(dirname "$0")"

# 1. 虚拟环境
if [ ! -d venv ]; then
    echo "[start.sh] 创建虚拟环境..."
    python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

# 2. 依赖
echo "[start.sh] 安装依赖..."
pip install -q -r requirements.txt

# 3. 配置（可用环境变量覆盖）
export ANYTHINGLLM_BASE_URL="${ANYTHINGLLM_BASE_URL:-http://localhost:3001}"
export MCP_HOST="${MCP_HOST:-127.0.0.1}"
export MCP_PORT="${MCP_PORT:-8000}"

# 4. 前置检查：未配置 Key 直接拒绝启动，避免起一个必然 401 的服务
if [ -z "${ANYTHINGLLM_API_KEY}" ]; then
    echo "[start.sh] ✘ 未设置 ANYTHINGLLM_API_KEY 环境变量" >&2
    echo "           请在 AnythingLLM → 设置 → API Keys 创建后执行：" >&2
    echo "           ANYTHINGLLM_API_KEY=你的Key bash start.sh" >&2
    exit 1
fi

# 5. 健康检查 AnythingLLM 服务
if command -v curl >/dev/null 2>&1; then
    if ! curl -sf -o /dev/null --max-time 3 "${ANYTHINGLLM_BASE_URL}"; then
        echo "[start.sh] ⚠ AnythingLLM（${ANYTHINGLLM_BASE_URL}）暂不可达，请先启动 AnythingLLM 服务"
    fi
fi

# 6. 启动
echo "[start.sh] 启动 MCP Server（http://${MCP_HOST}:${MCP_PORT}/mcp）..."
exec python server.py
