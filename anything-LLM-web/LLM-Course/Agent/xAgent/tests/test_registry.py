"""工具注册表与系统提示词测试。"""

from __future__ import annotations

import unittest

from xAgent.core.config import AgentConfig, SearchConfig
from xAgent.core.prompts import build_system_prompt
from xAgent.core.tools import build_default_registry


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_registry(
            AgentConfig(), SearchConfig(api_key="test-key")
        )

    def test_fetch_web_and_search_web_are_registered(self) -> None:
        self.assertIn("fetchWeb", self.registry.names)
        self.assertIn("searchWeb", self.registry.names)

    def test_tool_schemas(self) -> None:
        fetch = self.registry.get("fetchWeb")
        search = self.registry.get("searchWeb")
        assert fetch is not None and search is not None

        self.assertEqual(fetch.required_parameters, ("url",))
        self.assertEqual(search.required_parameters, ("query",))
        self.assertIn("url", fetch.parameters["properties"])
        self.assertIn("top_k", search.parameters["properties"])

        fetch_schema = fetch.to_openai_schema()
        self.assertEqual(fetch_schema["type"], "function")
        self.assertEqual(fetch_schema["function"]["name"], "fetchWeb")

    def test_system_prompt_lists_new_tools(self) -> None:
        prompt = build_system_prompt(self.registry)
        self.assertIn("fetchWeb", prompt)
        self.assertIn("searchWeb", prompt)

    def test_tool_errors_are_returned_to_the_model(self) -> None:
        result = self.registry.execute("fetchWeb", {})
        self.assertFalse(result.ok)
        self.assertIn("url", result.content)

        result = self.registry.execute("searchWeb", {})
        self.assertFalse(result.ok)
        self.assertIn("query", result.content)

        result = self.registry.execute("notExists", {})
        self.assertFalse(result.ok)
        self.assertIn("未知工具", result.content)


if __name__ == "__main__":
    unittest.main()
