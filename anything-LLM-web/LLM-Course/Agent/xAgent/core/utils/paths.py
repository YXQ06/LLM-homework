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


def normalize_roots(roots) -> tuple[str, ...]:
    """把白名单配置归一化为绝对路径元组；空输入返回空元组（表示不限制）。"""
    result = []
    for root in roots or ():
        text = str(root).strip()
        if text:
            result.append(os.path.normcase(os.path.abspath(os.path.expanduser(text))))
    return tuple(result)


def ensure_within_roots(path: str, roots, label: str = "路径") -> None:
    """校验 ``path`` 落在 ``roots`` 白名单内，越界时抛 ``PermissionError``。

    ``roots`` 为空表示不做限制（保持试用版本的开放行为）；配置了白名单后，
    所有文件工具的读写、删除、移动、重命名都必须落在允许的根目录之内，
    防止 Agent 被恶意提示词诱导去操作系统目录等敏感位置。
    """
    normalized = normalize_roots(roots)
    if not normalized:
        return
    target = os.path.normcase(os.path.abspath(path))
    for root in normalized:
        if target == root:
            return
        try:
            if os.path.commonpath([target, root]) == root:
                return
        except ValueError:
            # Windows 不同盘符之间 commonpath 会抛 ValueError，视为不在范围内
            continue
    raise PermissionError(
        f"{label}超出允许范围：{path}（允许的根目录：{', '.join(normalized)}）"
    )
