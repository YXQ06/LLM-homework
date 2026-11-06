"""上下文压缩、归档和 recallContext 检索测试（完全离线）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from xAgent.core.agent import Agent
from xAgent.core.config import AgentConfig, LLMConfig, SearchConfig
from xAgent.core.context import (
    ArchiveStore,
    ContextManager,
    estimate_tokens,
    usage_total_tokens,
)
from xAgent.core.events import (
    CompactionFailed,
    CompactionFinished,
    CompactionStarted,
    ContextUsage,
    TurnFailed,
)
from xAgent.core.llm.client import OpenAICompatibleClient
from xAgent.core.tools import build_default_registry


class FakeClient:
    """只返回固定摘要，并记录是否带 tools 调用。"""

    def __init__(self, context_window: int = 100_000) -> None:
        self.config = SimpleNamespace(context_window=context_window)
        self.last_usage: dict[str, Any] | None = None
        self.requests: list[tuple[list[dict], Any]] = []

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.requests.append(([dict(message) for message in messages], tools))
        self.last_usage = {"total_tokens": 3_200}
        return {
            "role": "assistant",
            "content": "# 工作摘要\n\n- 用户要求完成测试任务。\n- 下一步继续验证。",
        }


class ArchiveAndRecallTest(unittest.TestCase):
    def test_archive_preserves_messages_and_search_returns_location(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ArchiveStore(Path(temp_dir) / ".agents" / "context")
            messages = [
                {"role": "system", "content": "系统提示词"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "readTextFile",
                                "arguments": '{"path": "data.csv"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": "关键结果：产量为 12.5 吨/公顷",
                },
            ]

            record = store.save(messages)
            path = Path(temp_dir) / ".agents" / "context" / (
                record.archive_id + ".jsonl"
            )
            self.assertTrue(path.is_file())

            lines = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual([item["role"] for item in lines], ["system", "assistant", "tool"])
            self.assertEqual(lines[1]["tool_calls"][0]["function"]["name"], "readTextFile")
            self.assertEqual(lines[2]["tool_call_id"], "call-1")

            result = store.search("产量")
            self.assertIn(record.archive_id, result)
            self.assertIn("第 3 条消息", result)
            self.assertIn("12.5", result)

            scoped = store.search("产量", archive_id=record.archive_id)
            self.assertIn("归档 " + record.archive_id, scoped)

            with self.assertRaises(ValueError):
                store.search("产量", archive_id="../escape")

    def test_recall_context_is_registered_as_normal_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ArchiveStore(Path(temp_dir) / ".agents" / "context")
            store.save(
                [
                    {"role": "user", "content": "请记住配置文件路径是 config.ini"},
                    {"role": "assistant", "content": "好的，已记录。"},
                ]
            )

            registry = build_default_registry(
                AgentConfig(), SearchConfig(), project_root=temp_dir
            )
            self.assertIn("recallContext", registry.names)
            result = registry.execute(
                "recallContext", {"text": "config.ini"}
            )
            self.assertTrue(result.ok)
            self.assertIn("归档编号", result.content)
            self.assertIn("config.ini", result.content)


class ContextLengthTest(unittest.TestCase):
    def test_default_window_is_128k_and_usage_prefers_api_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = SimpleNamespace(config=SimpleNamespace(context_window=0))
            manager = ContextManager(client, AgentConfig(), temp_dir)
            snapshot = manager.snapshot(
                [{"role": "system", "content": "hi"}], resolve_window=True
            )
            self.assertEqual(snapshot.window_tokens, 128_000)
            self.assertTrue(snapshot.window_is_default)

            snapshot = manager.snapshot(
                [{"role": "system", "content": "hi"}],
                usage={"total_tokens": 80_000},
                resolve_window=False,
            )
            self.assertEqual(snapshot.used_tokens, 80_000)
            self.assertAlmostEqual(snapshot.ratio, 0.625)

    def test_token_estimation_and_usage_fallbacks(self) -> None:
        self.assertGreater(estimate_tokens("中文内容"), estimate_tokens("abc"))
        self.assertEqual(usage_total_tokens({"total_tokens": 123}), 123)
        self.assertEqual(
            usage_total_tokens(
                {"prompt_tokens": 100, "completion_tokens": 23}
            ),
            123,
        )
        self.assertIsNone(usage_total_tokens(None))


class AgentCompactionTest(unittest.TestCase):
    def _make_agent(self, temp_dir: str, client: FakeClient) -> Agent:
        return Agent(
            client,
            build_default_registry(AgentConfig(), project_root=temp_dir),
            AgentConfig(compact_target_tokens=5_000),
            system_prompt="你是测试助手。",
            project_root=temp_dir,
        )

    def test_compact_archives_then_replaces_history_with_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeClient()
            agent = self._make_agent(temp_dir, client)
            agent.messages.extend(
                [
                    {"role": "user", "content": "请分析 data.csv 的产量数据"},
                    {"role": "assistant", "content": "已经完成读取，下一步做方差分析。"},
                ]
            )

            events = list(agent.compact())

            started = [e for e in events if isinstance(e, CompactionStarted)]
            finished = [e for e in events if isinstance(e, CompactionFinished)]
            failed = [e for e in events if isinstance(e, CompactionFailed)]
            self.assertEqual(len(started), 1)
            self.assertEqual(len(finished), 1)
            self.assertEqual(failed, [])

            # 压缩只能调用一次 LLM，且不能携带任何工具。
            self.assertEqual(len(client.requests), 1)
            self.assertIsNone(client.requests[0][1])

            archive_path = Path(finished[0].archive_path)
            self.assertTrue(archive_path.is_file())
            self.assertEqual(finished[0].archive_id, archive_path.stem)

            # 保留 system prompt，只留下摘要 + 归档说明。
            self.assertEqual(agent.messages[0]["role"], "system")
            self.assertEqual(agent.messages[0]["content"], "你是测试助手。")
            self.assertEqual(len(agent.messages), 2)
            compacted = agent.messages[1]["content"]
            self.assertIn("【此前会话上下文摘要】", compacted)
            self.assertIn("recallContext", compacted)
            self.assertIn(finished[0].archive_id, compacted)

    def test_failed_summary_keeps_original_messages(self) -> None:
        class FailingClient(FakeClient):
            def chat(self, messages, tools=None):  # type: ignore[override]
                raise RuntimeError("接口失败")

        with tempfile.TemporaryDirectory() as temp_dir:
            agent = self._make_agent(temp_dir, FailingClient())
            original = [
                {"role": "system", "content": "你是测试助手。"},
                {"role": "user", "content": "原始问题"},
            ]
            agent.messages = [dict(message) for message in original]

            events = list(agent.compact())
            failed = [e for e in events if isinstance(e, CompactionFailed)]
            self.assertEqual(len(failed), 1)
            self.assertEqual(agent.messages, original)

    def test_threshold_blocks_new_prompts_until_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeClient(context_window=1_000)
            agent = self._make_agent(temp_dir, client)
            agent._last_usage_total = 800  # 模拟接口返回的 80% 使用量

            events = list(agent.send("继续执行"))

            failed = [e for e in events if isinstance(e, TurnFailed)]
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0].kind, "context_limit")
            self.assertEqual(client.requests, [])
            usage = [e for e in events if isinstance(e, ContextUsage)]
            self.assertTrue(any(event.warning for event in usage))
            self.assertTrue(agent.needs_compaction())


class _FakeResponse:
    def __init__(self, payload: Any = None, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = "" if payload is None else json.dumps(payload, ensure_ascii=False)

    def json(self) -> Any:
        if self.payload is None:
            raise ValueError("响应不是 JSON")
        return self.payload


class _FakeSession:
    """记录请求，并按顺序返回预设响应。"""

    def __init__(self, *responses: _FakeResponse) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def _next(self, method: str, url: str, kwargs: dict[str, Any]) -> _FakeResponse:
        self.requests.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"没有为 {method} {url} 准备响应")
        return self.responses.pop(0)

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._next("POST", url, kwargs)

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._next("GET", url, kwargs)

    def close(self) -> None:
        pass


class LLMClientContextTest(unittest.TestCase):
    def _config(self, **overrides: Any) -> LLMConfig:
        values = {"base_url": "https://example.test/v1", "model": "demo-model"}
        values.update(overrides)
        return LLMConfig(**values)

    def test_chat_records_usage_from_response(self) -> None:
        session = _FakeSession(
            _FakeResponse(
                {
                    "choices": [{"message": {"role": "assistant", "content": "好的"}}],
                    "usage": {"total_tokens": 1_234},
                }
            )
        )
        client = OpenAICompatibleClient(self._config(), session=session)

        message = client.chat([{"role": "user", "content": "你好"}])

        self.assertEqual(message["content"], "好的")
        self.assertEqual(client.last_usage, {"total_tokens": 1_234})
        method, url, kwargs = session.requests[0]
        self.assertEqual((method, url), ("POST", "https://example.test/v1/chat/completions"))
        self.assertFalse(kwargs["json"]["stream"])

    def test_chat_clears_usage_when_response_has_none(self) -> None:
        session = _FakeSession(
            _FakeResponse({"choices": [{"message": {"content": "第一次"}}], "usage": {"total_tokens": 9}}),
            _FakeResponse({"choices": [{"message": {"content": "第二次"}}]}),
        )
        client = OpenAICompatibleClient(self._config(), session=session)

        client.chat([{"role": "user", "content": "一"}])
        self.assertEqual(client.last_usage, {"total_tokens": 9})

        client.chat([{"role": "user", "content": "二"}])
        self.assertIsNone(client.last_usage)

    def test_fetch_context_window_prefers_current_model(self) -> None:
        session = _FakeSession(
            _FakeResponse(
                {
                    "data": [
                        {"id": "other-model", "context_length": 8_000},
                        {"id": "demo-model", "max_model_len": 32_768},
                    ]
                }
            )
        )
        client = OpenAICompatibleClient(self._config(), session=session)

        self.assertEqual(client.fetch_context_window(), 32_768)
        self.assertEqual(session.requests[0][0], "GET")
        self.assertTrue(session.requests[0][1].endswith("/models"))

        # 结果会被缓存，不再重复请求。
        self.assertEqual(client.fetch_context_window(), 32_768)
        self.assertEqual(len(session.requests), 1)

    def test_fetch_context_window_returns_none_when_unavailable(self) -> None:
        session = _FakeSession(_FakeResponse({"data": [{"id": "demo-model"}]}))
        client = OpenAICompatibleClient(self._config(), session=session)
        self.assertIsNone(client.fetch_context_window())

        # 失败同样缓存，避免每次对话都去探测。
        self.assertIsNone(client.fetch_context_window())
        self.assertEqual(len(session.requests), 1)

    def test_fetch_context_window_skips_request_when_configured(self) -> None:
        session = _FakeSession()
        client = OpenAICompatibleClient(
            self._config(context_window=64_000), session=session
        )

        self.assertEqual(client.fetch_context_window(), 64_000)
        self.assertEqual(session.requests, [])

    def test_context_manager_uses_client_fetch_context_window(self) -> None:
        class ClientWithWindow:
            config = SimpleNamespace(context_window=0)
            last_usage = None

            def __init__(self) -> None:
                self.calls = 0

            def fetch_context_window(self) -> int:
                self.calls += 1
                return 200_000

        with tempfile.TemporaryDirectory() as temp_dir:
            client = ClientWithWindow()
            manager = ContextManager(client, AgentConfig(), temp_dir)
            snapshot = manager.snapshot([{"role": "user", "content": "hi"}])

            self.assertEqual(snapshot.window_tokens, 200_000)
            self.assertFalse(snapshot.window_is_default)
            self.assertEqual(client.calls, 1)
            self.assertEqual(manager.window_info().tokens, 200_000)


if __name__ == "__main__":
    unittest.main()
