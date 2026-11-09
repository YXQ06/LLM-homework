# LLM-homework

《大模型概念、技术与应用实践》期末项目 —— 从「调一次大模型接口」到「带工具、技能与 MCP 的本地智能体」。

仓库按课程进阶顺序组织成 5 个可独立运行的部分：先是单次对话、多轮对话 + Function Calling、
十个文件操作工具，再是完整的定制智能体 `xAgent`，最后是两类 MCP 服务与一个文档上传页面。
每一部分都有独立的 README，说明怎么装、怎么配、怎么验证。

> 仓库原名 `YXQ06`，已重命名为 `LLM-homework`，内容保持原样。

---

## 1. 模块一览

| # | 模块 | 一句话 | 入口 | 依赖 |
|---|---|---|---|---|
| 1 | `Agent/Practice01` | 单次对话，打字机逐字输出 | `python chat.py` | `requests` + OpenAI 兼容服务 |
| 2 | `Agent/Practice02` | 多轮对话 + 2 个 Function Call（`getDate` / `isLeapYear`） | `python chat.py` | 同上 |
| 3 | `Agent/Practice03` | 在 Practice02 上加 10 个本地文件操作工具 | `python chat.py` | 同上 |
| 4 | `Agent/xAgent` | 定制智能体：24 个本地工具 + Skill 机制 + MCP 客户端 + TUI/CLI/Web | `python -m xAgent` | `textual` `trafilatura` `mcp` |
| 5 | `AnythingLLMMCP` | 把 AnythingLLM 封装成 MCP Server（4 个工具） | `python server.py` | `mcp` `httpx` |
| 6 | `PetHospitalMCP` | 宠物医院业务的 MCP Server（12 个工具） | `python server.py` | `mcp` `httpx` |
| 7 | `AnythingLLMWebPage` | 单文件网页：批量上传文档并向量化 | `python -m http.server 5173` | 浏览器 + AnythingLLM |

`PetHospitalServer/windows` 放的是宠物医院桌面端与服务端的运行材料（`pethospital.exe`、
`mcp_mvp` 源码、数据库与开发文档），是上面第 6 项的**上游服务**，本身不是课程作业代码。

---

## 2. 目录结构

```text
LLM-homework/
└── anything-LLM-web/
    └── LLM-Course/
        ├── .gitignore                     # 统一隐藏 config.ini / .env / 缓存
        ├── Agent/
        │   ├── AGENT.md                   # Agent 级长期规则（启动时注入）
        │   ├── .mcp.json                  # 多 MCP Server 配置（stdio + HTTP）
        │   ├── Practice01/                # 单次对话 + 打字机
        │   ├── Practice02/                # 多轮对话 + 2 个工具
        │   ├── Practice03/                # 10 个文件工具 + 架构图 .mmd
        │   └── xAgent/                    # 定制智能体（主要工作量）
        │       ├── __main__.py            # 入口：TUI / --cli
        │       ├── core/                  # agent 主循环、提示词、技能、MCP、工具
        │       │   ├── tools/             # files / system / web / search / agronomy / cli
        │       │   └── mcp/               # 配置、连接（stdio/http/sse）、工具映射
        │       ├── tui/                   # Textual 界面
        │       ├── mcp_servers/           # 农学 MCP Server（stdio，默认关闭）
        │       ├── tests/                 # 14 个 unittest 文件
        │       ├── .env.example           # 网页前端配置模板
        │       └── README.md              # 800 行：架构、工具清单、配置、排障
        ├── AnythingLLMMCP/                # AnythingLLM 的 MCP 封装
        │   ├── server.py                  # 4 个工具，Streamable HTTP
        │   └── README.md
        ├── AnythingLLMWebPage/            # 文档上传页面
        │   ├── index.html                 # 单文件，无构建
        │   └── README.md
        ├── PetHospitalMCP/                # 宠物医院 MCP（12 工具）
        │   ├── server.py
        │   ├── test_client.py
        │   └── README.md
        ├── PetHospitalServer/windows/     # 上游 REST 服务 + 桌面端材料
        ├── AnythingLLMServer/             # 预留目录
        └── Playground/                    # 临时试验目录
```

---

## 3. 环境要求

- Python **3.10+**（开发与验证环境：Windows 11 + Python 3.10）
- 一个 OpenAI 兼容的模型服务（本地 LM Studio / Ollama / 云端 API 均可），用于 Practice 与 xAgent
- 可选：AnythingLLM（桌面版，默认 `http://localhost:3001`），用于 MCP 与上传页面
- 可选：`pandas` `numpy` `matplotlib` `openpyxl` `python-docx`，用于 xAgent 的 Excel / 绘图 / Word 报告

