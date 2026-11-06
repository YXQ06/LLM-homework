"""TUI 主题定义与持久化。

只负责界面主题，不涉及任何 Agent 业务逻辑。主题名称会写入用户目录下的
``~/.xagent/tui_settings.json``，下次启动时自动恢复。

可通过环境变量 ``XAGENT_TUI_STATE`` 指定状态文件位置（便于测试或便携部署）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from textual.theme import Theme

DEFAULT_THEME = "dark"

#: 主题名称 -> Textual Theme。
#:
#: 两套主题都只覆盖 Textual 的标准颜色变量，因此现有 CSS 中的
#: ``$background / $surface / $panel / $primary / $text`` 会自动联动。
XAGENT_THEMES: dict[str, Theme] = {
    "dark": Theme(
        name="xagent-dark",
        primary="#22313a",
        secondary="#3d6b7d",
        warning="#d9a441",
        error="#ff5f56",
        success="#4caf50",
        accent="#5aa9d6",
        foreground="#e6edf1",
        background="#0d1317",
        surface="#141b20",
        panel="#10161a",
        boost="#1b252b",
        dark=True,
    ),
    "light": Theme(
        name="xagent-light",
        primary="#dce8f2",
        secondary="#b9d2e3",
        warning="#a86400",
        error="#c62828",
        success="#2e7d32",
        accent="#1565c0",
        foreground="#1c252b",
        background="#ffffff",
        surface="#f5f7f9",
        panel="#ffffff",
        boost="#eaf1f7",
        dark=False,
    ),
}

THEME_NAMES = tuple(XAGENT_THEMES)
THEME_LABELS = {"dark": "深色", "light": "浅色"}

_STATE_ENV_VAR = "XAGENT_TUI_STATE"
_STATE_FILENAME = "tui_settings.json"


def _state_path() -> Path:
    override = os.environ.get(_STATE_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".xagent" / _STATE_FILENAME


def normalize_theme(name: str | None) -> str:
    """把任意输入归一化成受支持的主题名。"""
    if name in XAGENT_THEMES:
        return str(name)
    return DEFAULT_THEME


def load_theme() -> str:
    """读取上次使用的主题；文件不存在或损坏时返回默认深色。"""
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_THEME
    return normalize_theme(data.get("theme") if isinstance(data, dict) else None)


def save_theme(name: str) -> None:
    """持久化主题选择。写入失败不会影响 TUI 运行。"""
    name = normalize_theme(name)
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再替换，避免程序中断留下半个 JSON。
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name,
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump({"theme": name}, handle, ensure_ascii=False, indent=2)
            temp_path = Path(handle.name)
        temp_path.replace(path)
    except OSError:
        return


def theme_display_name(name: str) -> str:
    """返回 Textual 中注册的主题 ID（如 ``xagent-dark``）。"""
    return XAGENT_THEMES[normalize_theme(name)].name


__all__ = [
    "DEFAULT_THEME",
    "THEME_LABELS",
    "THEME_NAMES",
    "XAGENT_THEMES",
    "load_theme",
    "normalize_theme",
    "save_theme",
    "theme_display_name",
]
