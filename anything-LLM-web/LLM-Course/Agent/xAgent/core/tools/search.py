"""百度千帆 AI 搜索工具：基于 Qianfan AI Search Web Search API。

使用 config.ini 中 [search] 的 baidu_qianfan_api_key 调用
https://qianfan.baidubce.com/v2/ai_search/web_search 获取搜索结果。
"""
from __future__ import annotations

import json
from typing import Any, Mapping

import requests

from ..config import AgentConfig, SearchConfig
from ..errors import ToolError
from ..utils.text import clip_text
from .base import ToolSpec, object_schema, text_argument

_DEFAULT_TOP_K = 5
_MAX_TOP_K = 10


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

        api_key = (self.search_config.api_key or "").strip()
        if not api_key:
            raise ToolError("未配置百度千帆 API Key，请在 config.ini 的 [search] 段设置 baidu_qianfan_api_key")

        endpoint = (self.search_config.endpoint or "https://qianfan.baidubce.com/v2/ai_search/web_search").strip()
        timeout = max(1.0, float(self.search_config.timeout or 30.0))

        body = {
            "messages": [{"role": "user", "content": query}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": top_k}],
        }

        try:
            resp = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=timeout,
            )
        except requests.exceptions.Timeout as exc:
            raise ToolError(f"百度搜索请求超时（{timeout}s）") from exc
        except requests.exceptions.RequestException as exc:
            raise ToolError(f"百度搜索网络请求失败：{exc}") from exc

        if resp.status_code >= 400:
            raise ToolError(f"百度搜索 API 返回 HTTP {resp.status_code}：{resp.text[:500]}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise ToolError(f"百度搜索 API 返回非 JSON：{resp.text[:200]}") from exc

        if not isinstance(data, Mapping):
            raise ToolError("百度搜索 API 返回的数据结构不符合预期（不是 JSON 对象）")

        api_error = _format_api_error(data)
        if api_error:
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
