# LLM 多轮对话 + Function Call 练习（Practice02）

基于 `Practice01` 扩展：在多轮对话基础上新增两个 function call 工具——`getDate`（获取当前电脑的日期和时间，调用本地 `date` 命令）和 `isLeapYear`（用 Python 代码计算输入年份是否是闰年）。

## 与 Practice01 的差异

1. **新增工具 `getDate`**：请求体携带 `tools` 定义，模型判断需要时返回 `tool_calls`；
2. **日期时间由本地命令获取**：`getDate` 通过 `subprocess` 调用本地 `date` 命令（Windows 执行 `date /t` 与 `time /t`，其它平台执行 `date +格式`），**不使用** Python 的 `datetime` 模块；
3. **新增工具 `isLeapYear`**：参数为整数 `year`，闰年判断（能被 4 整除且不能被 100 整除，或能被 400 整除）由 Python 代码 `is_leap_year()` 完成；
4. **工具调用循环**：模型要求调用工具时，程序执行工具并回传结果，再次请求模型，直到给出最终回答（单次提问最多 `MAX_TOOL_ROUNDS` 次，防止死循环）；
5. **双模式工具调用**（适配本课程使用的 `deepseek-r1-distill-qwen-1.5b`——它不输出原生 `tool_calls`）：
   - 原生模式：模型返回 `tool_calls` 时按 OpenAI 规范执行，结果以 `role="tool"` 消息回传；
   - 文本兜底模式：从模型回复文本中解析 `{"name": "getDate", "arguments": {}}` JSON（自动归一化全角引号等标点），结果以 `【工具执行结果】` 的 user 消息回传；
   - 时间类 / 闰年类问题模型漏调或调错工具时自动追问一次；模型反复重复调用同一工具或达到调用上限时，直接用工具结果作为答案（闰年问题优先取 `isLeapYear` 的结果），保证演示可用。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `chat.py` | 主程序：读配置 → 多轮对话 → 工具声明 / 分发执行 → 打字机输出 |
| `config.ini` | 配置文件，`[llm]` 分区下的 `base_url` / `model` / `api_key` |

## 环境要求

- Python 3.8+
- 依赖库：`requests`

```bash
pip install requests
```

需要一个支持 function call（`tools` / `tool_calls`）的 OpenAI 兼容接口服务。

## 配置

编辑 `config.ini`：

```ini
[llm]
base_url = http://127.0.0.1:1234/v1
model = deepseek-r1-distill-qwen-1.5b
api_key = sk-local-dummy
```

## 运行

```bash
python chat.py
```

出现 `请输入问题：` 后输入内容并回车，例如：

```text
请输入问题：现在几点了？
```

若模型决定调用工具，控制台会打印：

```text
[调用工具] getDate({})
[工具结果] 2026/10/19 周一 14:30
```

或闰年问题：

```text
请输入问题：2024年是闰年吗？

[调用工具] isLeapYear({"year": 2024})
[工具结果] 2024 年是闰年
```

随后模型基于工具结果生成最终回答，以打字机样式打印。

> PowerShell 控制台若出现中文乱码，先执行 `chcp 65001` 再运行。

## 实现要点

- **工具定义**：`TOOLS` 常量按 OpenAI function call 规范声明 `getDate`（无参数）与 `isLeapYear`（必填整数参数 `year`）
- **本地命令取时间**：`get_date()` 只用 `subprocess.run` 调用系统 `date` 命令，`errors="replace"` 兜底编码差异，命令失败会返回错误文本给模型
- **Python 计算闰年**：`is_leap_year()` 用纯 Python 实现闰年规则；`call_tool()` 对 `year` 做类型校验，非法参数返回错误文本给模型而不是崩溃
- **工具分发**：`call_tool()` 按工具名路由，未知工具或执行异常均以字符串回传，不让程序崩溃
- **上下文完整保留**：本轮的 user 消息、assistant 回复、`role="tool"` 结果都会写入 `messages`，后续轮次可继续引用
- **异常处理**：配置缺失 / 请求超时 / HTTP 错误 / 网络异常 / 返回数据格式异常 / 工具执行异常，均有中文提示

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `HTTP 错误 400` 并提示 `tools` 不支持 | 所连模型或服务不支持 function call，换支持工具调用的模型 |
| 模型始终不调用工具 | 换用支持 tool_calls 的模型，或在提问中明确“用工具计算/查询” |
| `[工具结果] getDate 执行失败` | 检查本机 `date` / `time` 命令是否可用（PowerShell 下可手动执行验证） |
| `isLeapYear 参数错误` | 模型传入的 `year` 不是整数，重问时在问题里写清年份（如“2024年是闰年吗”） |
| `请求超时（120s）` | 调小 `TIMEOUT` 或换更快的本地模型；若本地模型长时间无响应，检查推理服务状态（GPU 异常时可在 LM Studio 中用 CPU 模式重载模型） |
| `网络请求失败` | 接口服务未启动或端口不通，先确认服务状态 |
