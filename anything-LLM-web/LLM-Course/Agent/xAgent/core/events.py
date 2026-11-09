"""Agent 对外发布的事件与结果对象。

core 通过“事件迭代器”与界面通信：界面只依赖这些纯数据对象，
不需要知道 HTTP、工具实现或参数容错细节，因此可以复用于 TUI / Web / Desktop。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Union


@dataclass(frozen=True)
class TurnStarted:
    """一轮提问开始。"""

    prompt: str


@dataclass(frozen=True)
class ToolCallStarted:
    """模型要求调用某个工具，即将执行。"""

    call_id: str
    name: str
    arguments: Mapping[str, Any]
    textual: bool = False  # True 表示来自文本 JSON 兜底（非原生 tool_calls）


@dataclass(frozen=True)
class ToolCallFinished:
    """工具执行完毕。"""

    call_id: str
    name: str
    arguments: Mapping[str, Any]
    result: str
    ok: bool
    duration_ms: int


@dataclass(frozen=True)
class ArgumentRepaired:
    """参数容错层自动修正了模型给出的参数。"""

    tool: str
    detail: str


@dataclass(frozen=True)
class NudgeIssued:
    """模型漏调工具时，向其追加的一次性提醒。"""

    tool: str
    prompt: str


@dataclass(frozen=True)
class ContextUsage:
    """上下文使用量更新（用于 TUI 状态条和压缩警告）。"""

    used_tokens: int
    window_tokens: int
    percent: float
    window_is_default: bool = False
    warning: str = ""


@dataclass(frozen=True)
class CompactionStarted:
    """开始执行 /compact：原始上下文即将归档并交给 LLM 生成摘要。"""

    archive_id: str
    message_count: int
    original_text: str = ""


@dataclass(frozen=True)
class CompactionFinished:
    """上下文压缩成功，历史消息已被摘要替换。"""

    archive_id: str
    archive_path: str
    created_at: str
    summary: str
    original_text: str = ""
    compacted_tokens: int = 0


@dataclass(frozen=True)
class CompactionFailed:
    """上下文压缩失败，原有上下文保持不变。"""

    message: str
    archive_id: str = ""


@dataclass(frozen=True)
class AnswerReady:
    """最终回答生成完毕。"""

    text: str


@dataclass(frozen=True)
class TurnFailed:
    """本轮失败（配置、网络、接口格式、模型空回答等）。"""

    message: str
    kind: str = "error"


@dataclass(frozen=True)
class ToolRun:
    """一次工具执行的完整记录，供界面展示或日志分析。"""

    name: str
    arguments: Mapping[str, Any]
    result: str
    ok: bool


@dataclass(frozen=True)
class TurnResult:
    """一轮提问的最终结果。"""

    prompt: str
    answer: str = ""
    messages: tuple[dict[str, Any], ...] = ()
    tool_runs: tuple[ToolRun, ...] = ()
    error: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class TurnFinished:
    """一轮提问结束（无论成功还是失败，界面都应据此解除忙碌状态）。"""

    result: TurnResult


AgentEvent = Union[
    TurnStarted,
    ToolCallStarted,
    ToolCallFinished,
    ArgumentRepaired,
    NudgeIssued,
    ContextUsage,
    CompactionStarted,
    CompactionFinished,
    CompactionFailed,
    AnswerReady,
    TurnFailed,
    TurnFinished,
]


def empty_result(prompt: str, error: str | None = None) -> TurnResult:
    return TurnResult(prompt=prompt, error=error)


__all__ = [
    "AgentEvent",
    "AnswerReady",
    "ArgumentRepaired",
    "CompactionFailed",
    "CompactionFinished",
    "CompactionStarted",
    "ContextUsage",
    "NudgeIssued",
    "ToolCallFinished",
    "ToolCallStarted",
    "ToolRun",
    "TurnFailed",
    "TurnFinished",
    "TurnResult",
    "TurnStarted",
    "empty_result",
]
