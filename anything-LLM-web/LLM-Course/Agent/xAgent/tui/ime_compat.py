"""Textual 中文输入法（IME）快速输入兼容补丁。

Textual 8.2.x 在启用 Kitty 键盘协议时，输入法一次提交多个字符会变成一条
以冒号分隔码点的按键报告（例如输入「你帮我检查一下」后按空格）。``XTermParser``
对超过 32 个字符的转义序列直接放弃解析，并把原始转义序列当成普通按键交给
输入框，于是界面里会出现 ``[32;;20320:24110:...`` 这类“Unicode 编码”，
而不是中文。

这里参考上游问题 Textualize/textual#6721 的修复思路，做两个兼容处理：

1. 把转义序列长度上限从 32 提高到 1024；
2. 用不会灾难性回溯的等价正则替换 ``_re_extended_key``。

补丁只修改 Textual 的解析器常量，不影响业务代码。升级 Textual 后如果上游
已修复，本模块会识别标记并跳过重复应用。
"""

from __future__ import annotations

import re

from textual import _xterm_parser

# 与上游保持一致的等价正则，但只允许在分号处切分，避免长数字串导致
# 正则回溯爆炸。捕获组数量与顺序必须与原表达式一致。
_EXTENDED_KEY_PATTERN = re.compile(
    r"\x1b\[([\d:]*(?:;[\d:]*){0,2};?)([u~ABCDEFHPQRS])"
)

# 窗口大小报告的四个字段都以惰性匹配结尾，长序列也可能回溯。限制它只对
# 短序列生效，超长序列交给上面的按键解析逻辑处理。
_IN_BAND_RESIZE_PATTERN = re.compile(
    r"(?=[\s\S]{0,64}\Z)"
    r"\x1b\[48;(\d+(?:\:.*?)?);(\d+(?:\:.*?)?);(\d+(?:\:.*?)?);(\d+(?:\:.*?)?)t"
)

_APPLIED_FLAG = "_xagent_ime_fix_applied"


def apply_textual_ime_fix() -> bool:
    """应用 IME 兼容补丁，返回本次是否真正修改了 Textual 解析器。"""
    if getattr(_xterm_parser, _APPLIED_FLAG, False):
        return False

    current = getattr(_xterm_parser, "_MAX_SEQUENCE_SEARCH_THRESHOLD", 32)
    _xterm_parser._MAX_SEQUENCE_SEARCH_THRESHOLD = max(int(current), 1024)
    _xterm_parser._re_extended_key = _EXTENDED_KEY_PATTERN
    if hasattr(_xterm_parser, "_re_in_band_window_resize"):
        _xterm_parser._re_in_band_window_resize = _IN_BAND_RESIZE_PATTERN

    # _parse_extended_key 带 lru_cache，若同一进程里已有旧解析结果需要清掉。
    parse_extended_key = getattr(_xterm_parser.XTermParser, "_parse_extended_key", None)
    cache_clear = getattr(parse_extended_key, "cache_clear", None)
    if callable(cache_clear):
        cache_clear()

    setattr(_xterm_parser, _APPLIED_FLAG, True)
    return True


__all__ = ["apply_textual_ime_fix"]
