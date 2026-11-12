#!/usr/bin/env bash
# xAgent 启动脚本（Linux / macOS）
# Windows 用户请使用 run_xAgent.bat（等价逻辑，见同目录）
# 作用：安装依赖 → 按 .mcp.json 拉起 MCP Server → 启动 xAgent
set -e
cd "$(dirname "$0")/.."   # 与 run_xAgent.bat 的 pushd "%~dp0.." 一致，切到 Agent 目录

# 1. 依赖
echo "[start.sh] 安装依赖..."
python3 -m pip install -q -r xAgent/requirements.txt

# 2. 启动 MCP Server（run_xAgent.bat 中 start_mcp_servers.ps1 的 POSIX 等价实现）
if [ -f ".mcp.json" ]; then
    echo "[start.sh] 按 .mcp.json 拉起 MCP Server..."
    python3 - <<'PYEOF'
import json, os, subprocess, sys, time

cfg = json.load(open(".mcp.json", encoding="utf-8"))
servers = cfg.get("mcpServers") or {}
procs = []
for name, spec in servers.items():
    if spec.get("enabled") is False:
        print(f"[mcp] {name}: 已配置为 disabled，跳过")
        continue
    try:
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (spec.get("env") or {}).items()})
        procs.append((name, subprocess.Popen(
            [str(spec["command"])] + [str(a) for a in spec.get("args", [])],
            env=env,
            cwd=os.getcwd(),
        )))
        print(f"[mcp] {name}: 已启动")
    except Exception as exc:
        print(f"[mcp] {name}: 启动失败（{exc}），xAgent 将以纯本地工具模式运行")

if procs:
    time.sleep(3)  # 给 MCP Server 一点初始化时间

try:
    sys.exit(subprocess.call([sys.executable, "-m", "xAgent"] + sys.argv[1:]))
finally:
    for name, p in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
            print(f"[mcp] {name}: 已停止")
PYEOF
else
    echo "[start.sh] 未找到 .mcp.json，直接启动 xAgent..."
    exec python3 -m xAgent "$@"
fi
