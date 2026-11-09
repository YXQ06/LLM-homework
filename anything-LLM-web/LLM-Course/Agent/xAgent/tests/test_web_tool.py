"""fetchWeb 工具的离线单元测试。"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from xAgent.core.config import AgentConfig
from xAgent.core.errors import ToolError
from xAgent.core.tools.web import WebToolbox

SAMPLE_HTML = b"""<!doctype html>
<html>
<head><title>Demo Page</title></head>
<body>
  <nav>NAVIGATION-MENU</nav>
  <article>
    <h1>Main Heading</h1>
    <p>First paragraph with <a href="https://example.com/more">a useful link</a>.</p>
    <h2>Sub Heading</h2>
    <ul><li>Item one</li><li>Item two</li></ul>
    <table>
      <thead><tr><th>Name</th><th>Value</th></tr></thead>
      <tbody>
        <tr><td>Alpha</td><td>1</td></tr>
        <tr><td>Beta</td><td>2</td></tr>
      </tbody>
    </table>
  </article>
  <aside>ASIDE-ADS</aside>
  <footer>FOOTER-COPYRIGHT</footer>
</body>
</html>
"""

CAPTCHA_HTML = b"""<!doctype html>
<html><head><title>Baidu Security Verification</title></head>
<body><script>window.BIOC_OPTIONS={};</script>seccaptcha</body></html>
"""


def completed(stdout: bytes = b"", stderr: bytes = b"", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["curl"], returncode=code, stdout=stdout, stderr=stderr
    )


class WebToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.toolbox = WebToolbox(AgentConfig(web_timeout_seconds=3))

    def test_curl_is_invoked_with_argument_list_and_without_shell(self) -> None:
        url = "https://example.com/demo"
        with (
            patch(
                "xAgent.core.tools.web.shutil.which",
                return_value=r"C:\Windows\System32\curl.exe",
            ),
            patch(
                "xAgent.core.tools.web.subprocess.run",
                return_value=completed(stdout=SAMPLE_HTML),
            ) as run,
        ):
            result = self.toolbox.fetch_web({"url": url})

        command = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertIsInstance(command, list)
        self.assertEqual(command[-1], url)
        self.assertNotIn("shell", kwargs)
        self.assertFalse(kwargs.get("shell", False))
        self.assertIn("--max-time", command)
        self.assertIn(url, result)
        self.assertIn("# Main Heading", result)

    def test_extracts_markdown_and_removes_navigation_and_footer(self) -> None:
        markdown = self.toolbox._extract_markdown(
            SAMPLE_HTML, "https://example.com/demo"
        )

        self.assertIn("# Main Heading", markdown)
        self.assertIn("## Sub Heading", markdown)
        self.assertIn("First paragraph", markdown)
        self.assertIn("[a useful link](https://example.com/more)", markdown)
        self.assertIn("- Item one", markdown)
        self.assertIn("- Item two", markdown)
        self.assertIn("| Name | Value |", markdown)
        self.assertIn("| Alpha | 1 |", markdown)
        self.assertNotIn("NAVIGATION-MENU", markdown)
        self.assertNotIn("ASIDE-ADS", markdown)
        self.assertNotIn("FOOTER-COPYRIGHT", markdown)

    def test_rejects_non_http_url(self) -> None:
        for url in ("ftp://example.com", "not-a-url", ""):
            with self.subTest(url=url):
                with self.assertRaises(ToolError):
                    self.toolbox.fetch_web({"url": url})

    def test_curl_missing(self) -> None:
        with patch("xAgent.core.tools.web.shutil.which", return_value=None):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.fetch_web({"url": "https://example.com"})
        self.assertIn("curl", str(ctx.exception))

    def test_subprocess_timeout(self) -> None:
        with (
            patch(
                "xAgent.core.tools.web.shutil.which", return_value="curl.exe"
            ),
            patch(
                "xAgent.core.tools.web.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="curl", timeout=8),
            ),
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.fetch_web({"url": "https://example.com"})
        self.assertIn("超时", str(ctx.exception))

    def test_http_error_from_curl(self) -> None:
        with (
            patch(
                "xAgent.core.tools.web.shutil.which", return_value="curl.exe"
            ),
            patch(
                "xAgent.core.tools.web.subprocess.run",
                return_value=completed(stderr=b"curl: (22) HTTP 404", code=22),
            ),
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.fetch_web({"url": "https://example.com/missing"})
        message = str(ctx.exception)
        self.assertIn("网页访问失败", message)
        self.assertIn("HTTP", message)
        self.assertIn("404", message)

    def test_oversized_page_from_curl(self) -> None:
        with (
            patch(
                "xAgent.core.tools.web.shutil.which", return_value="curl.exe"
            ),
            patch(
                "xAgent.core.tools.web.subprocess.run",
                return_value=completed(stderr=b"curl: (63) too large", code=63),
            ),
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.fetch_web({"url": "https://example.com/big"})
        self.assertIn("大小上限", str(ctx.exception))

    def test_empty_response_body(self) -> None:
        with (
            patch(
                "xAgent.core.tools.web.shutil.which", return_value="curl.exe"
            ),
            patch(
                "xAgent.core.tools.web.subprocess.run",
                return_value=completed(stdout=b""),
            ),
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.fetch_web({"url": "https://example.com"})
        self.assertIn("空内容", str(ctx.exception))

    def test_invalid_html_is_reported(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            self.toolbox._extract_markdown(b"\x00\x01\x02\xff", "https://example.com")
        self.assertIn("HTML 解析失败", str(ctx.exception))

    def test_page_without_main_content_is_reported(self) -> None:
        html = b"<html><body><nav>menu</nav><footer>copy</footer></body></html>"
        with self.assertRaises(ToolError) as ctx:
            self.toolbox._extract_markdown(html, "https://example.com")
        self.assertIn("正文提取失败", str(ctx.exception))

    def test_browser_like_headers_are_sent(self) -> None:
        url = "https://baike.baidu.com/item/%E6%B0%B4%E7%A8%BB"
        with (
            patch(
                "xAgent.core.tools.web.shutil.which", return_value="curl.exe"
            ),
            patch(
                "xAgent.core.tools.web.subprocess.run",
                return_value=completed(stdout=SAMPLE_HTML),
            ) as run,
        ):
            self.toolbox.fetch_web({"url": url})

        command = run.call_args.args[0]
        joined = " ".join(command)
        self.assertIn("--user-agent", command)
        self.assertIn("Chrome/131", joined)
        self.assertNotIn("xAgent/1.0", joined)
        self.assertIn("sec-ch-ua", joined)
        self.assertIn("Accept-Language: zh-CN", joined)
        self.assertIn("Referer: https://www.baidu.com/", joined)
        self.assertIn("--cookie-jar", command)

    def test_antibot_403_is_reported_and_not_retried(self) -> None:
        with (
            patch(
                "xAgent.core.tools.web.shutil.which", return_value="curl.exe"
            ),
            patch(
                "xAgent.core.tools.web.subprocess.run",
                return_value=completed(stdout=CAPTCHA_HTML),
            ) as run,
            patch(
                "xAgent.core.tools.web._read_http_status", return_value=403
            ),
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.fetch_web(
                    {"url": "https://baike.baidu.com/item/%E6%B0%B4%E7%A8%BB"}
                )

        run.assert_called_once()
        message = str(ctx.exception)
        self.assertIn("反爬", message)
        self.assertIn("searchWeb", message)

    def test_transient_5xx_is_retried_once(self) -> None:
        with (
            patch(
                "xAgent.core.tools.web.shutil.which", return_value="curl.exe"
            ),
            patch(
                "xAgent.core.tools.web.subprocess.run",
                return_value=completed(stdout=SAMPLE_HTML),
            ) as run,
            patch(
                "xAgent.core.tools.web._read_http_status",
                side_effect=[503, 200],
            ),
        ):
            result = self.toolbox.fetch_web({"url": "https://example.com/demo"})

        self.assertEqual(run.call_count, 2)
        self.assertIn("# Main Heading", result)

    def test_result_is_clipped_to_configured_limit(self) -> None:
        toolbox = WebToolbox(AgentConfig(max_tool_result_chars=100))
        with (
            patch(
                "xAgent.core.tools.web.shutil.which", return_value="curl.exe"
            ),
            patch(
                "xAgent.core.tools.web.subprocess.run",
                return_value=completed(stdout=SAMPLE_HTML),
            ),
        ):
            result = toolbox.fetch_web({"url": "https://example.com/demo"})
        self.assertIn("已截断", result)


if __name__ == "__main__":
    unittest.main()
