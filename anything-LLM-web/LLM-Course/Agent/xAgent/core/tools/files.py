"""本地文件系统工具：目录查询、文本读取、创建、编辑、删除、重命名。

按“试用版本”的要求，编辑、删除、重命名不做路径安全限制，可操作任意
有权限的路径；读取与编辑仅接受纯文本（UTF-8 / GBK / UTF-16）。
"""

from __future__ import annotations

import mimetypes
import os
import shutil
import time
from typing import Any, Mapping

from ..config import AgentConfig
from ..utils.paths import resolve_path
from ..utils.text import decode_text, human_size, looks_binary
from .base import ToolSpec, object_schema, text_argument

_EXTENSION_KINDS = {
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

_MODE_ALIASES = {
    "overwrite": "overwrite",
    "覆盖": "overwrite",
    "append": "append",
    "追加": "append",
    "replace": "replace",
    "替换": "replace",
}


class FileToolbox:
    """文件工具集合。

    持有 ``AgentConfig``，因此列表条数、读取字节数等上限可以统一配置，
    而不是散落在各个函数里。
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()

    # ------------------------------------------------------------------ 工具实现

    def get_current_directory(self, arguments: Mapping[str, Any]) -> str:
        return f"当前工作目录：{os.getcwd()}"

    def list_directory(self, arguments: Mapping[str, Any]) -> str:
        raw_path = text_argument(arguments, "path", default=".")
        show_hidden = bool((arguments or {}).get("show_hidden", False))
        path = resolve_path(raw_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"目录不存在：{path}")
        if not os.path.isdir(path):
            raise NotADirectoryError(
                f"不是目录：{path}（获取文件信息请用 getFileType / readTextFile）"
            )

        entries: list[tuple[int, str]] = []
        with os.scandir(path) as iterator:
            for entry in iterator:
                if not show_hidden and entry.name.startswith("."):
                    continue
                try:
                    stat = entry.stat()
                    mtime = time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)
                    )
                    if entry.is_dir(follow_symlinks=False):
                        entries.append((0, f"[目录] {entry.name}/（修改时间 {mtime}）"))
                    else:
                        entries.append(
                            (
                                1,
                                f"[文件] {entry.name}"
                                f"（{human_size(stat.st_size)}，修改时间 {mtime}）",
                            )
                        )
                except OSError as exc:
                    entries.append(
                        (2, f"[未知] {entry.name}（无法读取信息：{exc}）")
                    )

        entries.sort(key=lambda item: (item[0], item[1].lower()))
        total = len(entries)
        limit = self.config.max_list_entries
        lines = [f"目录：{path}", f"共 {total} 项（目录在前）："]
        if not total:
            lines.append("（空目录）")
        for _, line in entries[:limit]:
            lines.append(line)
        if total > limit:
            lines.append(f"...（仅显示前 {limit} 项）")
        return "\n".join(lines)

    def get_file_type(self, arguments: Mapping[str, Any]) -> str:
        raw_path = text_argument(arguments, "path", required=True)
        path = resolve_path(raw_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"文件不存在：{path}")
        if os.path.isdir(path):
            return f"路径：{path}\n类型：目录"

        extension = os.path.splitext(path)[1].lower()
        mime_type, _ = mimetypes.guess_type(path)
        if extension in _EXTENSION_KINDS:
            kind = _EXTENSION_KINDS[extension]
        elif extension:
            kind = f"{extension} 文件"
        else:
            kind = "无扩展名文件"

        with open(path, "rb") as handle:
            head = handle.read(8192)
        content_kind = "二进制" if looks_binary(head) else "纯文本"

        return (
            f"路径：{path}\n"
            f"类型：{kind}\n"
            f"扩展名：{extension or '无'}\n"
            f"MIME 类型：{mime_type or '未知'}\n"
            f"内容类别：{content_kind}"
        )

    def read_text_file(self, arguments: Mapping[str, Any]) -> str:
        raw_path = text_argument(arguments, "path", required=True)
        path = resolve_path(raw_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"文件不存在：{path}")
        if os.path.isdir(path):
            raise IsADirectoryError(f"这是目录，不能作为文件读取：{path}")

        limit = self.config.max_read_bytes
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
        if looks_binary(data):
            raise ValueError(f"不是纯文本文件，拒绝读取：{path}")

        truncated = len(data) > limit or size > limit
        data = data[:limit]
        text, encoding = decode_text(data, strict=False)

        header = f"文件：{path}\n编码：{encoding}；大小：{human_size(size)}"
        if truncated:
            header += f"（内容过大，仅显示前 {limit} 字节）"
        if not text:
            return header + "\n（空文件）"
        return header + "\n--- 内容开始 ---\n" + text + "\n--- 内容结束 ---"

    def create_file(self, arguments: Mapping[str, Any]) -> str:
        directory = text_argument(arguments, "directory", default=".")
        filename = text_argument(arguments, "filename", required=True)
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
            raise ValueError(
                "filename 只能是文件名，不能包含路径分隔符；目录请用 directory 参数"
            )

        directory_path = resolve_path(directory)
        if os.path.exists(directory_path) and not os.path.isdir(directory_path):
            raise NotADirectoryError(f"directory 不是目录：{directory_path}")
        os.makedirs(directory_path, exist_ok=True)

        file_path = os.path.join(directory_path, filename)
        if os.path.exists(file_path):
            raise FileExistsError(
                f"文件已存在：{file_path}（如需修改内容请用 editFile）"
            )
        with open(file_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)

        return (
            f"已创建文件：{file_path}\n"
            f"写入 {len(content)} 个字符"
            f"（{human_size(len(content.encode('utf-8')))}，UTF-8 编码）"
        )

    def edit_file(self, arguments: Mapping[str, Any]) -> str:
        raw_path = text_argument(arguments, "path", required=True)
        raw_mode = text_argument(arguments, "mode", default="overwrite").lower()
        mode = _MODE_ALIASES.get(raw_mode)
        if mode is None:
            raise ValueError(
                f"不支持的 mode：{raw_mode}（可选 overwrite / append / replace）"
            )

        path = resolve_path(raw_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"文件不存在：{path}（如需新建请用 createFile）")
        if os.path.isdir(path):
            raise IsADirectoryError(f"这是目录，不能作为文件编辑：{path}")

        with open(path, "rb") as handle:
            data = handle.read()
        if looks_binary(data):
            raise ValueError(f"不是纯文本文件，拒绝编辑：{path}")
        text, encoding = decode_text(data, strict=True)

        args = arguments or {}
        if mode in ("overwrite", "append"):
            if "content" not in args:
                raise ValueError(f"{mode} 模式缺少 content 参数")
            content = args.get("content")
            if not isinstance(content, str):
                content = str(content)
            if mode == "overwrite":
                new_text = content
                detail = f"已覆盖文件内容：{path}（{len(new_text)} 个字符）"
            else:
                new_text = text + content
                detail = f"已追加内容：{path}（新增 {len(content)} 个字符）"
        else:
            old_text = args.get("old_text")
            if old_text is None:
                raise ValueError("替换模式缺少 old_text 参数")
            if not isinstance(old_text, str):
                raise ValueError("old_text 必须是字符串")
            if old_text == "":
                raise ValueError("old_text 不能是空字符串")
            new_value = args.get("new_text", "")
            if new_value is None:
                new_value = ""
            if not isinstance(new_value, str):
                new_value = str(new_value)
            count = text.count(old_text)
            if count == 0:
                raise ValueError(f"未在文件中找到 old_text：{old_text!r}")
            new_text = text.replace(old_text, new_value)
            detail = f"已替换 {path} 中的 {count} 处内容"

        with open(path, "w", encoding=encoding, newline="") as handle:
            handle.write(new_text)
        return detail

    def create_directory(self, arguments: Mapping[str, Any]) -> str:
        raw_path = text_argument(arguments, "path", required=True)
        path = resolve_path(raw_path)
        if os.path.exists(path):
            if os.path.isdir(path):
                return f"目录已存在，无需创建：{path}"
            raise FileExistsError(f"同名文件已存在，无法创建目录：{path}")
        os.makedirs(path)
        return f"已创建目录：{path}"

    def delete_directory(self, arguments: Mapping[str, Any]) -> str:
        raw_path = text_argument(arguments, "path", required=True)
        path = resolve_path(raw_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"目录不存在：{path}")
        if not os.path.isdir(path):
            raise NotADirectoryError(
                f"path 指向文件，deleteDirectory 只能删除目录；"
                f"删除文件请用 deleteFile：{path}"
            )

        file_count = 0
        dir_count = 0
        for _, dirnames, filenames in os.walk(path):
            dir_count += len(dirnames)
            file_count += len(filenames)
        shutil.rmtree(path)
        return f"已删除目录：{path}（其中 {file_count} 个文件、{dir_count} 个子目录）"

    def delete_file(self, arguments: Mapping[str, Any]) -> str:
        """删除文件或目录。

        为保持原有 ``deleteFile`` 只删文件的语义，删除目录必须显式传入
        ``recursive=true``；否则仍返回可操作的错误提示。``deleteDirectory``
        的行为保持不变，仍可直接递归删除目录。
        """
        raw_path = text_argument(arguments, "path", required=True)
        recursive = bool((arguments or {}).get("recursive", False))
        path = resolve_path(raw_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"路径不存在：{path}")

        if os.path.isdir(path):
            if not recursive:
                raise IsADirectoryError(
                    f"path 指向目录：{path}。如确认删除整个目录，"
                    "请传入 recursive=true；也可以使用 deleteDirectory。"
                )
            file_count = 0
            dir_count = 0
            for _, dirnames, filenames in os.walk(path):
                dir_count += len(dirnames)
                file_count += len(filenames)
            shutil.rmtree(path)
            return (
                f"已删除目录：{path}（其中 {file_count} 个文件、"
                f"{dir_count} 个子目录）"
            )

        size = os.path.getsize(path)
        os.remove(path)
        return f"已删除文件：{path}（{human_size(size)}）"

    def move_file(self, arguments: Mapping[str, Any]) -> str:
        """移动或重命名文件/目录（跨磁盘移动时自动复制后删除）。"""
        raw_source = text_argument(arguments, "source", required=True)
        raw_target = text_argument(arguments, "destination", required=True)
        overwrite = bool((arguments or {}).get("overwrite", False))

        source = resolve_path(raw_source)
        if not os.path.exists(source):
            raise FileNotFoundError(f"源路径不存在：{source}")

        target = resolve_path(raw_target)
        # destination 指向已存在目录时，表示“移动到该目录下”，保留原文件名。
        if os.path.isdir(target):
            target = os.path.join(target, os.path.basename(source))
        target = os.path.abspath(target)

        if os.path.normcase(source) == os.path.normcase(target):
            raise ValueError("源路径与目标路径相同，无需移动")
        if os.path.exists(target):
            if not overwrite:
                raise FileExistsError(
                    f"目标已存在，拒绝覆盖：{target}（如需覆盖请传 overwrite=true）"
                )
            if os.path.isdir(target):
                raise IsADirectoryError(
                    f"目标是目录，moveFile 不会覆盖目录：{target}"
                )

        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)

        is_dir = os.path.isdir(source)
        shutil.move(source, target)
        action = "目录" if is_dir else "文件"
        return f"已移动{action}：{source} -> {target}"

    def rename_path(self, arguments: Mapping[str, Any]) -> str:
        raw_path = text_argument(arguments, "path", required=True)
        new_name = text_argument(arguments, "new_name", required=True)
        path = resolve_path(raw_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"路径不存在：{path}")

        has_separator = (
            os.path.isabs(new_name)
            or "/" in new_name
            or "\\" in new_name
            or bool(os.path.dirname(new_name))
        )
        target = (
            resolve_path(new_name)
            if has_separator
            else os.path.join(os.path.dirname(path), new_name)
        )
        target = os.path.abspath(target)

        if os.path.normcase(target) == os.path.normcase(path):
            raise ValueError("新名称与原名称相同")
        if os.path.exists(target):
            raise FileExistsError(f"目标已存在，拒绝覆盖：{target}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        is_dir = os.path.isdir(path)
        os.rename(path, target)
        return f"已将{'目录' if is_dir else '文件'}重命名：{path} -> {target}"

    # ------------------------------------------------------------------ 工具声明

    def specs(self) -> list[ToolSpec]:
        """生成 11 个文件工具的声明（名称、Schema、执行函数、追问示例）。"""
        return [
            ToolSpec(
                name="getCurrentDirectory",
                description="获取当前工作目录（进程的当前目录）的绝对路径",
                parameters=object_schema(),
                handler=self.get_current_directory,
                example_arguments={},
            ),
            ToolSpec(
                name="listDirectory",
                description="列出指定目录下的所有文件和子目录，包含大小与修改时间",
                parameters=object_schema(
                    properties={
                        "path": {
                            "type": "string",
                            "description": "要列出的目录路径，相对路径以当前工作目录为基准；省略时为当前目录",
                        },
                        "show_hidden": {
                            "type": "boolean",
                            "description": "是否显示以点开头的隐藏文件/目录，默认 false",
                        },
                    },
                    required=[],
                ),
                handler=self.list_directory,
                example_arguments={"path": "."},
            ),
            ToolSpec(
                name="getFileType",
                description="获取指定文件的类型（扩展名、MIME 类型、文本或二进制）",
                parameters=object_schema(
                    properties={
                        "path": {
                            "type": "string",
                            "description": "文件路径，相对路径以当前工作目录为基准",
                        }
                    },
                    required=["path"],
                ),
                handler=self.get_file_type,
                example_arguments={"path": "README.md"},
            ),
            ToolSpec(
                name="readTextFile",
                description="读取纯文本文件的内容（仅支持纯文本，二进制文件会被拒绝）",
                parameters=object_schema(
                    properties={
                        "path": {
                            "type": "string",
                            "description": "要读取的纯文本文件路径",
                        }
                    },
                    required=["path"],
                ),
                handler=self.read_text_file,
                example_arguments={"path": "README.md"},
            ),
            ToolSpec(
                name="createFile",
                description="在指定目录下创建新文件，目录不存在时会自动创建（含多级目录）",
                parameters=object_schema(
                    properties={
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
                    required=["filename"],
                ),
                handler=self.create_file,
                example_arguments={
                    "directory": ".",
                    "filename": "new.txt",
                    "content": "",
                },
            ),
            ToolSpec(
                name="editFile",
                description="编辑纯文本文件内容，支持覆盖、追加、替换三种模式（仅支持纯文本）",
                parameters=object_schema(
                    properties={
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
                    required=["path"],
                ),
                handler=self.edit_file,
                example_arguments={
                    "path": "new.txt",
                    "content": "新的内容",
                    "mode": "overwrite",
                },
            ),
            ToolSpec(
                name="createDirectory",
                description="创建新目录（支持多级目录，父目录不存在时自动创建）",
                parameters=object_schema(
                    properties={
                        "path": {
                            "type": "string",
                            "description": "要创建的目录路径",
                        }
                    },
                    required=["path"],
                ),
                handler=self.create_directory,
                example_arguments={"path": "new_folder"},
            ),
            ToolSpec(
                name="deleteDirectory",
                description="删除目录；支持删除非空目录（递归删除其中所有内容），请谨慎使用",
                parameters=object_schema(
                    properties={
                        "path": {
                            "type": "string",
                            "description": "要删除的目录路径",
                        }
                    },
                    required=["path"],
                ),
                handler=self.delete_directory,
                example_arguments={"path": "test_dir"},
            ),
            ToolSpec(
                name="deleteFile",
                description=(
                    "删除指定文件；也可以删除目录，但删除目录必须显式传 "
                    "recursive=true（普通文件请写到具体文件名，例如 test.md）"
                ),
                parameters=object_schema(
                    properties={
                        "path": {
                            "type": "string",
                            "description": "要删除的文件或目录路径",
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "path 是目录时是否递归删除其中全部内容，默认 false",
                        },
                    },
                    required=["path"],
                ),
                handler=self.delete_file,
                example_arguments={"path": "test.txt"},
            ),
            ToolSpec(
                name="moveFile",
                description=(
                    "移动或重命名文件/目录；目标目录不存在时自动创建，"
                    "跨磁盘移动会自动复制后删除"
                ),
                parameters=object_schema(
                    properties={
                        "source": {
                            "type": "string",
                            "description": "源文件或目录路径",
                        },
                        "destination": {
                            "type": "string",
                            "description": (
                                "目标路径；如果已存在同名目录，则移动到该目录下；"
                                "只写新名称时表示在原目录内重命名"
                            ),
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "目标文件已存在时是否覆盖，默认 false",
                        },
                    },
                    required=["source", "destination"],
                ),
                handler=self.move_file,
                example_arguments={
                    "source": "old.txt",
                    "destination": "archive/old.txt",
                },
            ),
            ToolSpec(
                name="renamePath",
                description="重命名文件或目录；new_name 只写名称时在原目录下改名，也可给出新路径",
                parameters=object_schema(
                    properties={
                        "path": {
                            "type": "string",
                            "description": "原文件或目录路径",
                        },
                        "new_name": {
                            "type": "string",
                            "description": "新名称或新路径；只写名称时表示在原目录下重命名",
                        },
                    },
                    required=["path", "new_name"],
                ),
                handler=self.rename_path,
                example_arguments={"path": "old.txt", "new_name": "new.txt"},
            ),
        ]
