"""工具层的统一出口。

界面或 Agent 只需要调用 ``build_default_registry()``，
不需要知道具体有哪些工具、它们如何实现。
"""

from __future__ import annotations

from ..config import AgentConfig, SearchConfig
from .base import ToolRegistry, ToolResult, ToolSpec
from .cli import CliToolbox
from .files import FileToolbox
from .search import SearchToolbox
from .system import build_system_tools
from .web import WebToolbox


def build_default_registry(config: AgentConfig | None = None, search_config: SearchConfig | None = None) -> ToolRegistry:
    """组装默认工具集：10 个文件工具 + 2 个系统工具 + 1 个网页工具 + 1 个搜索工具 + 1 个命令行工具。"""
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
    return registry


__all__ = [
    "CliToolbox",
    "FileToolbox",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "WebToolbox",
    "SearchToolbox",
    "build_default_registry",
]
