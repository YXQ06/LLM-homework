"""Skill 发现、System Prompt 注入与 loadSkill 工具测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xAgent.core.agent import Agent
from xAgent.core.config import AgentConfig
from xAgent.core.errors import ToolError
from xAgent.core.prompts import build_system_prompt
from xAgent.core.skills import SkillRegistry, find_skills_root
from xAgent.core.tools import build_default_registry


class SkillTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.project = Path(self._temp.name)
        self.skills_root = (
            self.project / "tools_skills" / ".agents" / "skills"
        )
        self._write_skill(
            self.skills_root / "alpha",
            name="alpha",
            description=(
                "Alpha skill for writing notices.\nIt has a reference file."
            ),
            body="# Alpha\n\nFULL-BODY-MARKER\n",
        )
        (self.skills_root / "alpha" / "references").mkdir(parents=True)
        (self.skills_root / "alpha" / "references" / "guide.md").write_text(
            "guide content", encoding="utf-8"
        )
        (self.skills_root / "alpha" / "references" / "nested").mkdir()
        (self.skills_root / "alpha" / "references" / "nested" / "guide.md").write_text(
            "nested guide", encoding="utf-8"
        )

        self._write_skill(
            self.skills_root / "beta",
            name="beta",
            description="Beta skill.",
            body="# Beta\n",
        )
        self._write_skill(
            self.skills_root / "invalid",
            name="",
            description="",
            body="# Invalid\n",
            raw="---\nversion: 1\n---\n# No name\n",
        )

    def _write_skill(
        self,
        directory: Path,
        *,
        name: str,
        description: str,
        body: str,
        raw: str | None = None,
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        if raw is None:
            if "\n" in description:
                described = "description: |\n" + "".join(
                    f"  {line}\n" for line in description.splitlines()
                )
            else:
                described = f'description: "{description}"\n'
            raw = (
                "---\n"
                f"name: {name}\n"
                f"{described}"
                "---\n"
                f"{body}"
            )
        (directory / "SKILL.md").write_text(raw, encoding="utf-8")


class SkillDiscoveryTest(SkillTestBase):
    def test_finds_skills_relative_to_project_root(self) -> None:
        with patch.dict(os.environ, {"XAGENT_SKILLS_DIR": ""}):
            root = find_skills_root(self.project)

        self.assertEqual(root, self.skills_root.resolve())
        self.assertEqual(root.name, "skills")

    def test_only_name_and_description_are_loaded(self) -> None:
        registry = SkillRegistry(
            project_root=self.project, max_read_bytes=200_000
        )
        self.assertEqual(registry.names, ("alpha", "beta"))
        self.assertEqual(registry.get("alpha").description, "Alpha skill for writing notices. It has a reference file.")
        self.assertNotIn("FULL-BODY-MARKER", registry.get("alpha").description)
        self.assertTrue(any("invalid" in warning for warning in registry.warnings))

    def test_block_description_and_nested_skill_are_supported(self) -> None:
        nested = self.skills_root / "alpha" / "nested-skill"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text(
            "---\n"
            "name: nested\n"
            "description: >\n"
            "  Folded description\n"
            "  on two lines.\n"
            "---\n"
            "# Nested\n",
            encoding="utf-8",
        )
        registry = SkillRegistry(root=self.skills_root, project_root=self.project)
        self.assertIn("nested", registry.names)
        self.assertEqual(
            registry.get("nested").description,
            "Folded description on two lines.",
        )

    def test_system_prompt_contains_skill_information(self) -> None:
        registry = SkillRegistry(root=self.skills_root, project_root=self.project)
        tools = build_default_registry(AgentConfig())
        prompt = build_system_prompt(tools, registry)
        self.assertIn("You have access to the following skills:", prompt)
        self.assertIn("- `alpha`: Alpha skill for writing notices.", prompt)
        self.assertIn("- `beta`: Beta skill.", prompt)
        self.assertIn("loadSkill", prompt)

    def test_agent_registers_load_skill_and_injects_prompt(self) -> None:
        tools = build_default_registry(AgentConfig())
        agent = Agent(
            object(),  # type: ignore[arg-type]
            tools,
            AgentConfig(),
            project_root=str(self.project),
        )
        self.assertIn("loadSkill", tools.names)
        self.assertTrue(
            any(
                tool["function"]["name"] == "loadSkill"
                for tool in tools.openai_tools()
            )
        )
        self.assertEqual(agent.skills.names, ("alpha", "beta"))
        self.assertIn("`alpha`", agent.system_prompt)
        self.assertIn("`beta`", agent.system_prompt)
        self.assertNotIn("FULL-BODY-MARKER", agent.system_prompt)


class LoadSkillTest(SkillTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.registry = SkillRegistry(
            root=self.skills_root, project_root=self.project
        )

    def test_loads_skill_md_when_only_name_is_given(self) -> None:
        result = self.registry.load_skill({"name": "alpha"})
        self.assertIn("Skill：alpha", result)
        self.assertIn("FULL-BODY-MARKER", result)
        self.assertIn("---", result)

        tool_result = self.registry.tool_spec().handler({"name": "alpha"})
        self.assertIn("FULL-BODY-MARKER", tool_result)

    def test_loads_attachment_relative_to_skill_directory(self) -> None:
        result = self.registry.load_skill(
            {"name": "alpha", "filename": "references/guide.md"}
        )
        self.assertIn("附件：references/guide.md", result)
        self.assertIn("guide content", result)
        self.assertNotIn("FULL-BODY-MARKER", result)

    def test_loads_attachment_by_project_relative_path(self) -> None:
        relative = (
            self.skills_root
            / "alpha"
            / "references"
            / "guide.md"
        ).relative_to(self.project)
        result = self.registry.load_skill(
            {"name": "alpha", "filename": str(relative)}
        )
        self.assertIn("guide content", result)

    def test_loads_attachment_by_absolute_path(self) -> None:
        absolute = self.skills_root / "alpha" / "references" / "guide.md"
        result = self.registry.load_skill(
            {"name": "alpha", "filename": str(absolute)}
        )
        self.assertIn("guide content", result)

    def test_bare_filename_is_found_recursively_when_unique(self) -> None:
        unique = self.skills_root / "alpha" / "references" / "nested" / "unique.md"
        unique.write_text("unique attachment", encoding="utf-8")
        result = self.registry.load_skill(
            {"name": "alpha", "filename": "unique.md"}
        )
        self.assertIn("unique attachment", result)
        self.assertIn("附件：references/nested/unique.md", result)

    def test_duplicate_bare_filenames_return_candidates(self) -> None:
        result = self.registry.load_skill(
            {"name": "alpha", "filename": "guide.md"}
        )
        self.assertIn("多个", result)
        self.assertIn("references/guide.md", result)
        self.assertIn("references/nested/guide.md", result)
        self.assertNotIn("guide content", result)

    def test_unknown_skill_returns_available_names(self) -> None:
        with self.assertRaises(ToolError) as context:
            self.registry.load_skill({"name": "missing"})
        self.assertIn("alpha", str(context.exception))
        self.assertIn("beta", str(context.exception))

    def test_relative_traversal_is_blocked(self) -> None:
        with self.assertRaises(ToolError):
            self.registry.load_skill(
                {"name": "alpha", "filename": "../beta/SKILL.md"}
            )

    def test_absolute_path_outside_skill_is_blocked(self) -> None:
        outside = self.skills_root / "beta" / "SKILL.md"
        with self.assertRaises(ToolError) as context:
            self.registry.load_skill(
                {"name": "alpha", "filename": str(outside)}
            )
        self.assertIn("目录之外", str(context.exception))


class CreateSkillTest(SkillTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.registry = SkillRegistry(
            root=self.skills_root, project_root=self.project
        )

    def test_creates_skill_and_generates_front_matter(self) -> None:
        result = self.registry.create_skill(
            {
                "name": "My New Skill",
                "skillContext": "# My New Skill\n\nDo something useful.",
            }
        )
        target = self.skills_root / "my-new-skill" / "SKILL.md"
        self.assertTrue(target.is_file())
        text = target.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("name: my-new-skill", text)
        self.assertIn("description:", text)
        self.assertIn("# My New Skill", text)
        self.assertIn("已创建 Skill：my-new-skill", result)
        self.assertIn("my-new-skill", self.registry.names)
        self.assertEqual(
            self.registry.get("my-new-skill").description, "My New Skill"
        )
        # 新建后可以立即通过 loadSkill 读取
        self.assertIn(
            "Do something useful.",
            self.registry.load_skill({"name": "my-new-skill"}),
        )

    def test_keeps_provided_front_matter_and_forces_directory_name(self) -> None:
        context = (
            "---\n"
            "name: Old Name\n"
            "description: 已有描述\n"
            'version: "1.0"\n'
            "---\n\n"
            "# 正文\n"
            "步骤一。\n"
        )
        self.registry.create_skill(
            {"name": "custom-skill", "skillContext": context}
        )
        text = (
            self.skills_root / "custom-skill" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("name: custom-skill", text)
        self.assertNotIn("name: Old Name", text)
        self.assertIn("description: 已有描述", text)
        self.assertIn('version: "1.0"', text)
        self.assertIn("步骤一。", text)
        self.assertEqual(
            self.registry.get("custom-skill").description, "已有描述"
        )

    def test_existing_skill_is_never_overwritten(self) -> None:
        original = (
            self.skills_root / "alpha" / "SKILL.md"
        ).read_text(encoding="utf-8")
        with self.assertRaises(ToolError) as context:
            self.registry.create_skill(
                {"name": "alpha", "skillContext": "# Replaced\n"}
            )
        self.assertIn("已存在", str(context.exception))
        self.assertIn("未做任何修改", str(context.exception))
        self.assertEqual(
            (self.skills_root / "alpha" / "SKILL.md").read_text(
                encoding="utf-8"
            ),
            original,
        )

    def test_duplicate_create_returns_existing_message(self) -> None:
        first = {"name": "repeat-skill", "skillContext": "# First\n"}
        self.registry.create_skill(first)
        with self.assertRaises(ToolError):
            self.registry.create_skill(first)

    def test_invalid_name_is_rejected(self) -> None:
        with self.assertRaises(ToolError):
            self.registry.create_skill(
                {"name": "中文名称", "skillContext": "# 正文\n"}
            )

    def test_creates_default_agents_skills_directory(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            fresh = Path(other)
            with patch.dict(os.environ, {"XAGENT_SKILLS_DIR": ""}):
                registry = SkillRegistry(project_root=fresh)
            self.assertIsNone(registry.root)
            registry.create_skill(
                {"name": "demo-skill", "skillContext": "# Demo\n\nBody.\n"}
            )
            expected = fresh / ".agents" / "skills" / "demo-skill" / "SKILL.md"
            self.assertTrue(expected.is_file())
            self.assertEqual(
                registry.creation_root, fresh / ".agents" / "skills"
            )

    def test_agent_registers_create_skill_tool(self) -> None:
        tools = build_default_registry(AgentConfig())
        Agent(
            object(),  # type: ignore[arg-type]
            tools,
            AgentConfig(),
            project_root=str(self.project),
        )
        self.assertIn("createSkill", tools.names)
        spec = tools.get("createSkill")
        self.assertIsNotNone(spec)
        self.assertEqual(
            set(spec.required_parameters), {"name", "skillContext"}
        )


if __name__ == "__main__":
    unittest.main()
