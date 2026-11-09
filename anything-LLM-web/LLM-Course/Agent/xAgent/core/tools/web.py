"""网页抓取工具：调用本地 curl 下载 HTML，再用 trafilatura 提取正文。

``fetchWeb`` 的目标是给 LLM 提供“可读的网页正文”，而不是整页 HTML：
导航栏、广告、页脚、脚本样式等会被 trafilatura 清理，正文中的标题、段落、
列表、表格和链接则尽量保留为 Markdown。
"""

from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

from ..config import AgentConfig
from ..errors import ToolError
from ..utils.text import clip_text
from .base import ToolSpec, object_schema, text_argument

# 标准桌面 Chrome 请求头。不要附加 xAgent/1.0 之类的自定义标识，
# 否则百度百科等站点会直接判定为爬虫并返回 403。
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8,"
    "application/signed-exchange;v=b3;q=0.7"
)
_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"
_SEC_CH_UA = '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"'
_MAX_DOWNLOAD_BYTES = 10_000_000
_MAX_REDIRECTS = 5
_MAX_ATTEMPTS = 2
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MIN_DELAY_SECONDS = 0.25
_MAX_DELAY_SECONDS = 0.75
# 跨进程复用普通浏览 Cookie（例如百度 BAIDUID），降低被反复挑战的概率。
_COOKIE_JAR = Path(tempfile.gettempdir()) / "xagent_web_cookies.txt"
_HTTP_STATUS_RE = re.compile(r"^HTTP/\S+\s+(\d{3})\b", re.MULTILINE | re.IGNORECASE)
_STDERR_STATUS_RE = re.compile(r"(?:HTTP\s+|returned error:\s*)(\d{3})\b", re.IGNORECASE)


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
    # 对中文路径/参数做百分号编码，避免 Windows 下 curl 解析失败。
    path = quote(parsed.path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="=&?/:@!$'()*+,;%-._~")
    return urlunsplit((parsed.scheme, parsed.netloc, path, query, parsed.fragment))


