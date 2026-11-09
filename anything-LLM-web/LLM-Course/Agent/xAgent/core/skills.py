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
_DEFAULT_SKILLS_PARTS: tuple[str, ...] = (".agents", "skills")

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
_FRONT_MATTER_BLOCK = re.compile(
    r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)(.*)$", re.DOTALL
)
# Agent Skills 规范：name 只能由小写字母、数字和连字符组成。
_SKILL_NAME_SEPARATOR = re.compile(r"[^a-z0-9]+")
_SKILL_NAME_HYPHENS = re.compile(r"-{2,}")
_SKILL_NAME_MAX_LENGTH = 64
_SKILL_DESCRIPTION_MAX_LENGTH = 1024
_DESCRIPTION_PREFIX = re.compile(r"^(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)")


def normalize_skill_name(raw: str) -> str:
    """把用户给出的名称规范化成 Agent Skills 目录名。

    统一转换为小写字母、数字和连字符，例如 ``My New Skill`` →
    ``my-new-skill``。无法转换（例如纯中文）时抛出 ``ToolError``，让模型
    明确改用英文名称，而不是生成一个不可用的目录。
    """
    value = (raw or "").strip().lower()
    if not value:
        raise ToolError("name 不能为空")

    normalized = _SKILL_NAME_SEPARATOR.sub("-", value)
    normalized = _SKILL_NAME_HYPHENS.sub("-", normalized).strip("-")
    if not normalized:
        raise ToolError(
            f"Skill 名称无法转换为小写字母、数字和连字符：{raw!r}。"
            "请使用英文名称，例如 my-new-skill"
        )
    if len(normalized) > _SKILL_NAME_MAX_LENGTH:
        raise ToolError(
            f"Skill 名称过长（{len(normalized)} 个字符），"
            f"最多 {_SKILL_NAME_MAX_LENGTH} 个字符：{normalized}"
        )
    return normalized


def _yaml_quote(value: str) -> str:
    """用双引号 YAML 标量输出字符串（JSON 字符串即合法的 YAML 标量）。"""
    return json.dumps(value, ensure_ascii=False)


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _derive_description(body: str, fallback: str) -> str:
    """从正文的第一段有效文字推导 description。"""
    for line in (body or "").splitlines():
        text = line.strip()
        if (
            not text
            or text in ("---", "...")
            or text.startswith(("```", "~~~", "<!--"))
        ):
            continue
        text = _DESCRIPTION_PREFIX.sub("", text)
        text = text.strip("*_`> ").strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            return _truncate(text, _SKILL_DESCRIPTION_MAX_LENGTH)
    return _truncate(f"{fallback} skill", _SKILL_DESCRIPTION_MAX_LENGTH)


def _set_front_matter_scalar(block: str, key: str, value: str) -> str:
    """替换或插入 Frontmatter 的顶层标量，保留其他键与注释。

    ``value`` 必须是已经格式化好的 YAML 标量（例如经过 ``_yaml_quote``）。
    如果旧值是 ``|`` / ``>`` 多行标量，会连同其缩进内容一起替换。
    """
    lines = block.splitlines()
    pattern = re.compile(rf"^{re.escape(key)}\s*:\s*(.*)$", re.IGNORECASE)
    result: list[str] = []
    replaced = False
    index = 0

    while index < len(lines):
        line = lines[index]
        match = pattern.match(line)
        if match is not None and not replaced:
            result.append(f"{key}: {value}")
            replaced = True
            index += 1
            # 跳过旧值可能附带的多行标量（缩进行，或空行后仍为缩进行）。
            while index < len(lines):
                current = lines[index]
                if current.strip() and not current[:1].isspace():
                    break
                if not current.strip():
                    lookahead = index
                    while lookahead < len(lines) and not lines[lookahead].strip():
                        lookahead += 1
                    if lookahead < len(lines) and lines[lookahead][:1].isspace():
                        index = lookahead
                        continue
                    break
                index += 1
            continue

        result.append(line)
        index += 1

    if not replaced:
        result.insert(0, f"{key}: {value}")
    return "\n".join(result)


