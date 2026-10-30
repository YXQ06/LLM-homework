"""Textual TUI 应用。

只消费 ``core`` 的事件流，所有业务逻辑、工具实现、参数容错都在 ``core`` 中。
界面层只负责布局、样式、事件展示和用户交互。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, RichLog, Static

from xAgent.core import (
    Agent,
    AgentEvent,
    AnswerReady,
    ArgumentRepaired,
    NudgeIssued,
    OpenAICompatibleClient,
    ToolCallFinished,
    ToolCallStarted,
    TurnFailed,
    TurnFinished,
    TurnStarted,
    build_default_registry,
    load_config,
)

from .chat_input import ChatTextArea, WaitingSpinner
from .ime_compat import apply_textual_ime_fix


class StatusBar(Static):
    """底部状态栏：显示当前状态和快捷键提示。"""

    status = reactive("就绪")
    model = reactive("")
    cwd = reactive("")

    def render(self) -> str:
        parts = [f"[bold cyan]{self.status}[/]"]
        if self.model:
            parts.append(f"模型：{self.model}")
        if self.cwd:
            parts.append(f"工作目录：{self.cwd}")
        parts.append("[dim]Enter 发送 | Shift+Enter 换行 | Ctrl+C 退出 | Ctrl+L 清屏[/]")
        return " | ".join(parts)


class ChatLog(RichLog):
    """对话日志：展示用户提问、工具调用、参数修正、最终回答。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, wrap=True, markup=True, auto_scroll=True, **kwargs)

    def user(self, text: str) -> None:
        self.write(f"[bold green]你[/] {text}")

    def system(self, text: str) -> None:
        self.write(f"[dim]{text}[/]")

    def tool_start(self, name: str, args: dict) -> None:
        import json

        args_text = json.dumps(args, ensure_ascii=False)
        if len(args_text) > 200:
            args_text = args_text[:200] + "..."
        self.write(f"[bold yellow]→ 工具[/] [cyan]{name}[/]({args_text})")

    def tool_finish(self, name: str, result: str, ok: bool) -> None:
        import json

        from xAgent.core.utils.text import preview_text

        icon = "[green]✓[/]" if ok else "[red]✗[/]"
        preview = preview_text(result, limit=800).replace("[", "\\[")
        self.write(f"{icon} [cyan]{name}[/]")
        self.write(f"[dim]{preview}[/]")

    def repair(self, tool: str, detail: str) -> None:
        self.write(f"[bold magenta]⚙ 参数修正[/] [cyan]{tool}[/]：{detail}")

    def answer(self, text: str) -> None:
        self.write(f"[bold blue]助手[/] {text}")

    def error(self, text: str) -> None:
        self.write(f"[bold red]错误[/] {text}")


class XAgentApp(App[None]):
    """xAgent TUI 主应用。"""

    CSS_PATH = Path(__file__).parent / "styles.tcss"
    TITLE = "xAgent - 本地文件操作 Agent（TUI）"
    BINDINGS = [
        Binding("ctrl+c", "quit", "退出", show=True),
        Binding("ctrl+l", "clear", "清屏", show=True),
    ]

    busy = reactive(False)

    def __init__(self) -> None:
        apply_textual_ime_fix()
        super().__init__()
        self.config = load_config()
        self.registry = build_default_registry(self.config.agent, self.config.search)
        self.client = OpenAICompatibleClient(self.config.llm)
        self.agent = Agent(self.client, self.registry, self.config.agent)
        self.status_bar = StatusBar()
        self.chat_log = ChatLog(id="chat-log")
        self.spinner = WaitingSpinner(id="waiting-spinner")
        self.chat_input = ChatTextArea(
            placeholder="请输入问题，Enter 发送，Shift+Enter 换行（支持粘贴多行文本）",
            id="input-box",
            compact=True,
        )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="main"):
            with VerticalScroll(id="log-container"):
                yield self.chat_log
            with Horizontal(id="input-container"):
                yield self.spinner
                yield Label(">", id="prompt-label")
                yield self.chat_input
        yield self.status_bar
        yield Footer()

    def on_mount(self) -> None:
        self.status_bar.model = self.config.llm.model
        try:
            import os

            self.status_bar.cwd = os.getcwd()
        except Exception:
            pass
        self.status_bar.status = "就绪"
        self.chat_input.focus()
        self.chat_log.system("xAgent TUI 已启动。输入问题即可开始对话。")
        self.chat_log.system(
            f"工具数：{len(self.registry)}（文件工具 + 系统工具 + 网页工具），"
            "支持多轮对话和自动参数修正。"
        )
        self.chat_log.system("Enter 发送，Shift+Enter 换行；可直接粘贴多行文本。")

    async def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        if self.busy:
            return
        prompt = event.text.strip()
        if not prompt:
            return
        event.text_area.text = ""
        await self._handle_turn(prompt)

    async def _handle_turn(self, prompt: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.status_bar.status = "思考中..."
        self.spinner.start()
        self.chat_input.disabled = True
        self.chat_log.user(prompt)

        try:
            loop = asyncio.get_running_loop()
            events = await loop.run_in_executor(None, self._run_agent, prompt)
            for event in events:
                self._handle_event(event)
        except Exception as exc:
            self.chat_log.error(str(exc))
            self.status_bar.status = "出错"
        finally:
            self.busy = False
            self.spinner.stop()
            self.chat_input.disabled = False
            self.chat_input.focus()
            if self.status_bar.status not in ("出错",):
                self.status_bar.status = "就绪"

    def _run_agent(self, prompt: str) -> list[AgentEvent]:
        return list(self.agent.send(prompt))

    def _handle_event(self, event: AgentEvent) -> None:
        if isinstance(event, TurnStarted):
            self.status_bar.status = "思考中..."
            return
        if isinstance(event, ToolCallStarted):
            import json

            self.status_bar.status = f"调用工具：{event.name}"
            self.chat_log.tool_start(event.name, dict(event.arguments))
            return
        if isinstance(event, ToolCallFinished):
            self.status_bar.status = "处理工具结果..."
            self.chat_log.tool_finish(event.name, event.result, event.ok)
            return
        if isinstance(event, ArgumentRepaired):
            self.chat_log.repair(event.tool, event.detail)
            return
        if isinstance(event, NudgeIssued):
            self.status_bar.status = "等待模型调用工具..."
            self.chat_log.system(f"[追问] {event.prompt}")
            return
        if isinstance(event, AnswerReady):
            self.status_bar.status = "生成回答"
            self.chat_log.answer(event.text)
            return
        if isinstance(event, TurnFailed):
            self.chat_log.error(event.message)
            self.status_bar.status = "出错"
            return
        if isinstance(event, TurnFinished):
            if event.result.error and not event.result.answer:
                pass
            self.status_bar.status = "就绪"
            return

    async def action_clear(self) -> None:
        self.chat_log.clear()
        self.chat_log.system("对话日志已清空。")


def run() -> None:
    app = XAgentApp()
    app.run()
