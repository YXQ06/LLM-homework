"""把 MCP 工具转换成 xAgent 的 ``ToolSpec``。

这里集中处理三件事：

1. **名称映射**：记录 LLM 看到的工具名与 ``(MCP Server, 原始工具名)`` 的
   对应关系；只有出现重名（多个 Server 之间，或与本地工具冲突）时，
   才给工具名加 ``<server>__<tool>`` 前缀。
2. **参数 Schema**：直接复用 MCP ``list_tools()`` 返回的 inputSchema，
   并做最小限度的结构校验。
3. **结果格式化**：把 MCP 的文本、图片、资源、结构化数据和错误标记
   转换成 LLM 可读的字符串。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from ..errors import ToolError
from ..tools.base import ToolSpec, object_schema
from ..utils.text import clip_text

_MAX_TOOL_NAME_LENGTH = 64
_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
_MULTI_UNDERSCORE = re.compile(r"_{2,}")

ToolCaller = Callable[["McpToolRoute", Mapping[str, Any]], str]


def sanitize_tool_name(raw: str) -> str:
    """把任意名称转换成 OpenAI function name 允许的字符集。"""
    text = _INVALID_NAME_CHARS.sub("_", str(raw or "").strip())
    text = _MULTI_UNDERSCORE.sub("__", text).strip("_-")
    return text or "tool"


@dataclass(frozen=True)
class DiscoveredTool:
    """``list_tools()`` 返回的单个工具（已去掉 SDK 版本差异）。"""

    server: str
    name: str
    description: str = ""
    parameters: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class McpToolRoute:
    """LLM 工具名 → MCP Server / 原始工具名 的映射。"""

    exposed: str
    server: str
    tool: str


@dataclass(frozen=True)
class McpToolBuildResult:
    """动态注册结果，便于测试与界面展示。"""

    specs: tuple[ToolSpec, ...]
    routes: Mapping[str, McpToolRoute]


def _truncate(value: str, limit: int = _MAX_TOOL_NAME_LENGTH) -> str:
    return value if len(value) <= limit else value[:limit].rstrip("_-")


def _allocate_names(
    tools: Iterable[DiscoveredTool],
    reserved_names: Iterable[str],
) -> list[tuple[DiscoveredTool, str]]:
    """为每个工具分配唯一名称，重名时才加服务器前缀。"""
    items = list(tools)
    sanitized = [sanitize_tool_name(item.name) for item in items]
    counts = Counter(sanitized)
    used = {name for name in reserved_names if name}

    allocated: list[tuple[DiscoveredTool, str]] = []
    for item, base_name in zip(items, sanitized):
        if counts[base_name] > 1 or base_name in used:
            server_prefix = sanitize_tool_name(item.server)
            candidate = _truncate(f"{server_prefix}__{base_name}")
        else:
            candidate = _truncate(base_name)

        if not candidate:
            candidate = "mcp_tool"
        if candidate in used:
            # 极端情况：前缀后仍然冲突（例如两个 Server 名称清洗后相同）。
            suffix = 2
            root = _truncate(candidate, _MAX_TOOL_NAME_LENGTH - 4)
            while f"{root}_{suffix}" in used:
                suffix += 1
            candidate = f"{root}_{suffix}"

        used.add(candidate)
        allocated.append((item, candidate))
    return allocated


def _normalize_parameters(schema: Any) -> dict[str, Any]:
    """保证参数 Schema 是合法的 JSON Schema object。"""
    if not isinstance(schema, Mapping):
        return object_schema()
    parameters = dict(schema)
    if parameters.get("type") not in (None, "object"):
        # MCP 规范要求 inputSchema 是 object；异常 Schema 用空对象兜底。
        return object_schema()
    parameters.setdefault("type", "object")
    properties = parameters.get("properties")
    if not isinstance(properties, Mapping):
        parameters["properties"] = {}
    else:
        parameters["properties"] = dict(properties)
    required = parameters.get("required")
    if required is None:
        pass
    elif isinstance(required, str):
        parameters["required"] = [required] if required else []
    elif isinstance(required, Iterable):
        parameters["required"] = [str(item) for item in required]
    else:
        parameters["required"] = []
    return parameters


def _tool_description(item: DiscoveredTool) -> str:
    description = (item.description or "").strip()
    prefix = f"[MCP:{item.server}]"
    if not description:
        return f"{prefix} MCP Server {item.server} 提供的工具 {item.name}。"
    return f"{prefix} {description}"


def build_mcp_tools(
    tools: Iterable[DiscoveredTool],
    reserved_names: Iterable[str],
    caller: ToolCaller,
) -> McpToolBuildResult:
    """把发现到的 MCP 工具注册成 xAgent ``ToolSpec``。"""
    allocated = _allocate_names(tools, reserved_names)
    specs: list[ToolSpec] = []
    routes: dict[str, McpToolRoute] = {}

    for item, exposed in allocated:
        route = McpToolRoute(exposed=exposed, server=item.server, tool=item.name)

        def handler(
            arguments: Mapping[str, Any],
            _route: McpToolRoute = route,
        ) -> str:
            return caller(_route, arguments or {})

        spec = ToolSpec(
            name=exposed,
            description=_tool_description(item),
            parameters=_normalize_parameters(item.parameters),
            handler=handler,
        )
        specs.append(spec)
        routes[exposed] = route

    return McpToolBuildResult(specs=tuple(specs), routes=routes)


def _get_field(value: Any, *names: str, default: Any = None) -> Any:
    """兼容 Pydantic 模型、字典以及不同 SDK 版本的字段命名。"""
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _dump(value: Any) -> str:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            data = model_dump(exclude_none=True)
        except TypeError:
            data = model_dump()
        return json.dumps(data, ensure_ascii=False, default=str)
    if isinstance(value, Mapping):
        return json.dumps(dict(value), ensure_ascii=False, default=str)
    return str(value)


def _format_block(block: Any) -> str:
    block_type = str(_get_field(block, "type", default="") or "").lower()
    if block_type == "text":
        return str(_get_field(block, "text", default="") or "")

    if block_type in ("image", "audio"):
        mime = _get_field(block, "mime_type", "mimeType", default="")
        data = _get_field(block, "data", default="") or ""
        label = "图片" if block_type == "image" else "音频"
        return (
            f"[{label}内容] MIME={mime or '未知'}，"
            f"base64 长度={len(str(data))}（二进制内容不直接写入上下文）"
        )

    if block_type == "resource":
        resource = _get_field(block, "resource", default=block)
        uri = _get_field(resource, "uri", default="")
        mime = _get_field(resource, "mime_type", "mimeType", default="")
        text = _get_field(resource, "text", default=None)
        blob = _get_field(resource, "blob", default=None)
        header = f"[资源] {uri}"
        if mime:
            header += f"（{mime}）"
        if text is not None:
            return f"{header}\n{text}"
        if blob is not None:
            return f"{header}\n[二进制资源，base64 长度={len(str(blob))}]"
        return header

    if block_type == "resource_link":
        name = _get_field(block, "name", default="")
        uri = _get_field(block, "uri", default="")
        description = _get_field(block, "description", default="")
        text = f"[资源链接] {name or uri}：{uri}"
        if description:
            text += f"\n{description}"
        return text

    return _dump(block)


def format_call_result(result: Any) -> tuple[str, bool]:
    """格式化 ``CallToolResult``，返回 ``(文本, 是否为错误)``。"""
    is_error = bool(_get_field(result, "is_error", "isError", default=False))
    blocks = _get_field(result, "content", default=[]) or []
    parts: list[str] = []

    if isinstance(blocks, (list, tuple)):
        for block in blocks:
            text = _format_block(block)
            if text:
                parts.append(text)
    elif blocks:
        parts.append(str(blocks))

    structured = _get_field(
        result, "structured_content", "structuredContent", default=None
    )
    if structured:
        parts.append(
            "结构化数据：\n"
            + json.dumps(structured, ensure_ascii=False, indent=2, default=str)
        )

    if not parts:
        return "（MCP 工具未返回内容）", is_error
    return "\n".join(parts), is_error


def clip_result(text: str, max_chars: int) -> str:
    """限制回传给模型的 MCP 结果长度。"""
    if max_chars <= 0:
        return text
    return clip_text(text, max_chars)


def require_arguments(arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    """确保参数是 JSON 对象；LLM 偶尔会返回 null 或字符串。"""
    if arguments is None:
        return {}
    if not isinstance(arguments, Mapping):
        raise ToolError(
            f"MCP 工具参数必须是 JSON 对象，收到的是 {type(arguments).__name__}"
        )
    return dict(arguments)


__all__ = [
    "DiscoveredTool",
    "McpToolBuildResult",
    "McpToolRoute",
    "build_mcp_tools",
    "clip_result",
    "format_call_result",
    "require_arguments",
    "sanitize_tool_name",
]
