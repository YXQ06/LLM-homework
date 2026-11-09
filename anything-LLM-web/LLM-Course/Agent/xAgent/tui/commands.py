"""斜杠命令定义、匹配与补全候选。

命令本身只描述“名称 + 说明 + 用法”，具体执行由 TUI App 中的
``_cmd_<name>`` 方法负责。这样补全面板、帮助文本和命令分发共用同一份
元数据，不会出现“面板里有、实际没实现”的情况。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    """一个斜杠命令的元数据。"""

    name: str
    description: str
    usage: str = ""
    aliases: tuple[str, ...] = ()

    @property
    def display_usage(self) -> str:
        return self.usage or self.name

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand(
        "/help",
        "显示所有斜杠命令及用法",
    ),
    SlashCommand(
        "/new",
        "保存当前会话并开始新会话",
        "/new [标题]",
    ),
    SlashCommand(
        "/resume",
        "打开历史会话列表并恢复会话",
        aliases=("/history",),
    ),
    SlashCommand(
        "/save",
        "保存当前会话到 .agents/sessions",
        "/save [标题]",
    ),
    SlashCommand(
        "/compact",
        "压缩上下文（原文会先归档）",
    ),
    SlashCommand(
        "/context",
        "查看当前上下文占用详情",
    ),
    SlashCommand(
        "/clear",
        "清空屏幕日志（不删除对话历史）",
    ),
    SlashCommand(
        "/tools",
        "列出当前注册的全部工具",
    ),
    SlashCommand(
        "/mcp",
        "查看 MCP 服务器与工具状态",
    ),
    SlashCommand(
        "/theme",
        "切换浅色 / 深色主题",
    ),
    SlashCommand(
        "/quit",
        "保存会话并退出",
        aliases=("/exit",),
    ),
)


def _token(text: str) -> str:
    return (text or "").strip().split(maxsplit=1)[0] if (text or "").strip() else ""


def find_command(text: str) -> SlashCommand | None:
    """按命令名或别名查找命令。"""
    token = _token(text).casefold()
    if not token:
        return None
    for command in COMMANDS:
        if token in (name.casefold() for name in command.names):
            return command
    return None


def command_arguments(text: str) -> str:
    """取出命令名之后的参数文本。"""
    stripped = (text or "").strip()
    parts = stripped.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def suggest_commands(text: str, limit: int = 8) -> list[SlashCommand]:
    """返回输入框当前内容对应的补全候选。

    只在“以 / 开头且尚未输入空格”时提供候选，避免命令参数也被当作命令名。
    """
    raw = text or ""
    stripped = raw.strip()
    # 只要出现空格 / 换行，就认为用户开始输入命令参数，不再显示候选。
    if (
        not stripped.startswith("/")
        or any(character.isspace() for character in raw)
    ):
        return []

    prefix = stripped.casefold()
    matches = [
        command
        for command in COMMANDS
        if any(name.casefold().startswith(prefix) for name in command.names)
    ]
    if not matches and prefix == "/":
        matches = list(COMMANDS)
    if limit > 0:
        matches = matches[:limit]
    return matches


def is_exact_command(text: str) -> bool:
    """输入内容是否正好是一个完整命令（不含参数）。"""
    stripped = (text or "").strip()
    return bool(stripped) and find_command(stripped) is not None and " " not in stripped


def format_command_help() -> str:
    """生成 /help 的多行说明。"""
    lines = ["可用斜杠命令："]
    for command in COMMANDS:
        lines.append(
            f"  {command.display_usage:<20} {command.description}"
        )
    lines.append("")
    lines.append("提示：输入 / 后会弹出补全面板，↑/↓ 选择，Tab 补全，Esc 关闭。")
    return "\n".join(lines)


__all__ = [
    "COMMANDS",
    "SlashCommand",
    "command_arguments",
    "find_command",
    "format_command_help",
    "is_exact_command",
    "suggest_commands",
]
