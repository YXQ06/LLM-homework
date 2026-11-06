"""上下文压缩结果浮动面板：左侧原文，右侧摘要。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class CompactPreviewScreen(ModalScreen[None]):
    """两栏预览压缩前后的上下文，点击关闭按钮返回对话。"""

    BINDINGS = [("escape", "dismiss", "关闭")]

    def __init__(self, original_text: str, summary: str) -> None:
        super().__init__()
        self._original_text = original_text
        self._summary = summary

    def compose(self) -> ComposeResult:
        with Vertical(id="compact-dialog"):
            yield Static("上下文压缩预览", id="compact-title")
            yield Static(
                "左侧为压缩前原文，右侧为 LLM 生成的压缩摘要。"
                "关闭面板后可继续对话。",
                id="compact-hint",
            )
            with Horizontal(id="compact-columns"):
                with Vertical(classes="compact-column"):
                    yield Static("压缩前（原始上下文）", classes="compact-heading")
                    with VerticalScroll(classes="compact-scroll"):
                        yield Static(
                            self._original_text or "（无内容）",
                            markup=False,
                            classes="compact-body",
                        )
                with Vertical(classes="compact-column"):
                    yield Static("压缩后（工作摘要）", classes="compact-heading")
                    with VerticalScroll(classes="compact-scroll"):
                        yield Static(
                            self._summary or "（无内容）",
                            markup=False,
                            classes="compact-body",
                        )
            yield Button("关闭", id="compact-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "compact-close":
            self.dismiss()
            event.stop()

    def action_dismiss(self) -> None:
        self.dismiss()


__all__ = ["CompactPreviewScreen"]