def compose_skill_markdown(name: str, skill_context: str) -> tuple[str, str]:
    """把 ``skillContext`` 规范化为合法的 ``SKILL.md``。

    返回 ``(完整 Markdown, description)``。如果模型已经给出 Frontmatter，
    则保留其中的其他字段，只强制 ``name`` 与目录名一致，并在缺少
    ``description`` 时自动补充。
    """
    text = (skill_context or "").lstrip("\ufeff").strip()
    if not text:
        raise ToolError(
            "skillContext 不能为空：请提供要写入 SKILL.md 的完整内容"
            "（YAML Frontmatter 与正文）"
        )

    match = _FRONT_MATTER_BLOCK.match(text)
    if match is not None:
        front_matter = match.group(1)
        body = match.group(2).strip()
        metadata = _parse_front_matter(front_matter)
        description = _flatten(metadata.get("description", ""))

        if not description:
            description = _derive_description(body, name)
            front_matter = _set_front_matter_scalar(
                front_matter, "description", _yaml_quote(description)
            )
        if _flatten(metadata.get("name", "")) != name:
            front_matter = _set_front_matter_scalar(front_matter, "name", name)
    else:
        body = text
        description = _derive_description(body, name)
        front_matter = (
            f"name: {name}\n"
            f"description: {_yaml_quote(description)}"
        )

    if not body.strip():
        raise ToolError(
            "skillContext 缺少正文：Frontmatter 之后必须包含 Skill 的具体说明"
        )

    markdown = f"---\n{front_matter.strip()}\n---\n\n{body.rstrip()}\n"
    return markdown, description


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

    @property
    def creation_root(self) -> Path:
        """创建新 Skill 时使用的根目录。

        优先沿用已发现的 Skill 根目录；否则使用 ``XAGENT_SKILLS_DIR``；
        最后默认在当前项目的 ``.agents/skills`` 下创建。
        """
        if self.root is not None:
            return self.root

        explicit = os.environ.get(SKILLS_DIR_ENV)
        if explicit:
            candidate = Path(
                os.path.expandvars(os.path.expanduser(explicit))
            ).expanduser()
            try:
                candidate = candidate.resolve()
            except OSError:
                pass
            return candidate

        return self.project_root.joinpath(*_DEFAULT_SKILLS_PARTS)

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

    def create_tool_spec(self) -> ToolSpec:
        """生成 ``createSkill`` 的 Function Calling 声明。"""
        return ToolSpec(
            name="createSkill",
            description=(
                "根据用户要求创建一个新的 Agent Skill。工具会在当前项目的 Skill 根目录"
                "（通常是 .agents/skills）下创建以小写字母、数字和连字符命名的子目录，"
                "并写入符合 Agent Skills 规范的 SKILL.md。skillContext 是 SKILL.md 的"
                "完整内容，可以包含 YAML Frontmatter；若缺少 Frontmatter 或 "
                "description，工具会根据 name 与正文自动补齐。已存在的 Skill 不会被"
                "覆盖。references/、scripts/ 等辅助目录或文件请在创建成功后继续调用 "
                "createDirectory / createFile 等 fileOps 工具完成"
            ),
            parameters=object_schema(
                properties={
                    "name": {
                        "type": "string",
                        "description": (
                            "Skill 名称，例如 my-new-skill；统一规范化为小写字母、"
                            "数字和连字符组成的英文目录名"
                        ),
                    },
                    "skillContext": {
                        "type": "string",
                        "description": (
                            "要写入 SKILL.md 的完整内容，包含 YAML Frontmatter"
                            "（name、description）与正文；如果只给正文，工具会自动"
                            "补充 Frontmatter"
                        ),
                    },
                },
                required=["name", "skillContext"],
            ),
            handler=self.create_skill,
            example_arguments={
                "name": "my-new-skill",
                "skillContext": (
                    "---\n"
                    "name: my-new-skill\n"
                    "description: 一句话说明这个 Skill 的用途\n"
                    "---\n\n"
                    "# 使用步骤\n"
                    "1. ...\n"
                ),
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

    def create_skill(self, arguments: Mapping[str, Any]) -> str:
        """创建新的 Skill 目录与 ``SKILL.md``，不覆盖已存在的 Skill。"""
        raw_name = text_argument(arguments, "name", required=True)
        skill_context = text_argument(
            arguments, "skillContext", required=True
        )
        name = normalize_skill_name(raw_name)

        root = self.creation_root
        target = root / name

        existing = self.get(name)
        existing_dir = self._find_existing_directory(root, name)
        if existing is not None or existing_dir is not None or target.exists():
            location = existing_dir or target
            if existing is not None:
                location = existing.directory
            raise ToolError(
                f"Skill 已存在，未做任何修改：{location}。"
                f"如需查看现有内容请调用 loadSkill（name={name!r}）；"
                "如需修改请先征得用户同意再使用 editFile，createSkill 不会覆盖已有 Skill"
            )

        markdown, description = compose_skill_markdown(name, skill_context)

        try:
            root.mkdir(parents=True, exist_ok=True)
            target.mkdir()
        except FileExistsError as exc:
            raise ToolError(
                f"Skill 已存在，未做任何修改：{target}（可能是并发创建）"
            ) from exc
        except OSError as exc:
            raise ToolError(f"创建 Skill 目录失败：{target}（{exc}）") from exc

        skill_file = target / SKILL_FILENAME
        try:
            with skill_file.open("x", encoding="utf-8", newline="") as handle:
                handle.write(markdown)
        except OSError as exc:
            # 写入失败时清理刚创建的空目录，避免留下半个 Skill。
            try:
                if not any(target.iterdir()):
                    target.rmdir()
            except OSError:
                pass
            raise ToolError(f"写入 SKILL.md 失败：{skill_file}（{exc}）") from exc

        if self.root is None:
            self.root = root

        try:
            resolved_dir = target.resolve()
            resolved_file = skill_file.resolve()
        except OSError:
            resolved_dir, resolved_file = target, skill_file
        self.register(
            Skill(
                name=name,
                description=description,
                directory=resolved_dir,
                file=resolved_file,
            )
        )

        example_file = target / "references" / "example.md"
        lines = [
            f"已创建 Skill：{name}",
            f"目录：{target}",
            f"文件：{skill_file}",
            f"描述：{description}",
            (
                f"写入 {len(markdown)} 个字符"
                f"（{human_size(len(markdown.encode('utf-8')))}，UTF-8 编码）"
            ),
            "SKILL.md 已包含 YAML Frontmatter（name / description）与正文。",
            (
                "如需添加 references/、scripts/ 等辅助目录或文件，"
                "请继续调用 createDirectory / createFile 等 fileOps 工具，"
                f"例如 {example_file}"
            ),
            f"现在可以调用 loadSkill（name={name!r}）加载并检查完整内容。",
        ]
        return "\n".join(lines)

    def _find_existing_directory(
        self, root: Path, name: str
    ) -> Path | None:
        """在创建根目录中按名称查找已存在的目录（忽略大小写）。"""
        if not root.is_dir():
            return None
        target = root / name
        if target.exists():
            return target
        try:
            with os.scandir(root) as iterator:
                for entry in iterator:
                    if entry.is_dir() and entry.name.lower() == name.lower():
                        return Path(entry.path)
        except OSError:
            return None
        return None

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
    "compose_skill_markdown",
    "find_skills_root",
    "normalize_skill_name",
]
