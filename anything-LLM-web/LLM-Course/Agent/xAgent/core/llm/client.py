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

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

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
        return message

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "OpenAICompatibleClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def dumps_arguments(arguments: Mapping[str, Any]) -> str:
    """调试用：把工具参数序列化成一行 JSON 文本。"""
    try:
        return json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(arguments)
