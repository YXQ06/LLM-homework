"""输入框上方的斜杠命令补全引导面板。"""

from __future__ import annotations

from typing import Iterable

from textual.widgets import Static

from .commands import SlashCommand


def _escape_markup(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[")


class CommandPalette(Static):
    """显示候选命令，支持 ↑/↓ 高亮与 Tab 补全。"""

    def __init__(self, **kwargs: object) -> None:
        super().__init__("", markup=True, **kwargs)
        self._commands: tuple[SlashCommand, ...] = ()
        self.selected = 0

    @property
    def visible(self) -> bool:
        return self.has_class("visible")

    @property
    def current(self) -> SlashCommand | None:
        if not self._commands:
            return None
        index = min(max(0, self.selected), len(self._commands) - 1)
        return self._commands[index]

    def show_commands(
        self,
        commands: Iterable[SlashCommand],
        *,
        selected: int = 0,
    ) -> None:
        self._commands = tuple(commands)
        if not self._commands:
            self.hide()
            return
        self.selected = min(max(0, selected), len(self._commands) - 1)
        self.add_class("visible")
        self.update(self._render_content())

    def move(self, delta: int) -> None:
        if not self._commands:
            return
        self.selected = (self.selected + delta) % len(self._commands)
        self.update(self._render_content())

    def hide(self) -> None:
        self._commands = ()
        self.selected = 0
        self.remove_class("visible")
        self.update("")

    def _render_content(self) -> str:
        lines = ["[bold]斜杠命令[/]  [dim]↑/↓ 选择 · Tab 补全 · Esc 关闭[/]"]
        for index, command in enumerate(self._commands):
            marker = "▶" if index == self.selected else " "
            name = _escape_markup(command.display_usage)
            description = _escape_markup(command.description)
            line = f"{marker} [cyan]{name}[/]  [dim]{description}[/]"
            if index == self.selected:
                line = f"[reverse]{line}[/]"
            lines.append(line)
        return "\n".join(lines)


__all__ = ["CommandPalette"]
