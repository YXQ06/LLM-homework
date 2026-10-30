"""在 Practice02（多轮对话 + function call）的基础上，新增一组本地文件操作工具。

与 Practice02 的差异：
1. 保留 getDate / isLeapYear 两个工具；
2. 新增 10 个文件系统工具：
   getCurrentDirectory、listDirectory、getFileType、readTextFile、
   createFile、editFile、createDirectory、deleteDirectory、
   deleteFile、renamePath；
3. 文件操作全部用 Python 标准库实现（os / shutil / mimetypes / codecs），
   读取与编辑只接受纯文本（UTF-8 / GBK / UTF-16），二进制文件会被拒绝；
4. 相对路径以“当前工作目录”为基准；按“试用代码”的要求，编辑、删除目录、
   删除文件、重命名文件/目录不再施加路径安全限制（请谨慎操作）；
5. 一轮提问允许多步工具调用（如先建目录、再建文件、最后写内容），
   同一轮里完全相同的调用只执行一次，连续两轮完全相同的调用会直接收尾，
   防止模型陷入死循环。

仍使用非流式请求（stream=False），完整回答以打字机效果逐字输出；仅按 Ctrl+C 退出。
"""

import codecs
import configparser
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.ini")
TIMEOUT = 300  # 延长超时，适配较慢的本地模型
TYPE_DELAY = 0.02  # 每个字符的输出间隔（秒）
MAX_TOOL_ROUNDS = 8  # 单轮提问最多连续调用工具的次数，文件操作常需多步
TEMPERATURE = 0.2  # 低温让小模型更稳定地按约定输出工具调用 JSON
MAX_LIST_ENTRIES = 200  # 列目录时最多展示的条目数
MAX_READ_BYTES = 200_000  # readTextFile 最多读取的字节数
MAX_TOOL_RESULT_CHARS = 12_000  # 回传给模型的单个工具结果最大长度

# 需要走工具的问题的简单识别：命中则要求模型必须调用对应工具
TOOL_HINTS = [
    (re.compile(r"重命名|改名|rename", re.IGNORECASE), "renamePath"),
    (re.compile(r"删除[^，。；\n]*?文件|删除[^，。；\n]*?\.(txt|py|md|json|csv|log)", re.IGNORECASE), "deleteFile"),
    (re.compile(r"删除[^，。；\n]*?(目录|文件夹)", re.IGNORECASE), "deleteDirectory"),
    (re.compile(r"(创建|新建|建立)[^，。；\n]*?(目录|文件夹)", re.IGNORECASE), "createDirectory"),
    (re.compile(r"(创建|新建)[^，。；\n]*?文件|(创建|新建)[^，。；\n]*?\.(txt|py|md|json|csv|log)", re.IGNORECASE), "createFile"),
    (re.compile(r"编辑|修改|追加|替换|写入|edit|append|replace", re.IGNORECASE), "editFile"),
    (re.compile(r"文件类型|类型|格式|mime|type", re.IGNORECASE), "getFileType"),
    (re.compile(r"列出|列一下|有哪些|目录下|文件夹下|\bls\b|\blist\b", re.IGNORECASE), "listDirectory"),
    (re.compile(r"读取|查看|打开|显示|内容|\bread\b", re.IGNORECASE), "readTextFile"),
    (re.compile(r"当前(工作)?目录|工作目录|在哪个目录|\bpwd\b|\bcurrent\b", re.IGNORECASE), "getCurrentDirectory"),
    (re.compile(r"闰年|leap", re.IGNORECASE), "isLeapYear"),
    (re.compile(r"日期|时间|几点|几号|今天|现在|\bdate\b|\btime\b", re.IGNORECASE), "getDate"),
]

# 从用户问题中提取文件名（用于给小模型生成更具体的调用示例 / 修正错误参数）
# 这里只识别 ASCII 文件名，避免把“当前目录下的”这类中文前缀误当成文件名的一部分；
# 中文文件名仍可通过模型直接传入正确路径来操作。
FILENAME_RE = re.compile(
    r"([A-Za-z0-9_.\-]+\.(?:txt|md|py|json|csv|log|ini|xml|html|htm|css|js|yaml|yml"
    r"|png|jpg|jpeg|gif|pdf|zip|exe|dll))",
    re.IGNORECASE,
)

# 从问题中提取绝对目录（Windows 盘符路径 / UNC 路径），用于修正模型编造的 path。
# 遇到中文即停止，避免把“F:\...\Practice03目录下的 test.md”里的中文说明吞进路径。
PROMPT_DIR_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\)[^\s\u4e00-\u9fff，。；：、“”‘’\"'（）()]+"
)

# 这些工具只能作用于文件；若模型把 path 写成目录，可用问题里的文件名修正
FILE_ONLY_TOOLS = {"readTextFile", "editFile", "deleteFile"}

# 生成追问示例时，这些工具的 path 可以替换成问题里的真实文件名
NUDGE_PATH_TOOLS = FILE_ONLY_TOOLS | {"getFileType", "renamePath"}

# 这些工具的 path 参数是必需项，允许从常见别名（filename、file_path 等）补齐
PATH_ARG_TOOLS = {
    "listDirectory",
    "getFileType",
    "readTextFile",
    "editFile",
    "createDirectory",
    "deleteDirectory",
    "deleteFile",
    "renamePath",
}

