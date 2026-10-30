"""RunCli 工具的离线单元测试（不真正启动外部程序）。"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from xAgent.core.config import AgentConfig
from xAgent.core.errors import ToolError
from xAgent.core.prompts import build_system_prompt
from xAgent.core.tools import build_default_registry
from xAgent.core.tools.cli import CliToolbox


def completed(
    stdout: bytes = b"", stderr: bytes = b"", code: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["python"], returncode=code, stdout=stdout, stderr=stderr
    )


class RunCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.toolbox = CliToolbox(AgentConfig(cli_timeout_seconds=5))

    def test_command_and_args_are_passed_as_list_without_shell(self) -> None:
        with (
            patch(
                "xAgent.core.tools.cli.shutil.which",
                return_value=r"C:\Python313\python.exe",
            ),
            patch(
                "xAgent.core.tools.cli.subprocess.run",
                return_value=completed(stdout=b"Python 3.13.0\n"),
            ) as run,
        ):
            result = self.toolbox.run_cli(
                {"command": "python", "args": ["-c", "print('hi')"]}
            )

        argv = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertIsInstance(argv, list)
        self.assertEqual(
            argv, [r"C:\Python313\python.exe", "-c", "print('hi')"]
        )
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["timeout"], 5)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertFalse(kwargs["check"])

        self.assertIn("退出码：0", result)
        self.assertIn("状态：成功", result)
        self.assertIn("Python 3.13.0", result)
        self.assertIn("标准错误（stderr", result)
        self.assertIn("（无输出）", result)

    def test_nonzero_exit_code_is_returned_to_the_model(self) -> None:
        registry = build_default_registry(AgentConfig())
        with (
            patch(
                "xAgent.core.tools.cli.shutil.which", return_value="git.exe"
            ),
            patch(
                "xAgent.core.tools.cli.subprocess.run",
                return_value=completed(stdout=b"", stderr=b"fatal: not a repo", code=128),
            ),
        ):
            tool_result = registry.execute(
                "RunCli", {"command": "git", "args": ["status"]}
            )

        self.assertTrue(tool_result.ok)
        self.assertIn("退出码：128", tool_result.content)
        self.assertIn("状态：失败", tool_result.content)
        self.assertIn("fatal: not a repo", tool_result.content)

    def test_timeout_returns_partial_output(self) -> None:
        toolbox = CliToolbox(AgentConfig(cli_timeout_seconds=2))
        with (
            patch("xAgent.core.tools.cli.shutil.which", return_value="slow.exe"),
            patch(
                "xAgent.core.tools.cli.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    cmd="slow.exe",
                    timeout=2,
                    output=b"partial out\n",
                    stderr=b"partial err\n",
                ),
            ),
        ):
            result = toolbox.run_cli({"command": "slow", "args": []})

        self.assertIn("超时", result)
        self.assertIn("退出码：无", result)
        self.assertIn("partial out", result)
        self.assertIn("partial err", result)

    def test_long_output_is_truncated_with_explicit_marker(self) -> None:
        toolbox = CliToolbox(
            AgentConfig(cli_max_output_chars=10, max_tool_result_chars=100_000)
        )
        output = b"0123456789ABCDEF"
        with (
            patch("xAgent.core.tools.cli.shutil.which", return_value="echo.exe"),
            patch(
                "xAgent.core.tools.cli.subprocess.run",
                return_value=completed(stdout=output),
            ),
        ):
            result = toolbox.run_cli({"command": "echo", "args": []})

        self.assertIn("共 16 字符", result)
        self.assertIn("已截断为前 10 字符", result)
        self.assertIn("stdout 已截断：原始共 16 字符", result)
        self.assertIn("剩余 6 字符未显示", result)
        self.assertNotIn("ABCDEF", result)

    def test_gbk_output_is_decoded(self) -> None:
        with (
            patch("xAgent.core.tools.cli.shutil.which", return_value="cmd.exe"),
            patch(
                "xAgent.core.tools.cli.subprocess.run",
                return_value=completed(stdout="中文输出\n".encode("gbk")),
            ),
        ):
            result = self.toolbox.run_cli({"command": "cmd", "args": ["/c", "echo"]})

        self.assertIn("中文输出", result)

    def test_missing_program_raises_tool_error(self) -> None:
        with (
            patch("xAgent.core.tools.cli.shutil.which", return_value=None),
            patch(
                "xAgent.core.tools.cli.subprocess.run",
                side_effect=FileNotFoundError,
            ),
        ):
            with self.assertRaises(ToolError) as ctx:
                self.toolbox.run_cli({"command": "definitely-not-installed"})

        self.assertIn("找不到可执行的程序", str(ctx.exception))
        self.assertIn("PATH", str(ctx.exception))

    def test_arguments_are_validated(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            self.toolbox.run_cli({})
        self.assertIn("command", str(ctx.exception))

        with self.assertRaises(ToolError) as ctx:
            self.toolbox.run_cli({"command": "python", "args": "--version"})
        self.assertIn("字符串数组", str(ctx.exception))

        with self.assertRaises(ToolError) as ctx:
            self.toolbox.run_cli({"command": "python", "args": [1]})
        self.assertIn("args[0]", str(ctx.exception))

        with self.assertRaises(ToolError) as ctx:
            self.toolbox.run_cli({"command": "python", "args": {"a": 1}})
        self.assertIn("字符串数组", str(ctx.exception))

    def test_run_cli_is_registered_and_declared(self) -> None:
        registry = build_default_registry(AgentConfig())
        self.assertIn("RunCli", registry.names)

        spec = registry.get("RunCli")
        assert spec is not None
        self.assertEqual(spec.required_parameters, ("command",))
        args_schema = spec.parameters["properties"]["args"]
        self.assertEqual(args_schema["type"], "array")
        self.assertEqual(args_schema["items"]["type"], "string")
        self.assertEqual(spec.to_openai_schema()["function"]["name"], "RunCli")
        self.assertIn("RunCli", build_system_prompt(registry))


if __name__ == "__main__":
    unittest.main()
