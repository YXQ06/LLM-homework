"""首次运行自动生成配置后的提示对话框。"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


def _escape_markup(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[")


class ConfigCreatedScreen(ModalScreen[None]):
    """告诉用户配置已生成，需要修改后重新启动。"""

    BINDINGS = [("escape", "quit_app", "退出")]

    def __init__(self, config_path: str | Path) -> None:
        super().__init__()
        self._config_path = str(config_path)

    def compose(self) -> ComposeResult:
        with Vertical(id="config-dialog"):
            yield Static("首次运行：已生成 config.ini", id="config-title")
            yield Static(
                "当前目录没有 config.ini，xAgent 已创建一份带 mock 信息的"
                "完整配置。",
                id="config-message",
            )
            yield Static(
                f"配置文件：{_escape_markup(self._config_path)}\n\n"
                "请修改 \\[llm] 下的 base_url、model、api_key，然后重新启动 xAgent。"
                "在完成修改前，模型请求不会正常工作。",
                id="config-path",
            )
            yield Button("退出并修改配置", id="config-exit", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "config-exit":
            event.stop()
            self.quit_app()

    def action_quit_app(self) -> None:
        self.quit_app()

    def quit_app(self) -> None:
        self.dismiss(None)
        self.app.exit()


__all__ = ["ConfigCreatedScreen"]
