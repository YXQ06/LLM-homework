# xAgent

基于 Practice03 的 `chat.py` 代码改造而来，目标是**核心（core）与界面（tui）分离**，并为未来扩展 Web、Desktop 等界面预留统一的接口。除了本地文件与系统工具，还提供 `fetchWeb` 网页正文抓取工具、`searchWeb` 百度千帆 AI 搜索工具、可执行本地命令行的 `RunCli` 工具，以及可连接外部 MCP Server 并动态注册工具的 **MCP Client**。

## 设计思路

- **core 与 UI 分离**：`xAgent/core/` 包含配置、工具、Agent 编排、参数容错、LLM 客户端等全部业务逻辑，不依赖 Textual。
- **面向界面扩展**：`core` 通过 `Agent.send()` 返回 `AgentEvent` 迭代器，TUI、Web、Desktop 都只消费事件即可实现各自的交互方式。
- **业务模块化、层级化**：按“配置（config）→ 工具（tools）→ 参数容错（recovery）→ 提示词（prompts）→ LLM（llm）→ 编排（agent）→ 事件（events）”划分模块，严格遵循软件工程的代码隔离规范。
- **工具自动同步**：`core/prompts.py` 根据 `ToolRegistry` 自动生成系统提示词，新增工具时无需手动修改提示词。
- **MCP 动态工具**：进入 TUI 后由后台线程读取项目目录的 `.mcp.json`，连接 MCP Server、调用 `list_tools()`，把远端工具转换成与本地工具完全相同的 `ToolSpec`，统一走 Function Calling 执行链路；界面不会因为 MCP 连接而阻塞。
- **参数容错保留并强化**：沿用 Practice03 的思路（同义参数名归一化、缺失 path 补齐、path 指向目录修正、编造路径修正），并统一输出到 `ArgumentRepaired` 事件中，由界面展示。

## 目录结构

```
xAgent/
├── __init__.py
├── __main__.py        # 入口：python -m xAgent（TUI）或 --cli
├── config.ini
├── README.md
├── core/              # 核心业务逻辑（界面无关）
│   ├── __init__.py
│   ├── agent.py       # 多轮对话、工具循环、循环检测、漏调兜底
│   ├── agents_md.py   # AGENTS.md 读取与“一次注入、全程复用”消息构造
│   ├── config.py      # 配置加载、首次运行生成 config.ini
│   ├── context.py     # Token 监控、上下文归档、压缩摘要与关键词检索
│   ├── session.py     # JSONL 会话保存、列出与恢复
│   ├── errors.py      # 领域异常
│   ├── events.py      # 事件与结果对象（AgentEvent）
│   ├── prompts.py     # 根据 ToolRegistry 与 Skill 自动生成系统提示词
│   ├── recovery.py    # 参数容错、关键词提示、追问
│   ├── skills.py      # Skill 发现、Frontmatter 解析与 loadSkill 动态加载
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py  # OpenAI 兼容客户端
│   │   └── messages.py # 工具调用解析、消息构造
│   ├── mcp/            # MCP Client（配置、连接、工具映射）
│   │   ├── __init__.py
│   │   ├── config.py  # 读取 .mcp.json（stdio / streamable_http / sse）
│   │   ├── client.py  # MCP SDK 连接、list_tools、call_tool 转发
│   │   └── tools.py   # 工具命名映射、参数 Schema、结果格式化
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── base.py    # ToolSpec、ToolRegistry
│   │   ├── files.py   # 10 个本地文件操作工具
│   │   ├── system.py  # getDate、isLeapYear
│   │   ├── web.py     # fetchWeb（curl + trafilatura）
│   │   ├── search.py  # searchWeb（百度千帆 AI 搜索）
│   │   ├── recall.py  # recallContext（检索 .agents/context 归档）
│   │   └── cli.py     # RunCli（执行本地命令行程序）
│   └── utils/
│       ├── __init__.py
│       ├── paths.py   # 路径解析
│       └── text.py    # 编码、二进制探测、长度裁剪
├── tui/               # TUI 界面（使用 Textual）
│   ├── __init__.py
│   ├── app.py         # Textual App、事件消费、会话与斜杠命令、异步 MCP
│   ├── chat_input.py  # 多行输入框、等待转圈动画、补全按键转发
│   ├── command_palette.py # 斜杠命令补全引导面板
│   ├── commands.py    # 斜杠命令元数据、匹配与帮助文本
│   ├── compact_panel.py# 上下文压缩前后两栏预览面板
│   ├── config_missing.py # 首次生成配置后的提示对话框
│   ├── ime_compat.py  # 中文输入法快速输入兼容补丁
│   ├── mcp_status.py  # MCP 服务发现与在线状态探测（只读适配层）
│   ├── session_panel.py # 历史会话选择面板
│   ├── themes.py      # 浅色 / 深色主题定义与持久化
│   ├── tool_sidebar.py# 右侧“可用工具列表”面板
│   └── styles.tcss    # 样式表（全部使用主题变量）
└── tests/             # 离线单元测试 + 可选联网冒烟测试
```

