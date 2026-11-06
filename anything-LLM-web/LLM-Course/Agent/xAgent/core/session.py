"""会话保存、列出与恢复。

会话文件保存在项目目录下的 ``.agents/sessions/<id>.jsonl``（目录可通过
``config.ini`` 的 ``session_dir`` 调整）。文件采用简单的事件日志格式：

* 第一行是 ``session`` 头，记录标题、时间、AGENTS.md 元信息；
* 之后每一行是一条 ``message`` 事件，保存完整的 OpenAI 消息字典。

这样既能直接用文本工具查看，也能在恢复时按顺序重放消息，不需要数据库。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .agents_md import contains_agents_md

SESSION_SUFFIX = ".jsonl"
SESSION_VERSION = 1
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9._-]+")
_DEFAULT_TITLE = "未命名会话"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json_default(value: Any) -> str:
    return str(value)


@dataclass(frozen=True)
class SessionSummary:
    """历史会话列表中的一项。"""

    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    path: Path

    @property
    def preview(self) -> str:
        return f"{self.title}（{self.message_count} 条消息，更新于 {self.updated_at}）"


@dataclass(frozen=True)
class SessionRecord:
    """恢复会话所需的完整数据。"""

    id: str
    title: str
    created_at: str
    updated_at: str
    messages: tuple[dict[str, Any], ...]
    agents_md_path: str = ""
    agents_md_hash: str = ""
    path: Path | None = None


def derive_title(
    messages: Sequence[Mapping[str, Any]],
    fallback: str = _DEFAULT_TITLE,
) -> str:
    """从第一条真实用户消息生成短标题。"""
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if not content or contains_agents_md(content):
            continue
        text = " ".join(content.split())
        if len(text) > 60:
            text = text[:60] + "…"
        return text or fallback
    return fallback


def _validate_session_id(session_id: str) -> str:
    value = (session_id or "").strip()
    if value.lower().endswith(SESSION_SUFFIX):
        value = value[: -len(SESSION_SUFFIX)]
    if (
        not value
        or value in (".", "..")
        or not _SESSION_ID_RE.fullmatch(value)
    ):
        raise ValueError(f"会话编号不合法：{session_id!r}")
    return value


class SessionStore:
    """项目目录下的 JSONL 会话仓库。"""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory)

    def _path(self, session_id: str) -> Path:
        return self.directory / f"{_validate_session_id(session_id)}{SESSION_SUFFIX}"

    def new_session_id(self) -> str:
        """生成唯一会话编号，例如 ``20261106-153000``。"""
        base = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = base
        suffix = 2
        while (self.directory / f"{candidate}{SESSION_SUFFIX}").exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def save(
        self,
        session_id: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        title: str = "",
        created_at: str = "",
        project_root: str = "",
        agents_md_path: str = "",
        agents_md_hash: str = "",
    ) -> Path:
        """把当前消息完整写入会话事件日志（原子替换）。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        session_id = _validate_session_id(session_id)
        path = self.directory / f"{session_id}{SESSION_SUFFIX}"
        temp_path = path.with_suffix(SESSION_SUFFIX + ".tmp")

        normalized = [dict(message) for message in messages]
        final_title = (title or "").strip() or derive_title(normalized)
        created = (created_at or "").strip() or _now_text()
        updated = _now_text()
        header = {
            "type": "session",
            "version": SESSION_VERSION,
            "id": session_id,
            "title": final_title,
            "created_at": created,
            "updated_at": updated,
            "project_root": project_root,
            "agents_md": {
                "path": agents_md_path,
                "hash": agents_md_hash,
            },
        }

        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(header, ensure_ascii=False, default=_json_default)
                    + "\n"
                )
                for message in normalized:
                    handle.write(
                        json.dumps(
                            {
                                "type": "message",
                                "message": message,
                            },
                            ensure_ascii=False,
                            default=_json_default,
                        )
                        + "\n"
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return path

    def list_sessions(self, limit: int = 50) -> list[SessionSummary]:
        """按更新时间倒序列出历史会话。"""
        if not self.directory.is_dir():
            return []

        summaries: list[SessionSummary] = []
        for path in self.directory.glob(f"*{SESSION_SUFFIX}"):
            summary = self._read_summary(path)
            if summary is not None:
                summaries.append(summary)
        summaries.sort(key=lambda item: item.updated_at, reverse=True)
        if limit > 0:
            summaries = summaries[:limit]
        return summaries

    def _read_summary(self, path: Path) -> SessionSummary | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                first = handle.readline()
                if not first:
                    return None
                header = json.loads(first)
                if not isinstance(header, dict) or header.get("type") != "session":
                    return None
                count = 0
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("type") == "message":
                        count += 1
        except (OSError, ValueError):
            return None

        session_id = str(header.get("id") or path.stem)
        try:
            session_id = _validate_session_id(session_id)
        except ValueError:
            return None
        return SessionSummary(
            id=session_id,
            title=str(header.get("title") or _DEFAULT_TITLE),
            created_at=str(header.get("created_at") or ""),
            updated_at=str(header.get("updated_at") or ""),
            message_count=count,
            path=path,
        )

    def load(self, session_id: str) -> SessionRecord:
        """读取一个会话并返回可恢复的消息列表。"""
        path = self._path(session_id)
        if not path.is_file():
            raise ValueError(f"会话不存在：{path.name}")

        header: dict[str, Any] = {}
        messages: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if line_number == 1 and event.get("type") == "session":
                        header = event
                        continue
                    if event.get("type") != "message":
                        continue
                    message = event.get("message")
                    if isinstance(message, dict):
                        messages.append(dict(message))
        except OSError as exc:
            raise ValueError(f"读取会话失败：{exc}") from exc

        if not header:
            raise ValueError(f"会话文件缺少头部信息：{path.name}")

        agents_md = header.get("agents_md")
        agents_md_path = ""
        agents_md_hash = ""
        if isinstance(agents_md, Mapping):
            agents_md_path = str(agents_md.get("path") or "")
            agents_md_hash = str(agents_md.get("hash") or "")

        return SessionRecord(
            id=str(header.get("id") or _validate_session_id(session_id)),
            title=str(header.get("title") or _DEFAULT_TITLE),
            created_at=str(header.get("created_at") or ""),
            updated_at=str(header.get("updated_at") or ""),
            messages=tuple(messages),
            agents_md_path=agents_md_path,
            agents_md_hash=agents_md_hash,
            path=path,
        )


__all__ = [
    "SESSION_SUFFIX",
    "SESSION_VERSION",
    "SessionRecord",
    "SessionStore",
    "SessionSummary",
    "derive_title",
]
