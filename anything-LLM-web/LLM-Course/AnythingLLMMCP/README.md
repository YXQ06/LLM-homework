# AnythingLLM MCP Server

把本机 AnythingLLM 封装成 MCP Server，让任何 MCP 客户端（xAgent、Claude Desktop、Inspector）都能
**列工作区 → 看文档 → 传文档并向量化 → 基于知识库提问**。

- 语言：Python 3.10+，依赖 `mcp>=2.0.0`、`httpx>=0.27`
- 传输：Streamable HTTP
- 端点：`http://127.0.0.1:8000/mcp`
- 协议：同时支持 **MCP 2026-07-28**（无 `initialize` 握手、无 `Mcp-Session-Id`、无 GET 长连接）
  和 **2026-07-28 之前的握手协议**（`initialize` → `Mcp-Session-Id` → GET SSE 长连接 → DELETE 释放）
  —— 由 MCP SDK 2.x 根据请求头 `MCP-Protocol-Version` 自动分流，两个协议用同一个端点。

## 1. 准备

```bash
pip install -r requirements.txt
```

设置环境变量（**apikey 请同学们自己在 AnythingLLM 里创建**）：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `ANYTHINGLLM_API_KEY` | AnythingLLM API Key（必填） | 空 |
| `ANYTHINGLLM_BASE_URL` | AnythingLLM 地址 | `http://localhost:3001` |
| `MCP_HOST` / `MCP_PORT` | MCP 服务监听地址 | `127.0.0.1` / `8000` |

PowerShell 示例：

```powershell
$env:ANYTHINGLLM_API_KEY = "你的-api-key"
python server.py
```

或双击 `start.bat`。启动后终端会打印 MCP 端点与 Key 是否已配置。

## 2. 接入 MCP Client

复制 `mcp_config.json`（或 `Playground/mcp_config.json`）中的内容到你的 MCP 客户端配置：

```json
{
  "mcpServers": {
    "anythingllm-mcp": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

## 3. 工具（4 个）

| 工具 | 类型 | 作用 | 对应 AnythingLLM 接口 |
|---|---|---|---|
| `list_workspaces` | 只读 | 列出全部工作区（名称、slug、文档数、创建时间） | `GET /api/v1/workspaces` |
| `get_first_workspace_files` | 只读 | 第一个工作区的文档清单（文件名、类型、字数、分块、路径、时间） | `GET /workspaces` + `GET /workspace/{slug}` |
| `upload_document` | 写入 | 上传本地文档 → 加入工作区 → 触发向量化 | `POST /document/upload` + `POST /workspace/{slug}/update-embeddings` |
| `chat_with_workspace` | 写入 | 基于工作区知识库提问，返回回答 + 引用来源 | `POST /workspace/{slug}/chat` |

### 3.1 `list_workspaces`

无参数。输出示例：

```
共 2 个工作区：
1. 农学试验 | slug: agronomy-8f2c | 文档数: 12 | 创建: 2026-10-02T09:11:03.221Z
2. My Workspace | slug: my-workspace | 文档数: 0 | 创建: 2026-09-28T14:02:55.010Z
```

### 3.2 `get_first_workspace_files`

无需参数。返回第一个工作区的全部文件信息：文件名、类型、字数、分块 token 数、是否置顶、
存储路径、来源、创建/更新时间。

数据来源：

1. `GET /api/v1/workspaces` → 取第一个工作区
2. `GET /api/v1/workspace/{slug}` → 取该工作区的 `documents`

### 3.3 `upload_document`

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `file_path` | 是 | — | 本地文件路径（绝对路径或相对当前工作目录） |
| `workspace_slug` | 否 | 第一个工作区 | 目标工作区 slug |
| `chunk_size` | 否 | 1000 | 切片长度（字符，最小 100） |
| `chunk_overlap` | 否 | 20 | 相邻切片重叠（需小于 `chunk_size`） |

执行步骤与校验：

1. 校验文件存在、非空、不超过 100 MB，切片参数合法 → 否则返回结构化错误；
2. `POST /api/v1/document/upload`，multipart 字段名 **`file`**，`options` 里带
   `chunkSize` / `chunkOverlap` / `splitOn`；
3. 从返回的 `documents[0]` 取 `location`（缺失时取 `docpath`）；
4. `POST /api/v1/workspace/{slug}/update-embeddings`，`{"adds": ["<location>"]}` 触发向量化，
   读取 `failedToEmbed` 判断结果。

成功输出：

```
=== 上传完成 ===
文件: 试验数据说明.md（18.4 KB）
文档 id: 91c3…
标题: 试验数据说明.md
字数: 4210 | 预估 token: 1580
目标工作区: agronomy-8f2c
向量化: 成功，已可在该工作区中检索。
```

### 3.4 `chat_with_workspace`

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `message` | 是 | — | 问题文本，空串直接拒绝 |
| `workspace_slug` | 否 | 第一个工作区 | 目标工作区 |
| `mode` | 否 | `chat` | `chat` 带上下文；`query` 单次检索问答 |

返回回答正文，并附最多 5 条引用来源（标题、相似度、120 字片段），便于核对答案出处。

## 4. 错误语义

所有失败都以**结构化 JSON** 返回，而不是纯自然语言，模型可直接读到错误码：

```json
{"ok": false, "error": {"code": 401, "message": "未配置 AnythingLLM API Key：请设置环境变量 ANYTHINGLLM_API_KEY 后重启服务。"}}
```

| code | 场景 |
|---|---|
| 400 | 参数非法（空问题、chunk_overlap ≥ chunk_size、mode 取值错误、空文件） |
| 401 | 未配置 / 无效 API Key |
| 404 | 文件不存在、没有任何工作区 |
| 413 | 文件超过 100 MB |
| 500 | 上游接口报错、网络异常、向量化失败 |

## 5. 已知限制

- `upload_document` 的向量化依赖 AnythingLLM 返回 `location`/`docpath`；不同版本字段名不同，缺失时会提示手动加入工作区；
- 未实现文档删除与重新嵌入，需要时在 AnythingLLM 界面操作；
- `chat_with_workspace` 是同步等待回答，`mode=chat` 的长对话上下文由 AnythingLLM 侧维护。