## 工具清单

| 工具名 | 说明 |
| --- | --- |
| `getCurrentDirectory` | 获取当前工作目录 |
| `listDirectory` | 列出目录下文件和子目录（大小、修改时间） |
| `getFileType` | 获取文件类型（扩展名 / MIME / 文本或二进制） |
| `readTextFile` | 读取纯文本文件（二进制拒绝） |
| `createFile` | 创建文件（目录不存在时自动创建） |
| `editFile` | 编辑文件（overwrite / append / replace） |
| `createDirectory` | 创建目录（支持多级） |
| `deleteDirectory` | 删除目录（支持非空目录，递归删除） |
| `deleteFile` | 删除文件（只删文件） |
| `renamePath` | 重命名文件或目录 |
| `getDate` | 获取系统日期时间（通过本地 `date` / `time` 命令） |
| `isLeapYear` | 判断闰年 |
| `fetchWeb` | 调用本地 `curl` 下载网页，再用 `trafilatura` 提取 Markdown 正文 |
| `searchWeb` | 使用百度千帆 AI 搜索（baidu_search_v2）检索网页，返回标题、链接、来源、时间和摘要 |
| `RunCli` | 以参数列表方式执行本地命令行程序，返回 stdout、stderr 和退出码（超时终止、输出截断） |
| `loadSkill` | 按需加载 Skill 的完整 `SKILL.md` 或 Skill 目录内的附件文件 |
| `recallContext` | 在 `.agents/context/*.jsonl` 历史归档中做关键词检索；`text` 必填，`id` 可选 |
| `MCP 动态工具` | 进入 TUI 后由后台线程从 `.mcp.json` 连接 MCP Server，通过 `list_tools()` 动态注册，名称、描述和参数 Schema 全部来自 Server |

MCP 工具与上面这些本地工具共用同一个 `ToolRegistry`，LLM 不需要区分二者；
工具调用日志里会显示实际暴露给模型的工具名。

## Agent Skills

xAgent 以**程序启动时的当前工作目录**为项目根目录，从该目录向上查找
`.agents/skills` 或 `tools_skills/.agents/skills`。也可以用环境变量
`XAGENT_SKILLS_DIR` 直接指定 Skill 根目录。

每个 Skill 是包含 `SKILL.md` 的目录，`SKILL.md` 开头必须有 YAML Frontmatter：

```markdown
---
name: notice-writing
description: 撰写公司内部通知
---

# 具体步骤...
```

初始化阶段只读取 `name` 和 `description`，并把它们加入 System Prompt；
完整正文和附件只有在模型调用 `loadSkill` 时才会进入对话上下文：

```json
{"name": "loadSkill", "arguments": {"name": "notice-writing"}}
{"name": "loadSkill", "arguments": {"name": "notice-writing", "filename": "references/example.md"}}
```

`filename` 支持相对 Skill 目录的路径、项目相对路径和绝对路径；只写文件名时会在
该 Skill 目录内递归查找，存在多个同名文件时返回候选路径。所有读取都被限制在
指定 Skill 目录内部。

