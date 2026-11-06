# -*- coding: utf-8 -*-
"""通过 createSkill 的底层 handler（SkillRegistry.create_skill）创建 OfficeCLI 技能。"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

XAGENT = r"F:\xie_teacher_documents\anything-LLM-web\LLM-Course\Agent\xAgent"
sys.path.insert(0, XAGENT)

from core.skills import SkillRegistry

SKILL_CONTEXT = r"""---
name: officecli
description: "OfficeCLI 技能：用本地 officecli-win-x64.exe 读取、创建和修改 Word(.docx)、Excel(.xlsx)、PowerPoint(.pptx) 文档。触发词：OfficeCLI、officecli、调用 OfficeCLI、用 OfficeCLI 处理、docx/xlsx/pptx 读写。单文件 CLI，无需安装 Office 或联网；不确定语法时先运行 help，改完用 validate 验证。"
---

# OfficeCLI 技能

## 用途
使用本地可执行文件 `officecli-win-x64.exe` 读取、创建和修改 Word / Excel / PowerPoint 文档（.docx / .xlsx / .pptx）。它是 AI-friendly 的单文件 CLI，无依赖、不需要安装 Office。

## 可执行文件与参考文档
- 技能目录内副本：`F:\xie_teacher_documents\anything-LLM-web\LLM-Course\Agent\xAgent\.agents\skills\officecli\officecli-win-x64.exe`
- office_tools 内副本：`F:\xie_teacher_documents\anything-LLM-web\office_tools\OfficeCLI\officecli-win-x64.exe`
- 原始下载位置：`F:\xie_teacher_documents\anything-LLM-web\office_tools\officecli-win-x64.exe`
- 完整操作说明（参考手册）：`F:\xie_teacher_documents\anything-LLM-web\office_tools\OfficeCli操作说明.md`

官方说明中所有示例都写成 `officecli ...`，实际在 Windows 终端里把 `officecli` 替换为上面任一 exe 的完整路径即可，例如：

`F:\xie_teacher_documents\anything-LLM-web\office_tools\OfficeCLI\officecli-win-x64.exe create report.docx`

用 RunCli 工具调用时：`command` 填 exe 的完整路径，`args` 按顺序填参数数组（不经过 shell，含空格的参数作为一个整体元素即可；路径里的 `[N]` 无需额外转义）。

## 何时使用
- 用户要求创建、读取、查看、修改 .docx / .xlsx / .pptx 文档；
- 用户说「OfficeCLI」「officecli」「调用 OfficeCLI」「用 OfficeCLI 处理 Word / Excel / PPT」等。

## 核心原则（来自官方说明）
1. 分层策略：L1 读取（`view` / `get` / `query`）→ L2 DOM 编辑（`set` / `add` / `remove` / `move` / `swap`）→ L3 原始 XML（`raw` / `raw-set` / `add-part`）。优先用更高层。
2. 需要结构化输出时加 `--json`。
3. 不确定属性名、取值格式或命令语法时，一定先运行 help，不要猜：
   - `<exe> help`
   - `<exe> help docx paragraph`
   - `<exe> help docx set paragraph`
   - `<exe> help docx paragraph --json`
   格式别名：`word`→docx、`excel`→xlsx、`ppt` / `powerpoint`→pptx；动词：`add` / `set` / `get` / `query` / `remove`。
4. 长会话建议先 `open <file>`、结束时 `close <file>`（每次访问会自动启动 resident，空闲 60 秒超时；显式 open/close 为 12 分钟）。只有在非 officecli 程序要读取文件（python-docx、openpyxl、Word、渲染器、上传/交付）之前才需要 `save` 或 `close` 把修改刷新到磁盘；officecli 自身的 `get` / `query` / `view` / `dump` 总能看到最新编辑。
5. 修改完成后用 `validate` 和 / 或 `view <file> issues` 验证。

## 常用命令速查
```bash
<exe> create <file>                         # 按扩展名创建空白 docx/xlsx/pptx
<exe> view <file> <mode>                    # outline | stats | issues | text | annotated | html
<exe> get <file> <path> --depth N --json    # 读取节点及子节点
<exe> query <file> '<selector>'             # CSS 风格选择器
<exe> validate <file>                       # 按 OpenXML schema 校验
<exe> set <file> <path> --prop key=value    # 修改属性；--find X --replace Y 做查找替换
<exe> add <file> <parent> --type <type> --prop key=value
<exe> remove <file> <path>
<exe> batch <file> --commands '[...]' --json
<exe> dump <file> [<path>]                  # 导出可回放的 batch JSON
<exe> raw <file> <part>                     # 查看原始 XML
<exe> raw-set <file> <part> --xpath ... --action replace --xml ...
<exe> open <file> / <exe> save <file> / <exe> close <file>
```

最小示例：
```bash
<exe> create report.docx
<exe> add report.docx /body --type paragraph --prop text="Executive Summary" --prop style=Heading1
<exe> set data.xlsx /Sheet1/A1 --prop value="Name" --prop bold=true
<exe> create slides.pptx
<exe> add slides.pptx / --type slide --prop title="Q4 Report"
```

## 专门的文档场景（CLI 自带子技能）
`<exe> load_skill <name>` 会输出对应场景的 SKILL.md 规则，注意这是 officecli 自带命令，不是本 Agent 的 loadSkill 工具。开始前先检查：按最具体的 "When to use" 选一个，每个产物只加载一个、不要叠加；规则跨轮次有效，同一会话不必重复加载；两个不同产物分别加载。
- Word：`word`、`academic-paper`
- PowerPoint：`pptx`、`pitch-deck`（仅融资）、`morph-ppt`、`morph-ppt-3d`
- Excel：`excel`、`financial-model`、`data-dashboard`
例：融资路演 → `<exe> load_skill pitch-deck`。

## 常见坑
- 属性一律用 `--prop name="foo"`，没有 `--name`；
- 路径是 1-based（XPath 约定），`--index` 是 0-based（Excel 的 `add --type row/col` 例外，为 1-based）；
- PPT 的 `shape[1]` 通常是标题占位符，正文内容从 `shape[2]` 起；
- 修改前先关闭 PowerPoint / WPS 中打开的同名文件；
- 稳定 ID 路径（如 `shape[@id=...]`、`p[@paraId=...]`）在插入/删除后不会位移，多步操作优先使用；
- `batch` 默认原子：任一项失败会整体回滚；需要保留成功项时加 `--best-effort`。
"""

registry = SkillRegistry(project_root=XAGENT)
print("skills root:", registry.root)
print("names before:", registry.names)
result = registry.create_skill({"name": "OfficeCLI", "skillContext": SKILL_CONTEXT})
print("--- createSkill 返回 ---")
print(result)
print("--- 验证 ---")
print("names after:", registry.names)
skill = registry.get("officecli")
print("found:", skill is not None)
if skill is not None:
    print("file:", skill.file)
    print("description:", skill.description)