各模块依赖互不干扰，按需安装：

```bash
pip install -r Agent/xAgent/requirements.txt
pip install -r AnythingLLMMCP/requirements.txt
pip install -r PetHospitalMCP/requirements.txt
```

---

## 4. 快速开始

### 4.1 Practice01：单次对话

```bash
cd anything-LLM-web/LLM-Course/Agent/Practice01
pip install requests
# 编辑 config.ini，填入 base_url / model / api_key
python chat.py
```

预期：输入问题 → 打印完整回答（打字机效果逐字出现）。

### 4.2 Practice02：多轮对话 + Function Call

```bash
cd ../Practice02
python chat.py
```

问「现在几点」「2024 是闰年吗」，可看到模型发起 `getDate` / `isLeapYear` 调用，
工具结果回传后再生成回答（模型不支持原生 `tool_calls` 时走文本 JSON 兜底模式）。

### 4.3 Practice03：文件操作工具

```bash
cd ../Practice03
python chat.py
```

问「列出当前目录」「新建 notes/a.txt 并写入 hello」，会触发 `listDirectory` / `createDirectory` / `createFile`。
10 个工具的职责与实现见 `Practice03/README.md` 与 `chat_architecture.mmd`。

### 4.4 xAgent：定制智能体

```bash
cd anything-LLM-web/LLM-Course/Agent
pip install -r xAgent/requirements.txt
# 编辑 config.ini（[llm] base_url / model / api_key）
python -m xAgent          # Textual 界面
python -m xAgent --cli    # 纯命令行
```

也可用 `run_xAgent.bat` 双击启动。启动后左侧对话、右侧工具 / 命令 / Trace 侧栏，
输入 `/` 查看命令（`/sessions` 切换会话、`/compact` 压缩上下文、`/skills` 查看技能）。

运行测试（在 `Agent` 目录下）：

```bash
python -m unittest discover -s xAgent/tests -t . -q
```

### 4.5 AnythingLLMMCP：AnythingLLM 的 MCP 封装

```bash
cd ../AnythingLLMMCP
pip install -r requirements.txt
set ANYTHINGLLM_API_KEY=你的key      # PowerShell: $env:ANYTHINGLLM_API_KEY="你的key"
python server.py
```

MCP 端点 `http://127.0.0.1:8000/mcp`，客户端配置见 `mcp_config.json`。
工具：`list_workspaces` / `get_first_workspace_files` / `upload_document` / `chat_with_workspace`。

### 4.6 PetHospitalMCP：宠物医院 MCP

```bash
cd ../PetHospitalMCP
pip install -r requirements.txt
# 先启动上游 REST 服务（PetHospitalServer/windows/pethospital.exe，监听 127.0.0.1:8080）
python server.py        # 或双击 start.bat（会先检查依赖与上游健康状态）
```

12 个工具覆盖宠物档案 CRUD、就诊记录、收费、检索、统计与数据导出；联调用例见 `test_client.py`。

### 4.7 AnythingLLMWebPage：文档上传页面

```bash
cd ../AnythingLLMWebPage
python -m http.server 5173
```

浏览器打开 <http://localhost:5173> → 填 AnythingLLM 地址与 API Key → 刷新工作区 → 选文件上传。
密钥只存浏览器 `localStorage`，源码里不含任何 Key。

---

## 5. 密钥与隐私

仓库里**没有任何真实密钥**。所有可能含密钥的文件都在 `.gitignore` 中被挡住：

| 文件 | 内容 | 规则位置 |
|---|---|---|
| `Agent/Practice0*/config.ini` | 模型 `base_url` / `api_key` | `LLM-Course/.gitignore`、`Agent/.gitignore` |
| `Agent/xAgent/config.ini` | 模型与上下文配置 | 同上 |
| `Agent/xAgent/config.json` | 本地运行时配置 | `LLM-Course/.gitignore` |
| `Agent/xAgent/.env` | AnythingLLM 网页前端的 Key | `Agent/xAgent/.gitignore` |
| `**/__pycache__/`、`*.pyc` | 编译缓存 | `LLM-Course/.gitignore` |
| `.agents/sessions/`、`.agents/context/`、`logs/` | 会话与日志运行产物 | `Agent/xAgent/.gitignore` |

配置模板（可安全提交）：`Agent/xAgent/.env.example`。复制为 `.env` 后填真实值即可。

---

## 6. 实现要点（按模块）

### 6.1 xAgent

- **工具注册表**：`core/tools/__init__.py` 统一注册 24 个本地工具，`core/prompts.py` 据此自动生成工具声明，
  新增工具不需要改界面；
