"""RunCli 工具：以当前用户权限执行本地命令行程序。

设计要点：
* ``command`` 与 ``args`` 始终拼装成参数列表，通过 ``shell=False`` 执行，
  不把用户输入拼进 Shell 命令字符串，避免被 shell 解释；
* 返回 stdout、stderr 和退出码，让模型据此判断命令是否成功；
* 超时会终止进程，并回传已捕获的部分输出；
* 单路输出超过上限时截断，并明确标注原始长度与未显示部分。

教学阶段不设命令白名单、黑名单或审批机制，仅用于受控实验环境。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Mapping

from ..config import AgentConfig
from ..errors import ToolError
from ..utils.text import clip_text, decode_text
from .base import ToolSpec, object_schema, text_argument


def _parse_args(arguments: Mapping[str, Any]) -> list[str]:
    """把 args 规范成字符串列表；格式错误时给出可操作的提示。"""
    raw = (arguments or {}).get("args", [])
    if raw is None:
        return []
    if isinstance(raw, str):
        raise ToolError(
            'args 必须是字符串数组，例如 ["--version"]；'
            "不要把多个参数写成一个字符串"
        )
    if not isinstance(raw, (list, tuple)):
        raise ToolError(
            f"参数 args 必须是字符串数组，收到的是 {type(raw).__name__}"
        )

    parsed: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str):
            raise ToolError(
                f"args[{index}] 必须是字符串，收到的是 {type(item).__name__}；"
                '数字或布尔值请写成字符串，例如 "10"'
            )
        parsed.append(item)
    return parsed


def _decode_output(data: bytes | str | None) -> str:
    """解码子进程输出：Windows 中文环境可能是 GBK，统一按 UTF-8 / GBK 尝试。"""
    if not data:
        return ""
    if isinstance(data, str):
        return data
    text, _ = decode_text(data, strict=False)
    return text


def _format_argv(argv: list[str]) -> str:
    """生成便于阅读的命令展示文本（仅用于结果说明，不用于执行）。"""
    parts: list[str] = []
    for value in argv:
        if value and not any(char.isspace() for char in value):
            parts.append(value)
        else:
            parts.append(json.dumps(value, ensure_ascii=False))
    return " ".join(parts)


def _clip_stream(text: str, limit: int, label: str) -> tuple[str, bool]:
    """截断单路输出，并返回是否发生截断。"""
    if len(text) <= limit:
        return text, False
    kept = text[:limit]
    note = (
        f"\n...（{label} 已截断：原始共 {len(text)} 字符，"
        f"仅显示前 {limit} 字符，剩余 {len(text) - limit} 字符未显示）"
    )
    return kept + note, True


class CliToolbox:
    """命令行工具集合。"""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()

    def run_cli(self, arguments: Mapping[str, Any]) -> str:
        command = text_argument(arguments, "command", required=True)
        args = _parse_args(arguments)
        argv = [command, *args]

        # 解析成真实路径，兼容 Windows 上的 npm.cmd / pip.cmd 等批处理入口。
        executable = shutil.which(command) or command
        timeout = max(1, int(self.config.cli_timeout_seconds))

        try:
            # 参数列表 + shell=False：命令与参数不会被 shell 解析。
            # stdin 设为 DEVNULL，避免交互式程序等待输入导致长时间占用终端。
            completed = subprocess.run(
                [executable, *args],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return self._render(
                argv,
                stdout=_decode_output(exc.stdout),
                stderr=_decode_output(exc.stderr),
                exit_code=None,
                timeout=timeout,
                timed_out=True,
            )
        except FileNotFoundError as exc:
            raise ToolError(
                f"找不到可执行的程序：{command!r}。"
                "请确认程序已安装并加入 PATH；如果命令带参数，"
                "请把程序名放在 command，把参数放进 args"
            ) from exc
        except PermissionError as exc:
            raise ToolError(f"没有权限执行程序：{command!r}（{exc}）") from exc
        except OSError as exc:
            raise ToolError(f"启动程序失败：{command!r}（{exc}）") from exc

        return self._render(
            argv,
            stdout=_decode_output(completed.stdout),
            stderr=_decode_output(completed.stderr),
            exit_code=completed.returncode,
        )

    def _render(
        self,
        argv: list[str],
        *,
        stdout: str,
        stderr: str,
        exit_code: int | None,
        timeout: int = 0,
        timed_out: bool = False,
    ) -> str:
        """把执行结果整理成模型易读的文本。"""
        limit = max(1, int(self.config.cli_max_output_chars))
        stdout_text, stdout_truncated = _clip_stream(stdout, limit, "stdout")
        stderr_text, stderr_truncated = _clip_stream(stderr, limit, "stderr")

        lines = [f"命令：{_format_argv(argv)}"]
        if timed_out:
            lines.append(f"退出码：无（执行超过 {timeout} 秒，进程已被终止）")
            lines.append("状态：超时（以下为超时前捕获到的部分输出）")
        else:
            lines.append(f"退出码：{exit_code}")
            lines.append(
                "状态：成功" if exit_code == 0 else "状态：失败（命令返回非零退出码）"
            )

        lines.append("")
        stdout_header = f"标准输出（stdout，共 {len(stdout)} 字符"
        if stdout_truncated:
            stdout_header += f"，已截断为前 {limit} 字符"
        lines.append(stdout_header + "）：")
        lines.append(stdout_text if stdout_text else "（无输出）")

        lines.append("")
        stderr_header = f"标准错误（stderr，共 {len(stderr)} 字符"
        if stderr_truncated:
            stderr_header += f"，已截断为前 {limit} 字符"
        lines.append(stderr_header + "）：")
        lines.append(stderr_text if stderr_text else "（无输出）")

        return clip_text("\n".join(lines), self.config.max_tool_result_chars)

    def specs(self) -> list[ToolSpec]:
        """生成 RunCli 工具的声明。"""
        return [
            ToolSpec(
                name="RunCli",
                description=(
                    "执行本地已安装的命令行程序（例如 python、git、pip、ipconfig），"
                    "返回标准输出、标准错误和退出码；"
                    "command 只写程序名或可执行文件路径，参数必须放进 args 数组，"
                    "命令不经过 Shell 解析"
                ),
                parameters=object_schema(
                    properties={
                        "command": {
                            "type": "string",
                            "description": "要执行的程序名或可执行文件路径，例如 python",
                        },
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "参数列表，每个参数单独一个字符串，"
                                '例如 ["--version"]；不要写成一个完整命令字符串'
                            ),
                        },
                    },
                    required=["command"],
                ),
                handler=self.run_cli,
                example_arguments={"command": "python", "args": ["--version"]},
            )
        ]


__all__ = ["CliToolbox"]
