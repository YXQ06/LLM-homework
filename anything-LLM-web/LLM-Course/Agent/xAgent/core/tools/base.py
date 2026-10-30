"""工具注册与分发的通用基础设施。

每个工具由 ``ToolSpec``（名称 + JSON Schema + 执行函数 + 追问示例）描述，
``ToolRegistry`` 负责：
* 生成 OpenAI function call 的 ``tools`` 声明；
* 按名称分发执行；
* 把异常转换成可回传给模型的字符串，保证 Agent 不会因工具报错而崩溃。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping

from ..errors import ToolError

ToolHandler = Callable[[Mapping[str, Any]], str]


def object_schema(
    properties: Mapping[str, Any] | None = None,
    required: Iterable[str] | None = None,
) -> dict[str, Any]:
    """构造 JSON Schema 的 object 节点。"""
    return {
        "type": "object",
        "properties": dict(properties or {}),
        "required": list(required or []),
    }


def text_argument(
    arguments: Mapping[str, Any],
    key: str,
    *,
    required: bool = False,
    default: str = "",
) -> str:
    """从参数里取字符串，做类型与空值校验。"""
    value = (arguments or {}).get(key, default)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ToolError(
            f"参数 {key} 必须是字符串，收到的是 {type(value).__name__}"
        )
    value = value.strip()
    if required and not value:
        raise ToolError(
            f"缺少必填参数：{key}（{key} 不能为空，请在 arguments 中给出具体值）"
        )
    return value


@dataclass(frozen=True)
class ToolSpec:
    """单个工具的完整描述。"""

    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: ToolHandler
    example_arguments: Mapping[str, Any] = field(default_factory=dict)

    @property
    def parameter_names(self) -> tuple[str, ...]:
        properties = self.parameters.get("properties") or {}
        return tuple(properties) if isinstance(properties, Mapping) else ()

    @property
    def required_parameters(self) -> tuple[str, ...]:
        required = self.parameters.get("required") or ()
        return tuple(required) if isinstance(required, Iterable) else ()

    def to_openai_schema(self) -> dict[str, Any]:
        """转换成 OpenAI function call 规范的 ``tools`` 元素。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果（不抛异常，便于直接回传给模型）。"""

    name: str
    ok: bool
    content: str


class ToolRegistry:
    """工具注册表。"""

    def __init__(self, specs: Iterable[ToolSpec] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs or ():
            self.register(spec)

    def register(self, spec: ToolSpec) -> "ToolRegistry":
        if spec.name in self._specs:
            raise ValueError(f"工具重复注册：{spec.name}")
        self._specs[spec.name] = spec
        return self

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def openai_tools(self) -> list[dict[str, Any]]:
        return [spec.to_openai_schema() for spec in self._specs.values()]

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None) -> ToolResult:
        """执行工具：业务异常转成失败结果，返回给模型自行解释。"""
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(name=name, ok=False, content=f"未知工具：{name}")
        try:
            content = spec.handler(arguments or {})
        except (ToolError, ValueError, OSError, RuntimeError) as exc:
            return ToolResult(
                name=name, ok=False, content=f"{name} 工具执行失败：{exc}"
            )
        return ToolResult(name=name, ok=True, content=str(content))

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(self._specs.values())
