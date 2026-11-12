#!/usr/bin/env bash
# 宠物医院 MCP Server 启动脚本（Linux / macOS）
# Windows 用户请双击 start.bat
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
export PET_HOSPITAL_API_BASE="${PET_HOSPITAL_API_BASE:-http://127.0.0.1:8080}"
echo "[start.sh] 上游 REST API: ${PET_HOSPITAL_API_BASE}"

# 4. 健康检查上游服务（不可达时仅提醒，不阻塞启动）
if command -v curl >/dev/null 2>&1; then
    if ! curl -sf -o /dev/null --max-time 3 "${PET_HOSPITAL_API_BASE}/api/v1/stats"; then
        echo "[start.sh] ⚠ 上游 REST API 暂不可达，请确认 pethospital.exe 已启动；MCP 工具调用时会报 502"
    fi
fi

# 5. 启动
echo "[start.sh] 启动 MCP Server（http://localhost:8000/mcp）..."
exec python server.py
