"""对话输入框与等待动画。

``ChatTextArea`` 在 Textual ``TextArea`` 基础上调整了按键语义：

* ``Enter`` 提交问题；
* ``Shift+Enter``（以及 ``Ctrl+J``）插入换行；
* 粘贴内容时原样保留所有行，不会被截断成第一行；
* 斜杠命令补全面板打开时，↑/↓/Tab/Esc/Enter 交给上层处理。

``WaitingSpinner`` 是输入框前方的转圈动画，只在等待模型回复时显示。
"""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import Static, TextArea

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_COMPLETION_KEYS = {"up", "down", "tab", "escape", "enter"}


class ChatTextArea(TextArea):
    """多行对话输入框：Enter 发送，Shift+Enter 换行。"""

    completion_active = False

    class Submitted(Message):
        """用户按下 Enter 提交输入内容。"""

        def __init__(self, text: str, text_area: "ChatTextArea") -> None:
            super().__init__()
            self.text = text
            self.text_area = text_area

        @property
        def control(self) -> "ChatTextArea":
            return self.text_area

    class CompletionKey(Message):
        """补全面板打开时，把导航按键交给 App 处理。"""

        def __init__(self, key: str, text: str, text_area: "ChatTextArea") -> None:
            super().__init__()
            self.key = key
            self.text = text
            self.text_area = text_area

        @property
        def control(self) -> "ChatTextArea":
            return self.text_area

    async def _on_key(self, event: events.Key) -> None:
        # 补全面板打开时，方向键 / Tab / Esc / Enter 由 App 决定如何补全。
        if self.completion_active and event.key in _COMPLETION_KEYS:
            event.stop()
            event.prevent_default()
            self.post_message(
                self.CompletionKey(event.key, self.text, self)
            )
            return

        # 先处理“提交/换行”，避免基类把 Enter 当作普通换行插入。
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text, self))
            return

        # 部分终端不区分 Shift+Enter，Ctrl+J 是可移植的替代方案。
        if event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            result = self._replace_via_keyboard("\n", *self.selection)
            if result is not None:
                self.move_cursor(result.end_location)
            return

        await super()._on_key(event)


class WaitingSpinner(Static):
    """等待模型回复时显示的转圈动画。"""

    def __init__(self, interval: float = 0.1, **kwargs: object) -> None:
        super().__init__("", **kwargs)
        self._frame_index = 0
        self._timer = None
        self._interval = max(0.02, float(interval))

    def on_mount(self) -> None:
        # 隐藏但保留占位，避免出现/消失时输入框左右跳动。
        self.styles.visibility = "hidden"

    def start(self) -> None:
        """显示并开始动画。"""
        self.styles.visibility = "visible"
        if self._timer is None:
            self._timer = self.set_interval(self._interval, self._advance)
        self._advance()

    def stop(self) -> None:
        """停止动画并隐藏（保留占位）。"""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.styles.visibility = "hidden"

    def _advance(self) -> None:
        self._frame_index = (self._frame_index + 1) % len(_SPINNER_FRAMES)
        self.update(_SPINNER_FRAMES[self._frame_index])


__all__ = ["ChatTextArea", "WaitingSpinner"]
