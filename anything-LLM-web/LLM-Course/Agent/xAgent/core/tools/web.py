"""网页抓取工具：调用本地 curl 下载 HTML，再用 trafilatura 提取正文。

``fetchWeb`` 的目标是给 LLM 提供“可读的网页正文”，而不是整页 HTML：
导航栏、广告、页脚、脚本样式等会被 trafilatura 清理，正文中的标题、段落、
列表、表格和链接则尽量保留为 Markdown。
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Mapping
from urllib.parse import urlsplit

from ..config import AgentConfig
from ..errors import ToolError
from ..utils.text import clip_text
from .base import ToolSpec, object_schema, text_argument

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 xAgent/1.0"
)
_MAX_DOWNLOAD_BYTES = 10_000_000
_MAX_REDIRECTS = 5


def _decode_error(data: bytes) -> str:
    """curl 的错误输出在 Windows 上可能是 UTF-8 或本地代码页。"""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace").strip()


def _validate_url(url: str) -> str:
    if not url:
        raise ToolError("缺少必填参数：url（请给出完整网页地址）")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ToolError(f"不支持的 URL 协议：{parsed.scheme or '（空）'}，只允许 http/https")
    if not parsed.netloc:
        raise ToolError(f"URL 缺少主机名：{url!r}")
    return url


def _find_curl() -> str:
    executable = shutil.which("curl")
    if not executable:
        raise ToolError(
            "未找到本地 curl 程序，请先安装 curl 并确保它已加入 PATH"
        )
    return executable


class WebToolbox:
    """网页工具集合。"""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()

    def _download_html(self, url: str) -> bytes:
        timeout = max(1, int(self.config.web_timeout_seconds))
        command = [
            _find_curl(),
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--compressed",
            "--max-time",
            str(timeout),
            "--max-redirs",
            str(_MAX_REDIRECTS),
            "--max-filesize",
            str(_MAX_DOWNLOAD_BYTES),
            "--user-agent",
            _USER_AGENT,
            "--header",
            "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "--header",
            "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
            "--",
            url,
        ]

        try:
            # 参数列表方式执行，绝不使用 shell=True，避免 URL 被 shell 解释。
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout + 5,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"抓取网页超时（{timeout}s）：{url}") from exc
        except FileNotFoundError as exc:
            raise ToolError("运行 curl 失败：找不到可执行文件") from exc
        except OSError as exc:
            raise ToolError(f"运行 curl 失败：{exc}") from exc

        if completed.returncode != 0:
            detail = _decode_error(completed.stderr)
            if completed.returncode == 22:
                reason = "服务器返回 HTTP 错误（4xx/5xx）"
            elif completed.returncode == 28:
                reason = f"抓取网页超时（{timeout}s）"
            elif completed.returncode == 63:
                reason = f"网页超过大小上限（{_MAX_DOWNLOAD_BYTES} 字节）"
            else:
                reason = f"curl 退出码 {completed.returncode}"
            if detail:
                reason = f"{reason}：{detail}"
            raise ToolError(f"网页访问失败：{reason}")

        if not completed.stdout:
            raise ToolError(f"网页访问失败：服务器返回空内容（{url}）")
        return completed.stdout

    def _extract_markdown(self, html: bytes, url: str) -> str:
        try:
            import trafilatura
        except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可操作提示
            raise ToolError(
                "缺少 trafilatura 依赖，请执行：pip install trafilatura"
            ) from exc

        try:
            tree = trafilatura.load_html(html)
        except Exception as exc:
            raise ToolError(f"HTML 解析失败：{exc}") from exc
        if tree is None:
            raise ToolError("HTML 解析失败：返回内容不是有效网页（可能不是 HTML）")

        try:
            markdown = trafilatura.extract(
                tree,
                url=url,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                include_links=True,
                include_formatting=True,
                deduplicate=True,
                with_metadata=False,
                prune_xpath=["//nav", "//footer", "//aside"],
            )
        except Exception as exc:
            raise ToolError(f"正文提取失败：{exc}") from exc

        if not markdown or not markdown.strip():
            raise ToolError(
                "正文提取失败：未能从页面中识别出有效正文"
                "（页面可能需要登录、由 JavaScript 动态渲染，或内容过少）"
            )
        return markdown.strip()

    def fetch_web(self, arguments: Mapping[str, Any]) -> str:
        url = _validate_url(text_argument(arguments, "url", required=True))
        html = self._download_html(url)
        markdown = self._extract_markdown(html, url)
        markdown = clip_text(markdown, self.config.max_tool_result_chars)
        return f"网页 URL：{url}\n\n正文（Markdown）：\n{markdown}"

    def specs(self) -> list[ToolSpec]:
        """生成 fetchWeb 工具的声明。"""
        return [
            ToolSpec(
                name="fetchWeb",
                description=(
                    "抓取指定网页并提取正文（Markdown 格式），"
                    "适合阅读在线文章、文档、新闻等；会自动去除导航、广告和页脚"
                ),
                parameters=object_schema(
                    properties={
                        "url": {
                            "type": "string",
                            "description": "要抓取的完整网页 URL，必须以 http:// 或 https:// 开头",
                        }
                    },
                    required=["url"],
                ),
                handler=self.fetch_web,
                example_arguments={"url": "https://example.com"},
            )
        ]


__all__ = ["WebToolbox"]
