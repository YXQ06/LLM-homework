"""AGENTS.md 项目说明文件的读取与消息构造。

产品策略是“一次注入，全程复用”：

* 会话首次加载时读取项目当前目录下的 ``AGENTS.md``；
* 把它作为一条普通 user 消息追加到聊天历史；
* 之后所有轮次直接复用这条历史消息，不再重复读取文件；
* 压缩上下文时也会保留这条消息，避免项目约束被摘要丢掉。

这里只做文件读取和文本构造，不涉及 Agent 编排或会话持久化。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

AGENTS_MD_MARKER = "【项目说明：AGENTS.md】"


@dataclass(frozen=True)
class AgentsDocument:
    """一次 AGENTS.md 读取结果。"""

    path: Path
    text: str
    sha256: str
    truncated: bool = False


def load_agents_md(
    path: str | Path,
    *,
    max_chars: int = 200_000,
) -> AgentsDocument | None:
    """读取 AGENTS.md；文件不存在、为空或不可读时返回 ``None``。"""
    candidate = Path(path).expanduser()
    if not candidate.is_file():
        return None

    try:
        text = candidate.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None

    text = text.strip()
    if not text:
        return None

    truncated = False
    if max_chars > 0 and len(text) > max_chars:
        text = (
            text[:max_chars]
            + "\n\n...（AGENTS.md 内容过长，已按配置截断；"
            "完整内容请查看原文件）"
        )
        truncated = True

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return AgentsDocument(
        path=candidate.resolve(),
        text=text,
        sha256=digest,
        truncated=truncated,
    )


def build_agents_md_message(document: AgentsDocument) -> str:
    """构造注入到聊天历史中的消息内容。"""
    return (
        f"{AGENTS_MD_MARKER}\n"
        "以下内容来自项目当前目录的 AGENTS.md，请在本次会话中始终遵守；"
        "该说明只在会话首次加载时注入一次。\n\n"
        f"{document.text}"
    )


def contains_agents_md(content: object) -> bool:
    """判断一条消息内容是否是 AGENTS.md 注入消息。"""
    return AGENTS_MD_MARKER in str(content or "")


__all__ = [
    "AGENTS_MD_MARKER",
    "AgentsDocument",
    "build_agents_md_message",
    "contains_agents_md",
    "load_agents_md",
]
