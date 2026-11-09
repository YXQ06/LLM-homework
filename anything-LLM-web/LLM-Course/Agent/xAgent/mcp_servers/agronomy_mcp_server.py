"""耕云农学 MCP Server（stdio）。

把 ``core.tools.agronomy`` 中的本地农学能力暴露成标准 MCP 工具，
既可以由 xAgent 通过 ``.mcp.json`` 以热插拔方式连接，也可以被
AnythingLLM、Claude Desktop 等支持 MCP 的客户端直接使用。

工具列表
--------
* ``ReadFieldData``       读取 CSV / TSV / Excel 试验数据
* ``AnalyzeFieldTrial``   异常值 + ANOVA + LSD / Tukey + 字母分组
* ``RunPythonSandbox``    在 .agents/sandbox 中执行 Python 代码
* ``GenerateTrialReport`` 生成 Markdown / Word 分析报告

启动方式（在 xAgent 目录执行）::

    python mcp_servers/agronomy_mcp_server.py

依赖：``mcp>=1.9``（本脚本同时兼容 1.x FastMCP 与 2.x MCPServer）。
本项目默认在 ``.mcp.json`` 中以 ``"enabled": false`` 提供配置示例，
需要时把 enabled 改成 true 即可，不影响原有的 Bazi / PetHospital /
AnythingLLM 等 MCP Server。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 允许以 ``python mcp_servers/agronomy_mcp_server.py`` 直接运行：
# 脚本所在目录是 mcp_servers/，需要把 xAgent 根目录加入 sys.path。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import AgentConfig, load_config  # noqa: E402
from core.tools.agronomy import AgronomyToolbox  # noqa: E402


def _build_toolbox() -> AgronomyToolbox:
    """优先使用 xAgent/config.ini 的农学配置；读取失败时退回默认值。"""
    config: AgentConfig
    try:
        config = load_config().agent
    except Exception:
        config = AgentConfig()
    return AgronomyToolbox(config, project_root=os.getcwd())


TOOLBOX = _build_toolbox()


def _safe(call, **arguments) -> str:
    """统一把业务异常转换成可读文本，避免 MCP 连接因单次调用失败中断。"""
    try:
        return call(arguments)
    except Exception as exc:  # noqa: BLE001 - MCP 工具边界需要兜底
        return f"工具执行失败：{exc}"


# ---------------------------------------------------------------------------
# MCP 框架兼容层：mcp 2.x 使用 MCPServer，1.x 使用 FastMCP
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 取决于安装的 mcp 版本
    from mcp.server.mcpserver import MCPServer as _Server

    _SERVER_STYLE = "mcp2"
except Exception:  # pragma: no cover
    try:
        from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]

        _SERVER_STYLE = "mcp1"
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            "未安装可用的 MCP Python SDK。请执行：pip install 'mcp>=1.9'"
        ) from exc


server = _Server(
    name="AgronomyMCP",
    instructions=(
        "耕云农学田间试验辅助工具：读取试验数据、异常值检测、单因素方差分析、"
        "LSD/Tukey 多重比较、显著性字母分组、Python 沙盒与报告生成。"
    ),
)


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------


@server.tool(
    name="ReadFieldData",
    description="读取本地 CSV / TSV / Excel 农学试验数据，返回工作表、列名、行数与前 N 行预览。",
)
def read_field_data(path: str, sheet: str = "", max_rows: int = 30) -> str:
    return _safe(
        TOOLBOX.local_file_reader,
        path=path,
        sheet=sheet,
        max_rows=max_rows,
    )


@server.tool(
    name="AnalyzeFieldTrial",
    description=(
        "农学田间试验统计分析：按分组列自动分组，做 3σ/IQR 异常值检测、单因素 ANOVA、"
        "LSD 与 Tukey HSD 多重比较、显著性字母分组，并在本地保存 CSV / JSON / PNG。"
    ),
)
def analyze_field_trial(
    path: str,
    group_column: str,
    value_column: str,
    sheet: str = "",
    outlier_method: str = "both",
    alpha: float = 0.05,
    plot: bool = True,
) -> str:
    return _safe(
        TOOLBOX.field_trial_analysis,
        path=path,
        group_column=group_column,
        value_column=value_column,
        sheet=sheet,
        outlier_method=outlier_method,
        alpha=alpha,
        plot=plot,
    )


@server.tool(
    name="RunPythonSandbox",
    description=(
        "在项目内 .agents/sandbox 隔离子进程中执行 Python 代码，限定工作目录、"
        "超时终止、输出截断，适合 pandas/numpy 数据清洗与自定义统计。"
    ),
)
def run_python_sandbox(code: str, filename: str = "") -> str:
    return _safe(TOOLBOX.python_code_interpreter, code=code, filename=filename)


@server.tool(
    name="GenerateTrialReport",
    description=(
        "把分析结论生成为 Markdown（可选 Word .docx）报告并保存到本地，"
        "可附加原始分析结果文件。"
    ),
)
def generate_trial_report(
    title: str,
    content: str,
    output_path: str = "",
    format: str = "md",
    source: str = "",
) -> str:
    return _safe(
        TOOLBOX.report_generator,
        title=title,
        content=content,
        output_path=output_path,
        format=format,
        source=source,
    )


def main() -> None:
    """以 stdio 传输启动 MCP Server（xAgent 的 stdio MCP Client 会调用）。"""
    print(f"[AgronomyMCP] 已启动（{_SERVER_STYLE}），项目目录：{PROJECT_ROOT}", file=sys.stderr)
    try:
        asyncio.run(server.run_stdio_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
