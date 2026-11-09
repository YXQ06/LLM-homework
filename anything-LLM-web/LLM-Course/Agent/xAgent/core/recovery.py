"""参数容错层。

小模型经常把参数写错：漏掉 ``path``、用 ``filename`` 等别名、把文件路径
写成目录、编造不存在的绝对路径。本模块在不改变工具语义的前提下做“有把握”
的修正，并把每次修正记录成文字，由事件通知界面。

只有在能确定唯一目标时才修正，避免误删 / 误改文件。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .tools.base import ToolSpec
from .utils.paths import resolve_path

# 只能作用于文件的工具；若模型把 path 写成目录，可用问题里的文件名修正
FILE_ONLY_TOOLS = frozenset({"readTextFile", "editFile", "deleteFile"})

# 这些工具的 path 参数是必需项，允许从常见别名（filename、file_path 等）补齐
PATH_ARG_TOOLS = frozenset(
    {
        "listDirectory",
        "getFileType",
        "readTextFile",
        "editFile",
        "createDirectory",
        "deleteDirectory",
        "deleteFile",
        "renamePath",
    }
)

# 生成追问示例时，这些工具的 path 可以替换成问题里的真实文件名
NUDGE_PATH_TOOLS = FILE_ONLY_TOOLS | {"getFileType", "renamePath"}

# 小模型常用的同义参数名
PATH_ALIASES = ("file_path", "filepath", "filename", "file", "target", "dir", "directory")
RENAME_ALIASES = ("new_path", "newpath", "to", "target", "new_filename")

# 只识别 ASCII 文件名，避免把“当前目录下的”这类中文前缀误当成文件名的一部分
FILENAME_RE = re.compile(
    r"([A-Za-z0-9_.\-]+\.(?:txt|md|py|json|csv|log|ini|xml|html|htm|css|js|yaml|yml"
    r"|png|jpg|jpeg|gif|pdf|zip|exe|dll))",
    re.IGNORECASE,
)

# 从问题中提取绝对目录（Windows 盘符路径 / UNC 路径），遇到中文即停止
PROMPT_DIR_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\)[^\s\u4e00-\u9fff，。；：、“”‘’\"'（）()]+"
)

# 问题里出现的网页地址，用于给 fetchWeb 生成更具体的追问示例
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# 需要走工具的问题的简单识别：命中则要求模型必须调用对应工具
TOOL_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https?://|网页|网站|网址|抓取|fetch\s", re.IGNORECASE), "fetchWeb"),
    (
        re.compile(
            r"创建.*skill|新建.*skill|制作.*skill|写.*skill|增加.*skill|定义.*skill",
            re.IGNORECASE,
        ),
        "createSkill",
    ),
    (
        re.compile(
            r"搜索|查一下|找一下|最新|新闻|动态|行情|近况|谁是|是什么|百度|search",
            re.IGNORECASE,
        ),
        "searchWeb",
    ),
    (re.compile(r"重命名|改名|rename", re.IGNORECASE), "renamePath"),
    (
        re.compile(
            r"删除[^，。；\n]*?文件|删除[^，。；\n]*?\.(txt|py|md|json|csv|log)",
            re.IGNORECASE,
        ),
        "deleteFile",
    ),
    (re.compile(r"删除[^，。；\n]*?(目录|文件夹)", re.IGNORECASE), "deleteDirectory"),
    (
        re.compile(r"(创建|新建|建立)[^，。；\n]*?(目录|文件夹)", re.IGNORECASE),
        "createDirectory",
    ),
    (
        re.compile(
            r"(创建|新建)[^，。；\n]*?文件|(创建|新建)[^，。；\n]*?\.(txt|py|md|json|csv|log)",
            re.IGNORECASE,
        ),
        "createFile",
    ),
    (re.compile(r"编辑|修改|追加|替换|写入|edit|append|replace", re.IGNORECASE), "editFile"),
    (re.compile(r"文件类型|类型|格式|mime|type", re.IGNORECASE), "getFileType"),
    (
        re.compile(r"列出|列一下|有哪些|目录下|文件夹下|\bls\b|\blist\b", re.IGNORECASE),
        "listDirectory",
    ),
    (re.compile(r"读取|查看|打开|显示|内容|\bread\b", re.IGNORECASE), "readTextFile"),
    (
        re.compile(
            r"当前(工作)?目录|工作目录|在哪个目录|\bpwd\b|\bcurrent\b",
            re.IGNORECASE,
        ),
        "getCurrentDirectory",
    ),
    (re.compile(r"闰年|leap", re.IGNORECASE), "isLeapYear"),
    (
        re.compile(r"日期|时间|几点|几号|今天|现在|\bdate\b|\btime\b", re.IGNORECASE),
        "getDate",
    ),
)


@dataclass(frozen=True)
class RepairOutcome:
    """参数修正结果。"""

    arguments: dict[str, Any]
    notes: tuple[str, ...] = ()


def prompt_filenames(prompt: str) -> list[str]:
    """提取问题中出现的文件名，去重并保持出现顺序。"""
    names: list[str] = []
    for match in FILENAME_RE.finditer(prompt or ""):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


def prompt_directories(prompt: str) -> list[str]:
    """提取问题中真实存在的绝对目录，用于修正模型编造的 path。

    也接受 ``F:\\dir\\test.md`` 这种带文件名的写法（取所在目录）。
    """
    directories: list[str] = []
    for match in PROMPT_DIR_RE.finditer(prompt or ""):
        raw = match.group(0).rstrip("\\/")
        try:
            path = resolve_path(raw)
        except (ValueError, OSError):
            continue
        if not os.path.isdir(path):
            parent = os.path.dirname(path)
            path = parent if os.path.isdir(parent) else ""
        if path and path not in directories:
            directories.append(path)
    return directories


def infer_path_argument(name: str, prompt: str, fallback_path: str = "") -> str:
    """path 缺失时按“问题里的文件名 → 本轮上一次成功的路径”补齐。

    只在有把握时补：问题里只有一个文件名，或多个文件名中只有一个真实存在。
    都没有时返回空字符串，让工具返回清晰的缺参错误，而不是乱猜目标。
    """
    filenames = prompt_filenames(prompt)
    if len(filenames) == 1:
        return filenames[0]

    existing: list[str] = []
    for filename in filenames:
        try:
            path = resolve_path(filename)
        except (ValueError, OSError):
            continue
        hit = os.path.isfile(path) if name in FILE_ONLY_TOOLS else os.path.exists(path)
        if hit:
            existing.append(filename)
    if len(existing) == 1:
        return existing[0]

    return fallback_path or ""


def normalize_arguments(
    name: str,
    arguments: Mapping[str, Any] | None,
    prompt: str = "",
    fallback_path: str = "",
) -> dict[str, Any]:
    """把模型常用的同义参数名补齐成工具声明的参数名。"""
    args: dict[str, Any] = {key: value for key, value in (arguments or {}).items()}

    if name in PATH_ARG_TOOLS and not str(args.get("path") or "").strip():
        for key in PATH_ALIASES:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                args["path"] = value.strip()
                break

    if name in PATH_ARG_TOOLS and not str(args.get("path") or "").strip():
        inferred = infer_path_argument(name, prompt, fallback_path)
        if inferred:
            args["path"] = inferred

    if name == "renamePath" and not str(args.get("new_name") or "").strip():
        for key in RENAME_ALIASES:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                args["new_name"] = value.strip()
                break

    return args


def repair_file_path_call(
    name: str, arguments: Mapping[str, Any], prompt: str
) -> tuple[dict[str, Any], str]:
    """修正“path 指向目录”的明显参数错误。

    典型场景：用户说“删除当前目录下的 test.md”，模型却把 path 写成 ``./``。
    仅当问题里只出现一个文件名、且 path 确实指向目录时才修正。
    """
    args = dict(arguments or {})
    if name not in FILE_ONLY_TOOLS:
        return args, ""

    raw_path = args.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return args, ""

    try:
        points_to_dir = os.path.isdir(resolve_path(raw_path))
    except (ValueError, OSError):
        points_to_dir = False
    looks_like_dir = raw_path.strip() in (".", "./", ".\\", "..", "../", "..\\")
    if not (points_to_dir or looks_like_dir):
        return args, ""

    filenames = prompt_filenames(prompt)
    if len(filenames) != 1:
        return args, ""

    args["path"] = filenames[0]
    return (
        args,
        f"path={raw_path!r} 指向目录，已按问题中的文件名改为 {filenames[0]!r}",
    )


def repair_nonexistent_file_path(
    name: str,
    arguments: Mapping[str, Any],
    prompt: str,
    fallback_path: str = "",
) -> tuple[dict[str, Any], str]:
    """修正“path 不存在”的编造路径。

    只有原 path 不存在、且只找到一个候选文件时才修正，避免误操作。
    """
    args = dict(arguments or {})
    if name not in FILE_ONLY_TOOLS:
        return args, ""

    raw_path = args.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return args, ""
    try:
        path = resolve_path(raw_path)
    except (ValueError, OSError):
        return args, ""
    if os.path.exists(path):
        return args, ""

    raw_name = os.path.basename(raw_path.strip())
    prompt_names = prompt_filenames(prompt)
    if len(prompt_names) == 1:
        target_names = prompt_names
    elif raw_name in prompt_names:
        target_names = [raw_name]
    else:
        return args, ""

    search_dirs = prompt_directories(prompt)
    if not search_dirs:
        search_dirs = [os.getcwd()]
        if fallback_path:
            fallback_dir = (
                fallback_path
                if os.path.isdir(fallback_path)
                else os.path.dirname(fallback_path)
            )
            if fallback_dir and fallback_dir not in search_dirs:
                search_dirs.append(fallback_dir)

    candidates: list[str] = []
    for directory in search_dirs:
        for filename in target_names:
            candidate = os.path.join(directory, filename)
            if os.path.isfile(candidate) and candidate not in candidates:
                candidates.append(candidate)
    if len(candidates) != 1:
        return args, ""

    args["path"] = candidates[0]
    return (
        args,
        f"path={raw_path!r} 不存在，已改为问题指定的真实文件 {candidates[0]!r}",
    )


def repair_arguments(
    name: str,
    arguments: Mapping[str, Any] | None,
    prompt: str = "",
    last_path: str = "",
) -> RepairOutcome:
    """完整的参数修正链：别名归一化 → 补齐 path → 修正目录/编造路径。"""
    args = dict(arguments or {})
    notes: list[str] = []

    original_path = str(args.get("path") or "").strip()
    args = normalize_arguments(name, args, prompt, last_path)
    filled_path = str(args.get("path") or "").strip()
    if name in PATH_ARG_TOOLS and not original_path and filled_path:
        notes.append(f"缺少 path，已自动补为 {filled_path!r}")

    args, note = repair_file_path_call(name, args, prompt)
    if note:
        notes.append(note)
    args, note = repair_nonexistent_file_path(name, args, prompt, last_path)
    if note:
        notes.append(note)

    return RepairOutcome(arguments=args, notes=tuple(notes))


def match_hint(prompt: str) -> str:
    """根据问题关键词猜测模型应该调用的工具名，用于漏调工具时追问。"""
    for pattern, name in TOOL_HINTS:
        if pattern.search(prompt or ""):
            return name
    return ""


def build_nudge(name: str, spec: ToolSpec, prompt: str) -> str:
    """生成漏调工具时的追问，尽量把问题里的真实文件名写进示例。"""
    example_args = dict(spec.example_arguments)
    filenames = prompt_filenames(prompt)
    if len(filenames) == 1:
        filename = filenames[0]
        if name in NUDGE_PATH_TOOLS:
            example_args["path"] = filename
        elif name == "createFile":
            example_args["filename"] = filename
    if name == "fetchWeb":
        urls = URL_RE.findall(prompt or "")
        if urls:
            example_args["url"] = urls[0].rstrip("，。；：、）》」』）)]}")
    if name == "searchWeb":
        query = (prompt or "").strip()
        if query:
            example_args["query"] = query
    example = json.dumps({"name": name, "arguments": example_args}, ensure_ascii=False)
    return (
        "请不要直接回答，先调用工具获取真实结果，"
        f"只输出一行工具调用 JSON，例如 {example}，不要输出其他文字。"
    )
