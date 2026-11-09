"""Textual TUI 应用。

只消费 ``core`` 的事件流，所有业务逻辑、工具实现、参数容错都在 ``core`` 中。
界面层只负责布局、样式、事件展示和用户交互。

布局：顶部标题栏 + 上下文状态条 + 左侧对话主区域 + 右侧工具信息侧边栏
+ 底部状态栏。MCP 服务在进入界面后异步连接，完成后动态刷新侧边栏和工具表。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Button, Footer, Label, RichLog, Static

from xAgent.core import (
    Agent,
    AgentEvent,
    AnswerReady,
    ArgumentRepaired,
    CompactionFailed,
    CompactionFinished,
    CompactionStarted,
    ConfigError,
    ContextSnapshot,
    ContextUsage,
    McpManager,
    NudgeIssued,
    OpenAICompatibleClient,
    SessionStore,
    ToolCallFinished,
    ToolCallStarted,
    TurnFailed,
    TurnFinished,
    TurnStarted,
    build_default_registry,
    contains_agents_md,
    create_mcp_manager,
    ensure_config,
    load_config,
    resolve_config_path,
)
from xAgent.core.utils.text import preview_text

from .chat_input import ChatTextArea, WaitingSpinner
from .command_palette import CommandPalette
from .commands import (
    command_arguments,
    find_command,
    format_command_help,
    is_exact_command,
    suggest_commands,
)
from .compact_panel import CompactPreviewScreen
from .config_missing import ConfigCreatedScreen
from .ime_compat import apply_textual_ime_fix
from .mcp_status import McpServer, discover_mcp_servers
from .session_panel import SessionPickerScreen
from .themes import (
    XAGENT_THEMES,
    load_theme,
    normalize_theme,
    save_theme,
    theme_display_name,
)
from .tool_sidebar import ToolSidebar


def _escape_markup(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("[", "\\[")


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
        parts.append(
            "[dim]Enter 发送 | Shift+Enter 换行 | / 命令 | Ctrl+C 退出 | Ctrl+L 清屏[/]"
        )
        return " | ".join(parts)


class ContextBar(Static):
    """醒目的上下文使用量状态条。"""

    def update_snapshot(self, snapshot: ContextSnapshot) -> None:
        used_k = snapshot.used_tokens / 1000
        window_k = snapshot.window_tokens / 1000
        default_text = "（默认）" if snapshot.window_is_default else ""
        threshold = snapshot.warn_ratio * 100

        if snapshot.needs_compaction:
            color = "bold red"
            hint = "  ⚠ 已达到压缩阈值，请输入 /compact"
        elif snapshot.percent >= threshold * 0.8:
            color = "bold yellow"
            hint = ""
        else:
            color = "bold green"
            hint = ""

        self.update(
            f"[{color}]上下文：{used_k:.1f}k / {window_k:.1f}k"
            f"{default_text} | 已占用 {snapshot.percent:.1f}%[/]"
            f"[bold red]{hint}[/]"
        )


class TitleBar(Horizontal):
    """顶部标题栏：左侧标题，右侧主题与侧边栏开关。"""

    def __init__(
        self,
        title: str,
        theme_name: str = "dark",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._theme_name = normalize_theme(theme_name)
        self._sidebar_collapsed = False

    def compose(self) -> ComposeResult:
        yield Static(self._title, id="app-title")
        yield Button(
            "浅色",
            id="theme-toggle",
            classes="header-button",
            compact=True,
            flat=True,
            tooltip="切换浅色 / 深色主题（Ctrl+T）",
        )
        yield Button(
            "收起",
            id="sidebar-toggle",
            classes="header-button",
            compact=True,
            flat=True,
            tooltip="折叠 / 展开工具侧边栏（Ctrl+B）",
        )

    def on_mount(self) -> None:
        self.set_theme(self._theme_name)
        self.set_sidebar_collapsed(self._sidebar_collapsed)

    def set_theme(self, theme_name: str) -> None:
        self._theme_name = normalize_theme(theme_name)
        try:
            button = self.query_one("#theme-toggle", Button)
            # 按钮文字表示“点击后切换到的主题”。
            button.label = "浅色" if self._theme_name == "dark" else "深色"
        except Exception:
            pass

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        self._sidebar_collapsed = collapsed
        try:
            button = self.query_one("#sidebar-toggle", Button)
            button.label = "展开" if collapsed else "收起"
        except Exception:
            pass


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
        Binding("ctrl+t", "toggle_theme", "主题", show=True),
        Binding("ctrl+b", "toggle_sidebar", "侧栏", show=True),
    ]

    busy = reactive(False)

    def __init__(
        self,
        *,
        enable_mcp: bool = False,
        config_path: str | os.PathLike[str] | None = None,
        config_created: bool = False,
    ) -> None:
        apply_textual_ime_fix()
        super().__init__()
        self.config_path = resolve_config_path(config_path)
        self.config = load_config(self.config_path)
        self.config_created = config_created
        self.project_root = os.getcwd()
        self.registry = build_default_registry(
            self.config.agent,
            self.config.search,
            project_root=self.project_root,
        )
        self.client = OpenAICompatibleClient(self.config.llm)

        # MCP 只创建管理器，不在 __init__ 中连接；进入界面后由后台线程加载。
        self.mcp_manager: McpManager | None = None
        self._mcp_startup_messages: list[str] = []
        self._mcp_servers: list[McpServer] = []
        self._mcp_loading = False
        self._pending_mcp_specs: list[Any] | None = None
        self._pending_mcp_error = ""
        if enable_mcp:
            self.mcp_manager = create_mcp_manager(
                self.config.agent, project_root=self.project_root
            )

        self.agent = Agent(
            self.client,
            self.registry,
            self.config.agent,
            project_root=self.project_root,
        )

        # 会话保存 / 恢复
        session_dir = Path(self.config.agent.session_dir or ".agents/sessions")
        if not session_dir.is_absolute():
            session_dir = Path(self.project_root) / session_dir
        self.session_store = SessionStore(session_dir)
        self.session_id = self.session_store.new_session_id()
        self.session_title = ""
        self.session_created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._session_saved_path: Path | None = None

        for theme in XAGENT_THEMES.values():
            self.register_theme(theme)
        self._theme_name = load_theme()
        self.theme = theme_display_name(self._theme_name)

        self._sidebar_user_collapsed = False
        self._sidebar_auto_collapsed = False

        self.title_bar = TitleBar(self.TITLE, self._theme_name, id="title-bar")
        self.context_bar = ContextBar(id="context-bar")
        self.tool_sidebar = ToolSidebar(
            self.registry, skills=self.agent.skills, id="tool-sidebar"
        )
        self.status_bar = StatusBar()
        self.chat_log = ChatLog(id="chat-log")
        self.spinner = WaitingSpinner(
            interval=self.config.agent.tui_spinner_interval,
            id="waiting-spinner",
        )
        self.command_palette = CommandPalette(id="command-palette")
        self.chat_input = ChatTextArea(
            placeholder="请输入问题，Enter 发送，Shift+Enter 换行；输入 / 查看命令",
            id="input-box",
            compact=True,
        )

    def compose(self) -> ComposeResult:
        yield self.title_bar
        yield self.context_bar
        with Horizontal(id="body"):
            with Vertical(id="main"):
                with VerticalScroll(id="log-container"):
                    yield self.chat_log
                yield self.command_palette
                with Horizontal(id="input-container"):
                    yield self.spinner
                    yield Label(">", id="prompt-label")
                    yield self.chat_input
            yield self.tool_sidebar
        yield self.status_bar
        yield Footer()

    # ------------------------------------------------------------------ 启动

    def on_mount(self) -> None:
        self.status_bar.model = self.config.llm.model
        try:
            self.status_bar.cwd = os.getcwd()
        except Exception:
            pass
        self.status_bar.status = "就绪"
        self._refresh_context(resolve_window=False)
        self.chat_input.focus()
        self.chat_log.system("xAgent TUI 已启动。输入问题即可开始对话。")
        self.chat_log.system(
            f"工具数：{len(self.registry)}（本地工具 + MCP 工具），"
            "支持多轮对话和自动参数修正。"
        )
        for message in self._mcp_startup_messages:
            self.chat_log.system(message)
        self.chat_log.system(
            "Enter 发送，Shift+Enter 换行；输入 / 会弹出斜杠命令补全面板。"
        )
        self.chat_log.system(
            f"上下文达到 {self.config.agent.context_warn_ratio * 100:.0f}% 后"
            "需输入 /compact 压缩；压缩前原文会归档到 "
            f"{self.config.agent.context_archive_dir}/。"
        )
        self.chat_log.system(
            "Ctrl+T 切换主题，Ctrl+B 折叠工具侧栏，Ctrl+L 清屏。"
        )
        self._apply_responsive_layout(self.size.width)
        self._inject_agents_md()

        if self.mcp_manager is not None:
            self._start_mcp_loading()
        else:
            # 嵌入 / 测试场景：只读探测配置，不启动任何 MCP 服务。
            self.run_worker(
                self._discover_mcp_servers,
                name="mcp-discovery",
                group="mcp",
                thread=True,
                exclusive=True,
            )

        if self.config_created:
            self.call_after_refresh(
                lambda: self.push_screen(ConfigCreatedScreen(self.config_path))
            )

    # ------------------------------------------------------------------ MCP

    def _start_mcp_loading(self) -> None:
        manager = self.mcp_manager
        if manager is None:
            return
        self._mcp_loading = True
        self._apply_mcp_servers(self._mcp_placeholder_servers())
        if manager.config.servers:
            self.chat_log.system(
                "MCP 服务正在后台加载，界面可继续使用；完成后会自动刷新侧栏。"
            )
        self.run_worker(
            self._load_mcp_worker,
            name="mcp-load",
            group="mcp",
            thread=True,
            exclusive=True,
        )

    def _mcp_placeholder_servers(self) -> list[McpServer]:
        manager = self.mcp_manager
        if manager is None:
            return []
        servers: list[McpServer] = []
        for server in manager.config.servers:
            if server.enabled:
                detail = "正在后台连接…"
            else:
                detail = "已在配置中禁用"
            servers.append(
                McpServer(
                    name=server.name,
                    description=server.description or "MCP Server",
                    transport=server.transport_label,
                    endpoint=server.endpoint,
                    online=False,
                    detail=detail,
                    source=str(manager.config.path or ""),
                )
            )
        return servers

    def _load_mcp_worker(self) -> None:
        manager = self.mcp_manager
        if manager is None:
            return
        try:
            specs = manager.start(self.registry.names)
            error = ""
        except Exception as exc:  # MCP 失败不能影响本地工具
            specs = []
            error = str(exc)
        try:
            self.call_from_thread(self._finish_mcp_loading, specs, error)
        except RuntimeError:
            # 应用已退出时忽略。
            pass

    def _finish_mcp_loading(self, specs: list[Any], error: str) -> None:
        self._pending_mcp_specs = list(specs)
        self._pending_mcp_error = error
        self._apply_pending_mcp()

    def _apply_pending_mcp(self) -> None:
        if self.busy:
            # 避免在模型调用 / 工具执行期间修改工具注册表。
            self.set_timer(0.5, self._apply_pending_mcp)
            return
        if self._pending_mcp_specs is None:
            return

        specs = self._pending_mcp_specs
        error = self._pending_mcp_error
        self._pending_mcp_specs = None
        self._pending_mcp_error = ""
        self._mcp_loading = False

        registered = 0
        for spec in specs:
            if spec.name not in self.registry:
                self.registry.register(spec)
                registered += 1
        self.agent.refresh_system_prompt()
        self.tool_sidebar.refresh_tools()
        self._apply_mcp_servers(self._mcp_display_servers())

        manager = self.mcp_manager
        if manager is not None:
            for message in manager.startup_messages():
                self.chat_log.system(message)
        if error:
            self.chat_log.error(f"MCP 后台加载失败：{error}")
        if manager is not None and manager.config.servers:
            self.chat_log.system(
                f"MCP 后台加载完成：新增 {registered} 个工具，"
                f"当前工具总数 {len(self.registry)}。"
            )
        self._refresh_context(resolve_window=False)

    def _discover_mcp_servers(self) -> None:
        servers = discover_mcp_servers()
        try:
            self.call_from_thread(self._apply_mcp_servers, servers)
        except RuntimeError:
            # 应用已退出时忽略。
            pass

    def _apply_mcp_servers(self, servers: list[McpServer]) -> None:
        self._mcp_servers = list(servers)
        self.tool_sidebar.set_mcp_servers(self._mcp_servers)

    def _mcp_display_servers(self) -> list[McpServer]:
        """把真实连接状态转换成侧边栏需要的展示对象。"""
        manager = self.mcp_manager
        if manager is None:
            return []
        servers: list[McpServer] = []
        for status in manager.statuses:
            if not status.enabled:
                detail = "已在配置中禁用"
            elif status.connected:
                detail = f"已连接，发现 {status.tool_count} 个工具"
            elif status.error:
                detail = status.error
            else:
                detail = "未连接"
            servers.append(
                McpServer(
                    name=status.name,
                    description=status.description or "MCP Server",
                    transport=status.transport_label,
                    endpoint=status.endpoint,
                    online=status.connected,
                    detail=detail,
                    source=str(manager.config.path or ""),
                    tools=status.tools,
                )
            )
        return servers

    # ------------------------------------------------------------------ 会话

    def _inject_agents_md(self) -> None:
        """会话首次加载时注入 AGENTS.md，并立即写入会话事件日志。"""
        injector = getattr(self.agent, "inject_project_instructions", None)
        if not callable(injector):
            return
        path = Path(self.project_root) / self.config.agent.agents_md_filename
        try:
            document = injector(
                path, max_chars=self.config.agent.agents_md_max_chars
            )
        except Exception as exc:
            self.chat_log.error(f"读取 AGENTS.md 失败：{exc}")
            return
        if document is None:
            return

        suffix = "，内容已截断" if document.truncated else ""
        self.chat_log.system(
            f"已注入项目说明 {document.path.name}"
            f"（{len(document.text)} 字符{suffix}）；"
            "本会话后续轮次直接复用，不再重复读取。"
        )
        self._persist_session()

    def _persist_session(self, title: str = "") -> Path | None:
        messages = getattr(self.agent, "messages", None)
        if not isinstance(messages, list) or len(messages) <= 1:
            return None
        if title:
            self.session_title = title.strip()
        try:
            path = self.session_store.save(
                self.session_id,
                messages,
                title=self.session_title,
                created_at=self.session_created_at,
                project_root=self.project_root,
                agents_md_path=str(
                    getattr(self.agent, "agents_md_path", "") or ""
                ),
                agents_md_hash=str(
                    getattr(self.agent, "agents_md_hash", "") or ""
                ),
            )
        except Exception as exc:
            self.chat_log.error(f"保存会话失败：{exc}")
            return None
        self._session_saved_path = path
        return path

    def _start_new_session(self, title: str = "") -> None:
        self.agent.reset()
        self.session_id = self.session_store.new_session_id()
        self.session_title = title.strip()
        self.session_created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._session_saved_path = None
        self.chat_log.clear()
        self.chat_log.system(f"已开始新会话：{self.session_id}")
        self._inject_agents_md()
        self._refresh_context(resolve_window=False)

    def _on_session_picked(self, session_id: str | None) -> None:
        if not session_id:
            return
        self._load_session(session_id)

    def _load_session(self, session_id: str) -> None:
        try:
            record = self.session_store.load(session_id)
        except Exception as exc:
            self.chat_log.error(f"恢复会话失败：{exc}")
            return

        self.agent.restore_messages(
            record.messages,
            agents_md_path=record.agents_md_path,
            agents_md_hash=record.agents_md_hash,
        )
        self.session_id = record.id
        self.session_title = record.title
        self.session_created_at = record.created_at
        self._session_saved_path = record.path

        self.chat_log.clear()
        self.chat_log.system(
            f"已恢复会话：{record.title}"
            f"（{len(record.messages)} 条消息，更新于 {record.updated_at}）"
        )
        for message in self.agent.messages[1:]:
            role = str(message.get("role") or "unknown")
            content = str(message.get("content") or "")
            if contains_agents_md(content):
                self.chat_log.system(
                    "已恢复 AGENTS.md 项目说明；本次会话不再重复注入。"
                )
            elif role == "user":
                self.chat_log.user(content)
            elif role == "assistant" and content:
                self.chat_log.answer(content)
            elif content:
                self.chat_log.system(
                    f"【{role}】{preview_text(content, limit=300)}"
                )
        self._refresh_context(resolve_window=False)
        self.status_bar.status = "就绪"

    # ------------------------------------------------------------------ 斜杠命令

    async def _dispatch_command(self, text: str) -> None:
        command = find_command(text)
        if command is None:
            token = text.strip().split(maxsplit=1)[0]
            self.chat_log.error(f"未知命令：{token}")
            self.chat_log.system(_escape_markup(format_command_help()))
            return
        handler = getattr(self, f"_cmd_{command.name.lstrip('/')}", None)
        if handler is None:
            self.chat_log.error(f"命令尚未实现：{command.name}")
            return
        await handler(command_arguments(text))

    async def _cmd_help(self, _args: str) -> None:
        self.chat_log.system(_escape_markup(format_command_help()))

    async def _cmd_new(self, args: str) -> None:
        if len(getattr(self.agent, "messages", ())) > 1:
            self._persist_session()
        self._start_new_session(args)
        self.status_bar.status = "就绪"

    async def _cmd_save(self, args: str) -> None:
        path = self._persist_session(args)
        if path is None:
            self.chat_log.system("当前没有可保存的对话。")
        else:
            self.chat_log.system(f"会话已保存：{path}")

    async def _cmd_resume(self, _args: str) -> None:
        if len(getattr(self.agent, "messages", ())) > 1:
            self._persist_session()
        sessions = self.session_store.list_sessions(
            self.config.agent.session_max_history
        )
        if not sessions:
            self.chat_log.system(
                "还没有历史会话。先进行一轮对话，或使用 /save 保存当前会话。"
            )
            return
        self.push_screen(
            SessionPickerScreen(sessions), callback=self._on_session_picked
        )

    async def _cmd_compact(self, _args: str) -> None:
        await self._handle_compact()

    async def _cmd_context(self, _args: str) -> None:
        snapshot = self.agent.context_snapshot(resolve_window=True)
        self._apply_context_snapshot(snapshot)
        default_text = "（默认）" if snapshot.window_is_default else ""
        self.chat_log.system(
            "上下文使用情况：\n"
            f"  当前：{snapshot.used_tokens / 1000:.1f}k Tokens\n"
            f"  窗口：{snapshot.window_tokens / 1000:.1f}k{default_text}\n"
            f"  占用：{snapshot.percent:.1f}%\n"
            f"  压缩阈值：{snapshot.warn_ratio * 100:.0f}%\n"
            f"  归档目录：{self.agent.context.archive_dir}"
        )

    async def _cmd_clear(self, _args: str) -> None:
        self.action_clear()

    async def _cmd_tools(self, _args: str) -> None:
        specs = list(self.registry.specs)
        lines = [f"当前共注册 {len(specs)} 个工具："]
        for spec in specs:
            description = spec.description.strip() or "未提供说明"
            lines.append(f"  {spec.name:<24} {description}")
        self.chat_log.system(_escape_markup("\n".join(lines)))

    async def _cmd_mcp(self, _args: str) -> None:
        if self.mcp_manager is not None:
            statuses = self.mcp_manager.statuses
            if not statuses:
                if self._mcp_loading:
                    self.chat_log.system(
                        "MCP 服务正在后台加载，请稍后再输入 /mcp 查看。"
                    )
                else:
                    self.chat_log.system("尚未配置 MCP 服务。")
                return
            lines = ["MCP 服务状态："]
            for status in statuses:
                lines.append(
                    f"  {status.name}：{status.status_text}"
                    f"（{status.transport_label}，{status.tool_count} 个工具）"
                )
                if status.tools:
                    lines.append("    工具：" + "，".join(status.tools))
                if status.error:
                    lines.append(f"    错误：{status.error}")
            self.chat_log.system(_escape_markup("\n".join(lines)))
            return

        if not self._mcp_servers:
            self.chat_log.system("尚未配置 MCP 服务。")
            return
        lines = ["MCP 服务（只读探测）："]
        for server in self._mcp_servers:
            lines.append(
                f"  {server.name}：{server.status_text}"
                f"（{server.transport}）{server.detail}"
            )
            if server.tools:
                lines.append("    工具：" + "，".join(server.tools))
        self.chat_log.system(_escape_markup("\n".join(lines)))

    async def _cmd_theme(self, _args: str) -> None:
        self.action_toggle_theme()

    async def _cmd_quit(self, _args: str) -> None:
        self.action_quit()

    # ------------------------------------------------------------------ 输入

    async def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        await self._submit_text(event.text, event.text_area)

    async def _submit_text(self, text: str, text_area: ChatTextArea) -> None:
        if self.busy:
            return
        prompt = (text or "").strip()
        if not prompt:
            return

        self._hide_command_palette()

        if prompt.startswith("/"):
            text_area.text = ""
            self.chat_log.user(prompt)
            await self._dispatch_command(prompt)
            return

        # 达到阈值后只接受 /compact，普通提示词原样保留在输入框里。
        if self.agent.needs_compaction():
            snapshot = self.agent.context_snapshot(resolve_window=False)
            self._apply_context_snapshot(snapshot)
            self.chat_log.system(
                f"【上下文警告】已占用 {snapshot.percent:.1f}%，"
                "已暂停普通提问。请先输入 /compact 压缩上下文。"
            )
            self.status_bar.status = "需要压缩上下文"
            return

        text_area.text = ""
        await self._handle_turn(prompt)

    def on_text_area_changed(self, event: Any) -> None:
        text_area = getattr(event, "text_area", None)
        if text_area is not self.chat_input:
            return
        if self.busy:
            self._hide_command_palette()
            return
        self._update_command_palette(text_area.text)

    def _update_command_palette(self, text: str) -> None:
        commands = suggest_commands(
            text,
            limit=self.config.agent.tui_command_panel_max_items,
        )
        if not commands:
            self._hide_command_palette()
            return
        self.command_palette.show_commands(commands)
        self.chat_input.completion_active = True

    def _hide_command_palette(self) -> None:
        self.command_palette.hide()
        self.chat_input.completion_active = False

    async def on_chat_text_area_completion_key(
        self, event: ChatTextArea.CompletionKey
    ) -> None:
        if not self.command_palette.is_open:
            if event.key == "enter":
                await self._submit_text(event.text, event.text_area)
            return

        if event.key == "up":
            self.command_palette.move(-1)
            return
        if event.key == "down":
            self.command_palette.move(1)
            return
        if event.key == "escape":
            self._hide_command_palette()
            return
        if event.key == "tab":
            self._apply_command_completion()
            return
        if event.key == "enter":
            if is_exact_command(event.text):
                self._hide_command_palette()
                await self._submit_text(event.text, event.text_area)
            else:
                self._apply_command_completion()

    def _apply_command_completion(self) -> None:
        command = self.command_palette.current
        if command is None:
            return
        self.chat_input.text = command.name
        self.chat_input.move_cursor(self.chat_input.document.end)
        # 补全后保留面板，再按 Enter 才会真正执行，避免误触发。
        self._update_command_palette(self.chat_input.text)
        self.chat_input.focus()

    # ------------------------------------------------------------------ 对话执行

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
            self._refresh_context(resolve_window=False)
            if self.config.agent.session_auto_save_after_turn:
                self._persist_session()
            if self.status_bar.status not in ("出错", "压缩失败", "需要压缩上下文"):
                self.status_bar.status = "就绪"

    async def _handle_compact(self) -> None:
        """执行 /compact：归档 → 摘要 → 替换上下文 → 展示两栏预览。"""
        if self.busy:
            return
        self.busy = True
        self.status_bar.status = "压缩上下文中..."
        self.spinner.start()
        self.chat_input.disabled = True

        finished: CompactionFinished | None = None
        try:
            loop = asyncio.get_running_loop()
            events = await loop.run_in_executor(None, self._run_compact)
            for event in events:
                if isinstance(event, CompactionFinished):
                    finished = event
                self._handle_event(event)
        except Exception as exc:
            self.chat_log.error(str(exc))
            self.status_bar.status = "压缩失败"
        finally:
            self.busy = False
            self.spinner.stop()
            self.chat_input.disabled = False
            self.chat_input.focus()
            self._refresh_context(resolve_window=False)
            if self.status_bar.status not in ("出错", "压缩失败"):
                self.status_bar.status = "就绪"

        if finished is not None:
            if self.config.agent.session_auto_save_after_turn:
                self._persist_session()
            self.push_screen(
                CompactPreviewScreen(finished.original_text, finished.summary)
            )

    def _run_compact(self) -> list[AgentEvent]:
        return list(self.agent.compact())

    def _refresh_context(self, *, resolve_window: bool = True) -> None:
        try:
            snapshot = self.agent.context_snapshot(
                resolve_window=resolve_window
            )
            self._apply_context_snapshot(snapshot)
        except Exception:
            # 上下文状态条不能影响正常对话。
            pass

    def _apply_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        self.context_bar.update_snapshot(snapshot)

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
            if event.name == "createSkill" and event.ok:
                # 新建 Skill 后立即刷新右侧侧边栏的 Skill 列表。
                self.tool_sidebar.refresh_tools()
            return
        if isinstance(event, ArgumentRepaired):
            self.chat_log.repair(event.tool, event.detail)
            return
        if isinstance(event, NudgeIssued):
            self.status_bar.status = "等待模型调用工具..."
            self.chat_log.system(f"【追问】{event.prompt}")
            return
        if isinstance(event, ContextUsage):
            self.context_bar.update_snapshot(
                ContextSnapshot(
                    used_tokens=event.used_tokens,
                    window_tokens=event.window_tokens,
                    window_is_default=event.window_is_default,
                    warn_ratio=self.config.agent.context_warn_ratio,
                )
            )
            if event.warning:
                self.chat_log.system(f"【上下文警告】{event.warning}")
                self.status_bar.status = "需要压缩上下文"
            return
        if isinstance(event, CompactionStarted):
            self.status_bar.status = "压缩上下文中..."
            self.chat_log.system(
                f"开始压缩上下文：共 {event.message_count} 条消息，"
                f"归档编号 {event.archive_id}。"
            )
            return
        if isinstance(event, CompactionFinished):
            self.status_bar.status = "就绪"
            self.chat_log.system(
                f"上下文压缩完成：归档编号 {event.archive_id}，"
                f"压缩后约 {event.compacted_tokens / 1000:.1f}k Tokens。"
            )
            return
        if isinstance(event, CompactionFailed):
            self.status_bar.status = "压缩失败"
            self.chat_log.error(f"上下文压缩失败：{event.message}")
            self.chat_log.system("原有上下文已保留，可以重新执行 /compact。")
            return
        if isinstance(event, AnswerReady):
            self.status_bar.status = "生成回答"
            self.chat_log.answer(event.text)
            return
        if isinstance(event, TurnFailed):
            self.chat_log.error(event.message)
            self.status_bar.status = (
                "需要压缩上下文" if event.kind == "context_limit" else "出错"
            )
            return
        if isinstance(event, TurnFinished):
            if event.result.error and not event.result.answer:
                pass
            self.status_bar.status = "就绪"
            return

    # ------------------------------------------------------------------ 布局与动作

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(event.size.width)

    def _apply_responsive_layout(self, width: int) -> None:
        """窄窗口自动收起侧栏；恢复宽度后按用户选择决定是否展开。"""
        threshold = self.config.agent.tui_sidebar_collapse_width
        if width < threshold:
            if not self._sidebar_user_collapsed and not self.tool_sidebar.collapsed:
                self._sidebar_auto_collapsed = True
                self._set_sidebar_collapsed(True)
        elif self._sidebar_auto_collapsed and not self._sidebar_user_collapsed:
            self._sidebar_auto_collapsed = False
            self._set_sidebar_collapsed(False)

    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        self.tool_sidebar.set_collapsed(collapsed)
        self.title_bar.set_sidebar_collapsed(collapsed)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "theme-toggle":
            self.action_toggle_theme()
            event.stop()
        elif button_id == "sidebar-toggle":
            self.action_toggle_sidebar()
            event.stop()

    def on_tool_sidebar_toggle_requested(
        self, event: ToolSidebar.ToggleRequested
    ) -> None:
        self.action_toggle_sidebar()
        event.stop()

    def action_clear(self) -> None:
        self.chat_log.clear()
        self.chat_log.system("对话日志已清空。")

    def action_toggle_theme(self) -> None:
        """浅色 / 深色主题一键切换，并持久化到用户目录。"""
        next_name = "light" if self._theme_name == "dark" else "dark"
        self._theme_name = normalize_theme(next_name)
        self.theme = theme_display_name(self._theme_name)
        self.title_bar.set_theme(self._theme_name)
        save_theme(self._theme_name)

    def action_toggle_sidebar(self) -> None:
        """折叠 / 展开右侧工具面板。"""
        collapsed = self.tool_sidebar.toggle()
        self._sidebar_user_collapsed = collapsed
        self._sidebar_auto_collapsed = False
        self.title_bar.set_sidebar_collapsed(collapsed)

    def action_quit(self) -> None:
        """退出前按配置自动保存当前会话。"""
        if self.config.agent.session_auto_save_on_exit:
            self._persist_session()
        self.exit()


def run() -> None:
    """TUI 入口：首次运行自动生成 config.ini 并提示用户修改。"""
    try:
        config_path, created = ensure_config()
    except ConfigError as exc:
        print(f"配置初始化失败：{exc}")
        return

    try:
        app = XAgentApp(
            enable_mcp=True,
            config_path=config_path,
            config_created=created,
        )
    except ConfigError as exc:
        print(f"读取配置失败：{exc}")
        return
    app.run()
