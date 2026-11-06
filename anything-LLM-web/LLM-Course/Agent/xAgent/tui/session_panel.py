"""历史会话选择面板：↑/↓ 选择，Enter 恢复，Esc 取消。"""

from __future__ import annotations

from typing import Iterable

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from ..core.session import SessionSummary


def _escape_markup(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[")


class SessionPickerScreen(ModalScreen[str | None]):
    """返回选中的会话编号；取消时返回 ``None``。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, sessions: Iterable[SessionSummary]) -> None:
        super().__init__()
        self._sessions = tuple(sessions)

    def compose(self) -> ComposeResult:
        with Vertical(id="session-dialog"):
            yield Static("恢复历史会话", id="session-title")
            yield Static(
                "↑/↓ 选择会话，Enter 恢复，Esc 取消。"
                "恢复前会自动保存当前会话。",
                id="session-hint",
            )
            options = OptionList(id="session-list")
            yield options
            yield Button("取消", id="session-cancel")

    def on_mount(self) -> None:
        option_list = self.query_one("#session-list", OptionList)
        for session in self._sessions:
            title = _escape_markup(session.title)
            updated = _escape_markup(session.updated_at or "未知时间")
            count = session.message_count
            option_list.add_option(
                Option(
                    f"{title}\n"
                    f"  [dim]{session.id} · {count} 条消息 · 更新于 {updated}[/]",
                    id=session.id,
                )
            )
        if self._sessions:
            option_list.highlighted = 0
        option_list.focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        event.stop()
        self.dismiss(str(event.option_id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "session-cancel":
            event.stop()
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["SessionPickerScreen"]