def _referer_for(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    # 百度百科通常从百度搜索结果页点入，带百度 Referer 更接近真实访问。
    if "baidu.com" in host:
        return "https://www.baidu.com/"
    return f"{parsed.scheme}://{parsed.netloc}/"


def _read_http_status(header_path: str) -> int | None:
    """从 curl --dump-header 的输出中取最后一个 HTTP 状态码。"""
    try:
        raw = Path(header_path).read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    text = raw.decode("latin-1", errors="ignore")
    matches = _HTTP_STATUS_RE.findall(text)
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:  # pragma: no cover - 正则已限定三位数字
        return None


def _looks_like_antibot(html: bytes) -> str:
    """识别常见反爬/安全验证页面，返回命中特征；未命中返回空串。"""
    if not html:
        return ""
    head = html[:16_000].decode("utf-8", errors="ignore")
    lowered = head.lower()
    if "百度安全验证" in head or "seccaptcha" in lowered or "bioc_options" in lowered:
        return "百度安全验证"
    title_match = re.search(r"<title[^>]*>(.*?)</title>", head, re.IGNORECASE | re.DOTALL)
    title = (title_match.group(1) if title_match else "").strip()
    title_lower = title.lower()
    for marker in (
        "安全验证",
        "验证码",
        "人机验证",
        "access denied",
        "just a moment",
        "attention required",
    ):
        if marker in title_lower:
            return title or marker
    return ""


def _antibot_error(url: str, detail: str, marker: str = "") -> ToolError:
    suffix = f"：{marker}" if marker else ""
    return ToolError(
        f"网页访问失败：{detail}（触发站点反爬/安全验证{suffix}）。"
        "已携带标准浏览器请求头并复用 Cookie，且不会对 403 反复重试。"
        "建议改用 searchWeb 获取摘要，或更换镜像/其他权威站点链接。"
        f"目标：{url}"
    )


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

    def _run_curl(self, url: str, timeout: int):
        curl = _find_curl()
        referer = _referer_for(url)
        fd, header_path = tempfile.mkstemp(prefix="xagent_headers_", suffix=".txt")
        os.close(fd)
        command = [
            curl,
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
            "--dump-header",
            header_path,
            "--user-agent",
            _BROWSER_UA,
            "--header",
            f"Accept: {_ACCEPT}",
            "--header",
            f"Accept-Language: {_ACCEPT_LANGUAGE}",
            "--header",
            "Cache-Control: max-age=0",
            "--header",
            "Upgrade-Insecure-Requests: 1",
            "--header",
            "Sec-Fetch-Dest: document",
            "--header",
            "Sec-Fetch-Mode: navigate",
            "--header",
            "Sec-Fetch-Site: none",
            "--header",
            "Sec-Fetch-User: ?1",
            "--header",
            f"sec-ch-ua: {_SEC_CH_UA}",
            "--header",
            "sec-ch-ua-mobile: ?0",
            "--header",
            'sec-ch-ua-platform: "Windows"',
            "--header",
            f"Referer: {referer}",
            "--cookie",
            str(_COOKIE_JAR),
            "--cookie-jar",
            str(_COOKIE_JAR),
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
        finally:
            status = _read_http_status(header_path)
            try:
                os.unlink(header_path)
            except OSError:
                pass
        return completed, status

    def _download_html(self, url: str) -> bytes:
        timeout = max(1, int(self.config.web_timeout_seconds))
        completed = None
        status: int | None = None

        # 每次请求前加随机短延时，避免高频连续抓取被站点判定为爬虫。
        # 仅对 429/5xx 做一次短退避重试；401/403/404 绝不重试。
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            time.sleep(random.uniform(_MIN_DELAY_SECONDS, _MAX_DELAY_SECONDS))
            completed, status = self._run_curl(url, timeout)
            if status in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS:
                time.sleep(1.0)
                continue
            break

        if completed is None:  # pragma: no cover - 防御性分支
            raise ToolError(f"网页访问失败：未获取到响应（{url}）")

        body = completed.stdout or b""
        marker = _looks_like_antibot(body)

        if status is not None and status >= 400:
            detail = f"服务器返回 HTTP {status}"
            if status in (401, 407):
                raise ToolError(
                    f"网页访问失败：{detail}（需要登录或访问凭证）。"
                    "请改用 searchWeb 获取摘要或更换公开来源链接。"
                    f"目标：{url}"
                )
            # 部分站点（如百度百科）会用 403/404 等状态码返回安全验证页，
            # 因此先看页面特征，避免把反爬拦截误报成“页面不存在”。
            if marker or status in (403, 429):
                raise _antibot_error(url, detail, marker)
            if status == 404:
                raise ToolError(f"网页访问失败：{detail}（页面不存在）。目标：{url}")
            raise ToolError(f"网页访问失败：{detail}。目标：{url}")

        if completed.returncode != 0:
            detail = _decode_error(completed.stderr)
            if completed.returncode == 22:
                match = _STDERR_STATUS_RE.search(detail)
                http_code = int(match.group(1)) if match else None
                reason = (
                    f"服务器返回 HTTP {http_code}"
                    if http_code
                    else "服务器返回 HTTP 错误（4xx/5xx）"
                )
                if http_code == 403:
                    raise _antibot_error(url, reason)
            elif completed.returncode == 28:
                reason = f"抓取网页超时（{timeout}s）"
            elif completed.returncode == 63:
                reason = f"网页超过大小上限（{_MAX_DOWNLOAD_BYTES} 字节）"
            else:
                reason = f"curl 退出码 {completed.returncode}"
            if detail:
                reason = f"{reason}：{detail}"
            raise ToolError(f"网页访问失败：{reason}")

        if not body:
            raise ToolError(f"网页访问失败：服务器返回空内容（{url}）")
        if marker:
            # 某些站点返回 200 但正文其实是验证页，同样视为反爬拦截。
            raise _antibot_error(url, "返回了安全验证页面", marker)
        return body

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
                # trafilatura 的全局去重缓存会让同一页面第 4 次抓取后返回空正文；
                # 这里关闭跨请求去重，保证反复抓取同一权威页面仍能拿到内容。
                deduplicate=False,
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