# 模型漏调工具时，追问里给出的调用示例（用真实路径，避免模型照抄占位符）
NUDGE_EXAMPLES = {
    "getCurrentDirectory": {},
    "listDirectory": {"path": "."},
    "getFileType": {"path": "README.md"},
    "readTextFile": {"path": "README.md"},
    "createFile": {"directory": ".", "filename": "new.txt", "content": ""},
    "editFile": {"path": "new.txt", "content": "新的内容", "mode": "overwrite"},
    "createDirectory": {"path": "new_folder"},
    "deleteDirectory": {"path": "test_dir"},
    "deleteFile": {"path": "test.txt"},
    "renamePath": {"path": "old.txt", "new_name": "new.txt"},
    "isLeapYear": {"year": 2024},
    "getDate": {},
}

# 小模型常输出全角标点写 JSON，解析前先归一化成半角
FULLWIDTH_TRANS = str.maketrans(
    {
        "“": '"',  # “
        "”": '"',  # ”
        "‘": "'",  # ‘
        "’": "'",  # ’
        "：": ":",  # ：
        "，": ",",  # ，
    }
)

SYSTEM_PROMPT = (
    "你是本地文件管理助手。请严格根据工具返回的真实结果回答，"
    "不要编造目录内容、文件内容或操作结果。\n"
    "当前可用工具：\n"
    "1. getCurrentDirectory（无参数）：获取当前工作目录；\n"
    "2. listDirectory（path，可选 show_hidden）：列出目录下的文件和子目录；\n"
    "3. getFileType（path）：获取文件类型；\n"
    "4. readTextFile（path）：读取纯文本文件内容；\n"
    "5. createFile（directory，filename，content）：在指定目录创建新文件，目录不存在时自动创建；\n"
    "6. editFile（path，content，mode，old_text，new_text）：编辑纯文本文件，"
    "mode 可选 overwrite（覆盖）/ append（追加）/ replace（替换 old_text 为 new_text）；\n"
    "7. createDirectory（path）：创建新目录，支持多级；\n"
    "8. deleteDirectory（path）：删除目录（含非空目录，递归删除）；\n"
    "9. deleteFile（path）：删除文件；\n"
    "10. renamePath（path，new_name）：重命名文件或目录。\n"
    "11. getDate（无参数）：获取当前电脑的日期和时间；\n"
    "12. isLeapYear（year）：判断某年是否是闰年。\n"
    "规则：\n"
    "1. 涉及真实文件系统的问题必须先调用工具，禁止凭猜测回答；\n"
    "2. 一次只调用一个工具，拿到结果后再决定下一步；相对路径以当前工作目录为基准；\n"
    "3. path 必须指向具体对象：读取、编辑、删除文件时要写到文件名（例如 test.md），"
    "不能只写 ./ 或目录；deleteFile 只删文件，deleteDirectory 才删目录；"
    "每次调用都要完整给出参数，不要省略 path；\n"
    "4. 若接口支持原生 function call（tools 声明），使用原生 tool_calls 调用；\n"
    "5. 否则只输出一行 JSON 来调用工具，例如 "
    "{\"name\": \"listDirectory\", \"arguments\": {\"path\": \".\"}}，"
    "该行之外不要输出任何其他文字，也不要用 Markdown 代码块包裹；\n"
    "6. 收到工具结果后，用中文简洁回答用户，严禁改写、猜测或编造工具结果。"
)

# 向模型声明的 function call 工具列表
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getCurrentDirectory",
            "description": "获取当前工作目录（进程的当前目录）的绝对路径",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listDirectory",
            "description": "列出指定目录下的所有文件和子目录，包含大小与修改时间",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要列出的目录路径，相对路径以当前工作目录为基准；省略时为当前目录",
                    },
                    "show_hidden": {
                        "type": "boolean",
                        "description": "是否显示以点开头的隐藏文件/目录，默认 false",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getFileType",
            "description": "获取指定文件的类型（扩展名、MIME 类型、文本或二进制）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，相对路径以当前工作目录为基准",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readTextFile",
            "description": "读取纯文本文件的内容（仅支持纯文本，二进制文件会被拒绝）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的纯文本文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createFile",
            "description": "在指定目录下创建新文件，目录不存在时会自动创建（含多级目录）",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "文件所在目录，相对路径以当前工作目录为基准；省略时为当前目录",
                    },
                    "filename": {
                        "type": "string",
                        "description": "新文件名，只写文件名，不要包含路径分隔符，例如 note.txt",
                    },
                    "content": {
                        "type": "string",
                        "description": "文件初始内容，可为空字符串",
                    },
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editFile",
            "description": "编辑纯文本文件内容，支持覆盖、追加、替换三种模式（仅支持纯文本）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要编辑的纯文本文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "overwrite / append 模式下写入的完整内容",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append", "replace"],
                        "description": "编辑模式：overwrite 覆盖整个文件；append 追加到末尾；replace 替换 old_text，默认 overwrite",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "mode=replace 时要被替换的原文（必填）",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "mode=replace 时替换后的新文本（可为空字符串表示删除）",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createDirectory",
            "description": "创建新目录（支持多级目录，父目录不存在时自动创建）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要创建的目录路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteDirectory",
            "description": "删除目录；支持删除非空目录（递归删除其中所有内容），请谨慎使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要删除的目录路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteFile",
            "description": "删除指定文件（不会删除目录；path 必须写到具体文件名，例如 test.md）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要删除的文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "renamePath",
            "description": "重命名文件或目录；new_name 只写名称时在原目录下改名，也可给出新路径",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "原文件或目录路径",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "新名称或新路径；只写名称时表示在原目录下重命名",
                    },
                },
                "required": ["path", "new_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间（通过本地 date 命令读取系统时间）",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "计算输入的年份是否是闰年",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "要判断的年份，例如 2024",
                    }
                },
                "required": ["year"],
            },
        },
    },
]


