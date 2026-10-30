"""Agent 工具循环与 searchWeb 的集成测试（不访问网络）。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Mapping, Sequence
from unittest.mock import patch

from xAgent.core.agent import Agent
from xAgent.core.config import AgentConfig, SearchConfig
from xAgent.core.events import AnswerReady, ToolCallFinished, ToolCallStarted
from xAgent.core.tools import build_default_registry
from xAgent.tests.test_search_tool import FakeResponse, SUCCESS_PAYLOAD


class ScriptedClient:
    def __init__(self, responses: Sequence[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[list[dict[str, Any]]] = []
        self.tools_seen: list[list[dict[str, Any]]] = []

    def chat(
        self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        self.requests.append([dict(message) for message in messages])
        self.tools_seen.append(list(tools or []))
        return dict(self.responses.pop(0))


class AgentToolLoopTest(unittest.TestCase):
    def test_search_web_tool_call_is_executed_and_result_is_returned(self) -> None:
        registry = build_default_registry(
            AgentConfig(max_tool_rounds=3), SearchConfig(api_key="test-key")
        )
        client = ScriptedClient(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "searchWeb",
                                "arguments": json.dumps(
                                    {"query": "Python 3.13", "top_k": 1},
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": "根据搜索结果，Python 3.13 引入了自由线程模式。",
                },
            ]
        )
        agent = Agent(client, registry, AgentConfig(max_tool_rounds=3))

        with patch(
            "xAgent.core.tools.search.requests.post",
            return_value=FakeResponse(payload=SUCCESS_PAYLOAD),
        ):
            events = list(agent.send("搜索 Python 3.13 新特性"))

        started = [event for event in events if isinstance(event, ToolCallStarted)]
        finished = [event for event in events if isinstance(event, ToolCallFinished)]
        answers = [event for event in events if isinstance(event, AnswerReady)]

        self.assertEqual([event.name for event in started], ["searchWeb"])
        self.assertEqual([event.name for event in finished], ["searchWeb"])
        self.assertTrue(finished[0].ok)
        self.assertIn("Python 3.13 新特性", finished[0].result)
        self.assertIn("https://docs.python.org/3.13/whatsnew/3.13.html", finished[0].result)
        self.assertEqual(
            answers[0].text, "根据搜索结果，Python 3.13 引入了自由线程模式。"
        )

        # 第一次请求应声明 searchWeb；第二次请求应携带工具结果。
        self.assertTrue(
            any(
                tool["function"]["name"] == "searchWeb"
                for tool in client.tools_seen[0]
            )
        )
        second_messages = client.requests[1]
        tool_messages = [message for message in second_messages if message["role"] == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0]["tool_call_id"], "call-1")
        self.assertIn("Python 3.13 新特性", tool_messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
