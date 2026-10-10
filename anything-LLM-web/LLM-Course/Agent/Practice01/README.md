# LLM 单次对话练习（Practice01）

一个极简的 Python 练习项目：从 `config.ini` 读取 LLM 配置，向 OpenAI 兼容接口发起**一次**提问请求，拿到完整结果后以**打字机效果**逐字输出。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `chat.py` | 主程序：读配置 → 输入问题 → 非流式请求 → 打字机输出 |
| `config.ini` | 配置文件，`[llm]` 分区下的 `base_url` / `model` / `api_key` |

## 环境要求

- Python 3.8+
- 依赖库：`requests`

```bash
pip install requests
```

需要一个 OpenAI 兼容的接口服务，例如 LM Studio、Ollama、本地推理服务或云端 API。

## 配置

编辑 `config.ini`：

```ini
[llm]
base_url = http://127.0.0.1:1234/v1
model = deepseek-r1-distill-qwen-1.5b
api_key = sk-local-dummy
```

- `base_url`：接口根地址，程序会自动拼接 `/chat/completions`
- `model`：模型名称，需与本地实际加载的模型名一致
- `api_key`：密钥；本地服务可填任意非空值

## 运行

```bash
python chat.py
```

出现 `请输入问题：` 后输入内容并回车，模型的完整回答会以打字机样式打印出来。

> PowerShell 控制台若出现中文乱码，先执行 `chcp 65001` 再运行。

## 实现要点

- **单轮对话**：`messages` 只包含一条 user 消息，不携带任何历史上下文
- **非流式请求**：请求体显式设置 `"stream": False`，一次性接收完整 JSON 响应
- **打字机输出**：拿到完整文本后由 `print_typing()` 逐字写出（`TYPE_DELAY` 控制速度，换行处停顿稍长），并非流式传输
- **异常处理**：配置文件缺失 / 缺少 `[llm]` 分区 / 请求超时 / HTTP 错误 / 网络异常 / 返回数据格式异常，均打印中文错误信息并以退出码 1 结束

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `ModuleNotFoundError: No module named 'requests'` | 执行 `pip install requests` |
| `HTTP 错误 404` | `base_url` 路径不对，应指向 `/v1` 之类的接口根路径 |
| `HTTP 错误 400` | `model` 与服务端实际模型名不匹配，先查询 `/v1/models` 确认 |
| `请求超时（120s）` | 调小 `TIMEOUT` 或换更快的模型；本地模型首次加载较慢可适当调大 |
| `网络请求失败` | 接口服务未启动或端口不通，先确认服务状态 |