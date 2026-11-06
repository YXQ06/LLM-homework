"""上下文长度监控、压缩归档与历史检索。

本模块只做四件事，保持实现简单、便于阅读：

1. **估算 Token**：优先使用 LLM 接口返回的 ``usage``，没有时按字符估算；
2. **监控上下文窗口**：计算当前会话占模型窗口的比例，达到阈值时提示压缩；
3. **保存 / 检索归档**：把压缩前的消息逐行写入 ``.agents/context/*.jsonl``，
   并提供普通的关键词全文检索（不使用向量数据库）；
4. **生成压缩摘要**：调用一次不带工具的 LLM 请求，把历史整理成工作摘要。

没有引入任何第三方检索库；归档就是 JSONL，检索就是逐行读取 + 字符串匹配。
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import AgentConfig
from .llm.messages import extract_content

DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_ARCHIVE_DIR = ".agents/context"
DEFAULT_CHARS_PER_TOKEN = 4.0

COMPACTION_SYSTEM_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5,000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：涉及的技术栈、架构、协议、代码结构、数据格式、环境配置、关键文件路径、函数名称和命令等。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 recallContext 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

**最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。**"""

COMPACTION_USER_PROMPT = (
    "下面是需要压缩的历史上下文。请严格按照系统提示词的要求，"
    "只输出 Markdown 摘要，不要输出任何解释、前缀或后缀。"
)

# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------


def estimate_tokens(
    text: str,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
) -> int:
    """粗略估算文本 Token 数。

    中文 / 全角字符按 1 字符≈1 Token，英文、数字和符号按
    ``chars_per_token`` 个字符≈1 Token。这个估算只用于接口没有返回
    ``usage`` 时的兜底，不追求绝对精确。
    """
    if not text:
        return 0
    ratio = max(1.0, float(chars_per_token or DEFAULT_CHARS_PER_TOKEN))
    cjk = 0
    other = 0
    for character in str(text):
        code = ord(character)
        if code >= 0x2E80 or 0xFF00 <= code <= 0xFFEF:
            cjk += 1
        else:
            other += 1
    return max(1, int(math.ceil(cjk + other / ratio)))


def estimate_messages_tokens(
    messages: Sequence[Mapping[str, Any]],
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
) -> int:
    """估算一组对话消息占用的 Token 数（含角色和工具调用字段）。"""
    total = 0
    for message in messages:
        total += 4  # role / 分隔符等固定开销
        total += estimate_tokens(
            str(message.get("content") or ""), chars_per_token
        )
        tool_calls = message.get("tool_calls")
        if tool_calls:
            total += estimate_tokens(
                json.dumps(tool_calls, ensure_ascii=False, default=str),
                chars_per_token,
            )
        if message.get("tool_call_id"):
            total += estimate_tokens(
                str(message.get("tool_call_id")), chars_per_token
            )
    return total


def estimate_tools_tokens(
    tools: Sequence[Mapping[str, Any]] | None,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
) -> int:
    """估算工具声明占用的 Token 数。"""
    if not tools:
        return 0
    return estimate_tokens(
        json.dumps(list(tools), ensure_ascii=False, default=str),
        chars_per_token,
    )


def usage_total_tokens(usage: Mapping[str, Any] | None) -> int | None:
    """从接口的 ``usage`` 字段取总 Token 数，取不到返回 ``None``。"""
    if not isinstance(usage, Mapping):
        return None
    total = usage.get("total_tokens")
    if isinstance(total, (int, float)) and total > 0:
        return int(total)
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if isinstance(prompt, (int, float)) and isinstance(completion, (int, float)):
        value = int(prompt) + int(completion)
        if value > 0:
            return value
    return None


# ---------------------------------------------------------------------------
# 长度快照
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextWindowInfo:
    """模型上下文窗口信息。"""

    tokens: int
    is_default: bool = False


