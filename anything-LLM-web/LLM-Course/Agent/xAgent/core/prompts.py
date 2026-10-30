"""系统提示词构建。

工具清单直接从 ``ToolRegistry`` 生成，新增工具时提示词会自动同步，
避免“提示词里写了但代码没实现”或相反的情况。
"""

from __future__ import annotations

from .tools.base import ToolRegistry, ToolSpec


def _format_tool(spec: ToolSpec, index: int) -> str:
    if not spec.parameter_names:
        parameter_text = "无参数"
    else:
        parts = []
        for name in spec.parameter_names:
            parts.append(name if name in spec.required_parameters else f"{name}（可选）")
        parameter_text = "，".join(parts)
    return f"{index}. {spec.name}（{parameter_text}）：{spec.description}；"


def build_system_prompt(registry: ToolRegistry) -> str:
    """根据已注册工具生成内置系统提示词。"""
    tool_lines = "\n".join(
        _format_tool(spec, index)
        for index, spec in enumerate(registry, start=1)
    )
    return (
        "你是本地文件管理、网页内容、搜索与命令行助手。请严格根据工具返回的真实结果回答，"
        "不要编造目录内容、文件内容、网页内容、搜索结果、命令输出或操作结果。\n"
        "当前可用工具：\n"
        f"{tool_lines}\n"
        "规则：\n"
        "1. 涉及真实文件系统、网页内容、本地命令或需要获取最新信息的问题必须先调用工具，禁止凭猜测回答；\n"
        "2. 一次只调用一个工具，拿到结果后再决定下一步；相对路径以当前工作目录为基准；"
        "需要搜索网页信息时调用 searchWeb（传入 query，可选 top_k）；"
        "需要阅读某个具体网页时调用 fetchWeb，并把完整 URL 作为 url 参数传入；"
        "需要运行本地命令时调用 RunCli（command 为程序名，args 为参数数组），"
        "并根据返回的 stdout、stderr 和退出码判断执行结果；\n"
        "3. path 必须指向具体对象：读取、编辑、删除文件时要写到文件名（例如 test.md），"
        "不能只写 ./ 或目录；deleteFile 只删文件，deleteDirectory 才删目录；"
        "每次调用都要完整给出参数，不要省略 path；\n"
        "4. 若接口支持原生 function call（tools 声明），使用原生 tool_calls 调用；\n"
        "5. 否则只输出一行 JSON 来调用工具，例如 "
        '{"name": "listDirectory", "arguments": {"path": "."}}，'
        "该行之外不要输出任何其他文字，也不要用 Markdown 代码块包裹；\n"
        "6. 收到工具结果后，用中文简洁回答用户，严禁改写、猜测或编造工具结果。"
    )
