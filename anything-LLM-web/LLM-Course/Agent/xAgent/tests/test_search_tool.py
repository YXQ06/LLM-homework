"""searchWeb 工具的离线单元测试与配置读取测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from xAgent.core.config import AgentConfig, SearchConfig, load_config
from xAgent.core.errors import ToolError
from xAgent.core.tools.search import SearchToolbox, mask_secret, normalize_secret

SUCCESS_PAYLOAD = {
    "references": [
        {
            "title": "Python 3.13 新特性",
            "url": "https://docs.python.org/3.13/whatsnew/3.13.html",
            "website": "docs.python.org",
            "date": "2024-10-07",
            "content": "Python 3.13 引入了新的交互式解释器和实验性的自由线程模式。",
        },
        {
            "title": "Python 下载页面",
            "url": "https://www.python.org/downloads/",
            "snippet": "Download the latest Python release.",
        },
    ],
    "request_id": "test-request-id",
}


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: object | None = None,
        *,
        text: str | None = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error
        self.text = text if text is not None else json.dumps(
            payload, ensure_ascii=False
        )

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class SearchToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.agent_config = AgentConfig()
        self.toolbox = SearchToolbox(
            self.agent_config, SearchConfig(api_key="test-key", timeout=7)
        )

    def test_success_formats_titles_urls_and_summaries(self) -> None:
        response = FakeResponse(payload=SUCCESS_PAYLOAD)
        with patch(
            "xAgent.core.tools.search.requests.post", return_value=response
        ) as post:
            result = self.toolbox.search_web({"query": "Python 3.13", "top_k": 2})

        _, kwargs = post.call_args
        self.assertEqual(
            kwargs["headers"]["Authorization"], "Bearer test-key"
        )
        self.assertEqual(kwargs["json"]["search_source"], "baidu_search_v2")
        self.assertEqual(
            kwargs["json"]["resource_type_filter"], [{"type": "web", "top_k": 2}]
        )
        self.assertEqual(
            kwargs["json"]["messages"], [{"role": "user", "content": "Python 3.13"}]
        )
        self.assertEqual(kwargs["timeout"], 7)

        self.assertIn("搜索词：Python 3.13", result)
        self.assertIn("结果数：2", result)
        self.assertIn("Python 3.13 新特性", result)
        self.assertIn("https://docs.python.org/3.13/whatsnew/3.13.html", result)
        self.assertIn("docs.python.org", result)
        self.assertIn("2024-10-07", result)
        self.assertIn("自由线程模式", result)
        self.assertIn("Python 下载页面", result)
        self.assertIn("Download the latest Python release.", result)
        self.assertNotIn("test-key", result)

    def test_missing_api_key(self) -> None:
        toolbox = SearchToolbox(AgentConfig(), SearchConfig(api_key=""))
        with self.assertRaises(ToolError) as ctx:
            toolbox.search_web({"query": "test"})
        message = str(ctx.exception)
        self.assertIn("未配置", message)
        self.assertIn("baidu_qianfan_api_key", message)

    def test_http_error_response(self) -> None:
        response = FakeResponse(
            status_code=401,
            payload={"code": 216003, "message": "invalid key"},
            text='{"code":216003,"message":"invalid key"}',
        )
        with patch(
            "xAgent.core.tools.search.requests.post", return_value=response
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.search_web({"query": "test"})
        message = str(ctx.exception)
        self.assertIn("HTTP 401", message)
        self.assertIn("216003", message)

    def test_api_error_in_200_response(self) -> None:
        response = FakeResponse(
            payload={
                "code": 216003,
                "message": "Authentication error",
                "requestId": "req-1",
            }
        )
        with patch(
            "xAgent.core.tools.search.requests.post", return_value=response
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.search_web({"query": "test"})
        message = str(ctx.exception)
        self.assertIn("错误码 216003", message)
        self.assertIn("Authentication error", message)
        self.assertIn("req-1", message)

    def test_nested_error_in_200_response(self) -> None:
        response = FakeResponse(
            payload={"error": {"code": 400, "message": "bad request"}}
        )
        with patch(
            "xAgent.core.tools.search.requests.post", return_value=response
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.search_web({"query": "test"})
        self.assertIn("bad request", str(ctx.exception))

    def test_non_json_response(self) -> None:
        response = FakeResponse(text="<html>gateway error</html>", json_error=ValueError())
        with patch(
            "xAgent.core.tools.search.requests.post", return_value=response
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.search_web({"query": "test"})
        self.assertIn("非 JSON", str(ctx.exception))

    def test_timeout(self) -> None:
        with patch(
            "xAgent.core.tools.search.requests.post",
            side_effect=requests.exceptions.Timeout(),
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.search_web({"query": "test"})
        self.assertIn("超时", str(ctx.exception))

    def test_network_failure(self) -> None:
        with patch(
            "xAgent.core.tools.search.requests.post",
            side_effect=requests.exceptions.ConnectionError("connection refused"),
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.search_web({"query": "test"})
        self.assertIn("网络请求失败", str(ctx.exception))

    def test_no_results(self) -> None:
        response = FakeResponse(payload={"references": [], "request_id": "r"})
        with patch(
            "xAgent.core.tools.search.requests.post", return_value=response
        ):
            result = self.toolbox.search_web({"query": "nothing"})
        self.assertIn("未返回搜索结果", result)

    def test_invalid_top_k(self) -> None:
        for value in (0, 11, "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ToolError):
                    self.toolbox.search_web({"query": "test", "top_k": value})

    def test_nested_data_references_are_supported(self) -> None:
        response = FakeResponse(payload={"data": {"references": SUCCESS_PAYLOAD["references"]}})
        with patch(
            "xAgent.core.tools.search.requests.post", return_value=response
        ):
            result = self.toolbox.search_web({"query": "test"})
        self.assertIn("Python 3.13 新特性", result)


class SearchSecretTest(unittest.TestCase):
    def test_normalize_secret_removes_paste_artifacts(self) -> None:
        raw = '\ufeff  "Bearer bce-v3/ALTAK-abc/def"\r\n'
        self.assertEqual(normalize_secret(raw), "bce-v3/ALTAK-abc/def")
        self.assertEqual(
            normalize_secret("bce-v3/ALTAK-a\u200bb/def\n"),
            "bce-v3/ALTAK-ab/def",
        )

    def test_normalized_key_is_used_in_authorization_header(self) -> None:
        toolbox = SearchToolbox(
            AgentConfig(),
            SearchConfig(api_key=' \ufeff"Bearer test-key"\r\n', timeout=7),
        )
        response = FakeResponse(payload={"references": []})
        with patch(
            "xAgent.core.tools.search.requests.post", return_value=response
        ) as post:
            toolbox.search_web({"query": "test"})
        _, kwargs = post.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_placeholder_key_never_calls_api(self) -> None:
        toolbox = SearchToolbox(
            AgentConfig(), SearchConfig(api_key="mock-search-key")
        )
        with patch("xAgent.core.tools.search.requests.post") as post:
            with self.assertRaises(ToolError) as ctx:
                toolbox.search_web({"query": "test"})
        post.assert_not_called()
        self.assertIn("占位符", str(ctx.exception))

    def test_auth_error_is_not_retried(self) -> None:
        response = FakeResponse(
            status_code=401,
            payload={"code": 216003, "message": "invalid key"},
        )
        toolbox = SearchToolbox(
            AgentConfig(), SearchConfig(api_key="test-key", timeout=7)
        )
        with patch(
            "xAgent.core.tools.search.requests.post", return_value=response
        ) as post:
            with self.assertRaises(ToolError) as ctx:
                toolbox.search_web({"query": "test"})
        self.assertEqual(post.call_count, 1)
        message = str(ctx.exception)
        self.assertIn("HTTP 401", message)
        self.assertIn("216003", message)
        self.assertIn("已停止重试", message)
        self.assertNotIn("test-key", message)
        self.assertIn(mask_secret("test-key"), message)


class SearchConfigTest(unittest.TestCase):
    def test_api_key_is_read_from_search_section(self) -> None:
        content = """
[llm]
base_url = http://127.0.0.1:1234/v1
model = test-model

[agent]
max_tool_rounds = 3

[search]
baidu_qianfan_api_key = key-from-ini
endpoint = https://example.com/search
timeout = 9
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text(content, encoding="utf-8")
            config = load_config(path)

        self.assertEqual(config.search.api_key, "key-from-ini")
        self.assertEqual(config.search.endpoint, "https://example.com/search")
        self.assertEqual(config.search.timeout, 9)
        self.assertEqual(config.agent.max_tool_rounds, 3)


if __name__ == "__main__":
    unittest.main()
