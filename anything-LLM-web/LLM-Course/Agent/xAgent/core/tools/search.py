"""百度千帆 AI 搜索工具：基于 Qianfan AI Search Web Search API。

使用 config.ini 中 [search] 的 baidu_qianfan_api_key 调用
https://qianfan.baidubce.com/v2/ai_search/web_search 获取搜索结果。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Mapping

import requests

from ..config import AgentConfig, SearchConfig
from ..errors import ToolError
from ..utils.text import clip_text
from .base import ToolSpec, object_schema, text_argument

_DEFAULT_TOP_K = 5
_MAX_TOP_K = 10
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 1.0

# 复制粘贴密钥时常见的不可见字符：BOM、零宽空格/连接符、不换行空格等。
_INVISIBLE_CHARS = (
    "\ufeff",
    "\u200b",
    "\u200c",
    "\u200d",
    "\u2060",
    "\u00a0",
)
_WHITESPACE_RE = re.compile(r"\s+")
_BEARER_PREFIX_RE = re.compile(r"^bearer\s+", re.IGNORECASE)
_PLACEHOLDER_KEYS = frozenset(
    {
        "mock-search-key",
        "mock_search_key",
        "your_api_key",
        "your-baidu-api-key",
        "your_baidu_api_key",
        "<your_api_key>",
        "<api_key>",
    }
)


def normalize_secret(value: str | None) -> str:
    """清理密钥中的空白、换行、引号、Bearer 前缀和不可见字符。

    典型问题：从控制台复制密钥时带入了尾随空格/换行、成对引号，
    或整段 ``Bearer xxx`` 被再次拼进 Authorization 头，导致百度返回
    216003（Fail to parse apikey authorization）。
    """
    if not value:
        return ""
    text = str(value)
    for char in _INVISIBLE_CHARS:
        text = text.replace(char, "")
    text = text.strip()
    # 去掉误复制进来的成对引号（可能有多层）。
    while len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    # 允许用户直接填写 "Bearer xxx"，避免拼成 "Bearer Bearer xxx"。
    text = _BEARER_PREFIX_RE.sub("", text).strip()
    # 密钥本身不允许包含空白；这里把中间误插入的换行也一并清掉。
    text = _WHITESPACE_RE.sub("", text)
    return text


def mask_secret(value: str) -> str:
    """日志/报错中只展示密钥首尾，避免泄露完整密钥。"""
    if not value:
        return "(空)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _is_auth_error(status_code: int, data: Any, text: str = "") -> bool:
    """识别百度鉴权/授权类错误（401、216003、InvalidHTTPAuthHeader）。"""
    if status_code in (401, 403):
        return True
    if isinstance(data, Mapping) and str(data.get("code")).strip() == "216003":
        return True
    haystack = text or ""
    return "216003" in haystack or "InvalidHTTPAuthHeader" in haystack


def _auth_error_hint(api_key: str) -> str:
    return (
        "请检查 config.ini 的 [search] baidu_qianfan_api_key："
        "应填写完整的 bce-v3/ALTAK-... 密钥（不要填 mock-search-key 等占位符），"
        "并确保没有多余空格、换行、引号或重复的 Bearer 前缀。"
        f"当前密钥：{mask_secret(api_key)}。"
        "已停止重试，修复配置后重启服务再试。"
    )


def _format_api_error(data: Any) -> str:
    """把千帆 API 的 JSON 错误体整理成适合回传模型的文本。"""
    if not isinstance(data, Mapping):
        return ""

    code = data.get("code")
    if code is not None and str(code).strip() not in ("", "0"):
        message = (
            data.get("message")
            or data.get("msg")
            or data.get("error")
            or ""
        )
        request_id = data.get("request_id") or data.get("requestId") or ""
        detail = f"错误码 {code}"
        if message:
            detail += f"：{message}"
        if request_id:
            detail += f"（request_id={request_id}）"
        return detail

    error = data.get("error")
    if error not in (None, "", {}, []):
        if isinstance(error, (Mapping, list)):
            error = json.dumps(error, ensure_ascii=False)
        return f"错误信息：{error}"

    if data.get("success") is False:
        return "接口返回 success=false"
    return ""


class SearchToolbox:
    """搜索工具集合。"""

    def __init__(
        self,
        agent_config: AgentConfig | None = None,
        search_config: SearchConfig | None = None,
    ) -> None:
        self.agent_config = agent_config or AgentConfig()
        self.search_config = search_config or SearchConfig()

    def search_web(self, arguments: Mapping[str, Any]) -> str:
        query = text_argument(arguments, "query", required=True)
        if not query:
            raise ToolError("缺少必填参数：query")

        top_k_raw = (arguments or {}).get("top_k")
        try:
            top_k = int(top_k_raw) if top_k_raw is not None and str(top_k_raw).strip() != "" else _DEFAULT_TOP_K
        except (TypeError, ValueError):
            raise ToolError("参数 top_k 必须是整数")
        if top_k < 1 or top_k > _MAX_TOP_K:
            raise ToolError(f"参数 top_k 范围为 1~{_MAX_TOP_K}")

        api_key = normalize_secret(self.search_config.api_key)
        if not api_key:
            raise ToolError("未配置百度千帆 API Key，请在 config.ini 的 [search] 段设置 baidu_qianfan_api_key")
        if api_key.lower() in _PLACEHOLDER_KEYS:
            raise ToolError(
                f"检测到占位符 API Key（{mask_secret(api_key)}），"
                "本次不会向百度发起请求。请在 config.ini 的 [search] 段"
                "填写真实的 baidu_qianfan_api_key（bce-v3/ALTAK-...），"
                "或切换到备选搜索源。"
            )

        endpoint = (self.search_config.endpoint or "https://qianfan.baidubce.com/v2/ai_search/web_search").strip()
        timeout = max(1.0, float(self.search_config.timeout or 30.0))

        body = {
            "messages": [{"role": "user", "content": query}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": top_k}],
        }
        # Authorization 只拼接一次 Bearer；密钥已清理，不含引号/换行。
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        resp = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = requests.post(
                    endpoint,
                    headers=headers,
                    json=body,
                    timeout=timeout,
                )
            except requests.exceptions.Timeout as exc:
                raise ToolError(f"百度搜索请求超时（{timeout}s）") from exc
            except requests.exceptions.RequestException as exc:
                raise ToolError(f"百度搜索网络请求失败：{exc}") from exc

            # 仅对限流/网关类瞬时错误做一次短退避重试；
            # 401/216003 等鉴权错误绝不重试，避免继续使用错误密钥。
            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECONDS)
                continue
            break

        if resp is None:  # pragma: no cover - 防御性分支
            raise ToolError("百度搜索请求未返回响应")

        if resp.status_code >= 400:
            text = resp.text or ""
            data = None
            try:
                data = resp.json()
            except ValueError:
                data = None
            detail = f"HTTP {resp.status_code}"
            api_error = _format_api_error(data)
            if api_error:
                detail = f"{detail}，{api_error}"
            elif text:
                detail = f"{detail}：{text[:300]}"
            if _is_auth_error(resp.status_code, data, text):
                raise ToolError(f"百度搜索鉴权失败（{detail}）。{_auth_error_hint(api_key)}")
            raise ToolError(f"百度搜索 API 返回 {detail}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise ToolError(f"百度搜索 API 返回非 JSON：{resp.text[:200]}") from exc

        if not isinstance(data, Mapping):
            raise ToolError("百度搜索 API 返回的数据结构不符合预期（不是 JSON 对象）")

        api_error = _format_api_error(data)
        if api_error:
            if _is_auth_error(0, data):
                raise ToolError(f"百度搜索鉴权失败：{api_error}。{_auth_error_hint(api_key)}")
            raise ToolError(f"百度搜索 API 返回错误：{api_error}")

        refs = data.get("references")
        if refs is None:
            nested = data.get("data")
            if isinstance(nested, Mapping):
                refs = nested.get("references")
        if not isinstance(refs, list):
            refs = []

        lines: list[str] = []
        lines.append(f"搜索词：{query}")
        lines.append(f"结果数：{len(refs)}")
        lines.append("")

        for idx, ref in enumerate(refs[:top_k], start=1):
            if not isinstance(ref, dict):
                continue
            title = str(ref.get("title") or "").strip()
            url = str(ref.get("url") or "").strip()
            website = str(ref.get("website") or "").strip()
            date = str(ref.get("date") or "").strip()
            snippet = str(ref.get("snippet") or ref.get("content") or "").strip()
            lines.append(f"{idx}. {title}")
            if url:
                lines.append(f"   链接：{url}")
            meta = []
            if website:
                meta.append(f"来源：{website}")
            if date:
                meta.append(f"时间：{date}")
            if meta:
                lines.append(f"   {' | '.join(meta)}")
            if snippet:
                snippet = snippet.replace("\n", " ")
                if len(snippet) > 400:
                    snippet = snippet[:400] + "..."
                lines.append(f"   摘要：{snippet}")
            lines.append("")

        if not refs:
            lines.append("（未返回搜索结果）")

        result = "\n".join(lines)
        return clip_text(result, self.agent_config.max_tool_result_chars)

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="searchWeb",
                description="使用百度千帆 AI 搜索（baidu_search_v2）检索网页，返回标题、链接、来源、时间和摘要",
                parameters=object_schema(
                    properties={
                        "query": {"type": "string", "description": "搜索关键词或问题"},
                        "top_k": {"type": "integer", "description": "返回结果数量（1~10，默认 5）"},
                    },
                    required=["query"],
                ),
                handler=self.search_web,
                example_arguments={"query": "Python 3.13 新特性", "top_k": 5},
            )
        ]


__all__ = ["SearchToolbox"]
