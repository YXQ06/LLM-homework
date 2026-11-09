# AnythingLLM WebPage —— 文档上传与向量化页面

单文件、零构建的网页工具：把本地文档批量上传到 AnythingLLM，自动切片、加入指定工作区并触发向量化，
逐文件显示成功 / 失败与进度条。全部代码在一个 `index.html` 里，不依赖 npm、不用打包。

- 文件：`index.html`（约 240 行，原生 HTML/CSS/JS）
- 依赖：浏览器 + 本机运行的 AnythingLLM（默认 `http://localhost:3001`）
- 相关接口：AnythingLLM REST API v1（`/workspaces`、`/document/upload`）

---

## 1. 启动

页面是纯静态文件，用任意静态服务器打开即可（不要用 `file://` 打开，部分浏览器会限制跨域请求）：

```bash
cd LLM-Course/AnythingLLMWebPage
python -m http.server 5173
```

浏览器访问 <http://localhost:5173>。也可以直接放进 VS Code 的 Live Server 里运行。

同时确认 AnythingLLM 已启动：

```bash
curl http://localhost:3001/api/v1/workspaces -H "Authorization: Bearer <你的KEY>"
```

---

## 2. 配置（页面内完成，不写死在代码里）

页面第一步是“连接配置”，三项都在浏览器里填：

| 配置 | 说明 | 示例 |
| --- | --- | --- |
| 服务地址 | AnythingLLM 的地址，末尾不要带 `/` | `http://localhost:3001` |
| API Key | AnythingLLM → 设置 → API Keys → 新建 | `2QV8…`（用你自己的 Key） |
| 工作区 | 点“刷新工作区”后从下拉框选择 | `农学试验` |

- 点“刷新工作区”会调用 `GET /api/v1/workspaces`，把返回的 `name` / `slug` 填进下拉框，
  **不再像旧版那样把 slug 写死在代码里**；
- 点“保存到本机浏览器”把三项存进 `localStorage`（键名 `anythingllm.uploader.settings`），
  刷新页面后自动回填；
- Key 只存在本机浏览器，**源码中不含任何密钥**，可以直接提交到公开仓库。

---

## 3. 上传流程

1. 选择文件（支持多选，可一次拖入多个 `.md` / `.pdf` / `.docx` / `.txt`）；
2. 设置切片参数：

   | 参数 | 默认值 | 含义 |
   | --- | --- | --- |
   | 切片长度 | 1000 | 每块字符数，交给 AnythingLLM 的 `chunkSize` |
   | 重叠 | 20 | 相邻块重叠字符数 `chunkOverlap` |
   | 切分符 | `\n` | 按换行切分；输入框里写 `\n` 会被转成真实换行 |

3. 点“开始上传并向量化”，页面逐个文件执行：

   ```
   POST {base}/api/v1/document/upload?slug={工作区slug}
   Authorization: Bearer {API Key}
   Content-Type: multipart/form-data

   file    = <文件二进制>
   options = {"chunkSize":1000,"chunkOverlap":20,"splitOn":"\n","addToWorkspaces":["<slug>"]}
   ```

4. 每个文件独立 `try/catch`：成功计入“成功”，失败把 HTTP 状态码和响应体写进日志，
   单个文件失败不影响后续文件；
5. 上传期间按钮、文件框、工作区下拉框全部禁用，宽度进度条按“已处理文件数 / 总文件数”推进，
   结束后按钮恢复，并提示“已加入工作区并开始向量化”。

---

## 4. 状态与错误处理

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `401：API Key 无效或已删除` | Key 错、被删或复制时带了空格 | 到 AnythingLLM 重新创建 Key |
| `403：Key 权限不足，或服务端未开启跨域（CORS）` | 浏览器跨域被拦 | 用 `python -m http.server` 起页面；必要时在 AnythingLLM 侧放行来源 |
| 刷新工作区为空 | AnythingLLM 中还没有工作区 | 先在 AnythingLLM 里新建工作区 |
| 单个文件 `❌`、其余正常 | 文件格式不支持或体积超限 | 查看日志中的响应体，换格式或拆分文件 |

失败项会在日志里带上文件名、HTTP 状态码和原始返回文本，便于定位是 Key、路径还是文件格式问题。

---

## 5. 安全说明

- 仓库中不存在任何真实 API Key（历史提交里出现过的那一个已作废，请勿复用）；
- 密钥只存在浏览器 `localStorage`，换浏览器 / 清理缓存后需重新填写；
- 页面只与本机 `localhost:3001` 通信，不上传文件到第三方服务；
- 若要把页面部署到公网，请改用后端代理注入 Key（参考 `Agent/xAgent/serve_web.py` 的做法），
  不要把 Key 重新写回前端。

---

## 6. 已知限制

- 只做“上传 + 向量化”，不做文档删除、重新嵌入、对话检索（这些能力在 `AnythingLLMMCP` 里以 MCP 工具提供）；
- 切片参数是全局的，不能按文件单独设置；
- 没有断点续传，刷新页面后需要重新选择文件。
