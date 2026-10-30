# xAgent

基于 Practice03 的 `chat.py` 代码改造而来，目标是**核心（core）与界面（tui）分离**，并为未来扩展 Web、Desktop 等界面预留统一的接口。除了本地文件与系统工具，还提供 `fetchWeb` 网页正文抓取工具、`searchWeb` 百度千帆 AI 搜索工具，以及可执行本地命令行的 `RunCli` 工具。

## 设计思路

- **core 与 UI 分离**：`xAgent/core/` 包含配置、工具、Agent 编排、参数容错、LLM 客户端等全部业务逻辑，不依赖 Textual。
- **面向界面扩展**：`core` 通过 `Agent.send()` 返回 `AgentEvent` 迭代器，TUI、Web、Desktop 都只消费事件即可实现各自的交互方式。
- **业务模块化、层级化**：按“配置（config）→ 工具（tools）→ 参数容错（recovery）→ 提示词（prompts）→ LLM（llm）→ 编排（agent）→ 事件（events）”划分模块，严格遵循软件工程的代码隔离规范。
- **工具自动同步**：`core/prompts.py` 根据 `ToolRegistry` 自动生成系统提示词，新增工具时无需手动修改提示词。
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
│   ├── config.py      # 配置加载（支持 [llm]、[agent]、[search]）
│   ├── errors.py      # 领域异常
│   ├── events.py      # 事件与结果对象（AgentEvent）
│   ├── prompts.py     # 根据 ToolRegistry 自动生成系统提示词
│   ├── recovery.py    # 参数容错、关键词提示、追问
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py  # OpenAI 兼容客户端
│   │   └── messages.py # 工具调用解析、消息构造
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── base.py    # ToolSpec、ToolRegistry
│   │   ├── files.py   # 10 个本地文件操作工具
│   │   ├── system.py  # getDate、isLeapYear
│   │   ├── web.py     # fetchWeb（curl + trafilatura）
│   │   ├── search.py  # searchWeb（百度千帆 AI 搜索）
│   │   └── cli.py     # RunCli（执行本地命令行程序）
│   └── utils/
│       ├── __init__.py
│       ├── paths.py   # 路径解析
│       └── text.py    # 编码、二进制探测、长度裁剪
├── tui/               # TUI 界面（使用 Textual）
│   ├── __init__.py
│   ├── app.py         # Textual App、事件消费、布局
│   ├── chat_input.py  # 多行输入框、等待转圈动画
│   ├── ime_compat.py  # 中文输入法快速输入兼容补丁
│   └── styles.tcss    # 样式表
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

## TUI 输入

- `Enter` 发送问题；
- `Shift+Enter` 换行（部分终端不支持时可用 `Ctrl+J`）；
- 支持直接粘贴多行文本，不会再被截断成第一行；
- 等待模型回复时，输入框前方会显示转圈动画；
- `tui/ime_compat.py` 修复了 Textual 8.2.x 在输入法一次提交多个中文时把
  Kitty 按键报告误当成普通文本、显示成 `[32;;20320:...` 的问题。

## 配置

编辑 `xAgent/config.ini`：

```ini
[llm]
base_url = http://127.0.0.1:1234/v1
model = deepseek-r1-distill-qwen-1.5b
api_key = sk-local-dummy
timeout = 300
temperature = 0.2

[agent]
max_tool_rounds = 8
max_list_entries = 200
max_read_bytes = 200000
max_tool_result_chars = 12000
web_timeout_seconds = 20
cli_timeout_seconds = 30
cli_max_output_chars = 6000

[search]
baidu_qianfan_api_key = bce-v3/ALTAK-...
endpoint = https://qianfan.baidubce.com/v2/ai_search/web_search
timeout = 30
```

`fetchWeb` 需要本地可执行的 `curl`（Windows 10/11 通常自带 `curl.exe`），
并通过 `pip install trafilatura` 安装正文提取依赖。

`searchWeb` 需要在 `config.ini` 的 `[search]` 段配置百度千帆 AI 搜索的 API Key（`baidu_qianfan_api_key`）。

`RunCli` 使用 `subprocess` 以参数列表 + `shell=False` 执行命令：`command` 只写程序名或可执行文件路径，参数放进 `args` 数组。它会把 stdout、stderr 和退出码一起回传给模型；`cli_timeout_seconds` 控制单次执行超时，`cli_max_output_chars` 控制 stdout / stderr 各自的展示上限，超出时会明确标注截断情况。

## 安装依赖

```bash
cd F:\xie_teacher_documents\anything-LLM-web\LLM-Course\Agent\xAgent
pip install -r requirements.txt
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

## 核心接口（供未来 Web/Desktop 使用）

```python
from xAgent.core import Agent, load_config, build_default_registry, OpenAICompatibleClient

config = load_config()
registry = build_default_registry(config.agent, config.search)
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