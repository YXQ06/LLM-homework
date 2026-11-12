"""
AnythingLLM MCP Server

把本机 AnythingLLM 的 REST API 封装成 MCP 工具，覆盖"看 / 传 / 问 / 管"四类操作：

* ``get_first_workspace_files`` —— 看：第一个工作区的文档清单
* ``list_workspaces``           —— 看：所有工作区
* ``upload_document``           —— 传：本地文档上传 + 加入工作区向量化
* ``chat_with_workspace``       —— 问：针对工作区知识库提问（RAG 检索 + 对话）
* ``get_embed_status``          —— 管：工作区向量化状态（各文档分块/置顶信息）
* ``delete_document``           —— 管：把文档移出工作区（取消向量化）

传输方式：Streamable HTTP
协议兼容：MCP 2026-07-28（无握手/无会话）以及之前的握手协议（带 Mcp-Session-Id + GET 长连接）
"""

import json
import logging
import mimetypes
import os
from pathlib import Path

import httpx
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

# 统一日志格式（L5）：与 PetHospitalMCP 保持一致，支持 LOG_LEVEL 环境变量
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("anythingllm-mcp")

SERVER_NAME = "anythingllm-mcp"
HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8000"))

# AnythingLLM 连接配置（apikey 请在 AnythingLLM 界面 -> 设置 -> API Keys 中自行创建）
ANYTHINGLLM_BASE_URL = os.getenv("ANYTHINGLLM_BASE_URL", "http://localhost:3001").rstrip("/")
ANYTHINGLLM_API_KEY = os.getenv("ANYTHINGLLM_API_KEY", "")
TIMEOUT = 15.0

mcp = MCPServer(
    SERVER_NAME,
    instructions=(
        "查询与操作 AnythingLLM 工作区：列出工作区、查看文件清单、"
        "上传本地文档并向量化、针对工作区知识库提问。"
    ),
)


def _error(code: int, message: str) -> str:
    """统一的错误返回：结构化 JSON，模型可以直接读到错误码。"""
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message}},
        ensure_ascii=False,
    )


def _check_key() -> str | None:
    """缺少 API Key 时返回给模型的提示文本，已配置则返回 None。"""
    if not ANYTHINGLLM_API_KEY:
        return _error(401, "未配置 AnythingLLM API Key：请设置环境变量 ANYTHINGLLM_API_KEY 后重启服务。")
    return None


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


async def _post(path: str, payload: dict, *, timeout: float | None = None) -> dict:
    """POST JSON 到 AnythingLLM API。"""
    async with httpx.AsyncClient(timeout=timeout or TIMEOUT) as client:
        r = await client.post(
            f"{ANYTHINGLLM_BASE_URL}/api/v1{path}",
            json=payload,
            headers={"Authorization": f"Bearer {ANYTHINGLLM_API_KEY}"},
        )
    if r.status_code == 401:
        raise RuntimeError("AnythingLLM API Key 无效，请在 AnythingLLM 中重新创建 API Key 并设置环境变量 ANYTHINGLLM_API_KEY")
    if r.status_code >= 400:
        raise RuntimeError(f"AnythingLLM 返回 {r.status_code}：{r.text[:300]}")
    return r.json()


async def _upload(filename: str, content: bytes, options: dict) -> dict:
    """上传单个文件到 AnythingLLM 文档库（multipart，字段名 file）。"""
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    files = {"file": (filename, content, mime)}
    data = {"options": json.dumps(options, ensure_ascii=False)}
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(
            f"{ANYTHINGLLM_BASE_URL}/api/v1/document/upload",
            files=files,
            data=data,
            headers={"Authorization": f"Bearer {ANYTHINGLLM_API_KEY}"},
        )
    if r.status_code == 401:
        raise RuntimeError("AnythingLLM API Key 无效，请在 AnythingLLM 中重新创建 API Key 并设置环境变量 ANYTHINGLLM_API_KEY")
    if r.status_code >= 400:
        raise RuntimeError(f"上传失败：AnythingLLM 返回 {r.status_code}：{r.text[:300]}")
    return r.json()


async def _resolve_slug(slug: str) -> tuple[str | None, str | None]:
    """把工作区 slug 补全；传空时取列表第一个。返回 (slug, 错误文本)。"""
    if slug:
        return slug, None
    workspaces = (await _get("/workspaces")).get("workspaces") or []
    if not workspaces:
        return None, _error(404, "AnythingLLM 中还没有任何工作区，请先在 AnythingLLM 中新建工作区。")
    return workspaces[0]["slug"], None


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


