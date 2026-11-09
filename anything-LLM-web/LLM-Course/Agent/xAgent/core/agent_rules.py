"""AGENT.md 长期固定规则读取与系统提示词注入。

与 ``AGENTS.md``（会话级项目说明）不同，``AGENT.md``（单数）是**智能体级
长期规则**：

* xAgent 启动时自动检测项目根目录是否存在 ``AGENT.md``；
* 如果存在，读取全部内容并追加到 System Prompt 的最前端；
* 文件不存在、为空或不可读时直接跳过，不抛异常、不影响启动；
* 规则不进入聊天历史，因此不会占用额外的对话轮次，也不会被上下文压缩摘要。

本模块只负责文件读取与文本构造，不依赖 UI、LLM 或 MCP。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .utils.text import decode_text

AGENT_RULES_MARKER = "【全局长期规则：AGENT.md】"
AGENT_RULES_END_MARKER = "【AGENT.md 规则结束】"


@dataclass(frozen=True)
class AgentRulesDocument:
    """一次 AGENT.md 读取结果。"""

    path: Path
    text: str
    sha256: str
    truncated: bool = False


def load_agent_rules(
    path: str | Path,
    *,
    max_chars: int = 200_000,
) -> AgentRulesDocument | None:
    """读取 AGENT.md；文件不存在、为空或不可读时返回 ``None``。"""
    candidate = Path(path).expanduser()
    try:
        if not candidate.is_file():
            return None
        data = candidate.read_bytes()
    except OSError:
        # 权限不足、文件被占用等情况下静默跳过，保证 xAgent 正常启动。
        return None

    try:
        text, _ = decode_text(data, strict=False)
    except (OSError, ValueError):
        return None

    text = text.strip()
    if not text:
        return None

    truncated = False
    if max_chars > 0 and len(text) > max_chars:
        text = (
            text[:max_chars]
            + "\n\n...（AGENT.md 内容过长，已按配置截断；完整内容请查看原文件）"
        )
        truncated = True

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate.absolute()
    return AgentRulesDocument(
        path=resolved,
        text=text,
        sha256=digest,
        truncated=truncated,
    )


def build_agent_rules_prompt(document: AgentRulesDocument) -> str:
    """把 AGENT.md 包装成放在 System Prompt 最前端的长期规则块。"""
    return (
        f"{AGENT_RULES_MARKER}\n"
        "以下内容来自项目根目录的 AGENT.md，是智能体的长期固定规则，"
        "在本次及后续所有会话中始终生效；当它与临时的用户指令冲突时，"
        "应先说明冲突，再按本规则执行。\n\n"
        f"{document.text}\n"
        f"{AGENT_RULES_END_MARKER}"
    )


def contains_agent_rules(content: object) -> bool:
    """判断一段文本是否已经包含 AGENT.md 规则块。"""
    return AGENT_RULES_MARKER in str(content or "")


__all__ = [
    "AGENT_RULES_END_MARKER",
    "AGENT_RULES_MARKER",
    "AgentRulesDocument",
    "build_agent_rules_prompt",
    "contains_agent_rules",
    "load_agent_rules",
]
