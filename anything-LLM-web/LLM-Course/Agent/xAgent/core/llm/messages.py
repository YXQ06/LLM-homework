"""assistant 消息与工具调用的解析、归一化。

同时兼容两类模型：
* 原生 function call：读取 ``message.tool_calls``；
* 文本 JSON 兜底：从正文中扫描 ``{"name": ..., "arguments": ...}``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

# 小模型常输出全角标点写 JSON，解析前先归一化成半角
FULLWIDTH_TRANS = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "：": ":",
        "，": ",",
    }
)


@dataclass
class ToolCall:
    """统一的工具调用结构。"""

    call_id: str
    name: str
    arguments: dict[str, Any]
    textual: bool = False  # True = 从文本 JSON 解析出的调用


def extract_content(message: Mapping[str, Any]) -> str:
    """取出正文：DeepSeek-R1 等思考模型可能把正文放在 reasoning_content。"""
    return message.get("content") or message.get("reasoning_content") or ""


def assistant_history_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """构造写入历史的 assistant 消息，原生工具调用需带上 tool_calls 字段。"""
    payload: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("tool_calls"):
        payload["tool_calls"] = message["tool_calls"]
    return payload


def parse_text_tool_calls(text: str, known_names: Iterable[str]) -> list[ToolCall]:
    """从回复文本中解析文本式工具调用，适配不支持原生 tool_calls 的小模型。

    先归一化全角符号，再扫描文本里的每个 ``{``，尝试用 JSON 解码器完整解析；
    若对象含 ``name`` 且命中已注册工具名，则视为一次工具调用。
    """
    normalized = (text or "").translate(FULLWIDTH_TRANS)
    decoder = json.JSONDecoder()
    names = set(known_names)
    calls: list[ToolCall] = []
    pos = 0
    while True:
        start = normalized.find("{", pos)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(normalized[start:])
        except ValueError:
            pos = start + 1
            continue
        if isinstance(obj, dict) and obj.get("name") in names:
            arguments = obj.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            calls.append(
                ToolCall(
                    call_id=f"txt-{len(calls)}-{obj['name']}",
                    name=obj["name"],
                    arguments=arguments,
                    textual=True,
                )
            )
            pos = start + end
        else:
            pos = start + 1
    return calls


def normalize_tool_calls(
    message: Mapping[str, Any], known_names: Iterable[str]
) -> list[ToolCall]:
    """把模型回复统一成 ``ToolCall`` 列表。

    优先取原生 ``tool_calls``；没有时再从正文解析文本 JSON。
    """
    calls: list[ToolCall] = []
    for index, raw_call in enumerate(message.get("tool_calls") or []):
        if not isinstance(raw_call, Mapping):
            continue
        function = raw_call.get("function") or {}
        if not isinstance(function, Mapping):
            continue
        name = function.get("name") or ""
        if not name:
            continue
        raw_args = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except ValueError:
            arguments = {}
        calls.append(
            ToolCall(
                call_id=str(raw_call.get("id") or f"call-{index}"),
                name=str(name),
                arguments=dict(arguments) if isinstance(arguments, Mapping) else {},
            )
        )
    if calls:
        return calls

    return parse_text_tool_calls(extract_content(message), known_names)


def tool_result_message(call: ToolCall, result: str) -> dict[str, Any]:
    """构造“工具结果回传”消息。

    原生调用使用 OpenAI 规范的 ``role=tool``；文本式调用使用 user 消息，
    避免小模型触发原生工具模板。
    """
    if call.textual:
        return {
            "role": "user",
            "content": (
                f"【工具 {call.name} 的执行结果】\n{result}\n"
                "请仅根据该结果用中文简洁回答，严禁改写、猜测或编造。"
            ),
        }
    return {"role": "tool", "tool_call_id": call.call_id, "content": result}
