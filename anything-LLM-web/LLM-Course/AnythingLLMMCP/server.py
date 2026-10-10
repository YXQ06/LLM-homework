"""
AnythingLLM MCP Server

把本机 AnythingLLM 的 REST API 封装成一个 MCP Tool。
传输方式：Streamable HTTP
协议兼容：MCP 2026-07-28（无握手/无会话）以及之前的握手协议（带 Mcp-Session-Id + GET 长连接）
"""

import json
import os

import httpx
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

SERVER_NAME = "anythingllm-mcp"
HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8000"))

# AnythingLLM 连接配置（apikey 请在 AnythingLLM 界面 -> 设置 -> API Keys 中自行创建）
ANYTHINGLLM_BASE_URL = os.getenv("ANYTHINGLLM_BASE_URL", "http://localhost:3001").rstrip("/")
ANYTHINGLLM_API_KEY = os.getenv("ANYTHINGLLM_API_KEY", "")
TIMEOUT = 15.0

mcp = MCPServer(
    SERVER_NAME,
    instructions="查询 AnythingLLM 工作区中的文件信息。",
)


async def _get(path: str) -> dict:
    """调用 AnythingLLM API（自动带 Bearer Token）"""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(
            f"{ANYTHINGLLM_BASE_URL}/api/v1{path}",
            headers={"Authorization": f"Bearer {ANYTHINGLLM_API_KEY}"},
        )
    if r.status_code == 401:
        raise RuntimeError("AnythingLLM API Key 无效，请在 AnythingLLM 中重新创建 API Key 并设置环境变量 ANYTHINGLLM_API_KEY")
    r.raise_for_status()
    return r.json()


def _safe_json(raw, default=None):
    try:
        return json.loads(raw)
    except Exception:
        return default


@mcp.tool(
    name="get_first_workspace_files",
    description="获取 AnythingLLM 中第一个工作区的文件信息（文件名、路径、类型、大小/字数、分块数、创建与更新时间）",
    annotations=ToolAnnotations(title="第一个工作区文件信息", readOnlyHint=True, openWorldHint=False),
)
async def get_first_workspace_files() -> str:
    """
    获取 AnythingLLM 列表中第一个工作区里的所有文件信息。

    Returns:
        文本形式的文件清单
    """
    if not ANYTHINGLLM_API_KEY:
        return "未配置 AnythingLLM API Key：请设置环境变量 ANYTHINGLLM_API_KEY 后重启服务。"

    # 1. 找到第一个工作区
    workspaces = (await _get("/workspaces")).get("workspaces") or []
    if not workspaces:
        return "AnythingLLM 中还没有任何工作区。"

    ws = workspaces[0]

    # 2. 取该工作区的文档列表
    detail = (await _get(f"/workspace/{ws['slug']}")).get("workspace") or []
    documents = detail[0].get("documents", []) if detail else []

    lines = [
        "=== AnythingLLM 第一个工作区 ===",
        f"名称: {ws.get('name')} | slug: {ws.get('slug')} | id: {ws.get('id')}",
        f"创建时间: {ws.get('createdAt')} | 更新时间: {ws.get('lastUpdatedAt')}",
        f"文件总数: {len(documents)}",
        "",
    ]

    if not documents:
        lines.append("（该工作区下暂无文件）")
        return "\n".join(lines)

    for i, doc in enumerate(documents, 1):
        meta = _safe_json(doc.get("metadata"), {}) or {}
        ext = (doc.get("filename") or "").rsplit(".", 1)[-1].lower()
        lines.append(
            f"{i}. {doc.get('filename')}"
            f" | 类型: {ext or '未知'}"
            f" | 字数: {meta.get('wordCount', 'N/A')}"
            f" | 分块(token): {meta.get('token_count_estimate', 'N/A')}"
            f" | 置顶: {'是' if doc.get('pinned') else '否'}"
        )
        lines.append(f"   路径: {doc.get('docpath')}")
        lines.append(f"   来源: {meta.get('chunkSource') or 'N/A'}")
        lines.append(f"   创建: {doc.get('createdAt')} | 更新: {doc.get('lastUpdatedAt')}")

    return "\n".join(lines)


if __name__ == "__main__":
    print("AnythingLLM MCP Server 启动中...")
    print(f"AnythingLLM: {ANYTHINGLLM_BASE_URL} (API Key: {'已配置' if ANYTHINGLLM_API_KEY else '未配置'})")
    print(f"MCP 端点: http://{HOST}:{PORT}/mcp")
    print("传输: Streamable HTTP（兼容 2026-07-28 无握手协议 与 握手协议）")
    print("\n按 Ctrl+C 停止\n")

    mcp.run(transport="streamable-http", host=HOST, port=PORT)