def tool_names():
    return [t["function"]["name"] for t in TOOLS]


def parse_text_tool_calls(text):
    """从回复文本中解析文本式工具调用，适配不支持原生 tool_calls 的小模型。

    小模型常把 JSON 写成全角引号（“name”），先归一化全角符号再解析；
    扫描文本里的每个 '{'，尝试用 JSON 解码器完整解析出对象，
    若对象含 "name" 且命中已注册工具名，则视为一次工具调用。
    支持一次回复里给出多个工具调用，按出现顺序返回。
    """
    text = (text or "").translate(FULLWIDTH_TRANS)
    decoder = json.JSONDecoder()
    names = set(tool_names())
    calls = []
    pos = 0
    while True:
        match = text.find("{", pos)
        if match < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[match:])
        except ValueError:
            pos = match + 1
            continue
        if isinstance(obj, dict) and obj.get("name") in names:
            arguments = obj.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            calls.append((obj["name"], arguments))
            pos = match + end
        else:
            pos = match + 1
    return calls


def load_config(path=CONFIG_FILE):
    """读取 ini 配置，返回 [llm] 分区。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"配置文件不存在：{path}")

    cfg = configparser.ConfigParser()
    cfg.read(path, encoding="utf-8")
    if not cfg.has_section("llm"):
        raise ValueError(f"配置文件缺少 [llm] 分区：{path}")
    return cfg["llm"]


def get_date():
    """getDate 工具实现：调用本地 date 命令获取系统日期和时间。

    Windows 下执行 `date /t` 与 `time /t`（date 命令无参数会进入交互式输入，
    因此加 /t 只打印当前值）；其它平台执行 `date` 命令并带上格式化参数。
    全程通过 subprocess 调用系统命令，不使用 Python 的 datetime。
    """
    if os.name == "nt":
        commands = [["cmd", "/c", "date", "/t"], ["cmd", "/c", "time", "/t"]]
    else:
        commands = [["date", "+%Y-%m-%d %H:%M:%S"]]

    outputs = []
    for cmd in commands:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",  # 命令输出编码与系统不一致时避免崩溃
            timeout=10,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(f"执行命令 {' '.join(cmd)} 失败：{stderr or result.returncode}")
        line = (result.stdout or "").strip()
        if line:
            outputs.append(line)

    if not outputs:
        raise RuntimeError("date 命令没有返回内容")
    return " ".join(outputs)


def is_leap_year(year):
    """isLeapYear 工具实现：用 Python 代码计算某年是否是闰年。

    闰年规则：普通年份能被 4 整除但不能被 100 整除；世纪年份（整百年）能被 400 整除。
    """
    year = int(year)
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def _arg_text(arguments, key, required=False, default=""):
    """从参数里取字符串，做类型与空值校验。"""
    value = (arguments or {}).get(key, default)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"参数 {key} 必须是字符串，收到的是 {type(value).__name__}")
    value = value.strip()
    if required and not value:
        raise ValueError(
            f"缺少必填参数：{key}（{key} 不能为空，请在 arguments 中给出具体值）"
        )
    return value


def _resolve_path(raw):
    """把相对路径解析为基于当前工作目录的绝对路径。

    Windows 上模型常把 test.md 写成 /test.md，此时 os.path.abspath 会得到
    盘符根目录（如 F:\\test.md）。若该路径不存在、而当前目录下同名文件存在，
    则按相对路径处理，避免“文件明明在当前目录却提示不存在”。
    """
    raw = os.path.expandvars(os.path.expanduser(raw))
    if not raw:
        raise ValueError("路径不能为空")
    if os.path.isabs(raw):
        path = os.path.abspath(raw)
        looks_rooted = raw.startswith(("/", "\\")) and not raw.startswith(("//", "\\\\"))
        has_drive = bool(re.match(r"^[A-Za-z]:", raw))
        if os.name == "nt" and looks_rooted and not has_drive and not os.path.exists(path):
            candidate = os.path.abspath(raw.lstrip("/\\"))
            if os.path.exists(candidate):
                return candidate
        return path
    return os.path.abspath(os.path.join(os.getcwd(), raw))


def _prompt_filenames(prompt):
    """提取问题中出现的文件名，去重并保持出现顺序。"""
    names = []
    for match in FILENAME_RE.finditer(prompt or ""):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


def _prompt_directories(prompt):
    """提取问题中真实存在的绝对目录，用于修正模型编造的 path。

    也接受“F:\\dir\\test.md”这种带文件名的写法（取所在目录）。
    """
    dirs = []
    for match in PROMPT_DIR_RE.finditer(prompt or ""):
        raw = match.group(0).rstrip("\\/")
        try:
            path = _resolve_path(raw)
        except (ValueError, OSError):
            continue
        if not os.path.isdir(path):
            parent = os.path.dirname(path)
            path = parent if os.path.isdir(parent) else ""
        if path and path not in dirs:
            dirs.append(path)
    return dirs


def _infer_path_argument(name, prompt, fallback_path=""):
    """path 缺失时按“问题里的文件名 → 本轮上一次成功的路径”补齐。

    只在有把握时补：问题里只有一个文件名，或多个文件名中只有一个真实存在。
    都没有时返回空字符串，让工具返回清晰的缺参错误，而不是乱猜目标。
    """
    filenames = _prompt_filenames(prompt)
    if len(filenames) == 1:
        return filenames[0]

    existing = []
    for filename in filenames:
        try:
            path = _resolve_path(filename)
        except (ValueError, OSError):
            continue
        hit = os.path.isfile(path) if name in FILE_ONLY_TOOLS else os.path.exists(path)
        if hit:
            existing.append(filename)
    if len(existing) == 1:
        return existing[0]

    if fallback_path:
        return fallback_path
    return ""


def _normalize_arguments(name, arguments, prompt="", fallback_path=""):
    """把模型常用的同义参数名补齐成工具声明的参数名。

    小模型有时会把 path 写成 filename / file_path / directory，甚至直接漏掉
    path（例如 readTextFile({})）；这里依次做同义参数名归一化、问题文件名推断
    和本轮路径回退，尽量减少因参数不完整导致的失败。
    """
    args = dict(arguments or {})
    if name in PATH_ARG_TOOLS and not str(args.get("path") or "").strip():
        for key in (
            "file_path",
            "filepath",
            "filename",
            "file",
            "target",
            "dir",
            "directory",
        ):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                args["path"] = value.strip()
                break
    if name in PATH_ARG_TOOLS and not str(args.get("path") or "").strip():
        inferred = _infer_path_argument(name, prompt, fallback_path)
        if inferred:
            args["path"] = inferred
    if name == "renamePath" and not str(args.get("new_name") or "").strip():
        for key in ("new_path", "newpath", "to", "target", "new_filename"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                args["new_name"] = value.strip()
                break
    return args


def _repair_file_path_call(name, arguments, prompt):
    """修正“path 指向目录”的明显参数错误。

    典型场景：用户说“删除当前目录下的 test.md”，小模型却把 path 写成 "./"。
    仅当问题里只出现一个文件名、且 path 确实指向目录（或就是 . / ./）时才修正，
    避免在多个文件名的情况下猜错目标。返回 (修正后的参数, 修正说明)。
    """
    if name not in FILE_ONLY_TOOLS:
        return arguments, ""
    raw_path = (arguments or {}).get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return arguments, ""
    try:
        points_to_dir = os.path.isdir(_resolve_path(raw_path))
    except (ValueError, OSError):
        points_to_dir = False
    looks_like_dir = raw_path.strip() in (".", "./", ".\\", "..", "../", "..\\")
    if not (points_to_dir or looks_like_dir):
        return arguments, ""

    filenames = _prompt_filenames(prompt)
    if len(filenames) != 1:
        return arguments, ""
    fixed = dict(arguments or {})
    fixed["path"] = filenames[0]
    return fixed, f"path={raw_path!r} 指向目录，已按问题中的文件名改为 {filenames[0]!r}"


def _repair_nonexistent_file_path(name, arguments, prompt, fallback_path=""):
    """修正“path 不存在”的编造路径。

    典型场景：用户明确说“删除 F:\\...\\Practice03 目录下的 test.md”，
    模型却输出 path=/current/dir/test.md（占位路径）。此时若问题里只有一个
    文件名，或 path 的文件名正好出现在问题里，就在问题给出的绝对目录（没有
    则用当前目录 / 本轮上一次路径所在目录）中查找真实文件并替换 path。

    只有原 path 不存在、且只找到一个候选文件时才修正，避免误操作。
    """
    if name not in FILE_ONLY_TOOLS:
        return arguments, ""
    raw_path = (arguments or {}).get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return arguments, ""
    try:
        path = _resolve_path(raw_path)
    except (ValueError, OSError):
        return arguments, ""
    if os.path.exists(path):
        return arguments, ""

    raw_name = os.path.basename(raw_path.strip())
    prompt_names = _prompt_filenames(prompt)
    if len(prompt_names) == 1:
        target_names = prompt_names
    elif raw_name in prompt_names:
        target_names = [raw_name]
    else:
        return arguments, ""

    search_dirs = _prompt_directories(prompt)
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

    candidates = []
    for directory in search_dirs:
        for filename in target_names:
            candidate = os.path.join(directory, filename)
            if os.path.isfile(candidate) and candidate not in candidates:
                candidates.append(candidate)
    if len(candidates) != 1:
        return arguments, ""

    fixed = dict(arguments or {})
    fixed["path"] = candidates[0]
    return fixed, f"path={raw_path!r} 不存在，已改为问题指定的真实文件 {candidates[0]!r}"


def _human_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _looks_binary(data):
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


def _decode_text(data, strict=True):
    """按 BOM / UTF-8 / GBK 顺序解码文本，返回 (文本, 编码名)。

    strict=False 时，解码失败会用 UTF-8 替换无法识别的字节，用于读取展示；
    strict=True 时直接抛错，避免编辑文件时破坏原有内容。
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

    # 读取展示场景：允许多字节字符被截断，选替换字符最少的一种编码
    best_text = ""
    best_encoding = candidates[0]
    best_bad = None
    for encoding in candidates:
        text = data.decode(encoding, errors="replace")
        bad = text.count("\ufffd")
        if best_bad is None or bad < best_bad:
            best_text, best_encoding, best_bad = text, encoding, bad
    if best_bad:
        best_encoding += "（部分字符被替换）"
    return best_text, best_encoding


