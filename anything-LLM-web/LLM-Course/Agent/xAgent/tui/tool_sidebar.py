"""右侧“可用工具列表”侧边栏。

面板分两类展示：

* MCP 服务：名称、在线/离线状态、简要描述；
* 本地 Function Calling 工具：名称、入参简介、功能说明。

侧边栏只读取 ``ToolRegistry`` 和 MCP 适配层的数据，不参与工具执行。
整个面板使用一个 ``VerticalScroll`` + 一个 ``Static`` 渲染，既保持终端风格，
也避免为每个工具创建独立控件、拖慢界面刷新。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Static

from ..core.tools import ToolRegistry, ToolSpec
from .mcp_status import McpServer


def _escape_markup(text: str) -> str:
    """避免工具描述中的方括号被 Rich 当成样式标签。"""
    return text.replace("\\", "\\\\").replace("[", "\\[")


def _format_type(schema: Mapping[str, Any]) -> str:
    value = schema.get("type", "any")
    if isinstance(value, (list, tuple)):
        return "/".join(str(item) for item in value)
    return str(value)


def format_parameters(spec: ToolSpec) -> str:
    """把 JSON Schema 参数压缩成一行可读简介。"""
    properties = spec.parameters.get("properties") or {}
    if not isinstance(properties, Mapping) or not properties:
        return "无"

    required = set(spec.required_parameters)
    parts: list[str] = []
    for name, schema in properties.items():
        schema = schema if isinstance(schema, Mapping) else {}
        marker = "*" if name in required else ""
        description = str(schema.get("description") or "").strip()
        if len(description) > 60:
            description = description[:60] + "…"
        detail = f"（{description}）" if description else ""
        parts.append(f"{name}: {_format_type(schema)}{marker}{detail}")
    return "，".join(parts)


def format_tool(spec: ToolSpec) -> str:
    """单个本地工具的展示文本。"""
    description = str(spec.description or "").strip() or "未提供说明"
    return (
        f"[bold cyan]{_escape_markup(spec.name)}[/]\n"
        f"  [dim]入参：[/]{_escape_markup(format_parameters(spec))}\n"
        f"  [dim]说明：[/]{_escape_markup(description)}"
    )


def format_mcp_server(server: McpServer) -> str:
    """单个 MCP 服务的展示文本。"""
    if server.online:
        status = "[green]● 在线[/]"
    else:
        status = "[red]○ 离线[/]"

    lines = [
        f"{status}  [bold cyan]{_escape_markup(server.name)}[/]",
        f"  [dim]{_escape_markup(server.description)}[/]",
    ]
    meta_parts = [part for part in (server.transport, server.endpoint) if part]
    if meta_parts:
        meta = " · ".join(_escape_markup(part) for part in meta_parts)
        lines.append(f"  [dim]{meta}[/]")
    if server.detail:
        lines.append(f"  [dim]状态：{_escape_markup(server.detail)}[/]")
    if server.tools:
        lines.append(
            "  [dim]可用工具：[/]"
            + "，".join(
                f"[cyan]{_escape_markup(name)}[/]" for name in server.tools
            )
        )
    elif server.online:
        lines.append("  [dim]可用工具：未发现工具[/]")
    return "\n".join(lines)


class ToolSidebar(VerticalScroll):
    """右侧工具信息面板，支持折叠为窄边标识。"""

    collapsed = False

    class ToggleRequested(Message):
        """用户点击了面板内的收起/展开标识。"""

    def __init__(
        self,
        registry: ToolRegistry,
        mcp_servers: Iterable[McpServer] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.registry = registry
        self._mcp_servers = tuple(mcp_servers)

    def compose(self) -> ComposeResult:
        yield Static(self._render_content(), id="sidebar-content")

    def on_mount(self) -> None:
        self._update_content()

    def _render_mcp_servers(self) -> str:
        if not self._mcp_servers:
            return (
                "[dim]尚未配置 MCP 服务[/]\n"
                "[dim]可在项目根目录创建 mcp_servers.json，"
                "或在 config.ini 中增加 \\[mcp] 分区。[/]"
            )
        online = sum(1 for server in self._mcp_servers if server.online)
        header = f"[dim]共 {len(self._mcp_servers)} 个服务，在线 {online} 个[/]\n"
        return header + "\n\n".join(
            format_mcp_server(server) for server in self._mcp_servers
        )

    def _render_tools(self) -> str:
        specs = list(self.registry.specs)
        if not specs:
            return "[dim]当前没有注册本地工具。[/]"
        return "\n\n".join(format_tool(spec) for spec in specs)

    def _render_content(self) -> str:
        if self.collapsed:
            # 折叠态只保留窄边标识，点击任意位置展开。
            return "[bold cyan]◧[/]\n[dim]工\n具\n调\n用[/]"

        return "\n".join(
            (
                "[bold]可用工具列表[/]  [reverse] 收起 [/]",
                "",
                "[bold]MCP 服务列表[/]",
                "",
                self._render_mcp_servers(),
                "",
                f"[bold]本地 Function Calling 工具（{len(self.registry)}）[/]",
                "[dim]* 表示必填参数[/]",
                "",
                self._render_tools(),
            )
        )

    def _update_content(self) -> None:
        try:
            self.query_one("#sidebar-content", Static).update(
                self._render_content()
            )
        except Exception:
            # 组件尚未挂载时（比如 __init__ 阶段）忽略。
            pass

    def set_mcp_servers(self, servers: Iterable[McpServer]) -> None:
        """后台探测完成后刷新 MCP 列表。"""
        self._mcp_servers = tuple(servers)
        self._update_content()

    def refresh_tools(self) -> None:
        """工具注册表变化后刷新本地工具与 MCP 工具数量。"""
        self._update_content()

    def set_collapsed(self, collapsed: bool) -> None:
        """折叠 / 展开侧边栏。"""
        self.collapsed = collapsed
        self.set_class(collapsed, "collapsed")
        self._update_content()

    def toggle(self) -> bool:
        """切换折叠状态并返回新状态。"""
        self.set_collapsed(not self.collapsed)
        return self.collapsed

    def on_click(self, event: events.Click) -> None:
        """折叠态点击任意位置展开；展开态点击顶部标题行收起。"""
        if self.collapsed or event.y <= 2:
            self.post_message(self.ToggleRequested())
            event.stop()


__all__ = [
    "ToolSidebar",
    "format_mcp_server",
    "format_parameters",
    "format_tool",
]