## TUI 输入与界面

- `Enter` 发送问题；
- `Shift+Enter` 换行（部分终端不支持时可用 `Ctrl+J`）；
- 支持直接粘贴多行文本，不会再被截断成第一行；
- 等待模型回复时，输入框前方会显示转圈动画；
- `tui/ime_compat.py` 修复了 Textual 8.2.x 在输入法一次提交多个中文时把
  Kitty 按键报告误当成普通文本、显示成 `[32;;20320:...` 的问题。
- 界面为左右分栏：左侧保留对话历史、工具日志和输入框，右侧为“可用工具列表”
  侧边栏；窗口宽度小于 110 列时侧边栏自动收起，展开后恢复。
- `Ctrl+T` 或标题栏右上角按钮切换浅色 / 深色主题，选择会保存到
  `~/.xagent/tui_settings.json`，下次启动自动恢复。
- `Ctrl+B` 或标题栏按钮折叠 / 展开侧边栏；折叠态只保留窄边标识，点击即可展开。
- 标题栏下方的上下文状态条实时显示 `当前上下文 k / 模型窗口 k | 已占用百分比`。
  如果无法从 `/models` 接口或配置中获取模型窗口，会使用 128k 默认值并标注「默认」。
- MCP 服务在进入界面后才开始后台连接。侧边栏先显示“正在后台连接…”，连接完成后
  自动刷新为在线 / 离线状态，并列出每个 Server 实际发现的工具名。

## 斜杠命令与会话

在输入框中输入 `/` 会弹出补全引导面板：`↑/↓` 选择，`Tab` 补全，`Esc` 关闭，
`Enter` 执行完整命令。可用命令：

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示命令列表与用法 |
| `/new [标题]` | 自动保存当前会话，然后开始新会话 |
| `/resume`（别名 `/history`） | 打开历史会话面板；`↑/↓` 选择，`Enter` 恢复，`Esc` 取消 |
| `/save [标题]` | 把当前会话保存到 `.agents/sessions/<id>.jsonl` |
| `/compact` | 归档并压缩上下文 |
| `/context` | 查看上下文占用详情 |
| `/clear` | 清空屏幕日志（不删除对话历史） |
| `/tools` | 列出当前注册的全部工具 |
| `/mcp` | 查看 MCP 服务器与工具状态 |
| `/theme` | 切换浅色 / 深色主题 |
| `/quit`（别名 `/exit`） | 保存会话并退出 |

会话文件使用 JSONL 事件日志：第一行是会话头（标题、时间、AGENTS.md 哈希等），
之后每行是一条完整消息。默认目录为当前项目下的 `.agents/sessions`，可通过
`config.ini` 的 `session_dir` 修改。`/new`、`/resume`、`/quit` 前会按配置自动
保存当前会话；每轮对话结束后也可以按配置自动保存。

## AGENTS.md 项目说明

xAgent 以启动时的当前工作目录为项目根目录。会话首次加载时，如果根目录存在
`AGENTS.md`，会把它的内容作为一条普通 user 消息追加到聊天历史，并立即写入
会话事件日志；之后所有轮次、恢复会话以及上下文压缩都直接复用这条消息，不会
重复读取文件，也不会重复注入。这样既保证项目约束始终生效，也有利于保持模型
缓存稳定。

文件名和最大读取字符数可通过 `agents_md_filename`、`agents_md_max_chars` 配置；
内容过长时会截断并明确标注。

## 上下文压缩与历史检索

长时间任务很容易把上下文窗口占满，xAgent 用一套简单的本地方案解决：

1. **长度监控**：每次 LLM 响应后检查 Token 使用量，优先读取接口返回的 `usage`；
   没有 `usage` 时按中英文字符比例估算，并计入 System Prompt、用户消息、
   对话历史和工具调用消息。达到窗口的 60% 后，状态条变红并提示执行 `/compact`，
   普通提问会被暂停（`/compact` 仍可用）。