- **主循环**：`core/agent.py` 解析 `tool_calls` → 执行 → 以带 `tool_call_id` 的 `tool` 消息回填，
  `max_tool_rounds` 限制轮数；另有重复调用检测、参数容错重试、结果去重；
- **技能机制**：`core/skills.py` 支持发现 / 按需加载 / 创建（`loadSkill`、`createSkill`），
  系统提示词里只放名称与描述，正文按需读取（渐进加载）；
- **MCP 客户端**：`core/mcp/client.py` 支持 stdio / HTTP / SSE 三种传输，工具名加来源前缀防冲突，
  单个 Server 连接失败不影响其它工具；
- **上下文管理**：`core/context.py` 监控 token 占比、超阈值提醒 `/compact`、归档到 `.agents/context/*.jsonl`
  并支持 `recallContext` 关键词回捞；
- **会话持久化**：`core/session.py` 以 JSONL 记录事件，可恢复、可切换；
- **长期规则**：`core/agent_rules.py` 在启动时把 `AGENT.md` 注入系统提示词最前端（带 sha256 与长度截断）；
- **农学工具链**：`core/tools/agronomy.py` 读 CSV/Excel → 异常值处理 → ANOVA + LSD/Tukey → 生成 Markdown/Word 报告，
  同样的能力也封装成 stdio MCP Server（`mcp_servers/agronomy_mcp_server.py`，默认关闭）。

### 6.2 两个 MCP Server

- 传输统一用 Streamable HTTP，端点 `/mcp`，同时兼容新旧协议（由 SDK 按 `MCP-Protocol-Version` 分流）；
- 工具参数用类型注解生成 schema，并在函数体内做枚举 / 非空 / 范围校验；
- 上游 HTTP 调用统一 15s（上传 180s、向量化 300s）超时，鉴权失败返回可读提示；
- `AnythingLLMMCP` 的错误统一返回结构化 JSON（`{"ok": false, "error": {"code", "message"}}`），
  便于模型读取错误码而不是猜自然语言。

### 6.3 AnythingLLMWebPage

调用链：`GET /api/v1/workspaces`（填充下拉）→ `POST /api/v1/document/upload`（multipart，字段 `file`，
`options` 带 `chunkSize` / `chunkOverlap` / `splitOn`）→ 逐文件统计成功/失败 → 进度条与彩色日志。
上传期间禁用交互控件，401 / 403 / 网络错误分别给出可操作的提示。

---

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| [`Agent/xAgent/README.md`](anything-LLM-web/LLM-Course/Agent/xAgent/README.md) | 架构图、24 工具清单、配置矩阵、MCP 配置、排障、测试 |
| [`Agent/Practice01/README.md`](anything-LLM-web/LLM-Course/Agent/Practice01/README.md) | 单次对话实现与配置 |
| [`Agent/Practice02/README.md`](anything-LLM-web/LLM-Course/Agent/Practice02/README.md) | 双模式工具调用设计 |
| [`Agent/Practice03/README.md`](anything-LLM-web/LLM-Course/Agent/Practice03/README.md) | 10 个文件工具的参数与实现 |
| [`AnythingLLMMCP/README.md`](anything-LLM-web/LLM-Course/AnythingLLMMCP/README.md) | 4 个工具、错误码、调用链 |
| [`AnythingLLMWebPage/README.md`](anything-LLM-web/LLM-Course/AnythingLLMWebPage/README.md) | 启动、配置、接口、错误处理、安全说明 |
| [`PetHospitalMCP/README.md`](anything-LLM-web/LLM-Course/PetHospitalMCP/README.md) | 12 个业务工具、上游前置、Inspector / curl 调试 |
| `PetHospitalServer/windows/MCP_SERVER_DEVELOPMENT.md` | 宠物医院 REST 服务设计说明 |

---

## 8. 已知限制

- `PetHospitalMCP` 需要先启动 `pethospital.exe`（127.0.0.1:8080），否则所有工具返回上游不可用；
- `AnythingLLMMCP.upload_document` 的向量化依赖 AnythingLLM 返回 `location` / `docpath`，
  不同版本字段名不一致，缺失时会在结果里提示手动加入工作区；
- `AnythingLLMWebPage` 只做上传，不做删除与对话；跨域受限时需用静态服务器打开而不是 `file://`；
- `Agent/xAgent` 默认读取 `config.ini`，首次运行会自动生成一份带 mock 值的配置并提示修改；
- `AnythingLLMServer/` 与 `Playground/` 为空目录，仅作预留。
