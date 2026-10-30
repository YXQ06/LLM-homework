"""系统类工具：获取系统时间、判断闰年。"""

from __future__ import annotations

import os
import subprocess

from .base import ToolSpec, object_schema, text_argument  # noqa: F401  (统一出口)


def get_date() -> str:
    """调用本地 date 命令获取系统日期和时间。

    Windows 下执行 ``date /t`` 与 ``time /t``（date 无参数会进入交互式输入，
    因此加 /t 只打印当前值）；其它平台执行 ``date`` 命令并带格式化参数。
    """
    if os.name == "nt":
        commands = [["cmd", "/c", "date", "/t"], ["cmd", "/c", "time", "/t"]]
    else:
        commands = [["date", "+%Y-%m-%d %H:%M:%S"]]

    outputs: list[str] = []
    for command in commands:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",  # 命令输出编码与系统不一致时避免崩溃
            timeout=10,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(
                f"执行命令 {' '.join(command)} 失败：{stderr or result.returncode}"
            )
        line = (result.stdout or "").strip()
        if line:
            outputs.append(line)

    if not outputs:
        raise RuntimeError("date 命令没有返回内容")
    return " ".join(outputs)


def is_leap_year(year: int) -> bool:
    """闰年规则：能被 4 整除但不能被 100 整除；整百年能被 400 整除。"""
    year = int(year)
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def _tool_get_date(arguments: object) -> str:
    return get_date()


def _tool_is_leap_year(arguments: object) -> str:
    year = (arguments or {}).get("year")
    try:
        leap = is_leap_year(year)
    except (TypeError, ValueError):
        return f"isLeapYear 参数错误：year 必须是整数年份，收到的是 {year!r}"
    return f"{int(year)} 年{'是' if leap else '不是'}闰年"


def build_system_tools() -> list[ToolSpec]:
    """返回系统类工具声明。"""
    return [
        ToolSpec(
            name="getDate",
            description="获取当前电脑的日期和时间（通过本地 date 命令读取系统时间）",
            parameters=object_schema(),
            handler=_tool_get_date,
            example_arguments={},
        ),
        ToolSpec(
            name="isLeapYear",
            description="计算输入的年份是否是闰年",
            parameters=object_schema(
                properties={
                    "year": {
                        "type": "integer",
                        "description": "要判断的年份，例如 2024",
                    }
                },
                required=["year"],
            ),
            handler=_tool_is_leap_year,
            example_arguments={"year": 2024},
        ),
    ]