@mcp.tool(
    name="list_workspaces",
    description="列出 AnythingLLM 中的全部工作区（名称、slug、id、文档数与创建时间），用于确定上传或提问的目标工作区",
    annotations=ToolAnnotations(title="工作区列表", readOnlyHint=True, openWorldHint=False),
)
async def list_workspaces() -> str:
    """列出所有工作区，返回人类可读的清单。"""
    missing = _check_key()
    if missing:
        return missing

    try:
        workspaces = (await _get("/workspaces")).get("workspaces") or []
    except Exception as exc:  # 网络/鉴权异常统一转为结构化错误
        return _error(500, f"获取工作区失败：{exc}")

    if not workspaces:
        return "AnythingLLM 中还没有任何工作区。"

    lines = [f"共 {len(workspaces)} 个工作区："]
    for i, ws in enumerate(workspaces, 1):
        lines.append(
            f"{i}. {ws.get('name')} | slug: {ws.get('slug')} | "
            f"文档数: {ws.get('documentCount', 'N/A')} | 创建: {ws.get('createdAt')}"
        )
    return "\n".join(lines)


@mcp.tool(
    name="upload_document",
    description="把本机的一个文档上传到 AnythingLLM 并加入指定工作区做向量化（支持 md/txt/pdf/docx 等），返回文档 id、字数与向量化结果",
    annotations=ToolAnnotations(title="上传并向量化文档", readOnlyHint=False, openWorldHint=False),
)
async def upload_document(
    file_path: str,
    workspace_slug: str = "",
    chunk_size: int = 1000,
    chunk_overlap: int = 20,
) -> str:
    """
    上传本地文件到 AnythingLLM 文档库，并加入工作区向量化。

    Args:
        file_path: 本地文件的绝对路径或相对当前工作目录的路径
        workspace_slug: 目标工作区 slug；留空则使用列表中的第一个工作区
        chunk_size: 切片长度（字符），默认 1000
        chunk_overlap: 相邻切片重叠字符数，默认 20

    Returns:
        上传与向量化结果文本；失败时返回结构化 JSON 错误
    """
    missing = _check_key()
    if missing:
        return missing

    path = Path(file_path).expanduser()
    if not path.is_file():
        return _error(404, f"文件不存在或不是普通文件：{file_path}")

    size = path.stat().st_size
    if size == 0:
        return _error(400, f"文件为空：{file_path}")
    if size > 100 * 1024 * 1024:
        return _error(413, f"文件过大（{size / 1024 / 1024:.1f} MB），请拆分后再上传。")

    if chunk_size < 100:
        return _error(400, "chunk_size 不能小于 100。")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        return _error(400, "chunk_overlap 必须大于等于 0 且小于 chunk_size。")

    try:
        slug, err = await _resolve_slug(workspace_slug)
        if err:
            return err

        payload = await _upload(
            path.name,
            path.read_bytes(),
            {
                "chunkSize": chunk_size,
                "chunkOverlap": chunk_overlap,
                "splitOn": "\n",
            },
        )
    except Exception as exc:
        return _error(500, f"上传失败：{exc}")

    if not payload.get("success"):
        return _error(500, f"上传失败：{payload.get('error') or payload}")

    doc = (payload.get("documents") or [{}])[0]
    location = doc.get("location") or doc.get("docpath")
    lines = [
        "=== 上传完成 ===",
        f"文件: {path.name}（{size / 1024:.1f} KB）",
        f"文档 id: {doc.get('id', 'N/A')}",
        f"标题: {doc.get('title', path.name)}",
        f"字数: {doc.get('wordCount', 'N/A')} | 预估 token: {doc.get('token_count_estimate', 'N/A')}",
        f"目标工作区: {slug}",
    ]

    if not location:
        lines.append("注意: 返回结果中没有文档位置，未触发向量化，请在 AnythingLLM 界面手动加入工作区。")
        return "\n".join(lines)

    try:
        embed = await _post(
            f"/workspace/{slug}/update-embeddings",
            {"adds": [location]},
            timeout=300.0,
        )
    except Exception as exc:
        lines.append(f"注意: 文档已入库，但向量化失败（{exc}）；可在 AnythingLLM 界面手动重试。")
        return "\n".join(lines)

    if embed.get("failedToEmbed"):
        lines.append(f"向量化失败: {embed['failedToEmbed']}")
    else:
        lines.append("向量化: 成功，已可在该工作区中检索。")
    return "\n".join(lines)


@mcp.tool(
    name="chat_with_workspace",
    description="基于指定工作区的知识库提问（RAG 检索后回答），返回回答正文与引用来源；mode=chat 保留对话上下文，mode=query 只做单次检索问答",
    annotations=ToolAnnotations(title="工作区知识库问答", readOnlyHint=False, openWorldHint=False),
)
async def chat_with_workspace(
    message: str,
    workspace_slug: str = "",
    mode: str = "chat",
) -> str:
    """
    在工作区知识库上提问。

    Args:
        message: 用户问题
        workspace_slug: 目标工作区 slug；留空则使用列表中的第一个工作区
        mode: chat（带上下文）或 query（不保留上下文）

    Returns:
        回答正文 + 引用来源；失败时返回结构化 JSON 错误
    """
    missing = _check_key()
    if missing:
        return missing

    if not message or not message.strip():
        return _error(400, "message 不能为空。")
    if mode not in ("chat", "query"):
        return _error(400, "mode 只能是 chat 或 query。")

    try:
        slug, err = await _resolve_slug(workspace_slug)
        if err:
            return err
        data = await _post(
            f"/workspace/{slug}/chat",
            {"message": message.strip(), "mode": mode},
            timeout=300.0,
        )
    except Exception as exc:
        return _error(500, f"提问失败：{exc}")

    if data.get("error"):
        return _error(500, f"工作区返回错误：{data['error']}")

    answer = (data.get("textResponse") or "").strip() or "（工作区没有返回文本回答）"
    lines = [f"工作区: {slug}", "--- 回答 ---", answer]

    sources = data.get("sources") or []
    if sources:
        lines.append(f"--- 引用来源（{len(sources)} 条）---")
        for i, src in enumerate(sources[:5], 1):
            title = src.get("title") or src.get("docpath") or "未知文档"
            snippet = (src.get("text") or "").replace("\n", " ")[:120]
            score = src.get("score")
            score_text = f" | 相似度: {score:.3f}" if isinstance(score, (int, float)) else ""
            lines.append(f"{i}. {title}{score_text}")
            if snippet:
                lines.append(f"   {snippet}…")
    return "\n".join(lines)


