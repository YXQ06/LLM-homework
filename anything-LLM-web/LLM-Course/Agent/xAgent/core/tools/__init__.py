"""工具层的统一出口。

界面或 Agent 只需要调用 ``build_default_registry()``，
不需要知道具体有哪些工具、它们如何实现。
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import AgentConfig, SearchConfig
from .agronomy import AgronomyToolbox
from .base import ToolRegistry, ToolResult, ToolSpec
from .cli import CliToolbox
from .files import FileToolbox
from .recall import build_recall_tool
from .search import SearchToolbox
from .system import build_system_tools
from .web import WebToolbox


def build_default_registry(
    config: AgentConfig | None = None,
    search_config: SearchConfig | None = None,
    *,
    project_root: str | os.PathLike[str] | None = None,
) -> ToolRegistry:
    """组装默认工具集：本地文件、系统、网页、搜索、命令行和上下文检索工具。"""
    agent_config = config or AgentConfig()
    registry = ToolRegistry()
    for spec in FileToolbox(agent_config).specs():
        registry.register(spec)
    for spec in build_system_tools():
        registry.register(spec)
    for spec in WebToolbox(agent_config).specs():
        registry.register(spec)
    for spec in SearchToolbox(agent_config, search_config).specs():
        registry.register(spec)
    for spec in CliToolbox(agent_config).specs():
        registry.register(spec)

    # 农学田间试验工具：数据读取、Python 沙盒、统计分析、报告生成。
    # 与 MCP 工具一样按需注册，MCP 热插拔不影响这里的本地工具。
    for spec in AgronomyToolbox(
        agent_config, project_root=project_root
    ).specs():
        registry.register(spec)

    # recallContext 与压缩流程共用同一个归档目录。
    root = Path(project_root or os.getcwd()).resolve()
    archive_dir = Path(
        agent_config.context_archive_dir or ".agents/context"
    ).expanduser()
    if not archive_dir.is_absolute():
        archive_dir = root / archive_dir
    registry.register(
        build_recall_tool(
            archive_dir,
            max_chars=agent_config.recall_max_chars,
            max_results=agent_config.recall_max_results,
        )
    )
    return registry


__all__ = [
    "AgronomyToolbox",
    "CliToolbox",
    "FileToolbox",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "WebToolbox",
    "SearchToolbox",
    "build_default_registry",
]
