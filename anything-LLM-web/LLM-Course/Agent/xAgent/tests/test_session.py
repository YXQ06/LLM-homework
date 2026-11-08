"""会话、配置生成、AGENTS.md 与 TUI 斜杠命令测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from textual.widgets import OptionList

from xAgent.core.agent import Agent
from xAgent.core.agents_md import (
    AGENTS_MD_MARKER,
    contains_agents_md,
    load_agents_md,
)
from xAgent.core.config import AgentConfig, ensure_config, load_config
from xAgent.core.errors import ConfigError
from xAgent.core.events import CompactionFinished
from xAgent.core.session import SessionStore, derive_title
from xAgent.core.tools.base import ToolRegistry, ToolSpec
from xAgent.tui.app import XAgentApp
from xAgent.tui.commands import (
    COMMANDS,
    command_arguments,
    find_command,
    format_command_help,
    is_exact_command,
    suggest_commands,
)
from xAgent.tui.session_panel import SessionPickerScreen


class _FakeClient:
    def chat(self, messages, tools=None):  # noqa: ANN001, ANN201
        return {"role": "assistant", "content": "ok"}


class ConfigBootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._old_cwd = Path.cwd()

    def tearDown(self) -> None:
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def test_ensure_config_creates_mock_config_and_can_load_it(self) -> None:
        os.chdir(self.root)
        path, created = ensure_config()
        self.assertTrue(created)
        self.assertEqual(path, (self.root / "config.ini").resolve())
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("mock-model", text)
        self.assertIn("[llm]", text)
        self.assertIn("session_dir", text)
        self.assertIn("agents_md_filename", text)

        config = load_config(path)
        self.assertEqual(config.llm.model, "mock-model")
        self.assertEqual(config.agent.session_dir, ".agents/sessions")
        self.assertEqual(config.agent.mcp_timeout, 60.0)

        again, created_again = ensure_config()
        self.assertFalse(created_again)
        self.assertEqual(again, path)

    def test_explicit_missing_config_is_an_error(self) -> None:
        with self.assertRaises(ConfigError):
            ensure_config(self.root / "missing.ini")

    def test_percent_in_api_key_is_not_treated_as_interpolation(self) -> None:
        path = self.root / "config.ini"
        path.write_text(
            "[llm]\n"
            "base_url = http://127.0.0.1:8000/v1\n"
            "model = demo\n"
            "api_key = sk-50%off\n",
            encoding="utf-8",
        )
        config = load_config(path)
        self.assertEqual(config.llm.api_key, "sk-50%off")


class AgentsMdInjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _agent(self) -> Agent:
        return Agent(
            _FakeClient(),
            ToolRegistry(),
            AgentConfig(agents_md_max_chars=10_000),
            project_root=str(self.root),
        )

    def test_injection_happens_once_and_can_be_restored(self) -> None:
        (self.root / "AGENTS.md").write_text(
            "必须使用中文回答。", encoding="utf-8"
        )
        agent = self._agent()
        document = agent.inject_project_instructions(
            self.root / "AGENTS.md", max_chars=10_000
        )
        self.assertIsNotNone(document)
        self.assertEqual(len(agent.messages), 2)
        self.assertEqual(agent.messages[1]["role"], "user")
        self.assertIn(AGENTS_MD_MARKER, agent.messages[1]["content"])
        self.assertTrue(agent.agents_md_injected)
        self.assertEqual(agent.agents_md_hash, document.sha256)

        # 同一会话不会重复注入。
        self.assertIsNone(
            agent.inject_project_instructions(
                self.root / "AGENTS.md", max_chars=10_000
            )
        )
        self.assertEqual(len(agent.messages), 2)

        restored = self._agent()
        restored.restore_messages(
            agent.messages,
            agents_md_path=agent.agents_md_path,
            agents_md_hash=agent.agents_md_hash,
        )
        self.assertEqual(len(restored.messages), 2)
        self.assertTrue(restored.agents_md_injected)
        self.assertIsNotNone(restored._agents_md_message)
        self.assertIsNone(
            restored.inject_project_instructions(
                self.root / "AGENTS.md", max_chars=10_000
            )
        )

        restored.reset()
        self.assertFalse(restored.agents_md_injected)
        self.assertEqual(len(restored.messages), 1)

    def test_compaction_keeps_agents_md_message(self) -> None:
        (self.root / "AGENTS.md").write_text(
            "压缩后也必须保留的项目约束。", encoding="utf-8"
        )
        agent = self._agent()
        agent.inject_project_instructions(
            self.root / "AGENTS.md", max_chars=10_000
        )
        agent.messages.extend(
            [
                {"role": "user", "content": "旧问题"},
                {"role": "assistant", "content": "旧回答"},
            ]
        )
        record = SimpleNamespace(
            archive_id="archive-1",
            message_count=4,
            path=self.root / "archive.json",
            created_at="2026-01-01 12:00:00",
        )
        agent.context.archive.save = lambda messages: record
        agent.context.summarize = lambda messages: "压缩摘要"
        agent.context.archive_note = lambda item: "归档说明"
        agent.context.compacted_history = lambda summary, note: [
            {"role": "user", "content": summary},
            {"role": "assistant", "content": note},
        ]

        events = list(agent.compact())
        self.assertTrue(
            any(isinstance(event, CompactionFinished) for event in events)
        )
        self.assertTrue(
            any(
                contains_agents_md(message.get("content"))
                for message in agent.messages
            )
        )

    def test_truncation_and_empty_file(self) -> None:
        path = self.root / "AGENTS.md"
        path.write_text("x" * 100, encoding="utf-8")
        document = load_agents_md(path, max_chars=20)
        self.assertIsNotNone(document)
        self.assertTrue(document.truncated)
        self.assertLess(len(document.text), 100)

        path.write_text("   \n", encoding="utf-8")
        self.assertIsNone(load_agents_md(path, max_chars=20))


class SessionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = SessionStore(self.root / ".agents" / "sessions")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_list_and_load_round_trip(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "帮我整理文件"},
            {"role": "assistant", "content": "好的"},
        ]
        session_id = self.store.new_session_id()
        path = self.store.save(
            session_id,
            messages,
            agents_md_path="C:/project/AGENTS.md",
            agents_md_hash="abc123",
            project_root="C:/project",
        )
        self.assertTrue(path.is_file())

        sessions = self.store.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].id, session_id)
        self.assertEqual(sessions[0].message_count, 3)
        self.assertEqual(sessions[0].title, "帮我整理文件")

        record = self.store.load(session_id)
        self.assertEqual(list(record.messages), messages)
        self.assertEqual(record.agents_md_hash, "abc123")
        self.assertEqual(record.title, "帮我整理文件")

    def test_titles_ignore_agents_md_and_invalid_ids_are_rejected(self) -> None:
        self.assertEqual(
            derive_title(
                [
                    {
                        "role": "user",
                        "content": f"{AGENTS_MD_MARKER}\n项目说明",
                    },
                    {"role": "user", "content": "真正的问题"},
                ]
            ),
            "真正的问题",
        )
        with self.assertRaises(ValueError):
            self.store.save("../escape", [{"role": "user", "content": "x"}])
        with self.assertRaises(ValueError):
            self.store.load("../escape")

    def test_new_session_ids_are_unique(self) -> None:
        first = self.store.new_session_id()
        self.store.save(first, [{"role": "user", "content": "x"}])
        second = self.store.new_session_id()
        self.assertNotEqual(first, second)


class SlashCommandTest(unittest.TestCase):
    def test_lookup_aliases_and_suggestions(self) -> None:
        self.assertEqual(find_command("/new"), find_command("/new title"))
        self.assertEqual(find_command("/exit").name, "/quit")
        self.assertEqual(find_command("/history").name, "/resume")
        self.assertEqual(
            [command.name for command in suggest_commands("/re")],
            ["/resume"],
        )
        self.assertEqual(
            [command.name for command in suggest_commands("/", limit=100)],
            [command.name for command in COMMANDS],
        )
        self.assertEqual(suggest_commands("/new "), [])
        self.assertTrue(is_exact_command("/new"))
        self.assertFalse(is_exact_command("/new title"))
        self.assertEqual(command_arguments("/save 我的会话"), "我的会话")
        self.assertIn("/compact", format_command_help())


class _FakeMcpManager:
    def __init__(self) -> None:
        self.config = SimpleNamespace(servers=[], path=None)
        self.statuses = [
            SimpleNamespace(
                enabled=True,
                connected=True,
                name="demo",
                description="演示服务",
                transport_label="stdio",
                endpoint="python demo.py",
                tool_count=1,
                tools=("demo_tool",),
                error="",
            )
        ]

    def startup_messages(self):
        return ["[MCP] demo 已连接"]


class TuiCommandPaletteTest(unittest.IsolatedAsyncioTestCase):
    async def test_palette_navigation_and_help_command(self) -> None:
        app = XAgentApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.chat_input.text = "/"
            await pilot.pause()
            self.assertTrue(app.command_palette.is_open)
            self.assertTrue(app.chat_input.completion_active)

            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(app.command_palette.selected, 1)

            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(app.chat_input.text, "/new")

            app.chat_input.text = "/help"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.chat_input.text, "")
            self.assertFalse(app.command_palette.is_open)

            app.chat_input.text = ""
            await pilot.pause()
            self.assertFalse(app.command_palette.is_open)


class TuiSessionPickerTest(unittest.IsolatedAsyncioTestCase):
    async def test_enter_selects_and_escape_cancels(self) -> None:
        app = XAgentApp()
        from xAgent.core.session import SessionSummary

        sessions = [
            SessionSummary(
                id="20260101-120000",
                title="第一个会话",
                created_at="2026-01-01 12:00:00",
                updated_at="2026-01-01 12:10:00",
                message_count=2,
                path=Path("one.jsonl"),
            )
        ]
        chosen: list[str | None] = []
        async with app.run_test() as pilot:
            app.push_screen(
                SessionPickerScreen(sessions), callback=chosen.append
            )
            await pilot.pause()
            self.assertIsInstance(app.screen, SessionPickerScreen)
            self.assertIsInstance(
                app.screen.query_one("#session-list"), OptionList
            )
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(chosen, ["20260101-120000"])

            app.push_screen(
                SessionPickerScreen(sessions), callback=chosen.append
            )
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(chosen, ["20260101-120000", None])


class TuiAgentsAndMcpTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._old_cwd = Path.cwd()
        os.chdir(self.root)
        (self.root / "AGENTS.md").write_text(
            "本项目必须使用中文。", encoding="utf-8"
        )

    def tearDown(self) -> None:
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    async def test_agents_md_is_persisted_and_reinjected_on_new(self) -> None:
        app = XAgentApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(len(app.agent.messages), 2)
            self.assertIn(
                AGENTS_MD_MARKER, app.agent.messages[1]["content"]
            )
            sessions = app.session_store.list_sessions()
            self.assertEqual(len(sessions), 1)
            old_id = app.session_id

            app.agent.messages.append(
                {"role": "user", "content": "旧问题"}
            )
            app._persist_session()
            await app._cmd_new("")
            await pilot.pause()
            self.assertNotEqual(app.session_id, old_id)
            self.assertEqual(len(app.agent.messages), 2)
            self.assertIn(
                AGENTS_MD_MARKER, app.agent.messages[1]["content"]
            )

            app._load_session(old_id)
            await pilot.pause()
            self.assertEqual(app.session_id, old_id)
            self.assertTrue(
                any(
                    message.get("content") == "旧问题"
                    for message in app.agent.messages
                )
            )
            self.assertTrue(app.agent.agents_md_injected)

    async def test_async_mcp_result_registers_tools_and_updates_sidebar(self) -> None:
        app = XAgentApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.mcp_manager = _FakeMcpManager()  # type: ignore[assignment]
            spec = ToolSpec(
                name="demo_tool",
                description="演示工具",
                parameters={"type": "object", "properties": {}},
                handler=lambda **_: None,
            )
            app._finish_mcp_loading([spec], "")
            await pilot.pause()
            self.assertIn("demo_tool", app.registry)
            self.assertIn("demo_tool", app.agent.system_prompt)
            self.assertFalse(app._mcp_loading)
            self.assertEqual(app._mcp_servers[0].tools, ("demo_tool",))


if __name__ == "__main__":
    unittest.main()