@mcp.tool(
    name="get_embed_status",
    description="查询工作区的向量化状态：每个文档的路径、字数、分块 token 估算、置顶状态，用于确认上传后是否已成功入库",
    annotations=ToolAnnotations(title="向量化状态查询", readOnlyHint=True, openWorldHint=False),
)
async def get_embed_status(workspace_slug: str = "") -> str:
    """
    查询工作区中各文档的向量化状态。

    Args:
        workspace_slug: 目标工作区 slug；留空则使用列表中的第一个工作区

    Returns:
        文档清单及其分块/置顶状态；失败时返回结构化 JSON 错误
    """
    missing = _check_key()
    if missing:
        return missing

    try:
        slug, err = await _resolve_slug(workspace_slug)
        if err:
            return err
        detail = (await _get(f"/workspace/{slug}")).get("workspace") or []
    except Exception as exc:
        return _error(500, f"获取工作区状态失败：{exc}")

    documents = detail[0].get("documents", []) if detail else []
    if not documents:
        return f"工作区 {slug} 中暂无已入库文档（空工作区）。"

    lines = [f"=== 工作区 {slug} 向量化状态（共 {len(documents)} 个文档） ===", ""]
    for i, doc in enumerate(documents, 1):
        meta = _safe_json(doc.get("metadata"), {}) or {}
        lines.append(
            f"{i}. {doc.get('filename')}"
            f" | 字数: {meta.get('wordCount', 'N/A')}"
            f" | 分块(token): {meta.get('token_count_estimate', 'N/A')}"
            f" | 置顶: {'是' if doc.get('pinned') else '否'}"
            f" | 更新: {doc.get('lastUpdatedAt') or 'N/A'}"
        )
        lines.append(f"   路径: {doc.get('docpath')}")
    lines.append("")
    lines.append("说明: 文档出现在此列表中即表示已加入工作区并可被检索。")
    return "\n".join(lines)


@mcp.tool(
    name="delete_document",
    description="把一个文档移出指定工作区（取消其向量化与检索资格，文档本体仍保留在 AnythingLLM 文档库中）",
    annotations=ToolAnnotations(title="移出工作区文档", readOnlyHint=False, openWorldHint=False),
)
async def delete_document(
    doc_path: str,
    workspace_slug: str = "",
) -> str:
    """
    把文档移出工作区（通过 update-embeddings 的 deletes 通道取消向量化）。

    Args:
        doc_path: 文档在 AnythingLLM 中的路径（custom-documents/xxx.json 之类，
            可先调用 get_embed_status 查看）
        workspace_slug: 目标工作区 slug；留空则使用列表中的第一个工作区

    Returns:
        操作结果文本；失败时返回结构化 JSON 错误
    """
    missing = _check_key()
    if missing:
        return missing

    if not doc_path or not doc_path.strip():
        return _error(400, "doc_path 不能为空，可先调用 get_embed_status 查看文档路径。")

    try:
        slug, err = await _resolve_slug(workspace_slug)
        if err:
            return err
        await _post(
            f"/workspace/{slug}/update-embeddings",
            {"deletes": [doc_path.strip()]},
            timeout=120.0,
        )
    except Exception as exc:
        return _error(500, f"移出文档失败：{exc}")

    return (
        f"已把文档移出工作区 {slug}：{doc_path}\n"
        "该文档不再参与此工作区的检索；如需彻底删除文档本体，请在 AnythingLLM 界面操作。"
    )


if __name__ == "__main__":
    logger.info("AnythingLLM MCP Server 启动中...")
    logger.info("AnythingLLM: %s (API Key: %s)", ANYTHINGLLM_BASE_URL, "已配置" if ANYTHINGLLM_API_KEY else "未配置")
    logger.info("MCP 端点: http://%s:%s/mcp", HOST, PORT)
    logger.info("传输: Streamable HTTP（兼容 2026-07-28 无握手协议 与 握手协议）")

    mcp.run(transport="streamable-http", host=HOST, port=PORT)
