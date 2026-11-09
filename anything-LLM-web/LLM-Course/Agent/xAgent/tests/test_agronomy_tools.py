"""新增能力测试：文件工具、当前时间、AGENT.md 注入与农学统计。

全部测试都在临时目录中完成，不访问网络，也不依赖 scipy / seaborn。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

from xAgent.core.agent import Agent
from xAgent.core.agent_rules import AGENT_RULES_MARKER, load_agent_rules
from xAgent.core.config import AgentConfig, SearchConfig
from xAgent.core.tools import build_default_registry
from xAgent.core.tools.agronomy import AgronomyToolbox
from xAgent.core.tools.files import FileToolbox
from xAgent.core.tools.system import get_current_time, parse_date


class _ScriptedClient:
    """Agent 只会在 chat() 时使用客户端；本测试只检查初始化阶段。"""

    def __init__(self, responses: Sequence[Mapping[str, Any]] | None = None) -> None:
        self.responses = list(responses or [])
        self.last_usage: dict[str, Any] | None = None

    def chat(self, messages, tools=None):  # noqa: ANN001
        return self.responses.pop(0)


class NewFileToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.tools = FileToolbox(AgentConfig())

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_delete_file_only_deletes_files_by_default(self) -> None:
        folder = self.root / "folder"
        folder.mkdir()
        (folder / "data.txt").write_text("x", encoding="utf-8")

        with self.assertRaises(IsADirectoryError):
            self.tools.delete_file({"path": str(folder)})
        self.assertTrue(folder.is_dir())

        message = self.tools.delete_file(
            {"path": str(folder), "recursive": True}
        )
        self.assertIn("已删除目录", message)
        self.assertFalse(folder.exists())

    def test_move_file_moves_and_renames(self) -> None:
        source = self.root / "old.txt"
        source.write_text("hello", encoding="utf-8")
        target = self.root / "archive" / "new.txt"

        message = self.tools.move_file(
            {"source": str(source), "destination": str(target)}
        )
        self.assertIn("已移动文件", message)
        self.assertFalse(source.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "hello")

    def test_move_file_refuses_overwrite(self) -> None:
        first = self.root / "a.txt"
        second = self.root / "b.txt"
        first.write_text("a", encoding="utf-8")
        second.write_text("b", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            self.tools.move_file(
                {"source": str(first), "destination": str(second)}
            )


class CurrentTimeToolTest(unittest.TestCase):
    def test_leap_year_and_valid_date(self) -> None:
        self.assertEqual(parse_date("2024-02-29").isoformat(), "2024-02-29")

    def test_invalid_date_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_date("2023-02-29")
        with self.assertRaises(ValueError):
            parse_date("2024-13-01")

    def test_get_current_time_reports_invalid_date(self) -> None:
        text = get_current_time({"date": "2023-02-29"})
        self.assertIn("无效日期", text)
        self.assertIn("2023 年 2 月只有 28 天", text)

    def test_get_current_time_reports_weekday_and_week(self) -> None:
        text = get_current_time({"date": "2026-11-09"})
        self.assertIn("有效日期", text)
        self.assertIn("星期一", text)
        self.assertIn("ISO 周", text)
        self.assertIn("第 4 季度", text)


class AgentRulesInjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_agent_md_is_prepended_to_system_prompt(self) -> None:
        rules = self.root / "AGENT.md"
        rules.write_text("# 长期规则\n必须使用简体中文。", encoding="utf-8")

        registry = build_default_registry(
            AgentConfig(), SearchConfig(), project_root=self.root
        )
        agent = Agent(
            _ScriptedClient(),
            registry,
            AgentConfig(),
            project_root=str(self.root),
        )
        self.assertTrue(agent.system_prompt.startswith(AGENT_RULES_MARKER))
        self.assertIn("必须使用简体中文", agent.messages[0]["content"])
        self.assertEqual(agent.agent_rules_path, str(rules.resolve()))

    def test_missing_agent_md_does_not_break_startup(self) -> None:
        registry = build_default_registry(
            AgentConfig(), SearchConfig(), project_root=self.root
        )
        agent = Agent(
            _ScriptedClient(),
            registry,
            AgentConfig(),
            project_root=str(self.root),
        )
        self.assertNotIn(AGENT_RULES_MARKER, agent.system_prompt)
        self.assertIsNone(load_agent_rules(self.root / "AGENT.md"))


class AgronomyToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.config = AgentConfig(
            agronomy_sandbox_dir=str(self.root / "sandbox"),
            agronomy_output_dir=str(self.root / "outputs"),
            agronomy_code_timeout=30,
        )
        self.toolbox = AgronomyToolbox(self.config, project_root=self.root)

        self.csv = self.root / "trial.csv"
        self.csv.write_text(
            "品种,产量\n"
            "A,10.1\nA,10.3\nA,10.2\nA,10.0\n"
            "B,12.1\nB,12.3\nB,12.2\nB,12.0\n"
            "C,11.0\nC,11.2\nC,11.1\nC,11.3\n",
            encoding="utf-8-sig",
        )

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_local_file_reader_and_analysis(self) -> None:
        preview = self.toolbox.local_file_reader(
            {"path": str(self.csv), "max_rows": 3}
        )
        self.assertIn("品种", preview)
        self.assertIn("列数", preview)

        result = self.toolbox.field_trial_analysis(
            {
                "path": str(self.csv),
                "group_column": "品种",
                "value_column": "产量",
                "outlier_method": "none",
                "plot": False,
            }
        )
        self.assertIn("单因素方差分析", result)
        self.assertIn("LSD", result)
        self.assertIn("Tukey HSD", result)
        self.assertIn("显著性字母分组", result)

        outputs = list((self.root / "outputs").glob("field_trial_*"))
        self.assertTrue(any(path.suffix == ".json" for path in outputs))
        self.assertTrue(any(path.suffix == ".csv" for path in outputs))

    def test_python_sandbox_and_report(self) -> None:
        output = self.toolbox.python_code_interpreter(
            {"code": "print('hello agronomy')", "filename": "hello.py"}
        )
        self.assertIn("退出码：0", output)
        self.assertIn("hello agronomy", output)
        self.assertTrue((self.root / "sandbox" / "hello.py").is_file())

        report = self.toolbox.report_generator(
            {
                "title": "测试报告",
                "content": "## 结论\nA 品种产量最高。",
                "format": "md",
            }
        )
        self.assertIn("报告已生成", report)
        reports = list((self.root / "outputs").glob("report_*.md"))
        self.assertEqual(len(reports), 1)
        self.assertIn("测试报告", reports[0].read_text(encoding="utf-8"))


class RegistryContainsNewToolsTest(unittest.TestCase):
    def test_new_tools_are_registered(self) -> None:
        registry = build_default_registry(
            AgentConfig(), SearchConfig(), project_root="."
        )
        for name in (
            "deleteFile",
            "moveFile",
            "getCurrentTime",
            "LocalFileReader",
            "PythonCodeInterpreter",
            "FieldTrialAnalysis",
            "ReportGenerator",
        ):
            self.assertIn(name, registry.names)


if __name__ == "__main__":
    unittest.main()
