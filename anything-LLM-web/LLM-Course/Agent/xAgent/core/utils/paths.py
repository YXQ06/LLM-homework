"""路径处理：统一把相对路径解析为绝对路径。"""

from __future__ import annotations

import os
import re


def resolve_path(raw: str, base: str | os.PathLike[str] | None = None) -> str:
    """把相对路径解析为基于 ``base``（默认当前工作目录）的绝对路径。

    Windows 上模型常把 ``test.md`` 写成 ``/test.md``，此时 ``os.path.abspath``
    会得到盘符根目录（如 ``F:\\test.md``）。若该路径不存在、而当前目录下
    同名文件存在，则按相对路径处理，避免“文件明明在当前目录却提示不存在”。
    """
    if not isinstance(raw, str):
        raise ValueError("路径必须是字符串")
    raw = os.path.expandvars(os.path.expanduser(raw.strip()))
    if not raw:
        raise ValueError("路径不能为空")

    base_dir = os.path.abspath(base if base is not None else os.getcwd())

    if os.path.isabs(raw):
        path = os.path.abspath(raw)
        looks_rooted = raw.startswith(("/", "\\")) and not raw.startswith(("//", "\\\\"))
        has_drive = bool(re.match(r"^[A-Za-z]:", raw))
        if os.name == "nt" and looks_rooted and not has_drive and not os.path.exists(path):
            candidate = os.path.abspath(raw.lstrip("/\\"))
            if os.path.exists(candidate):
                return candidate
        return path

    return os.path.abspath(os.path.join(base_dir, raw))
