"""MCP Client 公共入口。

典型用法（CLI / TUI / 未来 Web 界面通用）::

    from xAgent.core.mcp import create_mcp_manager, register_mcp_tools

    manager = create_mcp_manager(config.agent)
    register_mcp_tools(registry, manager)   # 连接 + 发现 + 注册
    for line in manager.startup_messages():
        print(line)

``register_mcp_tools()`` 会把本地工具名作为保留名传给 ``manager.start()``，
确保 MCP 工具不会覆盖 ``RunCli``、``fetchWeb``、``loadSkill`` 等本地工具。
如果直接调用 ``manager.start()``，请自行传入 ``registry.names``。
"""

from __future__ import annotations

from typing import Any

from .client import McpManager, McpServerStatus
from .config import (
    MCP_CONFIG_ENV,
    MCP_CONFIG_FILENAME,
    SUPPORTED_TRANSPORTS,
    McpAutoStart,
    McpConfig,
    McpServerConfig,
    find_mcp_config_path,
    load_mcp_config,
)
from .tools import (
    DiscoveredTool,
    McpToolBuildResult,
    McpToolRoute,
    build_mcp_tools,
    format_call_result,
)

__all__ = [
    "MCP_CONFIG_ENV",
    "MCP_CONFIG_FILENAME",
    "SUPPORTED_TRANSPORTS",
    "DiscoveredTool",
    "McpAutoStart",
    "McpConfig",
    "McpManager",
    "McpServerConfig",
    "McpServerStatus",
    "McpToolBuildResult",
    "McpToolRoute",
    "build_mcp_tools",
    "create_mcp_manager",
    "find_mcp_config_path",
    "format_call_result",
    "load_mcp_config",
    "register_mcp_tools",
]


def create_mcp_manager(
    agent_config: Any | None = None,
    *,
    project_root: str | None = None,
    path: str | None = None,
) -> McpManager:
    """读取 ``.mcp.json`` 并创建管理器（此时尚未连接）。"""
    config = load_mcp_config(
        path,
        project_root=project_root,
        default_timeout=float(
            getattr(agent_config, "mcp_timeout", 60.0) or 60.0
        ),
        default_connect_timeout=float(
            getattr(agent_config, "mcp_connect_timeout", 20.0) or 20.0
        ),
    )
    max_chars = int(
        getattr(agent_config, "max_tool_result_chars", 12_000) or 12_000
    )
    return McpManager(
        config,
        max_result_chars=max_chars,
        startup_wait_extra=float(
            getattr(agent_config, "mcp_startup_wait_extra", 15.0) or 0.0
        ),
    )


def register_mcp_tools(registry: Any, manager: McpManager) -> list[str]:
    """连接 Server 并把发现的工具注册到现有 ``ToolRegistry``。"""
    registered: list[str] = []
    for spec in manager.start(getattr(registry, "names", ())):
        if spec.name in registry:
            # 正常情况下 build_mcp_tools 已避让本地工具名；这里只是兜底。
            continue
        registry.register(spec)
        registered.append(spec.name)
    return registered