2. **手动压缩**：在输入框执行 `/compact`。Agent 进入专门的压缩状态，不再执行
   任何 Function Calling 工具；先把完整原始上下文写入
   `.agents/context/YYYYMMDD-HHMMSS.jsonl`，落盘成功后才调用一次不带工具的 LLM
   生成约 5,000 Tokens 的 Markdown 工作摘要。
3. **替换上下文**：摘要成功后，保留 System Prompt 和工具定义，用摘要 + 归档说明
   替换历史消息；TUI 会弹出两栏浮动面板，左侧原文、右侧摘要，可滚动并关闭。
   压缩失败时原上下文保持不变，可以直接重新执行 `/compact`。
4. **历史检索**：摘要末尾会记录归档编号。Agent 认为摘要缺少细节时，可以调用普通
   工具 `recallContext`（`text` 必填，`id` 可选）在归档中做关键词全文检索，
   返回命中片段、归档编号和消息位置；不需要向量数据库或 Embedding。

相关配置位于 `config.ini` 的 `[llm]` / `[agent]`：

```ini
[llm]
context_window = 0          # 0 = 自动探测；探测不到用 128k 并显示「默认」

[agent]
context_warn_ratio = 0.6
compact_target_tokens = 5000
context_archive_dir = .agents/context
recall_max_chars = 6000
recall_max_results = 8
```

## MCP Client

xAgent 启动时会从**当前工作目录**（也就是用户的项目目录，而不是 xAgent
源码目录）读取 `.mcp.json`；如果当前目录没有，会向上逐级查找父目录。
也可以用环境变量 `XAGENT_MCP_CONFIG` 指定任意 JSON 文件。

### 配置文件格式

兼容 Claude Desktop / MCP 社区的 `{"mcpServers": {...}}` 结构，支持
`stdio` 与 `streamable_http`（额外兼容 `sse`）：

```json
{
  "mcpServers": {
    "local-tools": {
      "type": "stdio",
      "command": "python",
      "args": ["server.py"],
      "env": {"TOKEN": "${MCP_TOKEN}"},
      "cwd": ".",
      "timeout": 60
    },
    "remote-tools": {
      "type": "streamable_http",
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer ${MCP_TOKEN}"},
      "timeout": 60,
      "connect_timeout": 20
    }
  }
}
```

- `type` 支持 `stdio`、`streamable_http`、`streamable-http`、`http`、`sse`；
  省略时会根据 `command` / `url` 自动判断。
- `timeout` 是单次工具调用/读取超时；`connect_timeout` 是连接超时。
- `env`、`headers`、`url`、`command`、`args` 中的 `${VAR}`、`$VAR`、`%VAR%`
  会自动展开环境变量。
- 单个 Server 连接失败只会输出明确的错误信息，不影响其他 MCP Server、
  本地工具或 Agent 的正常对话。

#### 可选：启动时自动拉起本地服务

如果 `streamable_http` Server 运行在本机，可以在 `.mcp.json` 根节点增加
`autoStart`。xAgent 会在连接 MCP Server 之前执行这条命令，适合放一键启动
脚本（脚本应幂等，已在运行就跳过）：

```json
{
  "mcpServers": { "...": "..." },
  "autoStart": {
    "command": "powershell",
    "args": [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      "start_mcp_servers.ps1"
    ],
    "cwd": ".",
    "timeout": 180
  }
}
```

- `cwd` 相对 `.mcp.json` 所在目录解析；`args` 同样支持 `${VAR}`。
- `autoStart` 失败只会显示警告，不会阻止 xAgent 继续连接其他可用的 Server。
- 启动脚本的 stdout / stderr 写入 `xAgent/logs/auto-start.out.log` 和
  `xAgent/logs/auto-start.err.log`。
- 如果不想自动启动，删除 `.mcp.json` 中的 `autoStart` 节点即可，之后仍可
  手动运行 `start_mcp_servers.bat`。