def _assert_safe_target(path, action):
    """破坏性操作的路径安全检查（试用版本已停用，保留以便需要时恢复）。

    原来会禁止操作盘符根目录、当前目录及其上级、用户主目录等关键位置；
    现在 deleteDirectory / deleteFile / renamePath 不再调用本函数。
    """
    target = os.path.normcase(os.path.abspath(path))
    cwd = os.path.normcase(os.getcwd())
    drive, _ = os.path.splitdrive(target)

    protected = {
        os.path.normcase(os.path.abspath(os.sep)),
        os.path.normcase(drive + os.sep) if drive else "",
        cwd,
        os.path.normcase(os.path.expanduser("~")),
        os.path.normcase(os.environ.get("WINDIR", r"C:\Windows")),
        os.path.normcase(os.environ.get("ProgramFiles", r"C:\Program Files")),
        os.path.normcase(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    }
    protected.discard("")
    if target in protected:
        raise PermissionError(f"出于安全考虑，禁止{action}该路径：{path}")

    try:
        if os.path.commonpath([cwd, target]) == target:
            raise PermissionError(f"出于安全考虑，禁止{action}当前工作目录或其上级目录：{path}")
    except ValueError:
        pass  # 不同盘符，不存在包含关系


def _clip_result(text, limit=MAX_TOOL_RESULT_CHARS):
    """限制回传给模型的结果长度，避免超出小模型上下文。"""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...（结果过长，已截断，完整结果共 {len(text)} 字符）"


def _tool_get_current_directory(arguments):
    return f"当前工作目录：{os.getcwd()}"


def _tool_list_directory(arguments):
    raw_path = _arg_text(arguments, "path", default=".")
    show_hidden = bool((arguments or {}).get("show_hidden", False))
    path = _resolve_path(raw_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"目录不存在：{path}")
    if not os.path.isdir(path):
        raise NotADirectoryError(f"不是目录：{path}（获取文件信息请用 getFileType / readTextFile）")

    entries = []
    with os.scandir(path) as it:
        for entry in it:
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                stat = entry.stat()
                mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
                if entry.is_dir(follow_symlinks=False):
                    entries.append((0, f"[目录] {entry.name}/（修改时间 {mtime}）"))
                else:
                    entries.append(
                        (1, f"[文件] {entry.name}（{_human_size(stat.st_size)}，修改时间 {mtime}）")
                    )
            except OSError as exc:
                entries.append((2, f"[未知] {entry.name}（无法读取信息：{exc}）"))

    entries.sort(key=lambda item: (item[0], item[1].lower()))
    total = len(entries)
    lines = [f"目录：{path}", f"共 {total} 项（目录在前）："]
    if not total:
        lines.append("（空目录）")
    for _, line in entries[:MAX_LIST_ENTRIES]:
        lines.append(line)
    if total > MAX_LIST_ENTRIES:
        lines.append(f"...（仅显示前 {MAX_LIST_ENTRIES} 项）")
    return "\n".join(lines)


def _tool_get_file_type(arguments):
    raw_path = _arg_text(arguments, "path", required=True)
    path = _resolve_path(raw_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在：{path}")
    if os.path.isdir(path):
        return f"路径：{path}\n类型：目录"

    extension = os.path.splitext(path)[1].lower()
    mime_type, _ = mimetypes.guess_type(path)
    extension_kinds = {
        ".txt": "纯文本",
        ".md": "Markdown 文本",
        ".py": "Python 源代码",
        ".json": "JSON 文本",
        ".csv": "CSV 文本",
        ".log": "日志文本",
        ".ini": "INI 配置文本",
        ".xml": "XML 文本",
        ".html": "HTML 文本",
        ".htm": "HTML 文本",
        ".css": "CSS 文本",
        ".js": "JavaScript 源代码",
        ".yaml": "YAML 文本",
        ".yml": "YAML 文本",
        ".png": "PNG 图片",
        ".jpg": "JPEG 图片",
        ".jpeg": "JPEG 图片",
        ".gif": "GIF 图片",
        ".pdf": "PDF 文档",
        ".zip": "ZIP 压缩包",
        ".exe": "Windows 可执行文件",
        ".dll": "Windows 动态链接库",
    }
    if extension in extension_kinds:
        kind = extension_kinds[extension]
    elif extension:
        kind = f"{extension} 文件"
    else:
        kind = "无扩展名文件"

    with open(path, "rb") as handle:
        head = handle.read(8192)
    content_kind = "二进制" if _looks_binary(head) else "纯文本"

    return (
        f"路径：{path}\n"
        f"类型：{kind}\n"
        f"扩展名：{extension or '无'}\n"
        f"MIME 类型：{mime_type or '未知'}\n"
        f"内容类别：{content_kind}"
    )


def _tool_read_text_file(arguments):
    raw_path = _arg_text(arguments, "path", required=True)
    path = _resolve_path(raw_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在：{path}")
    if os.path.isdir(path):
        raise IsADirectoryError(f"这是目录，不能作为文件读取：{path}")

    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        data = handle.read(MAX_READ_BYTES + 1)
    if _looks_binary(data):
        raise ValueError(f"不是纯文本文件，拒绝读取：{path}")
    truncated = len(data) > MAX_READ_BYTES or size > MAX_READ_BYTES
    data = data[:MAX_READ_BYTES]
    text, encoding = _decode_text(data, strict=False)

    header = f"文件：{path}\n编码：{encoding}；大小：{_human_size(size)}"
    if truncated:
        header += f"（内容过大，仅显示前 {MAX_READ_BYTES} 字节）"
    if not text:
        return header + "\n（空文件）"
    return header + "\n--- 内容开始 ---\n" + text + "\n--- 内容结束 ---"


def _tool_create_file(arguments):
    directory = _arg_text(arguments, "directory", default=".")
    filename = _arg_text(arguments, "filename", required=True)
    content = (arguments or {}).get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        content = str(content)

    if (
        filename in (".", "..")
        or os.path.basename(filename) != filename
        or "/" in filename
        or "\\" in filename
    ):
        raise ValueError("filename 只能是文件名，不能包含路径分隔符；目录请用 directory 参数")

    directory_path = _resolve_path(directory)
    if os.path.exists(directory_path) and not os.path.isdir(directory_path):
        raise NotADirectoryError(f"directory 不是目录：{directory_path}")
    os.makedirs(directory_path, exist_ok=True)

    file_path = os.path.join(directory_path, filename)
    if os.path.exists(file_path):
        raise FileExistsError(f"文件已存在：{file_path}（如需修改内容请用 editFile）")
    with open(file_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)

    return (
        f"已创建文件：{file_path}\n"
        f"写入 {len(content)} 个字符（{_human_size(len(content.encode('utf-8')))}，UTF-8 编码）"
    )


def _tool_edit_file(arguments):
    raw_path = _arg_text(arguments, "path", required=True)
    mode = _arg_text(arguments, "mode", default="overwrite").lower()
    path = _resolve_path(raw_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在：{path}（如需新建请用 createFile）")
    if os.path.isdir(path):
        raise IsADirectoryError(f"这是目录，不能作为文件编辑：{path}")

    with open(path, "rb") as handle:
        data = handle.read()
    if _looks_binary(data):
        raise ValueError(f"不是纯文本文件，拒绝编辑：{path}")
    text, encoding = _decode_text(data, strict=True)

    if mode in ("overwrite", "覆盖"):
        if "content" not in (arguments or {}):
            raise ValueError("覆盖模式缺少 content 参数")
        content = (arguments or {}).get("content")
        if not isinstance(content, str):
            content = str(content)
        new_text = content
        detail = f"已覆盖文件内容：{path}（{len(new_text)} 个字符）"
    elif mode in ("append", "追加"):
        if "content" not in (arguments or {}):
            raise ValueError("追加模式缺少 content 参数")
        content = (arguments or {}).get("content")
        if not isinstance(content, str):
            content = str(content)
        new_text = text + content
        detail = f"已追加内容：{path}（新增 {len(content)} 个字符）"
    elif mode in ("replace", "替换"):
        old_text = (arguments or {}).get("old_text")
        if old_text is None:
            raise ValueError("替换模式缺少 old_text 参数")
        if not isinstance(old_text, str):
            raise ValueError("old_text 必须是字符串")
        if old_text == "":
            raise ValueError("old_text 不能是空字符串")
        new_value = (arguments or {}).get("new_text", "")
        if new_value is None:
            new_value = ""
        if not isinstance(new_value, str):
            new_value = str(new_value)
        count = text.count(old_text)
        if count == 0:
            raise ValueError(f"未在文件中找到 old_text：{old_text!r}")
        new_text = text.replace(old_text, new_value)
        detail = f"已替换 {path} 中的 {count} 处内容"
    else:
        raise ValueError(f"不支持的 mode：{mode}（可选 overwrite / append / replace）")

    with open(path, "w", encoding=encoding, newline="") as handle:
        handle.write(new_text)
    return detail


def _tool_create_directory(arguments):
    raw_path = _arg_text(arguments, "path", required=True)
    path = _resolve_path(raw_path)
    if os.path.exists(path):
        if os.path.isdir(path):
            return f"目录已存在，无需创建：{path}"
        raise FileExistsError(f"同名文件已存在，无法创建目录：{path}")
    os.makedirs(path)
    return f"已创建目录：{path}"


def _tool_delete_directory(arguments):
    raw_path = _arg_text(arguments, "path", required=True)
    path = _resolve_path(raw_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"目录不存在：{path}")
    if not os.path.isdir(path):
        raise NotADirectoryError(f"path 指向文件，deleteDirectory 只能删除目录；删除文件请用 deleteFile：{path}")

    file_count = 0
    dir_count = 0
    for _, dirnames, filenames in os.walk(path):
        dir_count += len(dirnames)
        file_count += len(filenames)
    shutil.rmtree(path)
    return f"已删除目录：{path}（其中 {file_count} 个文件、{dir_count} 个子目录）"


def _tool_delete_file(arguments):
    raw_path = _arg_text(arguments, "path", required=True)
    path = _resolve_path(raw_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在：{path}")
    if os.path.isdir(path):
        raise IsADirectoryError(
            f"path 指向目录，deleteFile 只能删除文件；"
            f"请提供具体文件名（例如 test.md），删除目录请用 deleteDirectory：{path}"
        )
    size = os.path.getsize(path)
    os.remove(path)
    return f"已删除文件：{path}（{_human_size(size)}）"


def _tool_rename_path(arguments):
    raw_path = _arg_text(arguments, "path", required=True)
    new_name = _arg_text(arguments, "new_name", required=True)
    path = _resolve_path(raw_path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"路径不存在：{path}")

    has_separator = (
        os.path.isabs(new_name)
        or "/" in new_name
        or "\\" in new_name
        or bool(os.path.dirname(new_name))
    )
    target = _resolve_path(new_name) if has_separator else os.path.join(os.path.dirname(path), new_name)
    target = os.path.abspath(target)

    if os.path.normcase(target) == os.path.normcase(path):
        raise ValueError("新名称与原名称相同")
    if os.path.exists(target):
        raise FileExistsError(f"目标已存在，拒绝覆盖：{target}")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    is_dir = os.path.isdir(path)
    os.rename(path, target)
    return f"已将{'目录' if is_dir else '文件'}重命名：{path} -> {target}"


def _is_leap_year_text(arguments):
    year = (arguments or {}).get("year")
    try:
        leap = is_leap_year(year)
    except (TypeError, ValueError):
        return f"isLeapYear 参数错误：year 必须是整数年份，收到的是 {year!r}"
    return f"{int(year)} 年{'是' if leap else '不是'}闰年"


TOOL_HANDLERS = {
    "getCurrentDirectory": _tool_get_current_directory,
    "listDirectory": _tool_list_directory,
    "getFileType": _tool_get_file_type,
    "readTextFile": _tool_read_text_file,
    "createFile": _tool_create_file,
    "editFile": _tool_edit_file,
    "createDirectory": _tool_create_directory,
    "deleteDirectory": _tool_delete_directory,
    "deleteFile": _tool_delete_file,
    "renamePath": _tool_rename_path,
    "getDate": lambda arguments: get_date(),
    "isLeapYear": _is_leap_year_text,
}


def call_tool(name, arguments, prompt="", fallback_path=""):
    """按工具名分发执行，返回给模型的字符串结果。"""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"未知工具：{name}"
    try:
        return handler(_normalize_arguments(name, arguments, prompt, fallback_path))
    except (ValueError, OSError, RuntimeError) as exc:
        return f"{name} 工具执行失败：{exc}"


def ask_llm(messages, conf):
    """向 OpenAI 兼容接口发起一次非流式请求，返回完整响应中的 message 字典。

    messages 为完整对话历史（含本轮提问），保证回答能参考之前的上下文；
    message 可能带有 content、reasoning_content 或 tool_calls。
    """
    url = conf["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {conf['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": conf["model"],
        "messages": messages,  # 携带全部历史，实现多轮上下文
        "tools": TOOLS,  # 声明 function call 工具
        "stream": False,  # 禁用流式，一次性返回完整结果
        "temperature": TEMPERATURE,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def extract_content(message):
    """取出正文：DeepSeek-R1 等思考模型可能把正文放在 reasoning_content。"""
    return message.get("content") or message.get("reasoning_content") or ""


def print_typing(text, delay=TYPE_DELAY):
    """打字机效果：逐字输出完整回答，换行符做短暂停顿。"""
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay if ch != "\n" else delay * 5)
    sys.stdout.write("\n")


def _assistant_message(message):
    """构造写入历史的 assistant 消息，原生工具调用需带上 tool_calls 字段。"""
    payload = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("tool_calls"):
        payload["tool_calls"] = message["tool_calls"]
    return payload


def normalize_tool_calls(message):
    """把模型回复统一成工具调用列表 [{"id", "name", "arguments"(dict)}]。

    优先取原生 tool_calls（支持 function call 的模型）；
    否则从回复文本中解析文本式 JSON 工具调用（小模型兜底）。
    """
    calls = []
    for call in message.get("tool_calls") or []:
        func = call.get("function") or {}
        raw_args = func.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except ValueError:
            arguments = {}
        calls.append(
            {
                "id": call.get("id", ""),
                "name": func.get("name", ""),
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
        )
    if calls:
        return calls

    for name, arguments in parse_text_tool_calls(extract_content(message)):
        calls.append({"id": f"txt-{name}", "name": name, "arguments": arguments})
    return calls


def _match_hint(prompt):
    """根据问题关键词猜测模型应该调用的工具名，用于漏调工具时追问。"""
    for pattern, name in TOOL_HINTS:
        if pattern.search(prompt):
            return name
    return ""


def _nudge_text(name, prompt=""):
    """生成漏调工具时的追问，尽量把问题里的真实文件名写进示例。"""
    example_args = dict(NUDGE_EXAMPLES.get(name, {}))
    filenames = _prompt_filenames(prompt)
    if len(filenames) == 1:
        filename = filenames[0]
        if name in NUDGE_PATH_TOOLS:
            example_args["path"] = filename
        elif name == "createFile":
            example_args["filename"] = filename
    example = json.dumps(
        {"name": name, "arguments": example_args},
        ensure_ascii=False,
    )
    return (
        "请不要直接回答，先调用工具获取真实结果，"
        f"只输出一行工具调用 JSON，例如 {example}，不要输出其他文字。"
    )


def _preview(text, limit=1000):
    """控制台预览过长结果，完整结果仍回传给模型。"""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...（控制台仅显示前 {limit} 字符，共 {len(text)} 字符）"


def run_turn(prompt, messages, conf):
    """处理一次提问：请求模型 → 若要求调用工具则执行并回传结果 → 循环直到拿到最终回答。

    返回 (最终回答文本, 本轮追加到历史的消息列表)。
    """
    turn_messages = [{"role": "user", "content": prompt}]
    final_answer = ""
    nudged = False  # 命中关键词但模型未调用工具时，是否已追问过一次
    tool_results = []  # 本轮已执行工具的 (名称, 结果)，用于兜底生成答案
    last_round_keys = None  # 上一轮的工具调用集合，用于识别模型原地打转
    last_path = ""  # 本轮上一次成功操作的路径，模型漏写 path 时可回退

    def fallback_answer():
        """模型始终没给出可用回答时，直接用最后一次工具结果作为答案。"""
        if not tool_results:
            return ""
        name, result = tool_results[-1]
        return f"根据 {name} 工具的返回结果：\n{result}"

    for _ in range(MAX_TOOL_ROUNDS):
        message = ask_llm(messages + turn_messages, conf)
        tool_calls = normalize_tool_calls(message)

        if not tool_calls:
            hint = _match_hint(prompt)
            if hint and not nudged and not tool_results:
                # 小模型可能漏调工具：本轮还没执行过任何工具时，追问一次要求它先调用工具
                nudged = True
                turn_messages.append(
                    {"role": "assistant", "content": message.get("content") or ""}
                )
                turn_messages.append({"role": "user", "content": _nudge_text(hint, prompt)})
                continue
            final_answer = extract_content(message)
            turn_messages.append(
                {"role": "assistant", "content": message.get("content") or ""}
            )
            break

        # 参数名归一化 + 补齐缺失的 path + 修正“path 指向目录”的明显错误（小模型常见）
        for call in tool_calls:
            before = dict(call["arguments"] or {})
            call["arguments"] = _normalize_arguments(
                call["name"], call["arguments"], prompt, last_path
            )
            if (
                call["name"] in PATH_ARG_TOOLS
                and not str(before.get("path") or "").strip()
                and str(call["arguments"].get("path") or "").strip()
            ):
                print(
                    f"\n[参数修正] {call['name']}: 缺少 path，已自动补为 "
                    f"{call['arguments']['path']!r}"
                )
            fixed, note = _repair_file_path_call(call["name"], call["arguments"], prompt)
            if note:
                print(f"\n[参数修正] {call['name']}: {note}")
                call["arguments"] = fixed
            fixed, note = _repair_nonexistent_file_path(
                call["name"], call["arguments"], prompt, last_path
            )
            if note:
                print(f"\n[参数修正] {call['name']}: {note}")
                call["arguments"] = fixed

        # 本轮与上一轮的工具调用完全相同：说明模型在原地打转，直接用已有结果收尾
        round_keys = [
            (call["name"], json.dumps(call["arguments"], sort_keys=True, ensure_ascii=False))
            for call in tool_calls
        ]
        if last_round_keys is not None and set(round_keys) == last_round_keys:
            final_answer = fallback_answer()
            turn_messages.append({"role": "assistant", "content": final_answer})
            break
        last_round_keys = set(round_keys)

        # 模型要求调用工具：先执行，再记录 assistant 回复，并逐个以 tool / user 消息回传结果
        round_results = {}  # 同一轮里完全相同的调用只执行一次，结果共享
        pairs = []
        for call, key in zip(tool_calls, round_keys):
            if key in round_results:
                pairs.append((call, round_results[key]))
                continue

            print(f"\n[调用工具] {call['name']}({json.dumps(call['arguments'], ensure_ascii=False)})")
            try:
                result = call_tool(
                    call["name"], call["arguments"], prompt, last_path
                )
            except Exception as exc:  # 工具异常也要回传给模型，让模型自行解释
                result = f"{call['name']} 执行异常：{exc}"
            result = _clip_result(result)
            if not result.startswith(f"{call['name']} 工具执行失败"):
                raw_path = call["arguments"].get("path")
                if isinstance(raw_path, str) and raw_path.strip():
                    try:
                        last_path = _resolve_path(raw_path)
                    except (ValueError, OSError):
                        pass
            round_results[key] = result
            tool_results.append((call["name"], result))
            print(f"[工具结果] {_preview(result)}")
            pairs.append((call, result))

        turn_messages.append(_assistant_message(message))
        for call, result in pairs:
            if call["id"].startswith("txt-"):
                # 文本式工具调用：用 user 消息回传结果，避免小模型触发原生工具模板
                turn_messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"【工具 {call['name']} 的执行结果】\n{result}\n"
                            "请仅根据该结果用中文简洁回答，严禁改写、猜测或编造。"
                        ),
                    }
                )
            else:
                turn_messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": result}
                )
    else:
        final_answer = fallback_answer()
        if not final_answer:
            print("（工具调用次数达到上限，未取得最终回答）", file=sys.stderr)

    # 模型没给出回答但工具已执行成功：直接用工具结果兜底，保证演示可见
    if not final_answer and tool_results:
        final_answer = fallback_answer()

    return final_answer, turn_messages


def main():
    try:
        conf = load_config()
    except (OSError, configparser.Error, KeyError) as exc:
        print(f"读取配置失败：{exc}", file=sys.stderr)
        sys.exit(1)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]  # 系统提示词固定放在最前

    try:
        while True:
            prompt = input("请输入问题：").strip()
            if not prompt:
                print("问题不能为空", file=sys.stderr)
                continue

            try:
                print("\n---------- 模型回答 ----------")
                answer, turn_messages = run_turn(prompt, messages, conf)
                if not answer:
                    print("（模型返回为空，未写入历史）", file=sys.stderr)
                    # 工具交互过程仍保留在上下文中，便于模型延续思路
                    messages.extend(turn_messages)
                    continue
                print_typing(answer)
                messages.extend(turn_messages)
            except requests.exceptions.Timeout:
                print(f"请求超时（{TIMEOUT}s）", file=sys.stderr)
            except requests.exceptions.HTTPError as exc:
                print(f"HTTP 错误 {exc.response.status_code}：{exc.response.text}", file=sys.stderr)
            except requests.exceptions.RequestException as exc:  # 含连接失败等网络异常
                print(f"网络请求失败：{exc}", file=sys.stderr)
            except (KeyError, IndexError, ValueError) as exc:
                print(f"返回数据格式异常：{exc}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("\n收到中断信号或输入流结束，退出程序。")


if __name__ == "__main__":
    main()
