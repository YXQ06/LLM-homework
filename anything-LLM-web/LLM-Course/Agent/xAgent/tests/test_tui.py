"""TUI 输入框、等待动画与输入法兼容补丁测试。"""

from __future__ import annotations

import asyncio
import time
import unittest

from textual import events
from textual._xterm_parser import XTermParser
from textual.app import App, ComposeResult
from textual.containers import Horizontal

from xAgent.tui.app import XAgentApp
from xAgent.tui.chat_input import ChatTextArea, WaitingSpinner
from xAgent.tui.ime_compat import apply_textual_ime_fix


class InputApp(App[None]):
    """只包含输入控件的最小测试 App。"""

    def __init__(self) -> None:
        super().__init__()
        self.submitted: list[str] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="row"):
            yield WaitingSpinner(id="spinner")
            yield ChatTextArea(id="ta")

    def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        self.submitted.append(event.text)


class ChatInputTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app = InputApp()
        self.pilot_context = self.app.run_test()
        self.pilot = await self.pilot_context.__aenter__()
        self.area = self.app.query_one(ChatTextArea)
        self.area.focus()
        await self.pilot.pause()

    async def asyncTearDown(self) -> None:
        await self.pilot_context.__aexit__(None, None, None)

    async def test_enter_submits_and_shift_enter_inserts_newline(self) -> None:
        await self.pilot.press("a", "b")
        await self.pilot.press("shift+enter")
        await self.pilot.press("c", "d")
        self.assertEqual(self.area.text, "ab\ncd")
        self.assertEqual(self.app.submitted, [])

        await self.pilot.press("enter")
        await self.pilot.pause()
        self.assertEqual(self.app.submitted, ["ab\ncd"])

    async def test_ctrl_j_also_inserts_newline(self) -> None:
        await self.pilot.press("a")
        await self.pilot.press("ctrl+j")
        await self.pilot.press("b")
        self.assertEqual(self.area.text, "a\nb")

    async def test_multiline_paste_keeps_all_lines(self) -> None:
        pasted = "第一行\n第二行\n第三行"
        await self.area._on_paste(events.Paste(pasted))
        self.assertEqual(self.area.text, pasted)

    async def test_enter_submits_multiline_text(self) -> None:
        self.area.text = "hello\nworld"
        await self.pilot.press("enter")
        await self.pilot.pause()
        self.assertEqual(self.app.submitted, ["hello\nworld"])

    async def test_spinner_visibility_and_timer(self) -> None:
        spinner = self.app.query_one(WaitingSpinner)
        self.assertEqual(str(spinner.styles.visibility), "hidden")

        spinner.start()
        self.assertEqual(str(spinner.styles.visibility), "visible")
        self.assertIsNotNone(spinner._timer)

        spinner.stop()
        self.assertEqual(str(spinner.styles.visibility), "hidden")
        self.assertIsNone(spinner._timer)

    async def test_spinner_is_placed_before_input(self) -> None:
        row = self.app.query_one("#row")
        children = list(row.children)
        self.assertIsInstance(children[0], WaitingSpinner)
        self.assertIsInstance(children[1], ChatTextArea)


class ImeCompatTest(unittest.TestCase):
    def test_long_ime_sequence_is_decoded_as_chinese_text(self) -> None:
        apply_textual_ime_fix()

        text = "你帮我检查一下"
        codepoints = ":".join(str(ord(character)) for character in text)

        # 模拟 Kitty 键盘协议下一次提交多个码点的转义序列（长度超过旧的 32 字符上限）。
        sequence = f"\x1b[{ord(text[0])};1;{codepoints}u"
        self.assertGreater(len(sequence), 32)

        keys = [
            event
            for event in XTermParser().feed(sequence)
            if isinstance(event, events.Key)
        ]
        self.assertEqual("".join(event.character or "" for event in keys), text)

    def test_standard_keys_still_work_after_patch(self) -> None:
        apply_textual_ime_fix()
        cases = {
            "\x1b[13u": "enter",
            "\x1b[13;2u": "shift+enter",
            "\x1b[3~": "delete",
            "\x1b[1;5A": "ctrl+up",
        }
        for sequence, expected in cases.items():
            with self.subTest(sequence=sequence):
                keys = [
                    event
                    for event in XTermParser().feed(sequence)
                    if isinstance(event, events.Key)
                ]
                self.assertIn(expected, [event.key for event in keys])


class SlowAgent:
    def send(self, prompt: str) -> list[object]:
        time.sleep(0.3)
        return []


class SpinnerDuringTurnTest(unittest.IsolatedAsyncioTestCase):
    async def test_spinner_is_visible_while_waiting_for_model(self) -> None:
        app = XAgentApp()
        app.agent = SlowAgent()  # type: ignore[assignment]
        async with app.run_test() as pilot:
            task = asyncio.create_task(app._handle_turn("测试问题"))
            try:
                await pilot.pause(0.1)
                self.assertTrue(app.busy)
                self.assertEqual(
                    str(app.spinner.styles.visibility), "visible"
                )
                self.assertTrue(app.chat_input.disabled)

                await task
                await pilot.pause()
                self.assertFalse(app.busy)
                self.assertEqual(
                    str(app.spinner.styles.visibility), "hidden"
                )
                self.assertFalse(app.chat_input.disabled)
            finally:
                if not task.done():
                    task.cancel()


if __name__ == "__main__":
    unittest.main()
