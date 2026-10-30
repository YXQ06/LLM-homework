"""文本与文件内容处理：编码识别、二进制探测、长度格式化。"""

from __future__ import annotations

import codecs


def human_size(size: int) -> str:
    """把字节数格式化成易读的大小。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def looks_binary(data: bytes) -> bool:
    """通过 BOM、NUL 字节和控制字符比例判断是否为二进制内容。"""
    if not data:
        return False
    if data.startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return False
    sample = data[:8192]
    if b"\x00" in sample:
        return True
    control = sum(1 for byte in sample if byte < 9 or 13 < byte < 32)
    return control / len(sample) > 0.10


def decode_text(data: bytes, *, strict: bool = True) -> tuple[str, str]:
    """按 BOM / UTF-8 / GBK 顺序解码文本，返回 (文本, 编码名)。

    ``strict=False`` 时，解码失败会用替换字符，用于读取展示；
    ``strict=True`` 时直接抛错，避免编辑文件时破坏原有内容。
    """
    if not data:
        return "", "utf-8"
    if data.startswith(codecs.BOM_UTF8):
        candidates = ("utf-8-sig",)
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        candidates = ("utf-16",)
    else:
        candidates = ("utf-8", "gbk")

    if strict:
        for encoding in candidates:
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        raise ValueError("无法识别文本编码（仅支持 UTF-8 / GBK / UTF-16）")

    best_text = ""
    best_encoding = candidates[0]
    best_bad: int | None = None
    for encoding in candidates:
        text = data.decode(encoding, errors="replace")
        bad = text.count("\ufffd")
        if best_bad is None or bad < best_bad:
            best_text, best_encoding, best_bad = text, encoding, bad
    if best_bad:
        best_encoding += "（部分字符被替换）"
    return best_text, best_encoding


def clip_text(text: str, limit: int) -> str:
    """限制回传给模型的单次工具结果长度。"""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...（结果过长，已截断，完整结果共 {len(text)} 字符）"


def preview_text(text: str, limit: int = 1500) -> str:
    """限制界面预览长度，完整结果仍会回传给模型。"""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...（界面仅显示前 {limit} 字符，共 {len(text)} 字符）"
