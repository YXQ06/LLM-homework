# AnythingLLM MCP Server

把本机 AnythingLLM 封装成一个 MCP Server，只提供 1 个工具：**提取第一个工作区的文件信息**。

- 语言：Python
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

或双击 `start.bat`。

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

## 3. 工具

### `get_first_workspace_files`

无需参数。返回 AnythingLLM 列表中第一个工作区的全部文件信息：文件名、类型、字数、
分块 token 数、是否置顶、存储路径、来源、创建/更新时间。

数据来源：

1. `GET /api/v1/workspaces` → 取第一个工作区
2. `GET /api/v1/workspace/{slug}` → 取该工作区的 `documents`
