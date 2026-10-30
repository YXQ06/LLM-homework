"""可选的联网冒烟测试。

默认跳过；显式设置 ``XAGENT_LIVE_TESTS=1`` 后才访问真实网页和百度千帆 API。
"""

from __future__ import annotations

import os
import unittest

from xAgent.core import load_config
from xAgent.core.tools.search import SearchToolbox
from xAgent.core.tools.web import WebToolbox

LIVE = os.environ.get("XAGENT_LIVE_TESTS") == "1"


@unittest.skipUnless(LIVE, "设置 XAGENT_LIVE_TESTS=1 后运行联网测试")
class LiveIntegrationTest(unittest.TestCase):
    def test_fetch_example_com(self) -> None:
        config = load_config()
        toolbox = WebToolbox(config.agent)
        result = toolbox.fetch_web({"url": "https://example.com"})

        self.assertIn("https://example.com", result)
        self.assertTrue(
            "documentation examples" in result or "文档示例" in result,
            f"未提取到 example.com 正文：{result[:200]}",
        )

    def test_search_with_configured_qianfan_key(self) -> None:
        config = load_config()
        self.assertTrue(
            config.search.api_key,
            "config.ini 的 [search] 段没有配置 baidu_qianfan_api_key",
        )
        toolbox = SearchToolbox(config.agent, config.search)
        result = toolbox.search_web(
            {"query": "Python 3.13 新特性", "top_k": 1}
        )

        self.assertIn("搜索词：Python 3.13 新特性", result)
        self.assertIn("结果数：", result)
        self.assertIn("链接：", result)


if __name__ == "__main__":
    unittest.main()