> 当前项目配置说明：原来的远程 `Bazi-MCP` 端点已返回 `Url is expired`，
> 因此改为在本机以 Streamable HTTP 方式运行同一个 `bazi-mcp` 包（端口
> `8092`），由 `start_mcp_servers.ps1` 自动拉起。这样 `Bazi-MCP` 和本地
> `Bazi` 都能在线，并且仍然保留 HTTP 传输方式。以后如果拿到新的远程 URL，
> 直接把 `Bazi-MCP` 的 `url` 换回去即可。

### 启动本地 MCP Server

`streamable_http` 类型的 MCP Server 需要独立进程。项目根目录的 `.mcp.json`
已经配置了 `autoStart`，所以正常启动 xAgent 时会自动拉起本地服务，**不需要
每次手动运行启动脚本**：

```powershell
cd F:\xie_teacher_documents\anything-LLM-web\LLM-Course\Agent
python -m xAgent
```

也可以手动管理这些服务：

```powershell
cd F:\xie_teacher_documents\anything-LLM-web\LLM-Course\Agent\xAgent

# 启动 8080（宠物医院 REST API）+ 8090（PetHospitalMCP）+ 8091（AnythingLLMMCP）+ 8092（Bazi-MCP）
.\start_mcp_servers.bat

# 或者只启动 MCP Server，不启动宠物医院后端
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_mcp_servers.ps1 -NoPetHospitalApi

# 停止 MCP Server（加 -IncludePetHospitalApi 同时停止 8080）
.\stop_mcp_servers.bat

# 一键启动服务并进入 TUI（启动脚本幂等，xAgent 启动时也会自动执行）
.\run_xAgent.bat
```

- `Bazi-MCP`（8092）使用本机安装的 `bazi-mcp` npm 包以 Streamable HTTP
  方式运行。如果 `.tools/bazi-http` 不存在（例如重新克隆项目），先执行一次：

  ```powershell
  cd xAgent
  mkdir .tools\bazi-http -Force
  cd .tools\bazi-http
  npm init -y
  npm install bazi-mcp@0.1.0
  ```

- `PetHospitalMCP`（8090）依赖宠物医院 REST API（8080）；`autoStart` 会同时
  启动后端。若手动只启动 MCP，后端未运行时 MCP 仍可连接，但查询类工具会
  返回“API 请求失败”。
- `AnythingLLMMCP`（8091）需要设置 `ANYTHINGLLM_API_KEY` 才能实际调用
  AnythingLLM 工具；未设置时 MCP 可连接，工具会返回缺少 API Key 的提示。
- 启动日志写入 `xAgent/logs/`。
- `autoStart` 只在 xAgent 启动时执行；已经打开的 xAgent 不会自动重连后来
  启动的 Server，需要重新打开 xAgent。

### 工具发现、命名与调用

1. TUI 挂载后由后台线程对每个 Server 建立 MCP `ClientSession`，调用
   `initialize()` 和 `list_tools()`；
2. 把每个远端工具转换成 `ToolSpec`，直接注册进现有 `ToolRegistry`，
   与 `RunCli`、`fetchWeb`、`loadSkill` 等本地工具一起出现在系统提示词中；
3. 内部维护 `LLM 工具名 -> (MCP Server, 原始工具名)` 的映射。工具名唯一时
   保持原名；多个 Server 重名或与本地工具冲突时自动加
   `<server>__<tool>` 前缀，保证 Function Calling 名称不冲突；
4. LLM 发起工具调用时，`ToolRegistry` 找到对应 `ToolSpec`，由 MCP Client
   转发给正确的 Server 执行 `call_tool()`；
5. 返回结果会整理文本、图片/音频占位、资源链接、`structuredContent`
   结构化数据；MCP 返回 `isError=true` 时会作为工具错误回传给模型。

右侧工具侧边栏在 MCP 后台加载完成后会展示真实的连接状态、工具数量以及每个
Server 发现的工具名。若以库的形式嵌入且未启用 MCP（`XAgentApp(enable_mcp=False)`），
`tui/mcp_status.py` 仍提供只读的配置发现与端口探测，不会启动或调用 MCP 服务。

### 代码中启用