@dataclass(frozen=True)
class ContextSnapshot:
    """某一时刻的上下文使用快照。"""

    used_tokens: int
    window_tokens: int
    window_is_default: bool = False
    warn_ratio: float = 0.6

    @property
    def ratio(self) -> float:
        if self.window_tokens <= 0:
            return 0.0
        return self.used_tokens / self.window_tokens

    @property
    def percent(self) -> float:
        return self.ratio * 100

    @property
    def needs_compaction(self) -> bool:
        return self.ratio >= self.warn_ratio

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.window_tokens - self.used_tokens)


# ---------------------------------------------------------------------------
# 归档文件
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchiveRecord:
    """一次上下文归档的结果。"""

    archive_id: str
    path: Path
    created_at: str
    message_count: int


@dataclass(frozen=True)
class RecallMatch:
    """一条历史检索命中。"""

    archive_id: str
    line_number: int
    role: str
    snippet: str


def _json_default(value: Any) -> str:
    """保证消息里出现非 JSON 类型时也能成功归档。"""
    return str(value)


def _message_search_text(message: Mapping[str, Any]) -> str:
    """把一条消息展开成用于关键词匹配的文本。"""
    parts: list[str] = []
    content = message.get("content")
    if content:
        parts.append(str(content))
    if message.get("tool_call_id"):
        parts.append(f"tool_call_id: {message['tool_call_id']}")
    tool_calls = message.get("tool_calls")
    if tool_calls:
        parts.append(
            "tool_calls: "
            + json.dumps(tool_calls, ensure_ascii=False, default=_json_default)
        )
    return "\n".join(parts)


def _snippet(text: str, position: int, limit: int = 480) -> str:
    """截取命中关键词附近的文本，避免把整条长消息塞回上下文。"""
    if len(text) <= limit:
        return text
    half = max(80, limit // 2)
    start = max(0, position - half)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end] + suffix


