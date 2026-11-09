"""系统类工具：获取系统时间、判断闰年、校验日期。

``getDate`` 沿用原来的实现（调用本地 ``date`` / ``time`` 命令）。
``getCurrentTime`` 是新增工具：完全使用 Python 标准库计算，内置闰年、
每月天数、ISO 周、季度等日期判断逻辑，并支持校验用户给出的日期。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import date, datetime

from .base import ToolSpec, object_schema, text_argument

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_DATE_PATTERN = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


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


def days_in_month(year: int, month: int) -> int:
    """返回指定年月的天数，内部包含年份/月份范围校验和闰年判断。"""
    year = int(year)
    month = int(month)
    if not 1 <= month <= 12:
        raise ValueError(f"月份必须在 1~12 之间，收到：{month}")
    if month == 2 and is_leap_year(year):
        return 29
    return (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]


def parse_date(text: str) -> date:
    """解析 ``YYYY-MM-DD`` 并校验日期真实存在（例如 2023-02-29 会被拒绝）。"""
    match = _DATE_PATTERN.match(str(text or "").strip())
    if not match:
        raise ValueError(f"日期格式必须为 YYYY-MM-DD，收到：{text!r}")
    year, month, day = (int(item) for item in match.groups())
    if not 1 <= month <= 12:
        raise ValueError(f"月份必须在 1~12 之间，收到：{month}")
    maximum = days_in_month(year, month)
    if not 1 <= day <= maximum:
        raise ValueError(
            f"{year} 年 {month} 月只有 {maximum} 天，收到：{day}"
        )
    return date(year, month, day)


def _describe_date(value: date) -> list[str]:
    """生成单个日期的详细信息（星期、年内天数、ISO 周、季度、闰年）。"""
    iso = value.isocalendar()
    quarter = (value.month - 1) // 3 + 1
    return [
        f"日期：{value.isoformat()}",
        f"星期：{_WEEKDAYS[value.weekday()]}",
        f"年内第 {value.timetuple().tm_yday} 天",
        f"ISO 周：{iso.year}-W{iso.week:02d}",
        f"季度：第 {quarter} 季度",
        f"当月天数：{days_in_month(value.year, value.month)} 天",
        f"{value.year} 年{'是' if is_leap_year(value.year) else '不是'}闰年",
    ]


def _now_in_timezone(timezone: str) -> tuple[datetime, str]:
    """获取指定时区的当前时间；时区为空或无效时回退到本机时区。"""
    name = (timezone or "").strip()
    if name:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(name)), f"时区：{name}"
        except Exception:
            # 无效时区不报错，回退到本地时间并明确标注。
            return datetime.now().astimezone(), f"时区 {name} 无效，已回退到本机时区"
    return datetime.now().astimezone(), "时区：本机时区"


def get_current_time(arguments: object) -> str:
    """返回当前时间；可选 ``date`` 参数用于校验指定日期。"""
    args = arguments if isinstance(arguments, dict) else {}
    timezone = text_argument(args, "timezone", default="")
    date_text = text_argument(args, "date", default="")

    now, zone_note = _now_in_timezone(timezone)
    lines = [
        f"当前时间：{now.isoformat(timespec='seconds')}",
        f"日期：{now.date().isoformat()}",
        f"星期：{_WEEKDAYS[now.weekday()]}",
        f"时间戳（Unix 秒）：{int(time.time())}",
        zone_note,
        f"{now.year} 年{'是' if is_leap_year(now.year) else '不是'}闰年",
        f"本月（{now.month} 月）共 {days_in_month(now.year, now.month)} 天",
    ]

    if date_text:
        lines.append("")
        lines.append(f"日期校验：{date_text}")
        try:
            parsed = parse_date(date_text)
        except ValueError as exc:
            lines.append(f"结果：无效日期（{exc}）")
        else:
            lines.append("结果：有效日期")
            lines.extend(_describe_date(parsed))
            delta = (parsed - now.date()).days
            lines.append(f"与今天相差：{delta} 天")

    return "\n".join(lines)


def _tool_get_date(arguments: object) -> str:
    return get_date()


def _tool_get_current_time(arguments: object) -> str:
    return get_current_time(arguments)


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
            name="getCurrentTime",
            description=(
                "获取当前系统的日期、时间、星期、ISO 周和季度；"
                "内置闰年与每月天数判断，可选 date 参数校验指定日期（YYYY-MM-DD）"
            ),
            parameters=object_schema(
                properties={
                    "timezone": {
                        "type": "string",
                        "description": "可选 IANA 时区，例如 Asia/Shanghai；省略时使用本机时区",
                    },
                    "date": {
                        "type": "string",
                        "description": "可选，要校验的日期，格式 YYYY-MM-DD",
                    },
                },
                required=[],
            ),
            handler=_tool_get_current_time,
            example_arguments={"timezone": "Asia/Shanghai", "date": "2024-02-29"},
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