```python
from xAgent.core import build_default_registry, load_config
from xAgent.core.mcp import create_mcp_manager, register_mcp_tools

config = load_config()
registry = build_default_registry(config.agent, config.search)
manager = create_mcp_manager(config.agent)
register_mcp_tools(registry, manager)

for message in manager.startup_messages():
    print(message)
# 退出时调用 manager.close()，或依赖进程退出时自动关闭
```

## 配置

xAgent 从**启动命令所在目录**读取 `config.ini`。正常通过 `python -m xAgent`
启动时，如果当前目录没有 `config.ini`，会先自动生成一份带 mock 信息的完整配置，
并弹出对话框提示修改 `[llm]` 后重新启动。直接以库方式构造 `XAgentApp` 时，如果
当前目录没有配置，会回退到 xAgent 源码目录的 `config.ini`（方便测试和嵌入）。
也可以设置环境变量 `XAGENT_CONFIG` 指定配置文件。

```ini
[llm]
base_url = http://127.0.0.1:1234/v1
model = deepseek-r1-distill-qwen-1.5b
api_key = sk-local-dummy
timeout = 300
temperature = 0.2
context_window = 0

[agent]
max_tool_rounds = 8
max_list_entries = 200
max_read_bytes = 200000
max_tool_result_chars = 12000
web_timeout_seconds = 20
cli_timeout_seconds = 30
cli_max_output_chars = 6000

# 上下文
context_warn_ratio = 0.6
compact_target_tokens = 5000
context_archive_dir = .agents/context
recall_max_chars = 6000
recall_max_results = 8
compact_transcript_max_chars = 0
compact_transcript_min_chars = 20000
compact_transcript_ratio = 0.7
context_estimate_chars_per_token = 4.0

# 会话
session_dir = .agents/sessions
session_max_history = 50
session_auto_save_after_turn = true
session_auto_save_on_exit = true

# AGENTS.md
agents_md_filename = AGENTS.md
agents_md_max_chars = 200000

# TUI
tui_sidebar_collapse_width = 110
tui_spinner_interval = 0.1
tui_command_panel_max_items = 8

# MCP 默认超时（.mcp.json 未显式配置时使用）
mcp_timeout = 60
mcp_connect_timeout = 20
mcp_startup_wait_extra = 15

[search]
baidu_qianfan_api_key = bce-v3/ALTAK-...
endpoint = https://qianfan.baidubce.com/v2/ai_search/web_search
timeout = 30
```

`fetchWeb` 需要本地可执行的 `curl`（Windows 10/11 通常自带 `curl.exe`），
并通过 `pip install trafilatura` 安装正文提取依赖。

`searchWeb` 需要在 `config.ini` 的 `[search]` 段配置百度千帆 AI 搜索的 API Key（`baidu_qianfan_api_key`）。

### 网页工具排障（searchWeb / fetchWeb）

- **searchWeb 返回 401 / 216003（Fail to parse apikey authorization）**：
  这是鉴权问题，不是网络问题。请确认启动目录下的 `config.ini`（通常是 `Agent/config.ini`）
  里 `[search] baidu_qianfan_api_key` 是完整的 `bce-v3/ALTAK-...` 密钥，而不是
  `mock-search-key` 等占位符；密钥不要带空格、换行、引号或重复的 `Bearer` 前缀。
  工具会在读取时自动清理这些字符，但占位符会被直接拒绝，不会向百度发送请求。
  鉴权失败时工具不会重试，修复配置后重启 xAgent 即可。
- **fetchWeb 返回 403 / curl (22)**：
  这是目标站点反爬，不是密钥问题。工具已携带标准 Chrome User-Agent、
  `Accept`/`Accept-Language`/`Sec-Fetch`/`sec-ch-ua` 等请求头，并复用普通 Cookie。
  若仍返回 403（例如百度百科触发“百度安全验证”），工具会停止重试并提示降级：
  改用 `searchWeb` 获取摘要，或更换镜像/其他权威站点链接。
  仅 429/5xx 会做一次短暂退避重试；401/403/404 不会重复请求。
- **抓取同一页面多次后正文为空**：已关闭 trafilatura 的跨请求全局去重缓存，
  重复抓取同一权威页面仍可正常返回正文。

