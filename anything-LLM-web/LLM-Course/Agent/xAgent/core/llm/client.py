"""OpenAI 兼容的 Chat Completions 客户端。

只负责“发请求 → 返回 assistant message 字典”，并把网络/协议异常
转换成 core.errors 中的领域异常，方便任何界面统一展示。
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Protocol, Sequence

import requests

from ..config import LLMConfig
from ..errors import (
    LLMConnectionError,
    LLMHTTPError,
    LLMResponseError,
    LLMTimeoutError,
)


class ChatClient(Protocol):
    """对话客户端协议，便于在测试或其它后端中替换实现。"""

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """返回 assistant message 字典（可能包含 content / tool_calls）。"""


class OpenAICompatibleClient:
    """通过 requests 调用 OpenAI 兼容接口，使用非流式请求。"""

    def __init__(self, config: LLMConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self._session = session or requests.Session()
        # 最近一次接口调用返回的 usage，供上下文监控优先使用。
        self.last_usage: dict[str, Any] | None = None
        self._context_window_cache: int | None = None
        self._context_window_checked = False

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        headers = self._headers()

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": list(messages),
            "stream": False,
            "temperature": self.config.temperature,
        }
        if tools:
            payload["tools"] = list(tools)

        try:
            response = self._session.post(
                self.config.chat_completions_url,
                headers=headers,
                json=payload,
                timeout=self.config.timeout,
            )
        except requests.exceptions.Timeout as exc:
            raise LLMTimeoutError(f"请求超时（{self.config.timeout:g}s）") from exc
        except requests.exceptions.RequestException as exc:
            raise LLMConnectionError(f"网络请求失败：{exc}") from exc

        if response.status_code >= 400:
            raise LLMHTTPError(response.status_code, response.text)

        try:
            data = response.json()
            message = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(f"返回数据格式异常：{exc}") from exc

        if not isinstance(message, dict):
            raise LLMResponseError("返回的 message 不是对象，无法解析")

        usage = data.get("usage") if isinstance(data, Mapping) else None
        self.last_usage = dict(usage) if isinstance(usage, Mapping) else None
        return message

    def fetch_context_window(self) -> int | None:
        """尝试从 OpenAI 兼容接口的 ``/models`` 读取上下文窗口大小。

        不同服务商字段不一致，这里只识别常见的上下文窗口字段；读取不到时
        返回 ``None``，由上层使用 128k 默认值并显示「默认」。
        """
        if self.config.context_window > 0:
            return self.config.context_window
        if self._context_window_checked:
            return self._context_window_cache
        self._context_window_checked = True

        try:
            response = self._session.get(
                self.config.models_url,
                headers=self._headers(),
                timeout=min(self.config.timeout, 5.0),
            )
        except requests.exceptions.RequestException:
            return None
        if response.status_code >= 400:
            return None

        try:
            data = response.json()
        except ValueError:
            return None

        entries: list[Any]
        if isinstance(data, Mapping):
            raw_entries = data.get("data") or data.get("models") or []
            entries = list(raw_entries) if isinstance(raw_entries, list) else []
        elif isinstance(data, list):
            entries = data
        else:
            entries = []

        if not entries:
            return None

        # 优先匹配当前模型名，其次退回到第一个能解析出窗口的条目。
        ordered = sorted(
            entries,
            key=lambda entry: 0
            if isinstance(entry, Mapping) and entry.get("id") == self.config.model
            else 1,
        )
        for entry in ordered:
            value = _find_context_window(entry)
            if value:
                self._context_window_cache = value
                return value
        return None

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "OpenAICompatibleClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


_CONTEXT_WINDOW_KEYS = (
    "context_window",
    "context_length",
    "max_context_length",
    "max_context_tokens",
    "max_model_len",
)


def _find_context_window(value: Any, depth: int = 3) -> int | None:
    """递归查找模型元数据里的上下文窗口字段。"""
    if depth < 0:
        return None
    if isinstance(value, Mapping):
        for key in _CONTEXT_WINDOW_KEYS:
            raw = value.get(key)
            if isinstance(raw, (int, float)) and int(raw) > 0:
                return int(raw)
            if isinstance(raw, str) and raw.strip().isdigit():
                number = int(raw.strip())
                if number > 0:
                    return number
        for nested in value.values():
            if isinstance(nested, (Mapping, list)):
                found = _find_context_window(nested, depth - 1)
                if found:
                    return found
    elif isinstance(value, list):
        for item in value:
            found = _find_context_window(item, depth - 1)
            if found:
                return found
    return None


def dumps_arguments(arguments: Mapping[str, Any]) -> str:
    """调试用：把工具参数序列化成一行 JSON 文本。"""
    try:
        return json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(arguments)
