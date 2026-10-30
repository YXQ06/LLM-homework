"""Agent Skills 发现与动态加载。

设计要点：
* 项目根目录取程序启动时的用户工作目录（``os.getcwd()``），不使用 Python
  安装目录，也不使用 xAgent 代码所在目录；
* 初始化阶段只读取各 ``SKILL.md`` 的 YAML Frontmatter（name / description），
  不读取正文；
* 完整内容只在模型调用 ``loadSkill`` 时读取，避免一次性占用上下文；
* 附件读取被限制在 Skill 目录内部，防止路径穿越。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .errors import ToolError
from .tools.base import ToolSpec, object_schema, text_argument
from .utils.text import decode_text, human_size, looks_binary

SKILL_FILENAME = "SKILL.md"
SKILLS_DIR_ENV = "XAGENT_SKILLS_DIR"

# 从项目根目录向上逐级查找；同时兼容标准布局与当前仓库的 tools_skills 布局。
_SKILLS_SUBDIRS: tuple[tuple[str, ...], ...] = (
    (".agents", "skills"),
    ("tools_skills", ".agents", "skills"),
)

_EXCLUDED_DIRS = {
    "__pycache__",
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    "venv",
    ".venv",
    "env",
}

_FRONT_MATTER_LIMIT = 256_000
_FRONT_MATTER_KEY = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$")


@dataclass(frozen=True)
class Skill:
    """一个已发现的 Skill（初始化阶段只保存 name 与 description）。"""

    name: str
    description: str
    directory: Path
    file: Path

    @property
    def relative_file(self) -> str:
        try:
            return str(self.file.relative_to(self.directory)).replace("\\", "/")
        except ValueError:
            return self.file.name


def find_skills_root(
    project_root: str | os.PathLike[str] | None = None,
) -> Path | None:
    """定位 Skill 根目录。

    优先使用 ``XAGENT_SKILLS_DIR``；否则从项目根目录开始向上查找
    ``.agents/skills`` 或 ``tools_skills/.agents/skills``。项目根目录默认取
    程序启动时的当前工作目录。
    """
    explicit = os.environ.get(SKILLS_DIR_ENV)
    if explicit:
        candidate = Path(
            os.path.expandvars(os.path.expanduser(explicit))
        ).expanduser()
        try:
            candidate = candidate.resolve()
        except OSError:
            return None
        return candidate if candidate.is_dir() else None

    root = Path(project_root or os.getcwd()).expanduser()
    try:
        root = root.resolve()
    except OSError:
        return None

    for base in (root, *root.parents):
        for parts in _SKILLS_SUBDIRS:
            candidate = base.joinpath(*parts)
            if candidate.is_dir():
                return candidate
    return None


def _parse_scalar(raw: str) -> str:
    """解析简单的 YAML 标量（普通字符串、单引号、双引号）。"""
    raw = raw.strip()
    if not raw:
        return ""
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        try:
            return str(json.loads(raw))
        except (TypeError, ValueError):
            return raw[1:-1]
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    # 去掉行尾注释，但保留 URL 中的 #。
    return re.sub(r"\s+#.*$", "", raw).strip()


def _parse_front_matter(block: str) -> dict[str, str]:
    """解析 Frontmatter 的顶层键值，支持 ``|`` / ``>`` 多行标量。"""
    result: dict[str, str] = {}
    lines = block.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[:1].isspace():
            index += 1
            continue

        match = _FRONT_MATTER_KEY.match(line)
        if match is None:
            index += 1
            continue

        key = match.group(1).lower()
        raw_value = match.group(2).strip()

        if raw_value in ("|", "|-", "|+", ">", ">-", ">+"):
            style = raw_value[0]
            index += 1
            collected: list[str] = []
            while index < len(lines):
                current = lines[index]
                if current.strip() and not current[:1].isspace():
                    break
                collected.append(current)
                index += 1

            while collected and not collected[-1].strip():
                collected.pop()
            indents = [
                len(current) - len(current.lstrip(" "))
                for current in collected
                if current.strip()
            ]
            indent = min(indents) if indents else 0
            dedented = [
                current[indent:] if current.strip() else ""
                for current in collected
            ]
            if style == "|":
                value = "\n".join(dedented)
            else:
                # folded：普通换行折叠为空格，空行保留为段落分隔。
                paragraphs: list[str] = []
                current_para: list[str] = []
                for current in dedented:
                    if current.strip():
                        current_para.append(current.strip())
                    else:
                        if current_para:
                            paragraphs.append(" ".join(current_para))
                            current_para = []
                        paragraphs.append("")
                if current_para:
                    paragraphs.append(" ".join(current_para))
                value = "\n".join(paragraphs)
            result[key] = value.strip()
            continue

        result[key] = _parse_scalar(raw_value)
        index += 1

    return result


def _read_front_matter(path: Path) -> dict[str, str] | None:
    """只读取 ``SKILL.md`` 开头的 Frontmatter，不读取完整正文。"""
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            first_line = handle.readline()
            if first_line.strip() != "---":
                return None

            collected: list[str] = []
            consumed = 0
            while consumed < _FRONT_MATTER_LIMIT:
                line = handle.readline()
                if not line:
                    return None
                if line.strip() in ("---", "..."):
                    return _parse_front_matter("".join(collected))
                collected.append(line)
                consumed += len(line)
    except OSError:
        return None
    return None


def _iter_skill_files(root: Path) -> Iterator[Path]:
    """递归查找 Skill 目录中的 ``SKILL.md``。"""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _EXCLUDED_DIRS and not name.startswith(".")
        )
        for filename in filenames:
            if filename.lower() == SKILL_FILENAME.lower():
                found.append(Path(dirpath) / filename)

    found.sort(
        key=lambda path: (
            len(path.relative_to(root).parts),
            str(path).lower(),
        )
    )
    return iter(found)


def _flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


@dataclass(frozen=True)
class _AttachmentLookup:
    path: Path | None = None
    candidates: tuple[Path, ...] = ()


class SkillRegistry:
    """Skill 注册表：负责发现、提示词片段生成和 ``loadSkill`` 工具执行。"""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        project_root: str | os.PathLike[str] | None = None,
        skills: Iterable[Skill] | None = None,
        max_read_bytes: int = 200_000,
    ) -> None:
        self.project_root = Path(project_root or os.getcwd()).expanduser()
        try:
            self.project_root = self.project_root.resolve()
        except OSError:
            pass

        if root is not None:
            candidate = Path(root).expanduser()
            try:
                candidate = candidate.resolve()
            except OSError:
                pass
            self.root: Path | None = candidate if candidate.is_dir() else None
        else:
            self.root = find_skills_root(self.project_root)

        self.max_read_bytes = max(1, int(max_read_bytes))
        self._skills: dict[str, Skill] = {}
        self.warnings: list[str] = []

        if skills is not None:
            for skill in skills:
                self.register(skill)
        elif self.root is not None:
            self._discover(self.root)

    # ------------------------------------------------------------------ 发现与注册

    def _discover(self, root: Path) -> None:
        for skill_file in _iter_skill_files(root):
            metadata = _read_front_matter(skill_file)
            if metadata is None:
                self.warnings.append(
                    f"跳过 {skill_file}：缺少合法的 YAML Frontmatter"
                )
                continue

            name = _flatten(metadata.get("name", ""))
            description = _flatten(metadata.get("description", ""))
            if not name or not description:
                self.warnings.append(
                    f"跳过 {skill_file}：Frontmatter 必须包含 name 和 description"
                )
                continue

            self.register(
                Skill(
                    name=name,
                    description=description,
                    directory=skill_file.parent.resolve(),
                    file=skill_file.resolve(),
                )
            )

    def register(self, skill: Skill) -> None:
        if not skill.name or not skill.description:
            self.warnings.append(
                f"跳过 {skill.file}：name 和 description 不能为空"
            )
            return
        if skill.name in self._skills:
            self.warnings.append(
                f"跳过重复 Skill：{skill.name}（{skill.file}）"
            )
            return
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    @property
    def skills(self) -> tuple[Skill, ...]:
        return tuple(self._skills.values())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._skills)

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: object) -> bool:
        return name in self._skills

    def __iter__(self) -> Iterator[Skill]:
        return iter(self._skills.values())

    # ------------------------------------------------------------------ 工具声明与执行

    def tool_spec(self) -> ToolSpec:
        """生成 ``loadSkill`` 的 Function Calling 声明。"""
        return ToolSpec(
            name="loadSkill",
            description=(
                "加载已注册 Skill 的完整说明或附件文件。只传 name 时读取该 Skill 的 "
                "SKILL.md 全文；同时传 filename 时读取该 Skill 目录内的附件，不再重复"
                "加载 SKILL.md。filename 支持相对 Skill 目录的路径、项目相对路径和绝对"
                "路径；只写文件名时会递归查找，若存在多个同名文件则返回候选路径"
            ),
            parameters=object_schema(
                properties={
                    "name": {
                        "type": "string",
                        "description": "Skill 名称，必须是系统提示词中列出的名称",
                    },
                    "filename": {
                        "type": "string",
                        "description": (
                            "可选的附件文件路径或文件名，例如 references/signs/aries.md；"
                            "省略时加载 SKILL.md 全文"
                        ),
                    },
                },
                required=["name"],
            ),
            handler=self.load_skill,
            example_arguments={
                "name": "horoscope-daily",
                "filename": "references/signs/aries.md",
            },
        )

    def load_skill(self, arguments: Mapping[str, Any]) -> str:
        name = text_argument(arguments, "name", required=True)
        filename = text_argument(arguments, "filename", default="")

        skill = self.get(name)
        if skill is None:
            available = "、".join(self.names) or "（当前没有发现任何 Skill）"
            raise ToolError(
                f"未知 Skill：{name}。可用 Skill：{available}"
            )

        if not filename:
            return self._read_file(skill, skill.file)

        lookup = self._locate_attachment(skill, filename)
        if lookup.path is not None:
            return self._read_file(skill, lookup.path)

        if lookup.candidates:
            lines = [
                f"Skill `{skill.name}` 中存在多个名为 `{filename}` 的文件，"
                "请用 filename 指定以下相对路径之一："
            ]
            for candidate in lookup.candidates:
                lines.append(
                    f"- {candidate.relative_to(skill.directory).as_posix()}"
                )
            return "\n".join(lines)

        raise ToolError(
            f"未找到 Skill `{skill.name}` 的附件：{filename}。"
            f"读取范围限制在 Skill 目录内：{skill.directory}"
        )

    # ------------------------------------------------------------------ 附件定位

    def _locate_attachment(self, skill: Skill, raw: str) -> _AttachmentLookup:
        expanded = os.path.expandvars(os.path.expanduser(raw.strip()))
        if not expanded:
            raise ToolError("filename 不能为空")

        candidates: list[Path] = []
        if os.path.isabs(expanded):
            candidates.append(Path(expanded))
        else:
            candidates.append(skill.directory / expanded)
            candidates.append(self.project_root / expanded)

        seen: set[str] = set()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            key = os.path.normcase(str(resolved))
            if key in seen:
                continue
            seen.add(key)

            if not self._within_skill(skill, resolved):
                continue
            if resolved.is_file():
                return _AttachmentLookup(path=resolved)

        if os.path.isabs(expanded):
            raise ToolError(
                f"拒绝读取 Skill `{skill.name}` 目录之外的文件：{expanded}"
            )

        # 只提供文件名（不含路径分隔符）时，在该 Skill 目录内递归查找。
        if "/" not in expanded and "\\" not in expanded:
            matches: list[Path] = []
            target = expanded.lower()
            try:
                for current in skill.directory.rglob("*"):
                    if not current.is_file():
                        continue
                    if current.name.lower() != target:
                        continue
                    resolved = current.resolve()
                    if not self._within_skill(skill, resolved):
                        continue
                    if os.path.normcase(str(resolved)) not in {
                        os.path.normcase(str(item)) for item in matches
                    }:
                        matches.append(resolved)
            except OSError:
                matches = []

            matches.sort(key=lambda path: str(path).lower())
            if len(matches) == 1:
                return _AttachmentLookup(path=matches[0])
            if len(matches) > 1:
                return _AttachmentLookup(
                    candidates=tuple(matches)
                )

        return _AttachmentLookup()

    @staticmethod
    def _within_skill(skill: Skill, candidate: Path) -> bool:
        """判断候选路径是否位于指定 Skill 目录内部。"""
        try:
            directory = skill.directory.resolve()
            candidate = candidate.resolve()
            common = Path(
                os.path.commonpath([str(directory), str(candidate)])
            )
            return os.path.normcase(str(common)) == os.path.normcase(
                str(directory)
            )
        except (OSError, ValueError):
            return False

    # ------------------------------------------------------------------ 文件读取

    def _read_file(self, skill: Skill, path: Path) -> str:
        if not self._within_skill(skill, path):
            raise ToolError(
                f"拒绝读取 Skill `{skill.name}` 目录之外的文件：{path}"
            )
        if not path.is_file():
            raise ToolError(f"文件不存在或不是普通文件：{path}")

        size = path.stat().st_size
        with path.open("rb") as handle:
            data = handle.read(self.max_read_bytes + 1)
        truncated = len(data) > self.max_read_bytes or size > self.max_read_bytes
        data = data[: self.max_read_bytes]

        if looks_binary(data):
            raise ToolError(f"不是纯文本文件，拒绝读取：{path}")

        text, encoding = decode_text(data, strict=False)
        relative = path.relative_to(skill.directory).as_posix()

        if path == skill.file:
            header = (
                f"Skill：{skill.name}\n"
                f"描述：{_flatten(skill.description)}\n"
                f"文件：{relative}\n"
                f"完整路径：{path}\n"
                f"编码：{encoding}；大小：{human_size(size)}"
            )
        else:
            header = (
                f"Skill：{skill.name}\n"
                f"附件：{relative}\n"
                f"完整路径：{path}\n"
                f"编码：{encoding}；大小：{human_size(size)}"
            )
        if truncated:
            header += (
                f"（文件过大，仅读取前 {self.max_read_bytes} 字节）"
            )

        return (
            header
            + "\n--- 内容开始 ---\n"
            + text
            + "\n--- 内容结束 ---"
        )


def build_skills_prompt(
    skills: SkillRegistry | Iterable[Skill] | None,
) -> str:
    """把 Skill 的 name / description 整理成可注入 System Prompt 的片段。"""
    if skills is None:
        return ""
    if isinstance(skills, SkillRegistry):
        items = skills.skills
    else:
        items = tuple(skills)
    if not items:
        return ""

    lines = ["You have access to the following skills:"]
    for skill in items:
        lines.append(f"- `{skill.name}`: {_flatten(skill.description)}")
    lines.append(
        "Use the `loadSkill` tool to load the full instructions of a skill when needed. "
        "Pass `name` only to read its SKILL.md. Pass `filename` as well to load an "
        "attachment referenced by the skill (for example `references/example.md`). "
        "You can load additional files referenced by a skill in the same way."
    )
    return "\n".join(lines)


__all__ = [
    "SKILLS_DIR_ENV",
    "SKILL_FILENAME",
    "Skill",
    "SkillRegistry",
    "build_skills_prompt",
    "find_skills_root",
]