class ArchiveStore:
    """``.agents/context`` 目录下的 JSONL 归档读写。"""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory)

    def _archive_path(self, archive_id: str) -> Path:
        value = (archive_id or "").strip()
        if value.lower().endswith(".jsonl"):
            value = value[:-6]
        if not value or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError(f"归档编号不合法：{archive_id!r}")
        if value in (".", ".."):
            raise ValueError(f"归档编号不合法：{archive_id!r}")
        return self.directory / f"{value}.jsonl"

    def _next_archive_id(self) -> str:
        base = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = base
        suffix = 2
        while (self.directory / f"{candidate}.jsonl").exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def save(self, messages: Sequence[Mapping[str, Any]]) -> ArchiveRecord:
        """完整保存原始消息，成功落盘后才返回。

        先写临时文件并 ``fsync``，再用 ``os.replace`` 原子替换目标文件，
        避免压缩过程中断时留下半截归档。
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        archive_id = self._next_archive_id()
        path = self.directory / f"{archive_id}.jsonl"
        temp_path = path.with_suffix(".jsonl.tmp")
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                for message in messages:
                    handle.write(
                        json.dumps(
                            dict(message),
                            ensure_ascii=False,
                            default=_json_default,
                        )
                    )
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        return ArchiveRecord(
            archive_id=archive_id,
            path=path,
            created_at=created_at,
            message_count=len(messages),
        )

    def list_archives(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(
            self.directory.glob("*.jsonl"),
            key=lambda item: item.name,
            reverse=True,
        )

    def iter_messages(self, path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    yield line_number, value

    def search(
        self,
        text: str,
        *,
        archive_id: str | None = None,
        max_chars: int = 6_000,
        max_results: int = 8,
    ) -> str:
        """在归档中做关键词全文检索，返回带来源位置的片段。"""
        query = (text or "").strip()
        if not query:
            raise ValueError("检索关键词不能为空")

        if archive_id:
            path = self._archive_path(archive_id)
            if not path.is_file():
                raise ValueError(f"归档不存在：{path.name}")
            files = [path]
            scope = f"归档 {path.stem}"
        else:
            files = self.list_archives()
            scope = "全部历史归档"

        if not files:
            return (
                f"检索范围：{scope}\n"
                "尚未找到上下文归档文件。只有执行过 /compact 压缩后才会生成归档。"
            )

        terms = [term.casefold() for term in query.split() if term]
        if not terms:
            terms = [query.casefold()]

        matches: list[RecallMatch] = []
        for path in files:
            for line_number, message in self.iter_messages(path):
                # System Prompt 属于固定配置，不是历史对话内容，检索时跳过，
                # 避免常见关键词被系统提示词占满结果。
                if message.get("role") == "system":
                    continue
                haystack = _message_search_text(message)
                lowered = haystack.casefold()
                if not all(term in lowered for term in terms):
                    continue
                positions = [
                    lowered.find(term) for term in terms if term in lowered
                ]
                position = min(positions) if positions else 0
                matches.append(
                    RecallMatch(
                        archive_id=path.stem,
                        line_number=line_number,
                        role=str(message.get("role") or "unknown"),
                        snippet=_snippet(haystack, position),
                    )
                )
                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break

        if not matches:
            return (
                f"检索范围：{scope}\n"
                f"关键词：{query}\n"
                "未找到匹配的历史内容。可以换一个更短、更具体的关键词重试。"
            )

        lines = [
            f"检索范围：{scope}",
            f"关键词：{query}",
            f"命中的归档：{', '.join(sorted({match.archive_id for match in matches}))}",
            "",
        ]
        used = sum(len(line) + 1 for line in lines)
        for index, match in enumerate(matches, start=1):
            block = (
                f"### 命中 {index}\n"
                f"- 归档编号：{match.archive_id}\n"
                f"- 位置：第 {match.line_number} 条消息（role={match.role}）\n"
                f"- 原始片段：\n{match.snippet}\n"
            )
            if used + len(block) > max_chars:
                lines.append("...（检索结果较多，已截断；请缩小关键词范围后重试）")
                break
            lines.append(block)
            used += len(block)
        return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# 上下文管理器
# ---------------------------------------------------------------------------


class ContextManager:
    """管理一次会话的上下文长度、归档和压缩摘要。"""

    def __init__(
        self,
        client: Any,
        config: AgentConfig,
        project_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.project_root = Path(project_root or os.getcwd()).resolve()

        raw_archive_dir = Path(
            config.context_archive_dir or DEFAULT_ARCHIVE_DIR
        ).expanduser()
        if not raw_archive_dir.is_absolute():
            raw_archive_dir = self.project_root / raw_archive_dir
        self.archive = ArchiveStore(raw_archive_dir)

        configured_window = int(
            getattr(getattr(client, "config", None), "context_window", 0) or 0
        )
        if configured_window > 0:
            self._window_tokens = configured_window
            self._window_is_default = False
            self._window_resolved = True
        else:
            self._window_tokens = DEFAULT_CONTEXT_WINDOW
            self._window_is_default = True
            self._window_resolved = False

    @property
    def archive_dir(self) -> Path:
        return self.archive.directory

    def window_info(self) -> ContextWindowInfo:
        return ContextWindowInfo(
            tokens=self._window_tokens,
            is_default=self._window_is_default,
        )

    def resolve_window(self) -> ContextWindowInfo:
        """尝试从模型接口获取上下文窗口；失败时保留 128k 默认值。"""
        if self._window_resolved:
            return self.window_info()

        getter = getattr(self.client, "fetch_context_window", None)
        value: int | None = None
        if callable(getter):
            try:
                raw = getter()
                if raw:
                    value = int(raw)
            except Exception:
                value = None

        if value and value > 0:
            self._window_tokens = value
            self._window_is_default = False
        else:
            self._window_tokens = DEFAULT_CONTEXT_WINDOW
            self._window_is_default = True
        self._window_resolved = True
        return self.window_info()

    def snapshot(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        usage: Mapping[str, Any] | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        resolve_window: bool = True,
    ) -> ContextSnapshot:
        """计算当前上下文使用量；优先使用接口返回的 Token 统计。"""
        if resolve_window:
            window = self.resolve_window()
        else:
            window = self.window_info()

        used = usage_total_tokens(usage)
        if used is None:
            chars_per_token = self.config.context_estimate_chars_per_token
            used = estimate_messages_tokens(
                messages, chars_per_token
            ) + estimate_tools_tokens(tools, chars_per_token)

        return ContextSnapshot(
            used_tokens=max(0, int(used)),
            window_tokens=window.tokens,
            window_is_default=window.is_default,
            warn_ratio=self.config.context_warn_ratio,
        )

    # -- 压缩 ---------------------------------------------------------------

    def summarize(self, messages: Sequence[Mapping[str, Any]]) -> str:
        """调用 LLM 生成 Markdown 摘要（不带任何工具）。"""
        transcript = format_messages(messages)
        if self.config.compact_transcript_max_chars > 0:
            budget_chars = self.config.compact_transcript_max_chars
        else:
            budget_chars = max(
                self.config.compact_transcript_min_chars,
                int(
                    self._window_tokens
                    * 3
                    * self.config.compact_transcript_ratio
                ),
            )
        if len(transcript) > budget_chars:
            half = budget_chars // 2
            transcript = (
                transcript[:half]
                + "\n\n...（中间部分因长度限制已省略，完整内容已保存到归档）...\n\n"
                + transcript[-half:]
            )

        response = self.client.chat(
            [
                {"role": "system", "content": COMPACTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": COMPACTION_USER_PROMPT + "\n\n" + transcript,
                },
            ],
            tools=None,
        )
        summary = extract_content(response).strip()
        if not summary:
            raise RuntimeError("模型没有返回摘要内容")
        return clip_to_token_budget(
            summary,
            self.config.compact_target_tokens,
            self.config.context_estimate_chars_per_token,
        )

    def archive_note(self, record: ArchiveRecord) -> str:
        return (
            f"此前上下文已于 {record.created_at} 完成压缩，"
            f"原始上下文保存在编号为 {record.archive_id} 的归档文件中。"
            "如需查找历史信息，请使用 recallContext 工具。"
        )

    @staticmethod
    def compacted_history(summary: str, note: str) -> list[dict[str, str]]:
        """用摘要替换历史消息，只保留一条上下文说明。"""
        content = (
            "【此前会话上下文摘要】\n\n"
            f"{summary.strip()}\n\n"
            "---\n\n"
            f"{note.strip()}"
        )
        return [{"role": "user", "content": content}]


# ---------------------------------------------------------------------------
# 文本格式化与裁剪
# ---------------------------------------------------------------------------


def format_messages(messages: Sequence[Mapping[str, Any]]) -> str:
    """把消息格式化成可读文本，供压缩 Prompt 和预览面板共用。"""
    lines: list[str] = []
    for index, message in enumerate(messages, start=1):
        role = message.get("role") or "unknown"
        lines.append(f"### 消息 {index} | role={role}")
        content = message.get("content")
        if content:
            lines.append(str(content))
        if message.get("tool_call_id"):
            lines.append(f"tool_call_id: {message['tool_call_id']}")
        tool_calls = message.get("tool_calls")
        if tool_calls:
            lines.append(
                "tool_calls: "
                + json.dumps(tool_calls, ensure_ascii=False, default=_json_default)
            )
        lines.append("")
    return "\n".join(lines).strip()


def clip_to_token_budget(
    text: str,
    max_tokens: int,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
) -> str:
    """把文本裁到指定 Token 预算内（用于保证摘要约 5,000 Tokens）。"""
    text = (text or "").strip()
    if max_tokens <= 0 or estimate_tokens(text, chars_per_token) <= max_tokens:
        return text

    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle], chars_per_token) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    suffix = "\n\n...（摘要过长，已按上下文预算截断）"
    return text[:low].rstrip() + suffix


__all__ = [
    "ArchiveRecord",
    "ArchiveStore",
    "ContextManager",
    "ContextSnapshot",
    "ContextWindowInfo",
    "DEFAULT_ARCHIVE_DIR",
    "DEFAULT_CHARS_PER_TOKEN",
    "DEFAULT_CONTEXT_WINDOW",
    "RecallMatch",
    "clip_to_token_budget",
    "estimate_messages_tokens",
    "estimate_tokens",
    "estimate_tools_tokens",
    "format_messages",
    "usage_total_tokens",
]