`RunCli` 使用 `subprocess` 以参数列表 + `shell=False` 执行命令：`command` 只写程序名或可执行文件路径，参数放进 `args` 数组。它会把 stdout、stderr 和退出码一起回传给模型；`cli_timeout_seconds` 控制单次执行超时，`cli_max_output_chars` 控制 stdout / stderr 各自的展示上限，超出时会明确标注截断情况。

## 安装依赖

```bash
cd F:\xie_teacher_documents\anything-LLM-web\LLM-Course\Agent\xAgent
pip install -r requirements.txt
```

MCP Client 依赖官方 MCP Python SDK（`mcp`，Python 3.10+），同时兼容
SDK 1.x / 2.x。若只需安装 MCP 依赖，可单独执行：

```bash
pip install "mcp>=1.9"
```

## 运行

```bash
# 进入 Agent 目录
cd F:\xie_teacher_documents\anything-LLM-web\LLM-Course\Agent

# 启动 TUI（默认）
python -m xAgent

# 启动 CLI 模式（无 TUI 环境也可用）
python -m xAgent --cli
```

`.mcp.json` 放在**启动命令所在目录**即可。例如从 `Agent` 目录启动时读取
`Agent/.mcp.json`；从 `xAgent` 目录启动时读取 `xAgent/.mcp.json`。也可以在
任意目录通过 `XAGENT_MCP_CONFIG` 指向配置文件。

## 测试

离线单元测试不需要网络，也不会调用真实的大模型或搜索接口。
请从 `Agent` 目录（`xAgent` 包的上一级）运行，确保能导入包：

```bash
cd F:\xie_teacher_documents\anything-LLM-web\LLM-Course\Agent
python -m unittest discover -s xAgent/tests -t . -v
```

如需额外执行真实联网冒烟测试（抓取 example.com、调用一次百度千帆搜索），
请显式设置环境变量：

```powershell
cd F:\xie_teacher_documents\anything-LLM-web\LLM-Course\Agent
$env:XAGENT_LIVE_TESTS = "1"
python -m unittest xAgent.tests.test_live_integration -v
```

联网测试会读取 `config.ini` 中的真实 API Key，但不会把 Key 写入日志或测试输出。

如需验证真实 MCP Server 的连接、`list_tools()` 和一次工具调用：

```powershell
cd F:\xie_teacher_documents\anything-LLM-web\LLM-Course\Agent
$env:XAGENT_LIVE_MCP = "1"
python -m unittest xAgent.tests.test_mcp_live -v
```

该测试会读取当前目录（或父目录）的 `.mcp.json`，连接所有配置的 Server；
连接失败时会打印明确的错误信息。未设置 `XAGENT_LIVE_MCP` 时自动跳过。

## 核心接口（供未来 Web/Desktop 使用）

```python
from xAgent.core import Agent, load_config, build_default_registry, OpenAICompatibleClient
from xAgent.core.mcp import create_mcp_manager, register_mcp_tools

config = load_config()
registry = build_default_registry(config.agent, config.search)

mcp_manager = create_mcp_manager(config.agent)
register_mcp_tools(registry, mcp_manager)  # 连接 .mcp.json 并注册 MCP 工具

client = OpenAICompatibleClient(config.llm)
agent = Agent(client, registry, config.agent)

# 事件流
for event in agent.send("列出当前目录下的文件"):
    ...
```

所有事件类型都定义在 `xAgent.core.events`，界面无需关心 HTTP 请求、工具实现细节。

## 备注

- `core` 不依赖 `textual`，只有 `tui` 模块才依赖 Textual。
- 与 Practice03 相同：`deleteDirectory`、`deleteFile`、`editFile`、`renamePath` 按“试用要求”不做路径安全限制，请谨慎操作。
- `RunCli` 按教学阶段要求暂不提供命令白名单、黑名单或审批机制，命令以运行 xAgent 的当前用户权限执行；仅用于受控教学实验环境。
- 参数容错逻辑已抽取到 `core/recovery.py`，便于统一维护和测试。